"""CRDC ingest: discipline and safety (the drug/police-involvement signal).

Civil Rights Data Collection (OCR), via the Urban Institute Education Data Portal
API. Public + charter only; private schools are not in CRDC (an honest gap, not an
error). Latest collection: 2020-21.

School totals: the discipline and chronic-absenteeism endpoints are crossed by
race x sex x disability x lep, so we request only the grand-total row
(race=99, sex=99, disability=99, lep=99) rather than summing subgroups (which would
double-count). Counts become per-enrollment rates using the CCD enrollment already
in `schools`, so a school must be in the spine first.

Writes fact_discipline_safety (suspension/expulsion/referral/arrest/chronic rates)
and fact_staffing (counselor ratio + SRO presence). Idempotent on source.
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import httpx

from school_lens.db import get_connection

API = "https://educationdata.urban.org/api/v1/schools/crdc"
DEFAULT_YEAR = 2021
SOURCE = "CRDC 2020-21 via Urban Institute API"
VINTAGE = "2020-2021"


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f < 0 else f


def _sum_or_none(*vals) -> float | None:
    """Sum the non-suppressed components; None only if every component is missing.

    Keeps a genuinely-zero total (some components 0) distinct from a fully
    suppressed one (all sentinels), so a suppressed cell never reads as 0.
    """
    present = [n for n in (_num(v) for v in vals) if n is not None]
    return sum(present) if present else None


def _pages(url: str, params: dict) -> list[dict]:
    out: list[dict] = []
    with httpx.Client(timeout=90, follow_redirects=True) as c:
        u: str | None = url
        p: dict | None = params
        while u:
            r = c.get(u, params=p)
            r.raise_for_status()
            d = r.json()
            out.extend(d.get("results", []) or [])
            u, p = d.get("next"), None  # next URL already carries the query
    return out


def _rate(count: float | None, enrollment, scale: float = 100.0) -> float | None:
    if count is None or not enrollment or enrollment <= 0:
        return None
    return round(count / enrollment * scale, 2)


def ingest_crdc(
    year: int = DEFAULT_YEAR,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Compute CRDC discipline/safety + counselor metrics for spine schools."""
    own = conn is None
    conn = conn or get_connection()
    ts = datetime.now(timezone.utc)
    disc_url = f"{API}/discipline/{year}/disability/sex/"
    staff_url = f"{API}/teachers-staff/{year}/"
    chronic_url = f"{API}/chronic-absenteeism/{year}/race/sex/"
    try:
        # Enrollment denominators come from the spine.
        enroll = dict(conn.execute(
            "SELECT nces_id, enrollment FROM schools WHERE enrollment IS NOT NULL"
        ).fetchall())
        if not enroll:
            return 0

        disc = {str(r["ncessch"]): r for r in _pages(
            disc_url, {"fips": 41, "race": 99, "sex": 99, "disability": 99, "lep": 99})}
        staff = {str(r["ncessch"]): r for r in _pages(staff_url, {"fips": 41})}
        chronic_rows = _pages(chronic_url, {"fips": 41, "race": 99, "sex": 99})
        chronic_field = next(
            (k for k in (chronic_rows[0].keys() if chronic_rows else [])
             if "chronic" in k.lower() and "absent" in k.lower()), None)
        chronic = {str(r["ncessch"]): r for r in chronic_rows}

        conn.execute("DELETE FROM fact_discipline_safety WHERE source LIKE 'CRDC%'")
        conn.execute("DELETE FROM fact_staffing WHERE source LIKE 'CRDC%'")
        n = 0
        for nces, enr in enroll.items():
            d = disc.get(nces)
            s = staff.get(nces)
            cab = chronic.get(nces)
            if not (d or s or cab):
                continue
            if d:
                susp = _sum_or_none(d["students_susp_out_sch_single"],
                                    d["students_susp_out_sch_multiple"])
                expul = _sum_or_none(d["expulsions_no_ed_serv"], d["expulsions_with_ed_serv"],
                                     d["expulsions_zero_tolerance"])
                referred = _num(d["students_referred_law_enforce"])
                arrested = _num(d["students_arrested"])
                cab_ct = _num(cab[chronic_field]) if (cab and chronic_field) else None
                conn.execute(
                    """
                    INSERT INTO fact_discipline_safety
                        (nces_id, school_year, suspension_rate, expulsion_rate,
                         law_enforcement_referral_rate, arrest_rate, chronic_absentee_rate,
                         source, source_vintage, ingest_ts, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [nces, VINTAGE, _rate(susp, enr), _rate(expul, enr),
                     _rate(referred, enr), _rate(arrested, enr), _rate(cab_ct, enr),
                     SOURCE, VINTAGE, ts, disc_url],
                )
            if s:
                couns = _num(s.get("counselors_fte"))
                ratio = round(enr / couns, 1) if (couns and couns > 0) else None
                le_fte = _num(s.get("law_enforcement_fte"))
                sro = True if (le_fte and le_fte > 0) or s.get("law_enforcement_ind") == 1 else (
                    False if le_fte == 0.0 else None)
                conn.execute(
                    """
                    INSERT INTO fact_staffing
                        (nces_id, school_year, student_counselor_ratio, sro_present,
                         source, source_vintage, ingest_ts, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [nces, VINTAGE, ratio, sro, SOURCE, VINTAGE, ts, staff_url],
                )
            n += 1
        return n
    finally:
        if own:
            conn.close()


def ingest(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    return ingest_crdc(conn=conn)
