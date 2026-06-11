"""Tract demographic context (CONTEXT only).

The Census ACS API now requires a key, so neighborhood context comes instead from
the Opportunity Insights tract covariates file (no key): median household income,
share with a college degree, and poverty share, by Census tract. Tract geometry
comes from Census TIGERweb (queried with the reused ArcGIS puller) and is stored
in EPSG:2913 for the spatial (point-in-tract / catchment) join.

These are geography-keyed CONTEXT metrics (CLAUDE.md 3.1): they describe the
neighborhood a school sits in, never a school's quality. Upserts into tract_acs.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import duckdb
import httpx
import pandas as pd

from school_lens.db import get_connection

# Wider than the metro-core bbox so full Washington + Multnomah tracts load; the OI
# covariate filter already restricts to the two counties, so over-pulling is safe.
TRACT_BBOX = (-123.6, 45.25, -121.85, 45.80)

OI_COVARIATES = "https://opportunityinsights.org/wp-content/uploads/2018/10/tract_covariates.csv"
TIGER_TRACTS = ("https://tigerweb.geo.census.gov/arcgis/rest/services/"
                "TIGERweb/Tracts_Blocks/MapServer")
TIGER_LAYER = 0
COUNTIES = {("41", "051"), ("41", "067")}  # (state, county): Multnomah, Washington
VINTAGE = "2010-2016"
SOURCE = "Opportunity Insights tract covariates"


def _num(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _geoid(state, county, tract) -> str:
    return f"{int(float(state)):02d}{int(float(county)):03d}{int(float(tract)):06d}"


def ingest_acs(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Load tract_acs (income/education/poverty + geometry) for the two counties."""
    from school_lens._vendor.normalize import to_target  # lazy reuse
    from school_lens._vendor.arcgis import query_arcgis_layer

    own = conn is None
    conn = conn or get_connection()
    ts = datetime.now(timezone.utc)
    try:
        # 1. Opportunity Insights covariates -> {GEOID: (med_hhinc, frac_coll, poor_share)}
        r = httpx.get(OI_COVARIATES, timeout=180, follow_redirects=True)
        r.raise_for_status()
        df = pd.read_csv(io.BytesIO(r.content), dtype=str)
        cov: dict[str, tuple] = {}
        for _, row in df.iterrows():
            st, co = row.get("state"), row.get("county")
            try:
                key = (f"{int(float(st)):02d}", f"{int(float(co)):03d}")
            except (TypeError, ValueError):
                continue
            if key not in COUNTIES:
                continue
            cov[_geoid(st, co, row.get("tract"))] = (
                _num(row.get("med_hhinc2016")),
                _num(row.get("frac_coll_plus2010")),
                _num(row.get("poor_share2010")),
            )
        if not cov:
            return 0

        # 2. TIGER tract geometry for the two-county bbox.
        gdf = to_target(query_arcgis_layer(TIGER_TRACTS, TIGER_LAYER, bbox_wgs84=TRACT_BBOX))

        conn.execute("DELETE FROM tract_acs")
        n = 0
        for _, g in gdf.iterrows():
            geom = g.geometry
            if geom is None or geom.is_empty:
                continue
            gid = str(g.get("GEOID") or "")
            c = cov.get(gid)
            if not c:
                continue
            inc, coll, poor = c
            conn.execute(
                """INSERT INTO tract_acs
                   (tract_geoid, median_household_income, pct_bachelors_plus, poverty_rate,
                    geometry, source, source_vintage, ingest_ts, source_url)
                   VALUES (?, ?, ?, ?, ST_GeomFromWKB(?), ?, ?, ?, ?)""",
                [gid, inc,
                 coll * 100 if coll is not None else None,
                 poor * 100 if poor is not None else None,
                 geom.wkb, SOURCE, VINTAGE, ts, OI_COVARIATES],
            )
            n += 1
        return n
    finally:
        if own:
            conn.close()


def ingest(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    return ingest_acs(conn=conn)
