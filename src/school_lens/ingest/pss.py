"""Private-school spine from the NCES EDGE private-school geocode FeatureServer.

PSS itself is a flat-file survey, but EDGE publishes the geocoded private-school
universe as an ArcGIS FeatureServer, so we reuse the same puller as the boundary
ingests. Loads name, address, and lat/lon for Washington + Multnomah private
schools into `schools` with school_type = private_*.

What this file does NOT carry (honest gaps, all CLAUDE.md 3.2 "data desert"):
- Grade level / grade span: not in the geocode file (it is in the PSS survey
  CSV). Level is inferred from the name where possible, else "other" (a K-8/K-12
  span); the comparison treats "other" privates as candidates in every band.
- Religious vs independent: inferred from the name (the survey file has the real
  RELIG field).
- Tuition: not in any federal dataset; it is published per school (a Phase-2
  per-school enrichment, not a column we can populate from here).

Academic metrics stay blank for privates by design (no state assessment / CRDC).
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import datetime, timezone

import duckdb

from school_lens.db import get_connection

# PSS survey public-use file (joins to the geocode rows by PPIN).
PSS_SURVEY = "https://nces.ed.gov/surveys/pss/zip/pss2122_pu_csv.zip"
_RELIG_TYPE = {"1": "private_religious", "2": "private_religious", "3": "private_independent"}
_PSS_LEVEL = {"1": "elementary", "2": "high", "3": "other"}

# Two-phase scrape cache (scripts/scrape_private.py writes it; we read it),
# kept in %LOCALAPPDATA% so it survives DB rebuilds.
CACHE = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "school-lens" / "private_cache.jsonl"


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f < 0:  # NaN or PSS missing-code
        return None
    return f

GEOCODE = ("https://services1.arcgis.com/Ua5sjt3LWTPigjyD/ArcGIS/rest/services/"
           "Private_School_Locations_Current/FeatureServer")
LAYER = 0
COUNTIES = {"067", "051"}  # Washington, Multnomah (county FIPS within state 41)

# Name fragments that imply a religious affiliation (the survey file has the real
# field; this is a display-grade inference).
_RELIG = (
    "catholic", "christ", "christian", "lutheran", "saint", "st.", "jewish",
    "hebrew", "torah", "yeshiva", "islamic", "muslim", "baptist", "adventist",
    "bible", "trinity", "grace", "holy", "our lady", "blessed", "cathedral",
    "parish", "methodist", "presbyterian", "calvary", "gospel", "evangel",
    "jesuit", "mennonite", "lds", "faith", "shepherd", "redeemer", "sacred",
)


def _title(s) -> str | None:
    return str(s).title() if s else None


# Standalone "St"/"Sts" (e.g., "St Cecilia"), which the keyword list misses.
_ST_RE = re.compile(r"\bsts?\b")


def _is_religious(name: str) -> bool:
    low = name.lower()
    return bool(_ST_RE.search(low)) or any(k in low for k in _RELIG)


def _level(name: str) -> str:
    low = name.lower()
    if "high school" in low or low.endswith(" hs") or "preparatory" in low or "prep" in low:
        return "high"
    if "middle" in low or "junior high" in low:
        return "middle"
    if "elementary" in low or "primary" in low:
        return "elementary"
    return "other"  # K-8 / K-12 span; treated as a candidate in every band


def ingest_pss(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Load Washington + Multnomah private schools into `schools`. Returns rows."""
    from school_lens._vendor.arcgis import query_arcgis_layer

    own = conn is None
    conn = conn or get_connection()
    ts = datetime.now(timezone.utc)
    try:
        gdf = query_arcgis_layer(GEOCODE, LAYER, where="STATE='OR'")
        if gdf.empty:
            return 0
        conn.execute("DELETE FROM schools WHERE source LIKE 'NCES EDGE private%'")
        conn.execute("DELETE FROM source_crosswalk WHERE source = 'NCES PSS'")
        n = 0
        for _, r in gdf.iterrows():
            if str(r.get("CNTY") or "") not in COUNTIES:
                continue
            ppin = str(r.get("PPIN") or "").strip()
            name = _title(r.get("NAME"))
            if not ppin or not name:
                continue
            lat, lon = r.get("LAT"), r.get("LON")
            stype = "private_religious" if _is_religious(name) else "private_independent"
            conn.execute(
                """INSERT INTO schools
                   (nces_id, name, school_type, level, district_name, street, city,
                    state, zip, lat, lon, point, source, source_vintage, ingest_ts, source_url)
                   VALUES (?, ?, ?, ?, '(private)', ?, ?, 'OR', ?, ?, ?, NULL, ?, ?, ?, ?)""",
                [ppin, name, stype, _level(name), _title(r.get("STREET")), _title(r.get("CITY")),
                 str(r.get("ZIP") or ""), lat, lon,
                 "NCES EDGE private school geocode", str(r.get("SCHOOLYEAR") or ""), ts, f"{GEOCODE}/0"],
            )
            conn.execute(
                """INSERT INTO source_crosswalk (source, source_id, nces_id, match_method, confidence)
                   VALUES ('NCES PSS', ?, ?, 'exact_id', 1.0)""",
                [ppin, ppin],
            )
            n += 1
        conn.execute(
            """UPDATE schools
               SET point = ST_Transform(ST_FlipCoordinates(ST_Point(lon, lat)),
                                        'EPSG:4326', 'EPSG:2913')
               WHERE source LIKE 'NCES EDGE private%' AND lat IS NOT NULL AND point IS NULL"""
        )
        return n
    finally:
        if own:
            conn.close()


def enrich_pss_survey(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Join the PSS survey file by PPIN: real enrollment, student-teacher ratio,
    religious orientation (-> school_type), and level (-> grade band). Returns
    privates enriched."""
    import io
    import zipfile

    import httpx
    import pandas as pd

    own = conn is None
    conn = conn or get_connection()
    ts = datetime.now(timezone.utc)
    try:
        r = httpx.get(PSS_SURVEY, timeout=180, follow_redirects=True)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        df = pd.read_csv(z.open(csv), dtype=str, encoding="latin-1", engine="python",
                         on_bad_lines="skip",
                         usecols=lambda c: c in ("PPIN", "NUMSTUDS", "NUMTEACH", "RELIG", "LEVEL"))
        survey = {str(row["PPIN"]).strip(): row for _, row in df.iterrows()}

        conn.execute("DELETE FROM fact_staffing WHERE source LIKE 'NCES PSS%'")
        privs = conn.execute(
            "SELECT nces_id FROM schools WHERE school_type LIKE 'private%'"
        ).fetchall()
        n = 0
        for (ppin,) in privs:
            row = survey.get(str(ppin))
            if row is None:
                continue
            enr, tch = _num(row.get("NUMSTUDS")), _num(row.get("NUMTEACH"))
            stype = _RELIG_TYPE.get(str(row.get("RELIG") or "").strip())
            level = _PSS_LEVEL.get(str(row.get("LEVEL") or "").strip())
            sets, params = [], []
            if enr is not None:
                sets.append("enrollment = ?"); params.append(int(enr))
            if stype:
                sets.append("school_type = ?"); params.append(stype)
            if level:
                sets.append("level = ?"); params.append(level)
            if sets:
                conn.execute(f"UPDATE schools SET {', '.join(sets)} WHERE nces_id = ?", [*params, ppin])
            if enr and tch and tch > 0:
                conn.execute(
                    """INSERT INTO fact_staffing
                       (nces_id, school_year, student_teacher_ratio, source, source_vintage, ingest_ts, source_url)
                       VALUES (?, '2021-2022', ?, 'NCES PSS survey', '2021-2022', ?, ?)""",
                    [ppin, round(enr / tch, 1), ts, PSS_SURVEY],
                )
            n += 1
        return n
    finally:
        if own:
            conn.close()


def load_private_enrichment(conn: duckdb.DuckDBPyConnection | None = None,
                            cache: pathlib.Path | None = None) -> int:
    """Load scraped tuition / acceptance (scripts/scrape_private.py -> CACHE jsonl)
    into fact_private_signals. Idempotent. Returns rows written."""
    own = conn is None
    conn = conn or get_connection()
    ts = datetime.now(timezone.utc)
    cache = cache or CACHE
    try:
        if not cache.exists():
            return 0
        conn.execute("DELETE FROM fact_private_signals WHERE source LIKE 'PrivateSchoolReview%'")
        n = 0
        for line in cache.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ppin = str(d.get("ppin") or "")
            tuition, acc = d.get("tuition"), d.get("acceptance")
            if not ppin or (tuition is None and acc is None):
                continue
            conn.execute(
                """INSERT INTO fact_private_signals
                   (nces_id, school_year, tuition, acceptance_rate, source, source_vintage, ingest_ts, source_url)
                   VALUES (?, '2024', ?, ?, 'PrivateSchoolReview', '2024', ?, ?)""",
                [ppin, tuition, acc, ts, d.get("url")],
            )
            n += 1
        return n
    finally:
        if own:
            conn.close()


def ingest(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    return ingest_pss(conn=conn)
