"""NCES ingest: the canonical school directory and geocodes.

CCD (Common Core of Data) supplies public + charter schools, enrollment,
teachers, geocodes, and the NCES School ID that is school-lens's primary key.
This is the FIRST ingest to run: it populates `schools`, which every other join
and the boundary crosswalk depend on.

Access path: the Urban Institute Education Data Portal API
(educationdata.urban.org), which serves CCD as clean JSON filterable by state
(fips=41 Oregon) and county_code, instead of the giant national flat files. The
directory endpoint carries directory + enrollment + teachers_fte in one call, so
the student-teacher ratio is computed here into fact_staffing.

PSS (private schools) is NOT in the Urban portal; private schools come from the
NCES PSS flat file in a separate ingest (see ingest/private_proxies.py).

Idempotent: clears prior CCD rows, then reinserts. Missing values in the API are
negative sentinels (-1/-2/-3); _num() maps them to None.
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import httpx

from school_lens.db import get_connection

API_TMPL = "https://educationdata.urban.org/api/v1/schools/ccd/directory/{year}/?fips=41"
DEFAULT_YEAR = 2023
# Washington + Multnomah counties (the data-load footprint; schema is national).
DEFAULT_COUNTIES = ("41067", "41051")

_LEVEL = {1: "elementary", 2: "middle", 3: "high"}  # else "other"

SOURCES = {
    "ccd": {"name": "NCES CCD via Urban Institute API", "url": API_TMPL},
}


def _num(v) -> float | None:
    """Parse a numeric API value; Urban uses negative sentinels for missing."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f < 0 else f


def _school_type(row: dict) -> str:
    if row.get("charter") == 1:
        return "charter"
    if row.get("magnet") == 1:
        return "magnet"
    return "traditional_public"


def fetch_ccd(year: int = DEFAULT_YEAR) -> list[dict]:
    """Pull all CCD directory rows for Oregon (following pagination)."""
    rows: list[dict] = []
    url: str | None = API_TMPL.format(year=year)
    with httpx.Client(timeout=90, follow_redirects=True) as c:
        while url:
            r = c.get(url)
            r.raise_for_status()
            d = r.json()
            rows.extend(d.get("results", []) or [])
            url = d.get("next")
    return rows


def ingest_ccd(
    year: int = DEFAULT_YEAR,
    counties: tuple[str, ...] = DEFAULT_COUNTIES,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Populate `schools` (+ crosswalk + fact_staffing ratio) from CCD. Returns rows."""
    own = conn is None
    conn = conn or get_connection()
    src = SOURCES["ccd"]["name"]
    vintage = f"{year - 1}-{year}"
    url = API_TMPL.format(year=year)
    ts = datetime.now(timezone.utc)
    try:
        rows = [r for r in fetch_ccd(year) if str(r.get("county_code")) in counties]
        # Idempotent refresh of the CCD-sourced rows only.
        conn.execute("DELETE FROM schools WHERE source LIKE 'NCES CCD%'")
        conn.execute("DELETE FROM source_crosswalk WHERE source = 'NCES CCD'")
        conn.execute("DELETE FROM fact_staffing WHERE source LIKE 'NCES CCD%'")
        n = 0
        for r in rows:
            nces = str(r.get("ncessch") or "").strip()
            if not nces:
                continue
            lat, lon = r.get("latitude"), r.get("longitude")
            ll_ok = (lat is not None and lon is not None
                     and 41.0 < lat < 47.0 and -125.0 < lon < -116.0)
            enr = _num(r.get("enrollment"))
            tfte = _num(r.get("teachers_fte"))
            conn.execute(
                """
                INSERT INTO schools
                    (nces_id, name, school_type, level, grade_low, grade_high,
                     district_name, street, city, state, zip, lat, lon, point,
                     enrollment, source, source_vintage, ingest_ts, source_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OR', ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                [nces, r.get("school_name"), _school_type(r),
                 _LEVEL.get(r.get("school_level"), "other"),
                 str(r.get("lowest_grade_offered")), str(r.get("highest_grade_offered")),
                 r.get("lea_name"), r.get("street_location"), r.get("city_location"),
                 str(r.get("zip_location") or ""),
                 lat if ll_ok else None, lon if ll_ok else None,
                 int(enr) if enr is not None else None, src, vintage, ts, url],
            )
            conn.execute(
                """INSERT INTO source_crosswalk (source, source_id, nces_id, match_method, confidence)
                   VALUES ('NCES CCD', ?, ?, 'exact_id', 1.0)""",
                [nces, nces],
            )
            if enr and tfte and tfte > 0:
                conn.execute(
                    """INSERT INTO fact_staffing
                       (nces_id, school_year, student_teacher_ratio,
                        source, source_vintage, ingest_ts, source_url)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [nces, vintage, round(enr / tfte, 1), src, vintage, ts, url],
                )
            n += 1
        # Build the 2913 point once for every CCD school with valid coordinates.
        conn.execute(
            """
            UPDATE schools
            SET point = ST_Transform(ST_FlipCoordinates(ST_Point(lon, lat)),
                                     'EPSG:4326', 'EPSG:2913')
            WHERE source LIKE 'NCES CCD%' AND lat IS NOT NULL AND point IS NULL
            """
        )
        return n
    finally:
        if own:
            conn.close()


def ingest(which: str = "ccd", conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Dispatch entry point. Currently only CCD (public/charter) is wired here."""
    if which != "ccd":
        raise ValueError(f"unknown NCES source '{which}'; only 'ccd' is wired (PSS is separate)")
    return ingest_ccd(conn=conn)
