"""The metric registry is the contract behind the scorecard; guard its integrity."""
from school_lens.domain import metrics as M
from school_lens.domain.metrics import MetricKind


def test_metric_ids_unique():
    ids = M.all_ids()
    assert len(ids) == len(set(ids))


def test_kind_partition_is_complete_and_disjoint():
    se, cx, pp = (set(M.school_effect_ids()), set(M.context_ids()),
                  set(M.private_proxy_ids()))
    assert se.isdisjoint(cx) and se.isdisjoint(pp) and cx.isdisjoint(pp)
    assert se | cx | pp == set(M.all_ids())


def test_every_metric_has_valid_fields():
    valid = (MetricKind.school_effect, MetricKind.context, MetricKind.private_proxy)
    for mid in M.all_ids():
        m = M.get(mid)
        assert m.kind in valid
        assert m.label and m.domain and m.unit and m.source


def test_default_weights_zero_out_context_and_private_proxy():
    w = M.default_weights()
    assert all(w[mid] == 1.0 for mid in M.school_effect_ids())
    assert all(w[mid] == 0.0 for mid in M.context_ids())
    assert all(w[mid] == 0.0 for mid in M.private_proxy_ids())


def test_growth_is_school_effect_levels_and_outcomes_are_context():
    # The core honesty rule (CLAUDE.md 3.1): growth measures the school; levels
    # and neighborhood adult outcomes do not.
    assert M.get("math_growth").kind == MetricKind.school_effect
    assert M.get("math_proficiency").kind == MetricKind.context
    assert M.get("adult_income_rank").kind == MetricKind.context


def test_private_proxies_are_the_best_cited_signals_and_excluded_from_public():
    # CLAUDE.md 3.2: private quality is assessed from low-confidence proxies kept
    # out of the school_effect comparison. The quality signals are college
    # matriculation (the best-cited proxy) plus accreditation (a quality floor);
    # the registry may also carry private attributes (tuition, acceptance rate)
    # that are descriptive, not quality. All of them stay out of school_effect
    # and out of the default quality score.
    pp = set(M.private_proxy_ids())
    assert {"college_matriculation", "accreditation"} <= pp
    assert pp.isdisjoint(M.school_effect_ids())
    weights = M.default_weights()
    assert all(weights[mid] == 0.0 for mid in pp)
    assert M.get("college_matriculation").kind == MetricKind.private_proxy
    assert 0.0 < M.PRIVATE_PROXY_CONFIDENCE < 1.0
