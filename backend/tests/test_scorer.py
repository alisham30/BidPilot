"""Offline scorer suite — no API key, no network, no database."""
from app.matching.scorer import spec_match
from app.schemas import SpecParam


def p(name, kind, value, numeric=None, unit=""):
    return SpecParam(name=name, kind=kind, value=value, numeric_value=numeric, unit=unit)


def test_numeric_exact_unit_normalization_kv_vs_v():
    req = [p("voltage_grade", "numeric_exact", "11 kV", 11, "kV")]
    r = spec_match(req, {"voltage_grade": "11000V"})
    assert r.pct == 100.0
    assert r.evidence[0].score == 1.0


def test_numeric_exact_partial_credit():
    req = [p("cross_section_sqmm", "numeric_exact", "95", 95, "sqmm")]
    r = spec_match(req, {"cross_section_sqmm": "70"})
    expected = round(100 * (1 - abs(70 - 95) / 95), 1)
    assert r.pct == expected


def test_numeric_min_meets_or_exceeds():
    req = [p("temp_rating", "numeric_min", "90", 90, "deg_c")]
    assert spec_match(req, {"temp_rating": "90"}).pct == 100.0
    assert spec_match(req, {"temp_rating": "105"}).pct == 100.0
    assert spec_match(req, {"temp_rating": "70"}).pct == round(100 * 70 / 90, 1)


def test_categorical_equivalence_aluminium_al():
    req = [p("conductor_material", "categorical", "Aluminium")]
    assert spec_match(req, {"conductor_material": "Al"}).pct == 100.0
    assert spec_match(req, {"conductor_material": "aluminum"}).pct == 100.0
    assert spec_match(req, {"conductor_material": "Copper"}).pct == 0.0


def test_categorical_no_fuzzy_matching():
    # "submarine" must never equal "submersible"-style near-misses
    req = [p("cable_type", "categorical", "submarine")]
    assert spec_match(req, {"cable_type": "submersible"}).pct == 0.0


def test_missing_spec_is_zero_never_assumed():
    req = [p("armouring", "categorical", "Armoured")]
    r = spec_match(req, {})
    assert r.pct == 0.0
    assert r.evidence[0].actual is None


def test_equal_weightage_and_threshold_edge_at_80():
    req = [
        p("a", "categorical", "x"),
        p("b", "categorical", "x"),
        p("c", "categorical", "x"),
        p("d", "categorical", "x"),
        p("e", "categorical", "x"),
    ]
    sku = {"a": "x", "b": "x", "c": "x", "d": "x", "e": "y"}
    r = spec_match(req, sku)
    assert r.pct == 80.0  # exactly at the default MTO threshold


def test_incomparable_units_fail_closed():
    req = [p("voltage_grade", "numeric_exact", "11 kV", 11, "kV")]
    assert spec_match(req, {"voltage_grade": "11 sqmm"}).pct == 0.0


def test_empty_specs_scores_zero():
    assert spec_match([], {"anything": "1"}).pct == 0.0


def test_dual_voltage_designation_450_750():
    req = [p("voltage_grade", "numeric_exact", "450/750", 450, "")]
    assert spec_match(req, {"voltage_grade": "450/750 V"}).pct == 100.0
    # compares on the upper (line) voltage: 750 V vs 1.1 kV is partial, not zero
    partial = spec_match(req, {"voltage_grade": "1.1 kV"}).pct
    assert 0 < partial < 100


def test_extraction_artifact_punctuation_is_stripped():
    req = [p("conductor_material", "categorical", 'Aluminium},{')]
    assert spec_match(req, {"conductor_material": "Al"}).pct == 100.0


def test_standard_slash_and_colon_year_are_equal():
    req = [p("standard", "categorical", "IS 694/2010")]
    assert spec_match(req, {"standard": "IS 694:2010"}).pct == 100.0


def test_compound_standard_matches_any_component():
    req = [p("standard", "categorical", "IS 694/2010 and IS:8130/1984")]
    assert spec_match(req, {"standard": "IS 694:2010"}).pct == 100.0


def test_multiword_value_contained_in_requirement_phrase():
    req = [p("cable_type", "categorical", "flat cable with PVC insulation and sheathed, ISI marked")]
    assert spec_match(req, {"cable_type": "Flat Cable"}).pct == 100.0


def test_single_word_never_containment_matches():
    # 'submarine cable' must not match a bare 'cable' product, and
    # submarine/submersible remain distinct
    req = [p("cable_type", "categorical", "submarine cable")]
    assert spec_match(req, {"cable_type": "cable"}).pct == 0.0
    assert spec_match(req, {"cable_type": "Submersible Cable"}).pct == 0.0


def test_armouring_yes_no_wording():
    req = [p("armouring", "categorical", "No")]
    assert spec_match(req, {"armouring": "Unarmoured"}).pct == 100.0
    req2 = [p("armouring", "categorical", "Steel")]
    assert spec_match(req2, {"armouring": "Armoured"}).pct == 100.0
