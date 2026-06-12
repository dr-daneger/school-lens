"""Attendance-boundary ingest via the reused house-hunter ArcGIS puller.

Flow (two idempotent passes, mirroring house-hunter's fetch/ingest split):
1. ingest_source(): for a named district source, walk each of its service roots,
   discover the attendance-area polygon layers, pull each with
   house_hunter.query_arcgis_layer (WGS84), reproject to 2913 with the reused
   normalize helpers, and land rows in `boundaries` with raw_name + level +
   geometry. nces_id is left null.
2. resolve_boundaries(): for rows where nces_id IS NULL, match raw_name + centroid
   to a school via _crosswalk.match_by_name_geo and fill nces_id +
   match_confidence. Re-runnable; only touches unresolved rows.

house_hunter is imported lazily so the rest of school-lens imports without it
installed. Install it first: pip install -e ../house-hunter.

Three verified sources, two service shapes (docs/data-sources.md):
- cop_metro  : City of Portland School_Boundaries FeatureServer (AGOL), ONE
               composite polygon layer of catchment cells covering the metro
               region; each cell names its assigned school per grade band in
               attributes (Grade_1/6/10_Choice1_Name). Dedicated handler
               (_ingest_cop_cells) dissolves cells into per-school catchments.
- beaverton  : one FeatureServer (FLO Analytics school-locator), attendance
               areas split across layers 1/2/3 (level read from the layer name).
- hillsboro  : three FeatureServers (Angelo Planning Group), one polygon
               attendance layer each at a non-zero, run-time-discovered layer id.

The per-level shapes share one discovery: enumerate the service's layers, keep
the polygon layers whose name mentions "attendance", and read the grade band
from the layer name. No layer ids are hard-coded. The composite shape reuses the
same discovery but reads grade band and school from cell attributes instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import duckdb

from school_lens.config import bbox, settings
from school_lens.db import get_connection
from school_lens.ingest._crosswalk import match_catchment, normalize_name

# Map a layer-name fragment to our grade-band level.
_LEVEL_HINTS = (("elementary", "elementary"), ("middle", "middle"), ("high", "high"))

# Attribute keys boundary layers use for the school name; checked
# case-insensitively, most specific first. Verified live: SchoolName (Hillsboro),
# School_Name_Abbreviated (Beaverton), plus common PortlandMaps variants.
_NAME_FIELDS = (
    "SchoolName", "School_Name", "School_Name_Abbreviated",
    "SCHOOL_NAME", "SCHOOLNAME", "ES_NAME", "MS_NAME", "HS_NAME",
    "SCHOOL", "School", "NAME", "Name",
)
# Keys that contain "name" but are never the school name (Hillsboro layers carry
# both SchoolName and PlanName; the generic fallback must not grab PlanName).
_NAME_ANTI = ("plan", "file", "user", "path", "layer", "data", "field")


@dataclass(frozen=True)
class BoundarySource:
    """A district boundary source: a label, a vintage tag, and 1+ service roots.

    kind selects the ingest path: "attendance_layers" (one polygon layer per
    grade band, level read from the layer name) or "composite_cells" (one cell
    layer, school + level read from each cell's attributes).
    """

    key: str
    label: str          # provenance source string written to every row
    school_year: str    # default vintage tag for this source
    roots: tuple[str, ...]
    kind: str = "attendance_layers"


def boundary_sources() -> dict[str, BoundarySource]:
    """The known boundary sources, built from settings so env overrides apply."""
    return {
        "cop_metro": BoundarySource(
            "cop_metro", "City of Portland School_Boundaries (AGOL)", "2025-2026",
            (settings.cop_school_boundaries_root,),
            kind="composite_cells",
        ),
        "beaverton": BoundarySource(
            "beaverton", "Beaverton SD School Locator (FLO Analytics)", "2025-2026",
            (settings.beaverton_locator_root,),
        ),
        "hillsboro": BoundarySource(
            "hillsboro", "Hillsboro SD (Angelo Planning Group)", "2022-2023",
            (settings.hillsboro_elementary_root,
             settings.hillsboro_middle_root,
             settings.hillsboro_high_root),
        ),
    }


def _level_of(layer_name: str) -> str | None:
    low = layer_name.lower()
    for frag, level in _LEVEL_HINTS:
        if frag in low:
            return level
    return None


def _name_of(props: dict) -> str | None:
    """Best-effort school name from a feature's attributes (case-insensitive)."""
    lower = {str(k).lower(): (k, v) for k, v in props.items()}
    for field in _NAME_FIELDS:
        hit = lower.get(field.lower())
        if hit and hit[1] not in (None, ""):
            return str(hit[1])
    # Generic fallback: a key that mentions a name but is not an anti-field.
    for klow, (_k, v) in lower.items():
        if "name" in klow and v not in (None, "") and not any(a in klow for a in _NAME_ANTI):
            return str(v)
    return None


def _discover_attendance_layers(service_root: str) -> list[tuple[int, str]]:
    """Return (layer_id, name) for polygon layers whose name mentions attendance.

    Works for both MapServer and FeatureServer; the /layers endpoint is shared.
    Polygon-only so point school-location layers and the like are skipped; a
    missing geometryType is treated as a polygon (do not over-filter).
    """
    import httpx

    r = httpx.get(f"{service_root.rstrip('/')}/layers", params={"f": "json"}, timeout=60)
    r.raise_for_status()
    layers = r.json().get("layers", []) or []
    hits: list[tuple[int, str]] = []
    for lyr in layers:
        name = str(lyr.get("name", ""))
        gtype = str(lyr.get("geometryType", "")).lower()
        is_polygon = (gtype == "") or ("polygon" in gtype)
        if "attendance" in name.lower() and is_polygon:
            hits.append((lyr["id"], name))
    return hits


def _ingest_service(
    service_root: str, *, source: str, school_year: str,
    conn: duckdb.DuckDBPyConnection,
) -> int:
    """Pull every attendance polygon layer on one service into `boundaries`."""
    from school_lens._vendor.normalize import to_target  # lazy reuse
    from school_lens._vendor.arcgis import query_arcgis_layer

    ingest_ts = datetime.now(timezone.utc)
    written = 0
    for layer_id, layer_name in _discover_attendance_layers(service_root):
        level = _level_of(layer_name)
        url = f"{service_root.rstrip('/')}/{layer_id}"
        gdf = query_arcgis_layer(service_root, layer_id, bbox_wgs84=bbox())
        if gdf.empty:
            continue
        gdf = to_target(gdf)  # -> EPSG:2913
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            raw_name = _name_of(row.drop(labels="geometry").to_dict())
            conn.execute(
                """
                INSERT INTO boundaries
                    (nces_id, raw_name, match_confidence, level, school_year,
                     geometry, source, source_vintage, ingest_ts, source_url)
                VALUES (NULL, ?, NULL, ?, ?, ST_GeomFromWKB(?), ?, ?, ?, ?)
                """,
                [raw_name, level, school_year, geom.wkb, source,
                 school_year, ingest_ts, url],
            )
            written += 1
    return written


# --- City of Portland composite cell layer (source: cop_metro) ---------------
# One polygon layer of catchment cells; each cell carries its assigned school
# per grade band as attributes (field names verified live 2026-06-11). This
# breaks the one-layer-per-grade-band assumption _ingest_service makes, hence
# the dedicated handler.
_COP_DISTRICT_FIELD = "Unified_SD_Name"
_COP_LEVEL_FIELDS = (
    ("elementary", "Grade_1_Choice1_Name"),
    ("middle", "Grade_6_Choice1_Name"),
    ("high", "Grade_10_Choice1_Name"),
)
# Districts whose boundaries come from a fresher district-direct source; their
# COP cells are skipped so two loads can never ship conflicting geometry.
_COP_DISTRICT_DIRECT = {"Beaverton SD 48J", "Hillsboro SD 1J"}

# 'Jr / Sr' inside one school's name ("Gaston Jr / Sr High School") must not be
# mistaken for a dual-assignment separator.
_JR_SR_RE = re.compile(r"\bjr\.?\s*/\s*sr\b", re.IGNORECASE)


def _cell_school_names(cell_name: object) -> list[str]:
    """Split a COP cell's school attribute into one or more school names.

    A spaced " / " separates the two schools of a dual-assignment zone
    ("Jefferson / Roosevelt"). A slash can also be part of a single school's
    name: unspaced ("Boise-Eliot/Humboldt") or the Jr/Sr pattern ("Gaston
    Jr / Sr High School"); neither is split.
    """
    s = str(cell_name or "").strip()
    if not s:
        return []
    if _JR_SR_RE.search(s):
        return [s]
    return [p.strip() for p in s.split(" / ") if p.strip()]


def _ingest_cop_cells(
    service_root: str, *, source: str, school_year: str,
    conn: duckdb.DuckDBPyConnection,
) -> int:
    """Dissolve the COP cell layer into one boundary row per (school, level).

    Cells are kept only for districts already present in the schools spine
    (minus the district-direct set), so an out-of-spine cell (e.g. Clackamas
    districts before their schools are loaded) can never reach
    resolve_boundaries and name-match the wrong district's school. If the
    spine later gains those districts, a re-ingest picks their cells up with
    no code change.

    Dual-assignment cells ("Jefferson / Roosevelt") are split on "/" and
    unioned into BOTH schools' catchments. Point-in-polygon assignment inside
    such a cell reports whichever school's catchment is checked first; that
    one-of-two report is an approximation of a genuine enrollment choice.
    """
    from shapely.ops import unary_union

    from school_lens._vendor.normalize import to_target  # lazy reuse
    from school_lens._vendor.arcgis import query_arcgis_layer

    spine_districts = {
        d for (d,) in conn.execute(
            "SELECT DISTINCT district_name FROM schools WHERE district_name IS NOT NULL"
        ).fetchall()
    }
    keep = spine_districts - _COP_DISTRICT_DIRECT

    ingest_ts = datetime.now(timezone.utc)
    written = 0
    for layer_id, _layer_name in _discover_attendance_layers(service_root):
        gdf = query_arcgis_layer(service_root, layer_id, bbox_wgs84=bbox())
        if gdf.empty or _COP_DISTRICT_FIELD not in gdf.columns:
            continue
        gdf = to_target(gdf[gdf[_COP_DISTRICT_FIELD].isin(keep)])  # -> EPSG:2913
        url = f"{service_root.rstrip('/')}/{layer_id}"
        for level, field in _COP_LEVEL_FIELDS:
            if field not in gdf.columns:
                continue
            cells: dict[str, list] = {}
            for cell_name, geom in zip(gdf[field], gdf.geometry):
                if not cell_name or geom is None or geom.is_empty:
                    continue
                for school in _cell_school_names(cell_name):
                    cells.setdefault(school, []).append(geom)
            for school, geoms in cells.items():
                conn.execute(
                    """
                    INSERT INTO boundaries
                        (nces_id, raw_name, match_confidence, level, school_year,
                         geometry, source, source_vintage, ingest_ts, source_url)
                    VALUES (NULL, ?, NULL, ?, ?, ST_GeomFromWKB(?), ?, ?, ?, ?)
                    """,
                    [school, level, school_year, unary_union(geoms).wkb,
                     source, school_year, ingest_ts, url],
                )
                written += 1
    return written


def ingest_source(
    key: str, *, school_year: str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Ingest a named boundary source (cop_metro|beaverton|hillsboro). Returns rows.

    Idempotent per source: existing rows with this source's label are deleted
    before the pull, so a re-ingest refreshes in place instead of duplicating.
    Raises KeyError for an unknown key (the CLI turns that into a clean error).
    """
    src = boundary_sources()[key]
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.execute("DELETE FROM boundaries WHERE source = ?", [src.label])
        total = 0
        ingest = _ingest_cop_cells if src.kind == "composite_cells" else _ingest_service
        for root in src.roots:
            total += ingest(
                root, source=src.label,
                school_year=school_year or src.school_year, conn=conn,
            )
        return total
    finally:
        if own:
            conn.close()


# Catchment label -> spine school name, for renames that tokenization cannot
# bridge (keyed by normalize_name of the label). The COP layer says "MLK Jr";
# CCD says "Dr. Martin Luther King Jr. School"; no token survives both.
_RAW_NAME_ALIASES = {
    "mlk": "Dr. Martin Luther King Jr. School",
}


def resolve_boundaries(conn: duckdb.DuckDBPyConnection | None = None,
                       *, max_ft: float = 5280.0, min_conf: float = 0.7) -> int:
    """Resolve unresolved boundaries' raw_name -> nces_id. Idempotent. Returns count.

    Matching is the match_catchment ladder (exact then token-subset, same grade
    band before cross-band), so 'Banks Elementary School' and 'Banks High
    School' disambiguate by the boundary's level and a renamed school still
    resolves ('McDaniel' -> 'Leodis V. McDaniel High School'). min_conf rejects
    the geo-only nearest fallback (confidence 0.5), so a boundary near a
    same-named school in a neighbouring district is never mis-linked.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        # Candidate schools with a 2913 point.
        cand_rows = conn.execute(
            "SELECT nces_id, name, level, ST_X(point), ST_Y(point) "
            "FROM schools WHERE point IS NOT NULL"
        ).fetchall()
        candidates = [(cid, name, lvl, (x, y)) for cid, name, lvl, x, y in cand_rows]
        if not candidates:
            return 0
        unresolved = conn.execute(
            """
            SELECT rowid, raw_name, level,
                   ST_X(ST_Centroid(geometry)), ST_Y(ST_Centroid(geometry))
            FROM boundaries WHERE nces_id IS NULL AND raw_name IS NOT NULL
            """
        ).fetchall()
        resolved = 0
        for rid, raw_name, level, cx, cy in unresolved:
            name = _RAW_NAME_ALIASES.get(normalize_name(raw_name), raw_name)
            match = match_catchment(name, level, (cx, cy), candidates, max_ft=max_ft)
            if match is None or match[1] < min_conf:
                continue
            nces_id, conf = match
            conn.execute(
                "UPDATE boundaries SET nces_id = ?, match_confidence = ? WHERE rowid = ?",
                [nces_id, conf, rid],
            )
            resolved += 1
        return resolved
    finally:
        if own:
            conn.close()
