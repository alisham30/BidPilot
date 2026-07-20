"""Golden fixture — pins the extraction schema + deterministic scorer output.

If a prompt or schema change alters how specs are represented, this fails
loudly instead of silently regressing matching.
"""
import json
from pathlib import Path

from app.matching.scorer import spec_match
from app.schemas import RFPDataset

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "golden_rfp.json").read_text())


def test_golden_dataset_still_validates():
    dataset = RFPDataset.model_validate(FIXTURE["dataset"])
    assert len(dataset.line_items) == 1
    assert len(dataset.line_items[0].specs) == 7
    assert len(dataset.tests) == 2


def test_golden_perfect_match_pins_100():
    dataset = RFPDataset.model_validate(FIXTURE["dataset"])
    r = spec_match(dataset.line_items[0].specs, FIXTURE["catalog_sku"]["specs"])
    assert r.pct == FIXTURE["expected_pct"]
    assert all(e.score == 1.0 for e in r.evidence)


def test_golden_near_miss_partial_credit_pinned():
    dataset = RFPDataset.model_validate(FIXTURE["dataset"])
    r = spec_match(dataset.line_items[0].specs, FIXTURE["near_miss_sku_specs"])
    assert r.pct == FIXTURE["expected_near_miss_pct"]
    gap = next(e for e in r.evidence if e.param == "cross_section_sqmm")
    assert 0 < gap.score < 1  # partial credit for numeric closeness
