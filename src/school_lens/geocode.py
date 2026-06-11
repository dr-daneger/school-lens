"""Address -> point, preferring the house-hunter parcels_base substrate.

parcels_base holds ~655k Washington + Multnomah parcels with siteaddr + geometry
in EPSG:2913. We match on normalized address (UPPER(TRIM(...)), the same key
house-hunter's Redfin worker used) and return the parcel centroid in WGS84 plus a
method tag so the UI can label match confidence. An external geocoder (Census)
is the documented fallback for addresses not in parcels_base; it is not wired yet.
"""
from __future__ import annotations

import duckdb

from school_lens.db import get_connection, house_hunter_attached
from school_lens.domain.models import GeocodeResult


def geocode(address: str, conn: duckdb.DuckDBPyConnection | None = None) -> GeocodeResult | None:
    """Resolve an address to a point via parcels_base. Returns None if not found."""
    own = conn is None
    conn = conn or get_connection(read_only=True)
    try:
        if not house_hunter_attached(conn):
            # Without the parcel substrate we cannot geocode locally yet.
            return None
        # Centroid -> 2913->4326, flip to standard [lon, lat], read X=lon Y=lat.
        row = conn.execute(
            """
            WITH m AS (
                SELECT siteaddr,
                       ST_FlipCoordinates(
                           ST_Transform(ST_Centroid(geometry), 'EPSG:2913', 'EPSG:4326')
                       ) AS pt
                FROM hh.parcels_base
                WHERE UPPER(TRIM(siteaddr)) = UPPER(TRIM(?))
                LIMIT 1
            )
            SELECT siteaddr, ST_X(pt) AS lon, ST_Y(pt) AS lat FROM m
            """,
            [address],
        ).fetchone()
        if row is None:
            return None
        matched_addr, lon, lat = row
        return GeocodeResult(
            address=address, lat=lat, lon=lon, method="parcels_base",
            confidence=0.95, matched_address=matched_addr,
        )
    finally:
        if own:
            conn.close()
