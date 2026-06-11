"""The metric registry: the single source of truth for how every metric is
grouped, scored, and labeled.

This registry encodes CLAUDE.md Sections 3.1 and 3.2. Each metric is tagged `kind`:

- school_effect: reflects what the school does (growth, on-track, cohort
  graduation). These are the only metrics used for the meets-or-beats academic
  comparison, because they are the least confounded by who enrolls.
- context: describes the population, environment, or neighborhood, and is
  confounded by selection. This includes proficiency LEVELS (dominated by
  incoming demographics), discipline/safety counts (well-documented demographic
  disproportionality), staffing ratios, and neighborhood adult outcomes
  (Opportunity Atlas). Context metrics are shown, grouped by domain, with the
  selection caveat attached; they are excluded from the default quality score so
  the tool never launders demographics into an implied school ranking. The user
  may weight them up explicitly.
- private_proxy: a low-confidence signal used ONLY to characterize private
  schools, which sit in a data desert (no state assessments, no CRDC; CLAUDE.md
  3.2). The online and research consensus is that there is no clean single
  measure of private-school quality: test-score LEVELS are selection-confounded
  just like public ones, and value-added (the cleanest signal) cannot be computed
  without assessment data private schools do not report. The least-bad signal
  parents and researchers actually cite is college matriculation / longer-run
  outcomes, with accreditation as a quality floor. Both are themselves
  selection-confounded, so they are kept OUT of the public school_effect
  comparison and OUT of the default quality score, and are surfaced in a
  dedicated panel at explicitly lower confidence (PRIVATE_PROXY_CONFIDENCE). They
  let a private option earn a place in the comparison on available evidence
  without being credited for unmeasured quality or penalized for missing cells.

`direction` is used for normalization (which end is "good"); for context and
private_proxy metrics it just orients the scale and should not be read as a
clean school-quality judgment.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class MetricKind(str, Enum):
    school_effect = "school_effect"
    context = "context"
    private_proxy = "private_proxy"


class MetricDirection(str, Enum):
    higher_is_better = "higher_is_better"
    lower_is_better = "lower_is_better"


class Metric(BaseModel):
    id: str
    label: str
    domain: str            # one of models.DOMAINS
    kind: MetricKind
    direction: MetricDirection
    unit: str
    source: str            # ingest source that supplies it (see docs/data-sources.md)


_SE = MetricKind.school_effect
_CX = MetricKind.context
_PP = MetricKind.private_proxy
_HI = MetricDirection.higher_is_better
_LO = MetricDirection.lower_is_better

# Default confidence for private_proxy values at gather time. Sits at the default
# meets-or-beats min_confidence floor (0.5): includable when a user deliberately
# weights these up, but never treated as authoritative. Encodes CLAUDE.md 3.2's
# "explicitly lower confidence."
PRIVATE_PROXY_CONFIDENCE = 0.5


METRICS: tuple[Metric, ...] = (
    # --- Academics: school_effect (clean-ish school signal) ---
    Metric(id="math_growth", label="Math academic growth", domain="academics",
           kind=_SE, direction=_HI, unit="percentile", source="ODE"),
    Metric(id="ela_growth", label="ELA academic growth", domain="academics",
           kind=_SE, direction=_HI, unit="percentile", source="ODE"),
    Metric(id="ninth_grade_on_track", label="9th-grade on-track", domain="academics",
           kind=_SE, direction=_HI, unit="percent", source="ODE"),
    Metric(id="grad_rate", label="4-year cohort graduation rate", domain="academics",
           kind=_SE, direction=_HI, unit="percent", source="ODE"),
    # --- Academics: context (levels, demographic-heavy) ---
    Metric(id="math_proficiency", label="Math proficiency (level)", domain="academics",
           kind=_CX, direction=_HI, unit="percent", source="ODE"),
    Metric(id="ela_proficiency", label="ELA proficiency (level)", domain="academics",
           kind=_CX, direction=_HI, unit="percent", source="ODE"),
    Metric(id="college_going_rate", label="College-going rate", domain="academics",
           kind=_CX, direction=_HI, unit="percent", source="ODE"),
    # --- Discipline & safety: context (confounded; shown in its own panel) ---
    Metric(id="suspension_rate", label="Out-of-school suspension rate",
           domain="discipline_safety", kind=_CX, direction=_LO, unit="percent", source="CRDC"),
    Metric(id="law_enforcement_referral_rate", label="Referrals to law enforcement",
           domain="discipline_safety", kind=_CX, direction=_LO, unit="per_100", source="CRDC"),
    Metric(id="arrest_rate", label="School-related arrests",
           domain="discipline_safety", kind=_CX, direction=_LO, unit="per_100", source="CRDC"),
    Metric(id="drug_incident_rate", label="Drug/alcohol incidents",
           domain="discipline_safety", kind=_CX, direction=_LO, unit="per_100", source="ODE"),
    Metric(id="chronic_absentee_rate", label="Chronic absenteeism",
           domain="discipline_safety", kind=_CX, direction=_LO, unit="percent", source="CRDC"),
    # --- Staffing: context ---
    Metric(id="student_teacher_ratio", label="Student-teacher ratio",
           domain="staffing", kind=_CX, direction=_LO, unit="ratio", source="NCES/PSS"),
    Metric(id="student_counselor_ratio", label="Student-counselor ratio",
           domain="staffing", kind=_CX, direction=_LO, unit="ratio", source="CRDC"),
    # --- Neighborhood adult outcomes: context (property of where kids grow up) ---
    Metric(id="adult_income_rank", label="Adult income rank (childhood tract)",
           domain="outcomes", kind=_CX, direction=_HI, unit="percentile", source="OpportunityAtlas"),
    Metric(id="incarceration_rate", label="Adult incarceration rate (childhood tract)",
           domain="outcomes", kind=_CX, direction=_LO, unit="percent", source="OpportunityAtlas"),
    Metric(id="neighborhood_median_income", label="Neighborhood median household income",
           domain="demographics", kind=_CX, direction=_HI, unit="usd", source="ACS/OI"),
    Metric(id="pct_bachelors_plus", label="Neighborhood adults with a bachelor's+",
           domain="demographics", kind=_CX, direction=_HI, unit="percent", source="ACS/OI"),
    Metric(id="poverty_rate", label="Neighborhood poverty rate",
           domain="demographics", kind=_CX, direction=_LO, unit="percent", source="ACS/OI"),
    # --- Private-school proxies: low-confidence, never in the public score (3.2) ---
    # Primary: the best private-quality signal parents and research actually cite.
    Metric(id="college_matriculation", label="College matriculation rate (private proxy)",
           domain="private_signals", kind=_PP, direction=_HI, unit="percent",
           source="School-published profile / NCES PSS / Niche"),
    # Floor: accredited by a recognized body (NWAC/Cognia, or NAIS membership).
    Metric(id="accreditation", label="Accreditation (private proxy)",
           domain="private_signals", kind=_PP, direction=_HI, unit="boolean",
           source="NCES PSS affiliation / NWAC / Cognia / NAIS"),
    # Private attributes (not quality; shown for context in the private panel).
    Metric(id="tuition", label="Annual tuition", domain="private_signals",
           kind=_PP, direction=_LO, unit="usd", source="PrivateSchoolReview"),
    Metric(id="acceptance_rate", label="Admission acceptance rate", domain="private_signals",
           kind=_PP, direction=_LO, unit="percent", source="PrivateSchoolReview"),
)

_BY_ID = {m.id: m for m in METRICS}


def get(metric_id: str) -> Metric:
    return _BY_ID[metric_id]


def all_ids() -> list[str]:
    return [m.id for m in METRICS]


def by_kind(kind: MetricKind) -> list[Metric]:
    return [m for m in METRICS if m.kind == kind]


def by_domain(domain: str) -> list[Metric]:
    return [m for m in METRICS if m.domain == domain]


def school_effect_ids() -> list[str]:
    return [m.id for m in METRICS if m.kind == _SE]


def context_ids() -> list[str]:
    return [m.id for m in METRICS if m.kind == _CX]


def private_proxy_ids() -> list[str]:
    return [m.id for m in METRICS if m.kind == _PP]


def default_weights() -> dict[str, float]:
    """Default scorecard weights: school_effect metrics count; context and
    private_proxy start at 0.

    This is the honest default (the quality score ignores demographics and the
    low-confidence private proxies). The UI exposes these so the user can weight
    other metrics up deliberately.
    """
    return {m.id: (1.0 if m.kind == _SE else 0.0) for m in METRICS}
