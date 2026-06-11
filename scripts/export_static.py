"""Bake the serving data into static JSON for the GitHub Pages site.

The FastAPI app gathers metric values per request; everything it gathers is a
pure function of the database, so it can be precomputed per school. The only
address-dependent operations (geocode, point-in-polygon assignment, radius
candidate search, scorecard build) move client-side in site/index.html.

Outputs (site/data/):
- metrics.json     the registry plus default weights, so the UI and the JS
                   scorecard port stay registry-driven
- schools.json     every located school with its gathered metric values
                   (school facts + tract context, confidence attached)
- boundaries.geojson  latest school_year boundary per (nces_id, level), WGS84

Run: python scripts/export_static.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from school_lens.api.app import _gather_context, _gather_values  # noqa: E402
from school_lens.db import get_connection  # noqa: E402
from school_lens.domain import metrics as M  # noqa: E402

SITE_DATA = Path(__file__).resolve().parent.parent / "site" / "data"

_GEOJSON = "ST_AsGeoJSON(ST_FlipCoordinates(ST_Transform({col}, 'EPSG:2913', 'EPSG:4326')))"


def export_metrics() -> dict:
    weights = M.default_weights()
    return {"metrics": [
        {"id": m.id, "label": m.label, "domain": m.domain, "kind": m.kind.value,
         "direction": m.direction.value, "unit": m.unit, "source": m.source,
         "default_weight": weights[m.id]}
        for m in M.METRICS]}


def export_schools(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT nces_id, name, school_type, level, district_name, enrollment,
                  data_completeness, lat, lon
           FROM schools WHERE point IS NOT NULL ORDER BY nces_id"""
    ).fetchall()
    out = []
    for nces_id, name, stype, level, district, enroll, completeness, lat, lon in rows:
        values = {**_gather_values(conn, nces_id), **_gather_context(conn, nces_id)}
        try:
            comp = json.loads(completeness) if isinstance(completeness, str) else (completeness or {})
        except ValueError:
            comp = {}
        out.append({
            "nces_id": nces_id, "name": name, "school_type": stype, "level": level,
            "district_name": district, "enrollment": enroll,
            "data_completeness": comp, "lat": lat, "lon": lon,
            "values": {mid: {"value": mv.value, "confidence": mv.confidence}
                       for mid, mv in values.items()},
        })
    return out


def export_boundaries(conn) -> dict:
    # Latest school_year per (nces_id, level): the same row assign_schools picks.
    rows = conn.execute(
        f"""SELECT nces_id, level, school_year, raw_name,
                   {_GEOJSON.format(col='geometry')} AS gj
            FROM (
              SELECT *, row_number() OVER (
                  PARTITION BY nces_id, level ORDER BY school_year DESC) AS rn
              FROM boundaries WHERE nces_id IS NOT NULL
            ) WHERE rn = 1"""
    ).fetchall()
    features = []
    for nces_id, level, year, raw_name, gj in rows:
        if not gj:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(gj),
            "properties": {"nces_id": nces_id, "level": level,
                           "school_year": year, "raw_name": raw_name},
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    conn = get_connection(read_only=True)
    try:
        metrics = export_metrics()
        schools = export_schools(conn)
        boundaries = export_boundaries(conn)
    finally:
        conn.close()

    (SITE_DATA / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (SITE_DATA / "schools.json").write_text(json.dumps(schools), encoding="utf-8")
    (SITE_DATA / "boundaries.geojson").write_text(json.dumps(boundaries), encoding="utf-8")

    sizes = {p.name: f"{p.stat().st_size / 1024:.0f} KB" for p in SITE_DATA.iterdir()}
    print(f"metrics: {len(metrics['metrics'])}")
    print(f"schools: {len(schools)} (with point)")
    print(f"boundaries: {len(boundaries['features'])} (latest per nces_id+level)")
    print(f"sizes: {sizes}")


if __name__ == "__main__":
    main()
