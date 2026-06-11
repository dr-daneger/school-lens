"""DuckDB connection management and the canonical school-lens schema.

The connection pattern is forked from house_hunter.db.get_connection (open a
DuckDB file, load the spatial extension). school-lens additionally ATTACHes
house_hunter.duckdb read-only so address geocoding can query its parcels_base
without copying 655k rows. READ_ONLY guarantees we never mutate the sibling DB.

Schema notes:
- Canonical key is nces_id (NCES School ID for public/charter, NCES PSS id for
  private). source_crosswalk maps any source's identifier to it.
- Geometry columns are EPSG:2913 (engineering CRS); reproject to 4326 only at the
  display seam.
- Every fact table carries provenance (source, source_vintage, ingest_ts,
  source_url) so any number traces back to its origin.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from school_lens.config import settings

HH_ALIAS = "hh"

# Provenance columns appended to every fact/dim table. Defined once as a string
# fragment so the columns stay identical across tables.
_PROV = (
    "source VARCHAR, "
    "source_vintage VARCHAR, "
    "ingest_ts TIMESTAMP, "
    "source_url VARCHAR"
)

SCHEMA: list[str] = [
    f"""
    CREATE TABLE IF NOT EXISTS schools (
        nces_id VARCHAR PRIMARY KEY,
        name VARCHAR,
        school_type VARCHAR,   -- traditional_public|charter|magnet|private_religious|private_independent
        level VARCHAR,         -- elementary|middle|high|other
        grade_low VARCHAR,
        grade_high VARCHAR,
        district_name VARCHAR,
        street VARCHAR,
        city VARCHAR,
        state VARCHAR,
        zip VARCHAR,
        lat DOUBLE,
        lon DOUBLE,
        point GEOMETRY,        -- EPSG:2913
        enrollment INTEGER,
        data_completeness VARCHAR,  -- JSON-encoded domain->completeness, 0..1
        {_PROV}
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS source_crosswalk (
        source VARCHAR,
        source_id VARCHAR,
        nces_id VARCHAR,
        match_method VARCHAR,  -- exact_id|name_geo|manual
        confidence DOUBLE,
        PRIMARY KEY (source, source_id)
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS boundaries (
        nces_id VARCHAR,         -- null until resolved via the crosswalk pass
        raw_name VARCHAR,        -- school name as published by the boundary source
        match_confidence DOUBLE, -- crosswalk confidence (null until resolved)
        level VARCHAR,           -- grade band the boundary applies to
        school_year VARCHAR,
        geometry GEOMETRY,       -- EPSG:2913
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS fact_academics (
        nces_id VARCHAR,
        school_year VARCHAR,
        ela_proficiency DOUBLE,        -- context (level)
        math_proficiency DOUBLE,       -- context (level)
        ela_growth DOUBLE,             -- school_effect
        math_growth DOUBLE,            -- school_effect
        grad_rate DOUBLE,              -- school_effect-ish (cohort)
        ninth_grade_on_track DOUBLE,   -- school_effect
        college_going_rate DOUBLE,     -- mixed; labeled in metric registry
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS fact_discipline_safety (
        nces_id VARCHAR,
        school_year VARCHAR,
        suspension_rate DOUBLE,
        expulsion_rate DOUBLE,
        law_enforcement_referral_rate DOUBLE,  -- per 100 students (computed at ingest)
        arrest_rate DOUBLE,                    -- per 100 students
        drug_incident_rate DOUBLE,             -- per 100 students
        chronic_absentee_rate DOUBLE,
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS fact_staffing (
        nces_id VARCHAR,
        school_year VARCHAR,
        student_teacher_ratio DOUBLE,
        student_counselor_ratio DOUBLE,
        sro_present BOOLEAN,
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS fact_finance (
        nces_id VARCHAR,
        school_year VARCHAR,
        per_pupil_spending DOUBLE,
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS fact_private_signals (
        nces_id VARCHAR,
        school_year VARCHAR,
        college_matriculation DOUBLE,  -- private_proxy: best-cited quality signal
        accreditation DOUBLE,          -- private_proxy: 1=recognized accreditation, else 0
        tuition DOUBLE,                -- private attribute: annual tuition (USD)
        acceptance_rate DOUBLE,        -- private attribute: admission acceptance rate (%)
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS tract_outcomes (
        tract_geoid VARCHAR,
        kfr_pooled_p25 DOUBLE,     -- adult income rank, parents at 25th pctile
        kfr_pooled_p75 DOUBLE,     -- adult income rank, parents at 75th pctile
        incarceration_rate DOUBLE,
        college_attendance DOUBLE,
        geometry GEOMETRY,         -- EPSG:2913 (Census TIGER tract)
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS tract_acs (
        tract_geoid VARCHAR,
        median_household_income DOUBLE,
        pct_bachelors_plus DOUBLE,
        poverty_rate DOUBLE,
        geometry GEOMETRY,         -- EPSG:2913
        {_PROV}
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS school_mentions (
        id VARCHAR,
        nces_id VARCHAR,
        published_date VARCHAR,
        raw_text VARCHAR,
        signal VARCHAR,            -- JSON-encoded LLM-extracted structured signal
        extraction_model VARCHAR,
        confidence DOUBLE,
        {_PROV}
    );
    """,
]


def get_connection(
    path: str | Path | None = None,
    *,
    read_only: bool = False,
    attach_house_hunter: bool = True,
) -> duckdb.DuckDBPyConnection:
    """Open school_lens.duckdb with spatial loaded; ATTACH house_hunter read-only."""
    db_path = str(path or settings.db_path)
    conn = duckdb.connect(db_path, read_only=read_only)
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    if attach_house_hunter:
        hh = Path(settings.house_hunter_db_path)
        # DuckDB shares one database instance across same-file connections in a
        # process, so the hh attachment is shared too. Attaching again (under
        # concurrent requests) raises "already exists"; guard + swallow that race.
        if hh.exists() and not house_hunter_attached(conn):
            try:
                conn.execute(f"ATTACH '{hh.as_posix()}' AS {HH_ALIAS} (READ_ONLY)")
            except duckdb.Error as e:
                if "already exists" not in str(e).lower():
                    raise
    return conn


def house_hunter_attached(conn: duckdb.DuckDBPyConnection) -> bool:
    """True if the house_hunter DB is ATTACHed on this connection."""
    rows = conn.execute("SELECT database_name FROM duckdb_databases()").fetchall()
    return any(r[0] == HH_ALIAS for r in rows)


def init_db(path: str | Path | None = None) -> bool:
    """Create the data dir and the canonical schema. Idempotent."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    # No need to ATTACH house-hunter just to create our own tables.
    conn = get_connection(path, attach_house_hunter=False)
    try:
        for ddl in SCHEMA:
            conn.execute(ddl)
    finally:
        conn.close()
    return True
