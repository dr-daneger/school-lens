"""Entity-resolution matcher: normalized names + proximity."""
from school_lens.ingest._crosswalk import match_by_name_geo, normalize_name


def test_normalize_strips_generic_tokens():
    assert normalize_name("Cedar Mill Elementary School") == "cedar mill"
    assert normalize_name("The Catlin Gabel School") == "catlin gabel"
    assert normalize_name("Sunset High School") == "sunset"


def test_exact_name_within_range_high_confidence():
    cands = [("A", "Cedar Mill Elementary", (0.0, 0.0)),
             ("B", "Bonny Slope Elementary", (10000.0, 0.0))]
    match = match_by_name_geo("Cedar Mill Elementary School", (100.0, 0.0), cands)
    assert match == ("A", 0.9)


def test_unique_exact_name_without_geometry():
    cands = [("A", "Cedar Mill Elementary", None)]
    assert match_by_name_geo("Cedar Mill Elementary", None, cands) == ("A", 0.7)


def test_geo_only_fallback_lower_confidence():
    cands = [("A", "Foo", (0.0, 0.0)), ("B", "Bar", (100000.0, 0.0))]
    assert match_by_name_geo("Unknown School", (50.0, 0.0), cands) == ("A", 0.5)


def test_no_match_when_out_of_range():
    cands = [("A", "Foo", (100000.0, 0.0))]
    assert match_by_name_geo("Unknown", (0.0, 0.0), cands) is None
