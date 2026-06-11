"""Entity resolution for source_crosswalk.

Public/charter sources usually carry the NCES id directly (exact_id match, done at
the call site). Sources that carry only a name and a location (district boundary
GIS, reviews, news) are resolved here by normalized name plus proximity. Geometry
is EPSG:2913 (feet), so plain Euclidean distance is correct over short ranges.
"""
from __future__ import annotations

import math
import re

# Tokens that carry no disambiguating signal in a school name. Includes the
# grade-band abbreviations district boundary layers use (ES/MS/HS/K8), so an
# abbreviated catchment name ("Aloha HS") normalizes to the same key as the full
# NCES name ("Aloha High School") -> both "aloha".
_DROP = {
    "school", "elementary", "elem", "middle", "high", "jr", "junior", "sr",
    "senior", "academy", "the", "of", "at", "and", "school's",
    "es", "ms", "hs", "k8", "jh", "jhs", "sh", "primary", "intermediate",
    "charter", "option",
}
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation and generic school-type tokens.

    'Cedar Mill Elementary School' -> 'cedar mill'
    'The Catlin Gabel School'      -> 'catlin gabel'
    """
    s = _PUNCT_RE.sub(" ", name.lower())
    return " ".join(t for t in s.split() if t and t not in _DROP)


def _dist_ft(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def match_by_name_geo(
    name: str,
    point: tuple[float, float] | None,
    candidates: list[tuple[str, str, tuple[float, float] | None]],
    *,
    max_ft: float = 2640.0,
) -> tuple[str, float] | None:
    """Resolve (name, point) to an nces_id among candidates.

    candidates: list of (nces_id, name, point_2913_or_None).
    Returns (nces_id, confidence) or None. Confidence: 0.9 exact name within
    range, 0.7 unique exact name without usable geometry, 0.5 geo-only nearest.
    """
    target = normalize_name(name)
    exact = [(cid, cn, cp) for (cid, cn, cp) in candidates if normalize_name(cn) == target]

    if exact:
        if point is not None:
            within = sorted(
                ((cid, _dist_ft(point, cp)) for (cid, cn, cp) in exact if cp is not None),
                key=lambda x: x[1],
            )
            within = [(cid, d) for cid, d in within if d <= max_ft]
            if within:
                return within[0][0], 0.9
        if len(exact) == 1:
            return exact[0][0], 0.7   # unique name match, geometry unavailable
        return None                    # ambiguous names, no geometry to disambiguate

    if point is None:
        return None
    geo = sorted(
        ((cid, _dist_ft(point, cp)) for (cid, cn, cp) in candidates if cp is not None),
        key=lambda x: x[1],
    )
    geo = [(cid, d) for cid, d in geo if d <= max_ft]
    return (geo[0][0], 0.5) if geo else None
