"""Canonical domain entities.

These are the in-memory representations behind the DuckDB schema in db.py. The
canonical key is nces_id; school_type distinguishes public/charter/private so the
comparison engine can reason about the public/charter/private data asymmetry
(CLAUDE.md Section 3.2).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# Domains used to group metrics and per-domain completeness. private_signals holds
# the low-confidence private-school proxies (CLAUDE.md 3.2), grouped apart so the
# UI can render them in a dedicated, explicitly-caveated panel.
DOMAINS = ("academics", "discipline_safety", "staffing", "finance", "outcomes",
           "demographics", "private_signals")


class SchoolType(str, Enum):
    traditional_public = "traditional_public"
    charter = "charter"
    magnet = "magnet"
    private_religious = "private_religious"
    private_independent = "private_independent"

    @property
    def is_public(self) -> bool:
        """Charters and magnets are public schools (full NCES/CRDC/ODE coverage)."""
        return self in {
            SchoolType.traditional_public,
            SchoolType.charter,
            SchoolType.magnet,
        }

    @property
    def is_private(self) -> bool:
        return not self.is_public


class SchoolLevel(str, Enum):
    elementary = "elementary"
    middle = "middle"
    high = "high"
    other = "other"


class MatchMethod(str, Enum):
    exact_id = "exact_id"
    name_geo = "name_geo"
    manual = "manual"


class Provenance(BaseModel):
    source: str
    source_vintage: str | None = None
    source_url: str | None = None
    ingest_ts: datetime | None = None


class School(BaseModel):
    nces_id: str
    name: str
    school_type: SchoolType
    level: SchoolLevel = SchoolLevel.other
    grade_low: str | None = None
    grade_high: str | None = None
    district_name: str | None = None
    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    enrollment: int | None = None
    # Per-domain fraction of expected fields present, 0..1, keyed by DOMAINS.
    data_completeness: dict[str, float] = Field(default_factory=dict)

    @property
    def overall_confidence(self) -> float:
        """Mean per-domain completeness; 0 when nothing is known."""
        if not self.data_completeness:
            return 0.0
        return sum(self.data_completeness.values()) / len(self.data_completeness)


class GeocodeResult(BaseModel):
    """Address resolved to a point. method records the substrate used so the UI
    can label confidence (parcels_base exact match vs external approximate)."""

    address: str
    lat: float
    lon: float
    method: str           # parcels_base | census | manual
    confidence: float
    matched_address: str | None = None


class Assignment(BaseModel):
    """The school an address is assigned to at one grade band."""

    level: SchoolLevel
    nces_id: str | None
    school_name: str | None
    school_year: str | None = None
    note: str | None = None      # e.g. "no boundary loaded for this level"
