"""Live verification of the Beaverton/Hillsboro attendance-boundary endpoints.

Runs the real ingest against a throwaway temp DuckDB (kept out of the repo tree
and out of Google Drive's reach per the Drive-lock convention) and prints what
landed: per (source, level) counts, how many features carried a school name, and
a sample row's ST_Area in EPSG:2913 (square feet, so a large number confirms the
WGS84 -> 2913 reprojection worked). ASCII only.

Run with house-hunter's venv interpreter (it has house_hunter + geopandas):
    .../house-hunter/.venv/Scripts/python.exe scripts/live_boundary_check.py
"""
import os
import pathlib
import sys
import tempfile

SL_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SL_SRC))

_tmp = pathlib.Path(tempfile.gettempdir()) / "school_lens_boundary_check.duckdb"
if _tmp.exists():
    _tmp.unlink()
os.environ["SL_DB_PATH"] = str(_tmp)

from school_lens.db import get_connection, init_db  # noqa: E402
from school_lens.ingest.boundaries import (  # noqa: E402
    _discover_attendance_layers,
    boundary_sources,
    ingest_source,
)

SOURCES = ("beaverton", "hillsboro")


def main() -> None:
    srcs = boundary_sources()
    print("=== discovery (which polygon layers each service exposes) ===")
    for key in SOURCES:
        for root in srcs[key].roots:
            layers = _discover_attendance_layers(root)
            tail = root.split("/services/")[-1]
            print(f"[{key}] {tail}")
            for lid, name in layers:
                print(f"    layer {lid}: {name}")

    init_db()
    conn = get_connection()
    print("\n=== ingest ===")
    for key in SOURCES:
        n = ingest_source(key, conn=conn)
        print(f"[{key}] inserted {n} boundary features")

    print("\n=== landed rows by source/level ===")
    rows = conn.execute(
        """
        SELECT source, level, COUNT(*) AS n, COUNT(raw_name) AS named,
               SUM(CASE WHEN geometry IS NOT NULL THEN 1 ELSE 0 END) AS geoms
        FROM boundaries GROUP BY source, level ORDER BY source, level
        """
    ).fetchall()
    for source, level, n, named, geoms in rows:
        print(f"  {source:<42} {str(level):<11} n={n:<4} named={named:<4} geoms={geoms}")

    print("\n=== sample features (name + area in sq ft, EPSG:2913) ===")
    samp = conn.execute(
        """
        SELECT source, level, raw_name, ROUND(ST_Area(geometry)) AS area_sqft
        FROM boundaries WHERE geometry IS NOT NULL AND raw_name IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (PARTITION BY source, level ORDER BY raw_name) <= 2
        ORDER BY source, level, raw_name
        """
    ).fetchall()
    for source, level, raw_name, area in samp:
        sd = srcs_key_for(source)
        print(f"  [{sd}] {str(level):<11} {str(raw_name):<28} area_sqft={area:,.0f}")
    conn.close()


def srcs_key_for(label: str) -> str:
    for k, v in boundary_sources().items():
        if v.label == label:
            return k
    return "?"


if __name__ == "__main__":
    main()
