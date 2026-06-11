"""EDFacts ingest: federal graduation + assessment proficiency (via Urban API).

Fills the academics facts that are available through a clean API:
- grad_rate (school_effect-ish, cohort): EDFacts adjusted-cohort graduation rate,
  total-row midpoint. Latest API year is 2018-19.
- ela_proficiency / math_proficiency (context levels): EDFacts assessments,
  all-grades all-students midpoint. Latest full pre-COVID year is 2017-18.

These are federal equivalents of the Oregon ODE report-card metrics; provenance on
every row says EDFacts. The OR-specific school_effect signals (median growth
percentile, 9th-grade on-track, college-going) live only in ODE's report-card
media files, which require browser automation; those remain a Phase-2 ingest in
ode.py. Joins on nces_id to the CCD spine. Idempotent on source.
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb

from school_lens.db import get_connection
from school_lens.ingest._urban import num, paginate

GRAD_YEAR = 2019
ASSESS_YEAR = 2018
# All-subgroup total codes for the assessment endpoint.
_ASSESS_TOTAL = dict(race=99, sex=99, lep=99, homeless=99, migrant=99,
                     disability=99, foster_care=99, military_connected=99,
                     econ_disadvantaged=99)
# All-subgroup total codes for the grad-rate endpoint.
_GRAD_TOTAL = dict(race=99, disability=99, econ_disadvantaged=99, lep=99,
                   foster_care=99, homeless=99)


def ingest_edfacts(conn: duckdb.DuckDBPyConnection | None = None) -> tuple[int, int]:
    """Write grad + proficiency into fact_academics for spine schools. Returns (grad, assess)."""
    own = conn is None
    conn = conn or get_connection()
    ts = datetime.now(timezone.utc)
    try:
        spine = {r[0] for r in conn.execute("SELECT nces_id FROM schools").fetchall()}
        if not spine:
            return (0, 0)
        conn.execute("DELETE FROM fact_academics WHERE source LIKE 'EDFacts%'")

        grad_url = f"/schools/edfacts/grad-rates/{GRAD_YEAR}/"
        grad_v = f"{GRAD_YEAR - 1}-{GRAD_YEAR}"
        ng = 0
        for r in paginate(grad_url, dict(fips=41, **_GRAD_TOTAL)):
            nces = str(r.get("ncessch") or "")
            gr = num(r.get("grad_rate_midpt"))
            if nces not in spine or gr is None:
                continue
            conn.execute(
                """INSERT INTO fact_academics
                   (nces_id, school_year, grad_rate, source, source_vintage, ingest_ts, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [nces, grad_v, gr, "EDFacts grad-rates via Urban API", grad_v, ts, grad_url],
            )
            ng += 1

        assess_url = f"/schools/edfacts/assessments/{ASSESS_YEAR}/grade-99/"
        assess_v = f"{ASSESS_YEAR - 1}-{ASSESS_YEAR}"
        na = 0
        for r in paginate(assess_url, dict(fips=41, **_ASSESS_TOTAL)):
            nces = str(r.get("ncessch") or "")
            ela = num(r.get("read_test_pct_prof_midpt"))
            math = num(r.get("math_test_pct_prof_midpt"))
            if nces not in spine or (ela is None and math is None):
                continue
            conn.execute(
                """INSERT INTO fact_academics
                   (nces_id, school_year, ela_proficiency, math_proficiency,
                    source, source_vintage, ingest_ts, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [nces, assess_v, ela, math,
                 "EDFacts assessments via Urban API", assess_v, ts, assess_url],
            )
            na += 1
        return (ng, na)
    finally:
        if own:
            conn.close()


def ingest(conn: duckdb.DuckDBPyConnection | None = None) -> tuple[int, int]:
    return ingest_edfacts(conn=conn)
