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
- pps        : PortlandMaps COP_OpenData MapServer (Multnomah), one MapServer
               with the attendance layers as sibling layers.
- beaverton  : one FeatureServer (FLO Analytics school-locator), attendance
               areas split across layers 1/2/3 (level read from the layer name).
- hillsboro  : three FeatureServers (Angelo Planning Group), one polygon
               attendance layer each at a non-zero, run-time-discovered layer id.

Both shapes are handled by the same discovery: enumerate the service's layers,
keep the polygon layers whose name mentions "attendance", and read the grade
band from the layer name. No layer ids are hard-coded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import duckdb

from school_lens.config import bbox, settings
from school_lens.db import get_connection
from school_lens.ingest._crosswalk import match_by_name_geo

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
    """A district boundary source: a label, a vintage tag, and 1+ service roots."""

    key: str
    label: str          # provenance source string written to every row
    school_year: str    # default vintage tag for this source
    roots: tuple[str, ...]


def boundary_sources() -> dict[str, BoundarySource]:
    """The known boundary sources, built from settings so env overrides apply."""
    return {
        "pps": BoundarySource(
            "pps", "PortlandMaps COP_OpenData", "2024-2025",
            (settings.portlandmaps_opendata_root,),
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


def ingest_source(
    key: str, *, school_year: str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Ingest a named boundary source (pps|beaverton|hillsboro). Returns rows.

    Raises KeyError for an unknown key (the CLI turns that into a clean error).
    """
    src = boundary_sources()[key]
    own = conn is None
    conn = conn or get_connection()
    try:
        total = 0
        for root in src.roots:
            total += _ingest_service(
                root, source=src.label,
                school_year=school_year or src.school_year, conn=conn,
            )
        return total
    finally:
        if own:
            conn.close()


def ingest_attendance_layers(
    mapserver_root: str | None = None,
    *,
    school_year: str = "2024-2025",
    source: str = "PortlandMaps COP_OpenData",
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Back-compat single-service ingest (defaults to PortlandMaps). Returns rows."""
    own = conn is None
    conn = conn or get_connection()
    try:
        return _ingest_service(
            mapserver_root or settings.portlandmaps_opendata_root,
            source=source, school_year=school_year, conn=conn,
        )
    finally:
        if own:
            conn.close()


def resolve_boundaries(conn: duckdb.DuckDBPyConnection | None = None,
                       *, max_ft: float = 5280.0, min_conf: float = 0.7) -> int:
    """Resolve unresolved boundaries' raw_name -> nces_id. Idempotent. Returns count.

    min_conf rejects the geo-only nearest fallback (confidence 0.5): a catchment is
    resolved only on a school-name match (0.7 unique, 0.9 name+geo), so a boundary
    near a same-named school in a neighbouring district is never mis-linked.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        # Candidate schools with a 2913 point.
        cand_rows = conn.execute(
            "SELECT nces_id, name, ST_X(point), ST_Y(point) FROM schools WHERE point IS NOT NULL"
        ).fetchall()
        candidates = [(cid, name, (x, y)) for cid, name, x, y in cand_rows]
        if not candidates:
            return 0
        unresolved = conn.execute(
            """
            SELECT rowid, raw_name, ST_X(ST_Centroid(geometry)), ST_Y(ST_Centroid(geometry))
            FROM boundaries WHERE nces_id IS NULL AND raw_name IS NOT NULL
            """
        ).fetchall()
        resolved = 0
        for rid, raw_name, cx, cy in unresolved:
            match = match_by_name_geo(raw_name, (cx, cy), candidates, max_ft=max_ft)
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
