"""Driving distance and time.

OSRM (self-hosted) is the routing engine. When an OSRM base URL is configured
(settings.osrm_url, e.g. http://127.0.0.1:5000), drive_distance() returns the
real network distance, duration, and optionally the route geometry for drawing.

When OSRM is not configured, or is configured but unreachable, it falls back to a
haversine straight-line distance, tagged so the UI never presents an approximate
number as a true drive metric:

    method="osrm"              real route from OSRM
    method="haversine_approx"  no OSRM configured; straight-line
    method="osrm_unreachable"  OSRM configured but the request failed; straight-line

Straight-line is also the cheap pre-filter for the comparison candidate set
(CLAUDE.md Section 6); the OSRM call is reserved for the few schools we display.
"""
from __future__ import annotations

import math

_EARTH_MILES = 3958.7613
_METERS_PER_MILE = 1609.344


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_MILES * math.asin(math.sqrt(a))


def osrm_route(
    origin: tuple[float, float],
    dest: tuple[float, float],
    *,
    osrm_url: str,
    profile: str = "driving",
    timeout: float = 10.0,
    with_geometry: bool = False,
) -> dict | None:
    """Query OSRM for one origin->dest route. Returns the parsed result or None.

    Returns None (never raises) on any transport, HTTP, or payload error, so the
    caller can fall back to the straight-line approximation. OSRM coordinates are
    lon,lat; distance is meters and duration is seconds.
    """
    import httpx

    lon1, lat1 = origin[1], origin[0]
    lon2, lat2 = dest[1], dest[0]
    base = osrm_url.rstrip("/")
    url = f"{base}/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}"
    params = {
        "overview": "full" if with_geometry else "false",
        "geometries": "geojson",
        "alternatives": "false",
        "steps": "false",
    }
    try:
        r = httpx.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route = data["routes"][0]
        miles = route["distance"] / _METERS_PER_MILE
        minutes = route["duration"] / 60.0
        out: dict = {
            "miles": round(miles, 2),
            "minutes": round(minutes, 1),
            "method": "osrm",
            "geometry": route.get("geometry") if with_geometry else None,
        }
        return out
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def drive_distance(
    origin: tuple[float, float],
    dest: tuple[float, float],
    *,
    osrm_url: str | None = None,
    with_geometry: bool = False,
) -> dict:
    """Distance (and time, when routing is available) between two (lat, lon) points.

    Returns {miles, minutes, method, geometry}. With OSRM configured and reachable,
    minutes and (optionally) geometry are populated. Otherwise minutes is None and
    method records which fallback produced the straight-line number.
    """
    if osrm_url:
        routed = osrm_route(origin, dest, osrm_url=osrm_url, with_geometry=with_geometry)
        if routed is not None:
            return routed
        method = "osrm_unreachable"  # configured but failed; surface it, don't hide it
    else:
        method = "haversine_approx"
    miles = haversine_miles(origin[0], origin[1], dest[0], dest[1])
    return {"miles": round(miles, 2), "minutes": None, "method": method, "geometry": None}
