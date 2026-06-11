"""The comparison engine.

Pure functions over an in-memory candidate set, so the scoring rules are testable
without a database. Three ideas, all from CLAUDE.md Sections 3 and 6:

1. Normalize each metric across the candidate set (min-max, direction-adjusted),
   so higher normalized always means "better on this metric." Missing data stays
   missing (None), never 0; a blank must not read as a bad score.
2. Quality score is a transparent weighted mean of school_effect metrics. The
   default weights (metrics.default_weights) zero out context metrics, so the
   default score never launders demographics. The user may weight context up.
3. meets-or-beats compares a candidate against the baseline (the assigned public
   school) only on school_effect metrics both report at sufficient confidence.
   With too few shared metrics the verdict is None ("not comparable"), not False;
   a private school is never failed for missing data (Section 3.2).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from school_lens.domain import metrics as M
from school_lens.domain.models import School, SchoolType

# A normalized-scale band within which two schools are called tied rather than
# one being credited as better. 0.01 = 1% of the per-metric normalized range.
TIE_BAND = 0.01


class MetricValue(BaseModel):
    value: float | None = None
    confidence: float = 1.0


class Candidate(BaseModel):
    school: School
    values: dict[str, MetricValue] = Field(default_factory=dict)


class ScoredMetric(BaseModel):
    metric_id: str
    raw: float | None
    normalized: float | None    # 0..1 within the candidate set, higher = better
    confidence: float
    kind: str
    domain: str
    direction: str


class CandidateScore(BaseModel):
    nces_id: str
    name: str
    school_type: SchoolType
    is_baseline: bool
    metrics: dict[str, ScoredMetric]
    quality_score: float | None
    meets_or_beats: bool | None
    comparison: dict | None
    data_completeness: dict[str, float]
    lat: float | None = None
    lon: float | None = None


class ScoreCard(BaseModel):
    baseline_nces_id: str | None
    candidates: list[CandidateScore]
    weights: dict[str, float]
    min_confidence: float
    notes: list[str] = Field(default_factory=list)


def _normalize(raw: float, lo: float, hi: float, direction: M.MetricDirection,
               eps: float = 1e-6) -> float:
    if hi - lo < eps:
        return 0.5  # no spread in the set; neutral
    norm = (raw - lo) / (hi - lo)
    if direction == M.MetricDirection.lower_is_better:
        norm = 1.0 - norm
    return norm


def _quality_score(scored: dict[str, ScoredMetric], weights: dict[str, float],
                   min_confidence: float) -> float | None:
    """Weighted mean of normalized metrics with weight > 0 and adequate confidence.

    With default weights this is a school_effect-only score (context weights are
    0). Returns None when no qualifying metric is present.
    """
    num = den = 0.0
    for mid, s in scored.items():
        w = weights.get(mid, 0.0)
        if w <= 0 or s.normalized is None or s.confidence < min_confidence:
            continue
        num += w * s.normalized
        den += w
    return (num / den) if den > 0 else None


def _meets_or_beats(cand: dict[str, ScoredMetric], base: dict[str, ScoredMetric],
                    min_confidence: float, min_shared: int) -> tuple[bool | None, dict]:
    """Compare candidate vs baseline on shared school_effect metrics only."""
    n_better = n_worse = n_tie = 0
    shared: list[str] = []
    for mid in M.school_effect_ids():
        cs, bs = cand.get(mid), base.get(mid)
        if not cs or not bs or cs.raw is None or bs.raw is None:
            continue
        if cs.confidence < min_confidence or bs.confidence < min_confidence:
            continue
        shared.append(mid)
        diff = (cs.normalized or 0.0) - (bs.normalized or 0.0)
        if abs(diff) <= TIE_BAND:
            n_tie += 1
        elif diff > 0:
            n_better += 1
        else:
            n_worse += 1
    verdict: bool | None = None
    if len(shared) >= min_shared:
        verdict = n_worse == 0  # meets or beats on every shared school_effect metric
    comparison = {
        "shared_metrics": shared,
        "n_shared": len(shared),
        "n_better": n_better,
        "n_worse": n_worse,
        "n_tie": n_tie,
        "comparable": len(shared) >= min_shared,
    }
    return verdict, comparison


def build_scorecard(
    candidates: list[Candidate],
    *,
    baseline_nces_id: str | None = None,
    weights: dict[str, float] | None = None,
    min_confidence: float = 0.5,
    min_shared: int = 2,
) -> ScoreCard:
    """Score a candidate set and, if a baseline is given, run meets-or-beats."""
    weights = weights or M.default_weights()
    notes: list[str] = []

    # Per-metric bounds across all candidates that report the metric.
    bounds: dict[str, tuple[float, float]] = {}
    for mid in M.all_ids():
        vals = [c.values[mid].value for c in candidates
                if mid in c.values and c.values[mid].value is not None]
        if vals:
            bounds[mid] = (min(vals), max(vals))

    # Score every candidate.
    scored_pairs: list[tuple[Candidate, dict[str, ScoredMetric], float | None]] = []
    for c in candidates:
        scored: dict[str, ScoredMetric] = {}
        for mid in M.all_ids():
            metric = M.get(mid)
            mv = c.values.get(mid)
            raw = mv.value if mv else None
            conf = mv.confidence if mv else 0.0
            norm = None
            if raw is not None and mid in bounds:
                lo, hi = bounds[mid]
                norm = _normalize(raw, lo, hi, metric.direction)
            scored[mid] = ScoredMetric(
                metric_id=mid, raw=raw, normalized=norm, confidence=conf,
                kind=metric.kind.value, domain=metric.domain,
                direction=metric.direction.value,
            )
        scored_pairs.append((c, scored, _quality_score(scored, weights, min_confidence)))

    baseline = next((p for p in scored_pairs
                     if p[0].school.nces_id == baseline_nces_id), None)
    if baseline_nces_id and baseline is None:
        notes.append(
            f"baseline {baseline_nces_id} not in candidate set; meets-or-beats skipped"
        )

    results: list[CandidateScore] = []
    for c, scored, quality in scored_pairs:
        is_base = baseline is not None and c.school.nces_id == baseline_nces_id
        mob: bool | None = None
        comp: dict | None = None
        if baseline is not None and not is_base:
            mob, comp = _meets_or_beats(scored, baseline[1], min_confidence, min_shared)
        results.append(CandidateScore(
            nces_id=c.school.nces_id, name=c.school.name,
            school_type=c.school.school_type, is_baseline=is_base,
            metrics=scored, quality_score=quality, meets_or_beats=mob,
            comparison=comp, data_completeness=c.school.data_completeness,
            lat=c.school.lat, lon=c.school.lon,
        ))

    return ScoreCard(
        baseline_nces_id=baseline_nces_id, candidates=results,
        weights=weights, min_confidence=min_confidence, notes=notes,
    )


def meets_or_beats_names(card: ScoreCard) -> list[str]:
    """Names of candidates that meet or beat the baseline (verdict True only)."""
    return [c.name for c in card.candidates if c.meets_or_beats is True]
