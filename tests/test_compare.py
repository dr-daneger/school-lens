"""The comparison engine encodes the two epistemic constraints; test the behavior."""
from school_lens.compare import Candidate, MetricValue, build_scorecard
from school_lens.domain.models import School, SchoolType


def _school(nid, name, stype=SchoolType.traditional_public):
    return School(nces_id=nid, name=name, school_type=stype)


def _by_id(card):
    return {c.nces_id: c for c in card.candidates}


def test_normalization_respects_direction():
    a = Candidate(school=_school("A", "A"), values={
        "math_growth": MetricValue(value=80), "suspension_rate": MetricValue(value=2)})
    b = Candidate(school=_school("B", "B"), values={
        "math_growth": MetricValue(value=40), "suspension_rate": MetricValue(value=10)})
    s = _by_id(build_scorecard([a, b]))
    # higher_is_better: A's higher growth normalizes to 1.0
    assert s["A"].metrics["math_growth"].normalized == 1.0
    assert s["B"].metrics["math_growth"].normalized == 0.0
    # lower_is_better: A's lower suspension rate is the better end -> 1.0
    assert s["A"].metrics["suspension_rate"].normalized == 1.0
    assert s["B"].metrics["suspension_rate"].normalized == 0.0


def test_missing_data_is_none_not_zero():
    a = Candidate(school=_school("A", "A"), values={"math_growth": MetricValue(value=80)})
    b = Candidate(school=_school("B", "B"), values={})
    s = _by_id(build_scorecard([a, b]))
    assert s["B"].metrics["math_growth"].normalized is None
    assert s["B"].quality_score is None  # no school_effect data -> no score, not 0


def test_quality_score_uses_only_school_effect_by_default():
    # B has far better proficiency (context) but worse growth; default score must
    # follow growth, never launder the demographic-heavy level.
    a = Candidate(school=_school("A", "A"), values={
        "math_growth": MetricValue(value=80), "math_proficiency": MetricValue(value=10)})
    b = Candidate(school=_school("B", "B"), values={
        "math_growth": MetricValue(value=40), "math_proficiency": MetricValue(value=90)})
    s = _by_id(build_scorecard([a, b]))
    assert s["A"].quality_score == 1.0
    assert s["B"].quality_score == 0.0


def test_meets_or_beats_and_insufficient_private_data():
    base = Candidate(school=_school("P", "Public"), values={
        "math_growth": MetricValue(value=50), "ela_growth": MetricValue(value=50)})
    better = Candidate(school=_school("C", "Charter", SchoolType.charter), values={
        "math_growth": MetricValue(value=60), "ela_growth": MetricValue(value=70)})
    worse = Candidate(school=_school("W", "WeakCharter", SchoolType.charter), values={
        "math_growth": MetricValue(value=40), "ela_growth": MetricValue(value=70)})
    private_nodata = Candidate(
        school=_school("Pr", "Private", SchoolType.private_independent), values={})
    s = _by_id(build_scorecard(
        [base, better, worse, private_nodata], baseline_nces_id="P"))
    assert s["C"].meets_or_beats is True    # >= on both shared school_effect metrics
    assert s["W"].meets_or_beats is False   # worse on math growth
    assert s["Pr"].meets_or_beats is None   # not comparable -> never failed for blanks
    assert s["P"].is_baseline is True


def test_private_proxy_never_pollutes_or_fails_the_public_comparison():
    # A private school known only by its proxies must not earn a default quality
    # score (proxies carry zero default weight) and must not be failed against a
    # public baseline (no shared school_effect metric -> verdict None). (3.2)
    base = Candidate(school=_school("P", "Public"), values={
        "math_growth": MetricValue(value=50), "ela_growth": MetricValue(value=50)})
    private = Candidate(
        school=_school("Pr", "Private", SchoolType.private_independent),
        values={"college_matriculation": MetricValue(value=95, confidence=0.5),
                "accreditation": MetricValue(value=1, confidence=0.5)})
    s = _by_id(build_scorecard([base, private], baseline_nces_id="P"))
    assert s["Pr"].quality_score is None     # proxies have zero default weight
    assert s["Pr"].meets_or_beats is None    # not comparable, never failed
    assert s["Pr"].metrics["college_matriculation"].raw == 95  # still surfaced for its panel
