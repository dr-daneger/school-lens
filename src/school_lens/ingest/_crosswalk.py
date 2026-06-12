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


def _match_subset(
    name: str,
    point: tuple[float, float] | None,
    candidates: list[tuple[str, str, tuple[float, float] | None]],
    *,
    max_ft: float,
    require_geo: bool,
) -> tuple[str, float] | None:
    """Token-subset match: one normalized name's token set contains the other's.

    Catches renames and partial catchment labels ('McDaniel' -> 'Leodis V.
    McDaniel High School', 'Boise-Eliot/Humboldt' -> 'Boise-Eliot Elementary
    School'). Geo-confirmed 0.85; unique without geometry 0.75; ambiguous None.
    """
    target = set(normalize_name(name).split())
    if not target:
        return None
    hits = []
    for cid, cn, cp in candidates:
        cand = set(normalize_name(cn).split())
        if cand and (target <= cand or cand <= target):
            hits.append((cid, cp))
    if not hits:
        return None
    if point is not None:
        within = sorted(
            ((cid, _dist_ft(point, cp)) for cid, cp in hits if cp is not None),
            key=lambda x: x[1],
        )
        within = [(cid, d) for cid, d in within if d <= max_ft]
        if within:
            return within[0][0], 0.85
    if require_geo:
        return None
    if len(hits) == 1:
        return hits[0][0], 0.75
    return None


def match_catchment(
    name: str,
    level: str | None,
    point: tuple[float, float] | None,
    candidates: list[tuple[str, str, str | None, tuple[float, float] | None]],
    *,
    max_ft: float = 2640.0,
) -> tuple[str, float] | None:
    """Resolve a catchment (name, grade band, centroid) to a school.

    candidates: list of (nces_id, name, level, point_2913_or_None). The grade
    band is signal match_by_name_geo cannot use: 'Banks Elementary School' and
    'Banks High School' both normalize to 'banks', but only one is a high
    school. Ladder, most precise first; each step must be unambiguous:

    1. exact normalized name among same-level schools (0.9 geo / 0.7 unique)
    2. token-subset among same-level schools (0.85 geo / 0.75 unique)
    3. exact normalized name across all levels (a K-8 catchment lists one
       school at two grade bands, e.g. a 'middle' cell naming an 'elementary'
       K-8 school)
    4. token-subset across all levels, geo-confirmed only (0.85)
    """
    same = [(cid, cn, cp) for (cid, cn, lvl, cp) in candidates if lvl == level]
    everything = [(cid, cn, cp) for (cid, cn, _lvl, cp) in candidates]

    match = match_by_name_geo(name, point, same, max_ft=max_ft)
    if match and match[1] >= 0.7:   # exclude the geo-only 0.5 nearest fallback
        return match
    match = _match_subset(name, point, same, max_ft=max_ft, require_geo=False)
    if match:
        return match
    match = match_by_name_geo(name, point, everything, max_ft=max_ft)
    if match and match[1] >= 0.7:
        return match
    return _match_subset(name, point, everything, max_ft=max_ft, require_geo=True)
