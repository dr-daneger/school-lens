"""Shared client for the Urban Institute Education Data Portal API.

One place for the base URL, the missing-value sentinel rule, and paginated
fetching, reused by the CCD / CRDC / EDFacts ingests (educationdata.urban.org).
"""
from __future__ import annotations

import httpx

BASE = "https://educationdata.urban.org/api/v1"


def num(v) -> float | None:
    """Parse a numeric API value; Urban uses negative sentinels (-1/-2/-3) for missing."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f < 0 else f


def paginate(endpoint: str, params: dict, *, timeout: float = 90.0) -> list[dict]:
    """Return all result rows for an endpoint path (relative to BASE), following `next`."""
    out: list[dict] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        url: str | None = f"{BASE}{endpoint}"
        p: dict | None = dict(params)
        while url:
            r = c.get(url, params=p)
            r.raise_for_status()
            d = r.json()
            out.extend(d.get("results", []) or [])
            url, p = d.get("next"), None  # next URL already carries the query
    return out
