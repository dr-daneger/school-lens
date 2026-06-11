"""Standalone integration smoke test: imports, schema DDL, spatial SQL idioms,
the DB-gather, the comparison wiring, and the API GeoJSON endpoints, all against
synthetic data in a throwaway temp DB. No real data or external services needed.

    python scripts/smoke_test.py

ASCII output only (cp1252 console). Exits non-zero if any check fails.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Point config at a temp DB and a non-existent house-hunter DB (so ATTACH is
# skipped) BEFORE importing any school_lens module (settings load at import).
_TMP = tempfile.mkdtemp(prefix="school_lens_smoke_")
os.environ["SL_DB_PATH"] = str(pathlib.Path(_TMP) / "sl.duckdb")
os.environ["SL_HOUSE_HUNTER_DB_PATH"] = str(pathlib.Path(_TMP) / "no_hh.duckdb")

FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


# 1. Import the whole package (catches syntax/import errors everywhere).
import importlib

MODS = [
    "school_lens.config", "school_lens.db", "school_lens.domain.models",
    "school_lens.domain.metrics", "school_lens.compare", "school_lens.geocode",
    "school_lens.spatial.assign", "school_lens.spatial.distance",
    "school_lens.ingest._crosswalk", "school_lens.ingest.boundaries",
    "school_lens.ingest.nces", "school_lens.ingest.crdc", "school_lens.ingest.ode",
    "school_lens.ingest.opportunity_atlas", "school_lens.ingest.acs",
    "school_lens.api.app", "school_lens.cli",
]
_ok = True
for m in MODS:
    try:
        importlib.import_module(m)
    except Exception as e:  # noqa: BLE001
        _ok = False
        print("  import error in", m, "->", repr(e))
check("import all modules", _ok)

# 2. Schema DDL executes (GEOMETRY columns, provenance fragment, etc.).
from school_lens.db import get_connection, init_db

init_db()
check("init_db schema", True)

# 3. Spatial round-trip + synthetic fixtures.
from shapely.geometry import box

con = get_connection()
lon, lat = -122.80, 45.50
x, y = con.execute(
    "SELECT ST_X(g), ST_Y(g) FROM (SELECT ST_Transform("
    "ST_FlipCoordinates(ST_Point(?, ?)), 'EPSG:4326', 'EPSG:2913') AS g)",
    [lon, lat],
).fetchone()
poly = box(x - 500, y - 500, x + 500, y + 500)  # 1000 ft square in 2913
con.execute(
    "INSERT INTO boundaries (nces_id, raw_name, level, school_year, geometry, source) "
    "VALUES (?, ?, ?, ?, ST_GeomFromWKB(?), ?)",
    ["N1", "Test High", "high", "2024-2025", poly.wkb, "smoke"],
)
for nid, dlon, dlat, stype in [("N1", 0.0, 0.0, "traditional_public"),
                               ("N2", 0.01, 0.01, "charter")]:
    con.execute(
        "INSERT INTO schools (nces_id, name, school_type, level, lat, lon, point) "
        "VALUES (?, ?, ?, 'high', ?, ?, "
        "ST_Transform(ST_FlipCoordinates(ST_Point(?, ?)), 'EPSG:4326', 'EPSG:2913'))",
        [nid, f"School {nid}", stype, lat + dlat, lon + dlon, lon + dlon, lat + dlat],
    )
con.execute("INSERT INTO fact_academics (nces_id, school_year, math_growth, ela_growth) "
            "VALUES ('N1', '2024-2025', 50, 50)")
con.execute("INSERT INTO fact_academics (nces_id, school_year, math_growth, ela_growth) "
            "VALUES ('N2', '2024-2025', 70, 60)")
con.close()

# 4. Point-in-polygon assignment.
from school_lens.spatial.assign import assign_schools

res = {a.level.value: a for a in assign_schools(lat, lon)}
check("assign finds boundary (high)", res["high"].nces_id == "N1")
check("assign reports missing levels honestly", res["elementary"].nces_id is None)

# 5. DB-gather + radius candidates + comparison wiring.
from school_lens.api.app import _candidates_near, _conn, _gather_values
from school_lens.compare import build_scorecard

c = _conn()
vals = _gather_values(c, "N1")
check("gather reads facts", vals.get("math_growth") is not None and vals["math_growth"].value == 50.0)
near = _candidates_near(c, lat, lon, 6.0)
check("candidates_near finds both schools", len(near) == 2)
c.close()

card = build_scorecard(near, baseline_nces_id="N1")
byid = {x.nces_id: x for x in card.candidates}
check("charter meets-or-beats public on growth", byid["N2"].meets_or_beats is True)

# 6. API GeoJSON endpoints (validates the flip+transform serialization SQL).
try:
    from fastapi.testclient import TestClient

    from school_lens.api.app import app
    client = TestClient(app)
    check("GET /api/health", client.get("/api/health").status_code == 200)
    rb = client.get("/api/boundary", params={"nces_id": "N1"})
    check("GET /api/boundary returns geojson", rb.status_code == 200 and bool(rb.json().get("features")))
    rs = client.get("/api/schools",
                    params={"west": lon - 0.1, "south": lat - 0.1, "east": lon + 0.1, "north": lat + 0.1})
    check("GET /api/schools returns geojson", rs.status_code == 200 and len(rs.json().get("features", [])) >= 1)
except Exception as e:  # noqa: BLE001
    check(f"API endpoints (error: {e!r})", False)

print("-" * 48)
print("RESULT:", "ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
