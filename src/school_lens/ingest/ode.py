"""ODE ingest: current Oregon report-card academics (At-A-Glance media).

The Oregon report-card "Media" page is a Kendo grid, but its download links are a
plain GET once you know the shape (found via a Playwright probe, scripts/ode_probe.py):

    /data/ReportCard/Media/DownloadFile?schlYr={id}&fldr=stateData&flNm=AAGmediaSchoolsAggregate

That statewide CSV carries, per school: ELA / Math proficiency (context), On-Track
to Graduate and On-Time Graduation (school_effect), College Going (context),
Regular Attenders, Class Size. Being current (2024-25), these supersede the stale
EDFacts values via the latest-value gather. High schools gain a SECOND
school_effect metric (grad + on-track), which activates meets-or-beats.

ODE's School ID is its own institution id, so rows are crosswalked to NCES by
normalized school name within Washington + Multnomah (the load footprint). Median
growth percentile (the elem/middle school_effect signal) is NOT in this file; it
lives in ODE's Accountability/assessment media and remains a follow-on.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import duckdb
import httpx
import pandas as pd

from school_lens.db import get_connection
from school_lens.ingest._crosswalk import normalize_name

DOWNLOAD = ("https://www.ode.state.or.us/data/ReportCard/Media/DownloadFile"
            "?schlYr={yr}&fldr=stateData&flNm=AAGmediaSchoolsAggregate")
DEFAULT_YEAR_ID = 26          # 26 = 2024-2025 (see Media/GetYears)
VINTAGE = "2024-2025"
COUNTIES = {"Washington", "Multnomah"}

# AAG column -> fact_academics column.
_MAP = {
    "English Language Arts": "ela_proficiency",
    "Mathematics": "math_proficiency",
    "On-Time Graduation 2023-2024": "grad_rate",
    "On-Track to Graduate": "ninth_grade_on_track",
    "College Going 2022-23": "college_going_rate",
}
_MISSING = {"", "*", "--", "-", "n/a", "nan", "redacted", "<null>"}


def _pct(v) -> float | None:
    """Parse an ODE percentage cell; '>95%'/'<5%' -> the bound, '*'/blank -> None."""
    s = str(v).strip().replace("%", "").replace(",", "")
    if s.lower() in _MISSING:
        return None
    if s[:1] in (">", "<"):
        s = s[1:]
    try:
        return float(s)
    except ValueError:
        return None


def fetch_aag(year_id: int = DEFAULT_YEAR_ID) -> tuple[pd.DataFrame, str]:
    url = DOWNLOAD.format(yr=year_id)
    r = httpx.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), dtype=str, engine="python", on_bad_lines="skip")
    return df, url


def ingest_ode(year_id: int = DEFAULT_YEAR_ID,
               conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Ingest ODE At-A-Glance academics into fact_academics. Returns schools matched."""
    own = conn is None
    conn = conn or get_connection()
    ts = datetime.now(timezone.utc)
    try:
        df, url = fetch_aag(year_id)
        # Build a unique normalized-name -> nces_id map from the spine; drop
        # ambiguous names so a collision never mis-attributes data.
        spine: dict[str, str] = {}
        dupes: set[str] = set()
        for nces, name in conn.execute(
            "SELECT nces_id, name FROM schools WHERE name IS NOT NULL"
        ).fetchall():
            k = normalize_name(name)
            if k in spine:
                dupes.add(k)
            spine[k] = nces
        for k in dupes:
            spine.pop(k, None)

        conn.execute("DELETE FROM fact_academics WHERE source LIKE 'ODE%'")
        conn.execute("DELETE FROM source_crosswalk WHERE source = 'ODE AAG'")
        df = df[df["County"].isin(COUNTIES)]
        matched = 0
        for _, r in df.iterrows():
            name = r.get("School Name")
            if not isinstance(name, str) or not name.strip():
                continue
            nces = spine.get(normalize_name(name))
            if not nces:
                continue
            vals = {col: _pct(r.get(src)) for src, col in _MAP.items()}
            if all(v is None for v in vals.values()):
                continue
            conn.execute(
                """INSERT INTO fact_academics
                   (nces_id, school_year, ela_proficiency, math_proficiency, grad_rate,
                    ninth_grade_on_track, college_going_rate,
                    source, source_vintage, ingest_ts, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [nces, VINTAGE, vals["ela_proficiency"], vals["math_proficiency"],
                 vals["grad_rate"], vals["ninth_grade_on_track"], vals["college_going_rate"],
                 "ODE AAG", VINTAGE, ts, url],
            )
            sid = r.get("School ID")
            if isinstance(sid, str) and sid.strip():
                conn.execute(
                    """INSERT INTO source_crosswalk (source, source_id, nces_id, match_method, confidence)
                       VALUES ('ODE AAG', ?, ?, 'name_geo', 0.9)""",
                    [sid.strip(), nces],
                )
            matched += 1
        return matched
    finally:
        if own:
            conn.close()


def ingest(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    return ingest_ode(conn=conn)
