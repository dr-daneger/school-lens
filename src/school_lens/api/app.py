"""FastAPI app: address->assignment, comparison scorecard, and map overlays.

Geometry is serialized with house-hunter's canonical idiom:
ST_AsGeoJSON(ST_FlipCoordinates(ST_Transform(geom,'EPSG:2913','EPSG:4326'))),
which yields standard [lon, lat] GeoJSON straight from SQL with no per-feature
Python work. The DB metric-gather is driven by the metric registry, so adding a
metric in domain/metrics.py (with a matching fact column) surfaces it everywhere.
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from school_lens.compare import Candidate, MetricValue, build_scorecard
from school_lens.config import PROJECT_ROOT, settings
from school_lens.db import get_connection, house_hunter_attached, init_db
from school_lens.domain import metrics as M
from school_lens.domain.models import School, SchoolType
from school_lens.geocode import geocode
from school_lens.spatial.assign import assign_schools
from school_lens.spatial.distance import drive_distance

app = FastAPI(title="school-lens", version="0.1.0")

# Fact tables whose columns are named exactly like their metric ids.
_DOMAIN_TABLE = {
    "academics": "fact_academics",
    "discipline_safety": "fact_discipline_safety",
    "staffing": "fact_staffing",
    "private_signals": "fact_private_signals",
}

_GEOJSON = "ST_AsGeoJSON(ST_FlipCoordinates(ST_Transform({col}, 'EPSG:2913', 'EPSG:4326')))"


def _conn():
    """Read-only connection, initializing an empty DB the first time if needed."""
    if not settings.db_path.exists():
        init_db()
    return get_connection(read_only=True)


def _safe_type(value: str | None) -> SchoolType:
    try:
        return SchoolType(value)
    except (ValueError, TypeError):
        return SchoolType.traditional_public


def _safe_completeness(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _gather_values(conn, nces_id: str) -> dict[str, MetricValue]:
    """Latest fact-row values for a school, keyed by metric id (registry-driven)."""
    values: dict[str, MetricValue] = {}
    for domain, table in _DOMAIN_TABLE.items():
        cols = [m.id for m in M.by_domain(domain)]
        if not cols:
            continue
        # Private proxies are explicitly lower-confidence (CLAUDE.md 3.2).
        conf = M.PRIVATE_PROXY_CONFIDENCE if domain == "private_signals" else 1.0
        # Merge across rows: take the latest non-null value per column. A school's
        # facts in one domain can arrive from different sources/years (e.g. the
        # student-teacher ratio from CCD and the counselor ratio from CRDC land in
        # separate fact_staffing rows); arg_max(col, school_year) ignores nulls so
        # each metric resolves to its most recent reported value.
        sel = ", ".join(f"arg_max({c}, school_year) AS {c}" for c in cols)
        row = conn.execute(
            f"SELECT {sel} FROM {table} WHERE nces_id = ?", [nces_id]
        ).fetchone()
        if not row:
            continue
        for cid, val in zip(cols, row):
            if val is not None:
                values[cid] = MetricValue(value=float(val), confidence=conf)
    return values


# Tract context metric id -> tract_acs column. These are geography-keyed CONTEXT
# (CLAUDE.md 3.1): the neighborhood the school sits in, never a school-quality
# signal. v1 uses the tract containing the school point; catchment area-weighting
# (CLAUDE.md 4) is the refinement.
_TRACT_CONTEXT = {
    "neighborhood_median_income": "median_household_income",
    "pct_bachelors_plus": "pct_bachelors_plus",
    "poverty_rate": "poverty_rate",
}


def _gather_context(conn, nces_id: str) -> dict[str, MetricValue]:
    """Neighborhood (tract) context for a school via point-in-tract containment."""
    cols = list(_TRACT_CONTEXT.values())
    row = conn.execute(
        f"""SELECT {', '.join('a.' + c for c in cols)}
            FROM schools s JOIN tract_acs a ON ST_Contains(a.geometry, s.point)
            WHERE s.nces_id = ? LIMIT 1""",
        [nces_id],
    ).fetchone()
    out: dict[str, MetricValue] = {}
    if row:
        for mid, val in zip(_TRACT_CONTEXT, row):
            if val is not None:
                out[mid] = MetricValue(value=float(val), confidence=0.8)  # tract proxy
    return out


def _candidate(conn, nces_id, name, stype, completeness, lat=None, lon=None) -> Candidate:
    school = School(
        nces_id=nces_id, name=name or nces_id, school_type=_safe_type(stype),
        data_completeness=_safe_completeness(completeness), lat=lat, lon=lon,
    )
    return Candidate(school=school,
                     values={**_gather_values(conn, nces_id), **_gather_context(conn, nces_id)})


def _candidates_near(conn, lat, lon, miles, level=None, baseline_nces_id=None) -> list[Candidate]:
    # Compare like with like: only schools of the baseline's grade band are
    # candidates (a high school is not comparable to an elementary school).
    rows = conn.execute(
        """
        WITH pt AS (
            SELECT ST_Transform(ST_FlipCoordinates(ST_Point(?, ?)),
                                'EPSG:4326', 'EPSG:2913') AS g
        )
        SELECT s.nces_id, s.name, s.school_type, s.data_completeness, s.lat, s.lon
        FROM schools s, pt
        WHERE s.point IS NOT NULL AND ST_DWithin(s.point, pt.g, ?)
          AND (? IS NULL OR s.level = ? OR (s.school_type LIKE 'private%' AND s.level = 'other'))
        """,
        [lon, lat, miles * 5280.0, level, level],
    ).fetchall()
    cands = [_candidate(conn, *r) for r in rows]
    seen = {c.school.nces_id for c in cands}
    if baseline_nces_id and baseline_nces_id not in seen:
        b = conn.execute(
            "SELECT nces_id, name, school_type, data_completeness, lat, lon FROM schools WHERE nces_id = ?",
            [baseline_nces_id],
        ).fetchone()
        if b:
            cands.append(_candidate(conn, *b))
    return cands


@app.get("/api/health")
def health():
    conn = _conn()
    try:
        return {"status": "ok", "house_hunter_attached": house_hunter_attached(conn)}
    finally:
        conn.close()


@app.get("/api/metrics")
def metrics_registry():
    """The metric registry as JSON, so the UI can render any metric (table column,
    chart axis) without code changes; new metrics in domain/metrics.py appear
    automatically."""
    return {"metrics": [
        {"id": m.id, "label": m.label, "domain": m.domain, "kind": m.kind.value,
         "direction": m.direction.value, "unit": m.unit, "source": m.source}
        for m in M.METRICS]}


@app.get("/api/address_suggest")
def address_suggest(q: str, limit: int = 10):
    """Address autocomplete from parcels_base. Scoped to Washington + Multnomah by
    construction (that is all parcels_base contains), and every suggestion is an
    exact siteaddr so it is guaranteed to geocode. Token-AND substring match so
    '1234 main' and 'main st' both work."""
    q = (q or "").strip()
    if len(q) < 3:
        return {"suggestions": []}
    conn = _conn()
    try:
        if not house_hunter_attached(conn):
            return {"suggestions": []}
        toks = [t for t in q.split() if t][:6]
        where = " AND ".join(["siteaddr ILIKE '%' || ? || '%'"] * len(toks))
        rows = conn.execute(
            f"""SELECT siteaddr FROM hh.parcels_base
                WHERE siteaddr IS NOT NULL AND {where}
                GROUP BY siteaddr ORDER BY length(siteaddr), siteaddr
                LIMIT ?""",
            [*toks, max(1, min(limit, 25))],
        ).fetchall()
        return {"suggestions": [r[0] for r in rows]}
    finally:
        conn.close()


@app.get("/api/districts")
def districts(level: str):
    """Schools with a catchment at this grade band, for the browse-by-school selector
    (anchor on a district directly, no address). Returns the school point too so the
    UI can drop a marker."""
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT DISTINCT b.nces_id, COALESCE(s.name, b.raw_name) AS name, s.lat, s.lon
               FROM boundaries b LEFT JOIN schools s ON s.nces_id = b.nces_id
               WHERE b.level = ? AND b.nces_id IS NOT NULL
               ORDER BY name""",
            [level],
        ).fetchall()
        return {"districts": [
            {"nces_id": r[0], "name": r[1], "lat": r[2], "lon": r[3]} for r in rows]}
    finally:
        conn.close()


@app.get("/api/assign")
def assign(address: str):
    conn = _conn()
    try:
        geo = geocode(address, conn)
        if geo is None:
            return {"address": address, "found": False,
                    "note": "address not found in parcels_base; external geocoder not wired"}
        out = []
        for a in assign_schools(geo.lat, geo.lon, conn):
            dist = slat = slon = None
            if a.nces_id:
                pr = conn.execute(
                    "SELECT lat, lon FROM schools WHERE nces_id = ?", [a.nces_id]
                ).fetchone()
                if pr and pr[0] is not None:
                    slat, slon = pr[0], pr[1]
                    dist = drive_distance(
                        (geo.lat, geo.lon), (slat, slon),
                        osrm_url=settings.osrm_url or None, with_geometry=True,
                    )
            out.append({**a.model_dump(), "distance": dist,
                        "school_lat": slat, "school_lon": slon})
        return {"address": address, "found": True,
                "geocode": geo.model_dump(), "assignments": out}
    finally:
        conn.close()


@app.get("/api/route")
def route(address: str, nces_id: str):
    """Drive distance/time from an address to one school (for the click-a-school
    popup). Geocodes the address, then routes to the school point via OSRM."""
    conn = _conn()
    try:
        geo = geocode(address, conn)
        if geo is None:
            return {"found": False, "note": "address not found"}
        pr = conn.execute("SELECT lat, lon FROM schools WHERE nces_id = ?", [nces_id]).fetchone()
        if not pr or pr[0] is None:
            return {"found": False, "note": "school has no location"}
        d = drive_distance((geo.lat, geo.lon), (pr[0], pr[1]),
                           osrm_url=settings.osrm_url or None, with_geometry=False)
        return {"found": True, **d}
    finally:
        conn.close()


@app.get("/api/compare")
def compare(address: str, baseline_level: str = "high", radius_miles: float | None = None):
    conn = _conn()
    try:
        geo = geocode(address, conn)
        if geo is None:
            return {"found": False, "note": "address not found in parcels_base"}
        assigns = {a.level.value: a for a in assign_schools(geo.lat, geo.lon, conn)}
        base = assigns.get(baseline_level)
        baseline_id = base.nces_id if base else None
        miles = radius_miles or settings.compare_radius_miles
        cands = _candidates_near(conn, geo.lat, geo.lon, miles,
                                 level=baseline_level, baseline_nces_id=baseline_id)
        card = build_scorecard(cands, baseline_nces_id=baseline_id)
        return {"found": True, "baseline_level": baseline_level,
                "baseline_nces_id": baseline_id, "radius_miles": miles,
                "scorecard": card.model_dump()}
    finally:
        conn.close()


@app.get("/api/schools")
def schools(west: float, south: float, east: float, north: float):
    conn = _conn()
    try:
        rows = conn.execute(
            f"""
            SELECT nces_id, name, school_type, level,
                   {_GEOJSON.format(col='point')} AS gj
            FROM schools
            WHERE point IS NOT NULL
              AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?
            """,
            [west, east, south, north],
        ).fetchall()
        features = [{
            "type": "Feature",
            "geometry": json.loads(gj) if gj else None,
            "properties": {"nces_id": nid, "name": name,
                           "school_type": stype, "level": level},
        } for nid, name, stype, level, gj in rows]
        return {"type": "FeatureCollection", "features": features}
    finally:
        conn.close()


@app.get("/api/boundary")
def boundary(nces_id: str, level: str | None = None):
    conn = _conn()
    try:
        rows = conn.execute(
            f"""
            SELECT level, school_year, raw_name,
                   {_GEOJSON.format(col='geometry')} AS gj
            FROM boundaries
            WHERE nces_id = ? AND (? IS NULL OR level = ?)
            ORDER BY school_year DESC
            """,
            [nces_id, level, level],
        ).fetchall()
        features = [{
            "type": "Feature",
            "geometry": json.loads(gj) if gj else None,
            "properties": {"nces_id": nces_id, "level": lvl,
                           "school_year": yr, "raw_name": rn},
        } for lvl, yr, rn, gj in rows]
        return {"type": "FeatureCollection", "features": features}
    finally:
        conn.close()


# Static UI mounted last so /api/* routes resolve first.
_UI_DIR = PROJECT_ROOT / "src" / "school_lens" / "ui" / "frontend" / "dist"
if _UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
