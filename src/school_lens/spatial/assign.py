"""Address point -> assigned school via point-in-polygon over boundaries.

Boundaries are EPSG:2913. The WGS84 (lon, lat) point is flipped to (lat, lon) and
transformed to 2913 (the inverse of house-hunter's display idiom), then tested
with ST_Contains against each grade-band boundary. Returns one Assignment per
level; levels with no loaded boundary are reported honestly rather than guessed.
"""
from __future__ import annotations

import duckdb

from school_lens.db import get_connection
from school_lens.domain.models import Assignment, SchoolLevel

_LEVELS = (SchoolLevel.elementary, SchoolLevel.middle, SchoolLevel.high)


def assign_schools(
    lat: float,
    lon: float,
    conn: duckdb.DuckDBPyConnection | None = None,
    school_year: str | None = None,
) -> list[Assignment]:
    """Return the assigned school for each grade band at (lat, lon)."""
    own = conn is None
    conn = conn or get_connection(read_only=True)
    try:
        out: list[Assignment] = []
        for level in _LEVELS:
            row = conn.execute(
                """
                WITH pt AS (
                    SELECT ST_Transform(
                        ST_FlipCoordinates(ST_Point(?, ?)), 'EPSG:4326', 'EPSG:2913'
                    ) AS g
                )
                SELECT b.nces_id, COALESCE(s.name, b.raw_name), b.school_year
                FROM boundaries b
                JOIN pt ON ST_Contains(b.geometry, pt.g)
                LEFT JOIN schools s ON s.nces_id = b.nces_id
                WHERE b.level = ?
                  AND (? IS NULL OR b.school_year = ?)
                ORDER BY b.school_year DESC
                LIMIT 1
                """,
                [lon, lat, level.value, school_year, school_year],
            ).fetchone()
            if row is None:
                # Distinguish "no boundary data loaded" from "outside all boundaries".
                has_any = conn.execute(
                    "SELECT COUNT(*) FROM boundaries WHERE level = ?", [level.value]
                ).fetchone()[0]
                note = ("address not inside any loaded boundary" if has_any
                        else "no boundary loaded for this level")
                out.append(Assignment(level=level, nces_id=None, school_name=None, note=note))
            else:
                nces_id, name, year = row
                out.append(Assignment(level=level, nces_id=nces_id,
                                      school_name=name, school_year=year))
        return out
    finally:
        if own:
            conn.close()
