"""Opportunity Atlas ingest: tract-level adult outcomes (CONTEXT only).

Opportunity Insights + Census. Adult income rank, incarceration rate, and college
attendance for children by CHILDHOOD Census tract (cohort born 1978-1985). These
are geography-keyed: they join to a school only by spatial containment through the
catchment (CLAUDE.md Section 4), and they are CONTEXT metrics, never school_effect
(Section 3.1) -- they describe where children grew up, not what a school did.

Upsert into tract_outcomes keyed on tract_geoid. Tract geometry comes from Census
TIGER (loaded separately) and is reprojected to 2913 for the area-weighting join.
"""
from __future__ import annotations

SOURCE = {
    "name": "Opportunity Atlas (Opportunity Insights / Census)",
    "url": "https://opportunityinsights.org/data/",
    "key": "Census tract GEOID",
    "fields": ["kfr_pooled_p25", "kfr_pooled_p75", "incarceration_rate", "college_attendance"],
}


def fetch():
    raise NotImplementedError(f"Opportunity Atlas fetch not wired; see {SOURCE['url']}")


def ingest(conn=None):
    raise NotImplementedError("Opportunity Atlas ingest not wired; confirm table + GEOID columns")
