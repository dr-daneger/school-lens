"""Build a populated school_lens.duckdb so the app is testable end to end.

Loads the verified attendance boundaries (PPS + Beaverton + Hillsboro) and, so the
OSRM drive route renders, the Beaverton school POINTS from the same FLO locator
FeatureServer (layer 0, "School Location"). Beaverton boundaries then resolve to
those points by name+geo (the polygon and point layers share the same abbreviated
names, so resolution is exact). Hillsboro/PPS show the catchment + the source's
school name but no route until their school points are loaded.

NOTE: the Beaverton point rows are interim demo data (canonical id 'BSD:<code>',
not a real NCES id). The Phase-2 NCES/PSS ingest replaces them. Idempotent:
rebuilds the runtime DB (in %LOCALAPPDATA%) from scratch. Run with house-hunter's
venv interpreter. ASCII only.
"""
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from school_lens.config import bbox, settings  # noqa: E402
from school_lens.db import get_connection, init_db  # noqa: E402
from school_lens.ingest.boundaries import (  # noqa: E402
    boundary_sources,
    ingest_source,
    resolve_boundaries,
)

_TYPE_LEVEL = {"ES": "elementary", "MS": "middle", "HS": "high"}


def load_beaverton_school_points(conn) -> int:
    """Insert Beaverton 'School Location' points (layer 0) into `schools`."""
    from school_lens._vendor.normalize import to_target
    from school_lens._vendor.arcgis import query_arcgis_layer

    root = settings.beaverton_locator_root
    gdf = query_arcgis_layer(root, 0, bbox_wgs84=bbox())  # WGS84 points
    if gdf.empty:
        return 0
    gdf2 = to_target(gdf)  # same rows, geometry in EPSG:2913 (consistent w/ boundaries)
    ts = datetime.now(timezone.utc)
    n = 0
    for (_, w), (_, t) in zip(gdf.iterrows(), gdf2.iterrows()):
        pw, p2913 = w.geometry, t.geometry
        if pw is None or pw.is_empty:
            continue
        code = w.get("SCHL_CODE")
        name = w.get("NAME") or w.get("FULLNAME")
        level = _TYPE_LEVEL.get(str(w.get("Type") or "").strip(), "other")
        conn.execute(
            """
            INSERT INTO schools
                (nces_id, name, school_type, level, district_name, lat, lon, point,
                 source, source_vintage, ingest_ts, source_url)
            VALUES (?, ?, 'traditional_public', ?, 'Beaverton SD', ?, ?,
                    ST_GeomFromWKB(?), ?, '2025-2026', ?, ?)
            """,
            [f"BSD:{code}", name, level, pw.y, pw.x, p2913.wkb,
             "Beaverton SD locator (interim demo points)", ts, f"{root}/0"],
        )
        n += 1
    return n


def main() -> None:
    db = settings.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    init_db()
    conn = get_connection()
    try:
        for key in boundary_sources():
            c = ingest_source(key, conn=conn)
            print(f"boundaries[{key:<9}] = {c}")
        sp = load_beaverton_school_points(conn)
        print(f"beaverton school points = {sp}")
        r = resolve_boundaries(conn=conn)
        print(f"resolved boundaries -> schools = {r}")
        print("-" * 50)
        for label, q in (
            ("schools", "SELECT COUNT(*) FROM schools"),
            ("boundaries", "SELECT COUNT(*) FROM boundaries"),
            ("boundaries resolved", "SELECT COUNT(*) FROM boundaries WHERE nces_id IS NOT NULL"),
        ):
            print(f"  {label:<22} {conn.execute(q).fetchone()[0]}")
    finally:
        conn.close()
    print(f"DB ready at {db}")


if __name__ == "__main__":
    main()
