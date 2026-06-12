"""Entity-resolution matcher: normalized names + proximity."""
from school_lens.ingest._crosswalk import match_by_name_geo, match_catchment, normalize_name
from school_lens.ingest.boundaries import _cell_school_names


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


# --- match_catchment: the level-aware ladder --------------------------------

# Rural district: every school normalizes to "banks", all far from the huge
# catchment's centroid. Only the grade band can disambiguate.
_BANKS = [
    ("E", "Banks Elementary School", "elementary", (90000.0, 0.0)),
    ("M", "Banks Middle School", "middle", (90000.0, 0.0)),
    ("H", "Banks High School", "high", (90000.0, 0.0)),
]


def test_level_disambiguates_same_normalized_name():
    assert match_catchment("Banks High School", "high", (0.0, 0.0), _BANKS) == ("H", 0.7)
    assert match_catchment("Banks Elementary School", "elementary", (0.0, 0.0), _BANKS) == ("E", 0.7)


def test_subset_resolves_renamed_school():
    cands = [("W", "Ida B. Wells-Barnett High School", "high", (100.0, 0.0)),
             ("L", "Lincoln High School", "high", (90000.0, 0.0))]
    # geo-confirmed subset ("ida b wells" is contained in the spine name)
    assert match_catchment("Ida B. Wells", "high", (0.0, 0.0), cands) == ("W", 0.85)
    # unique subset, centroid out of range, still resolves at lower confidence
    assert match_catchment("McDaniel", "high", (0.0, 0.0),
                           [("M", "Leodis V. McDaniel High School", "high", (90000.0, 0.0))]) == ("M", 0.75)


def test_subset_ambiguity_without_geo_returns_none():
    cands = [("A", "Sunnyside Environmental School", "elementary", (90000.0, 0.0)),
             ("B", "Sunnyside Mennonite Montessori School", "elementary", (95000.0, 0.0))]
    assert match_catchment("Sunnyside", "elementary", (0.0, 0.0), cands) is None


def test_k8_catchment_resolves_cross_level_on_exact_name():
    # A K-8 school sits in the spine at level "elementary" but the COP layer
    # names it on middle-grade cells too (step 3 of the ladder).
    cands = [("G", "Gaston Elementary School", "elementary", (90000.0, 0.0))]
    assert match_catchment("Gaston Elementary School", "middle", (0.0, 0.0), cands) == ("G", 0.7)


def test_cross_level_subset_requires_geo_confirmation():
    cands = [("B", "Bridger Creative Science School", "elementary", (90000.0, 0.0))]
    # subset + wrong level + centroid out of range: not enough evidence
    assert match_catchment("Bridger", "middle", (0.0, 0.0), cands) is None
    # same subset with the centroid nearby: resolves
    assert match_catchment("Bridger", "middle", (100.0, 0.0),
                           [("B", "Bridger Creative Science School", "elementary", (0.0, 0.0))]) == ("B", 0.85)


# --- COP cell-name parsing ---------------------------------------------------

def test_cell_dual_assignment_splits_on_spaced_slash():
    assert _cell_school_names("Jefferson / Roosevelt") == ["Jefferson", "Roosevelt"]


def test_cell_slash_named_school_stays_whole():
    assert _cell_school_names("Boise-Eliot/Humboldt") == ["Boise-Eliot/Humboldt"]
    assert _cell_school_names("Gaston Jr / Sr High School") == ["Gaston Jr / Sr High School"]


def test_cell_empty_values():
    assert _cell_school_names(None) == []
    assert _cell_school_names("  ") == []
