"""Verifier must flag wrong picks (never silently correct) and flag trap
tenders as recommend_no_bid, never forcing a match."""
import pytest

from app.agents import verifier
from app.db import SKU, PriceMaterial
from app.schemas import (
    MatchResult, PriceTable, RFPLineItem, SpecParam, TechItem, TechTable,
    VerifierExam, VerifierItemExam, PriceLine,
)


@pytest.fixture()
def catalog(session):
    session.add(SKU(sku_id="SKU-GOOD", name="3.5C 95 AL XLPE", category="LT Power",
                    specs={"conductor_material": "Aluminium", "cross_section_sqmm": "95",
                           "insulation_type": "XLPE"}))
    session.add(PriceMaterial(id="pm1", sku_id="SKU-GOOD", unit="m", unit_price=500.0, currency="INR"))
    session.commit()
    return session


def _item():
    return RFPLineItem(item_no="1", description="95 sqmm Al XLPE cable", quantity=100, unit="m",
                       specs=[
                           SpecParam(name="conductor_material", kind="categorical", value="Aluminium"),
                           SpecParam(name="cross_section_sqmm", kind="numeric_exact", value="95",
                                     numeric_value=95, unit="sqmm"),
                           SpecParam(name="insulation_type", kind="categorical", value="XLPE"),
                       ])


def _price(amount=50000.0, priced=True):
    return PriceTable(lines=[PriceLine(item_no="1", sku_id="SKU-GOOD", description="d",
                                       quantity=100, unit="m", unit_price=500.0,
                                       currency="INR", amount=amount, priced=priced)],
                      test_lines=[], material_total=amount if priced else 0,
                      test_total=0, grand_total=amount if priced else 0)


def _mock_exam(monkeypatch, items):
    monkeypatch.setattr(verifier.llm, "extract",
                        lambda schema, system, content, **kw: VerifierExam(items=items))


def test_wrong_claimed_score_is_flagged_not_corrected(catalog, monkeypatch):
    tech = TechTable(items=[TechItem(item_no="1", description="d", quantity=100, unit="m",
                                     top3=[MatchResult(sku_id="SKU-GOOD", pct=55.0, evidence=[])],
                                     top_pick="SKU-GOOD", below_threshold=True)])
    _mock_exam(monkeypatch, [VerifierItemExam(item_no="1", agrees_with_pick=True,
                                              concerns=[], unfulfillable=False)])
    verdict = verifier.verify(catalog, [_item()], tech, _price())
    item = verdict.per_item[0]
    assert item.status == "flagged"
    assert any("score mismatch" in r for r in item.reasons)  # flagged, never silently fixed


def test_correct_pick_verifies_and_proceeds(catalog, monkeypatch):
    tech = TechTable(items=[TechItem(item_no="1", description="d", quantity=100, unit="m",
                                     top3=[MatchResult(sku_id="SKU-GOOD", pct=100.0, evidence=[])],
                                     top_pick="SKU-GOOD", below_threshold=False)])
    _mock_exam(monkeypatch, [VerifierItemExam(item_no="1", agrees_with_pick=True,
                                              concerns=[], unfulfillable=False)])
    verdict = verifier.verify(catalog, [_item()], tech, _price())
    assert verdict.per_item[0].status == "verified"
    assert verdict.overall == "proceed"


def test_trap_item_is_flagged_and_no_bid(catalog, monkeypatch):
    # impossible spec — nothing matched, and the exam judges it unfulfillable
    trap = RFPLineItem(item_no="1", description="400 kV submarine cable", quantity=10, unit="km",
                       specs=[SpecParam(name="voltage_grade", kind="numeric_exact",
                                        value="400 kV", numeric_value=400, unit="kV")])
    tech = TechTable(items=[TechItem(item_no="1", description="trap", quantity=10, unit="km",
                                     top3=[], top_pick=None, below_threshold=True)])
    price = PriceTable(lines=[PriceLine(item_no="1", sku_id="", description="trap", quantity=10,
                                        unit="km", unit_price=0, currency="INR", amount=0, priced=False)],
                       test_lines=[], material_total=0, test_total=0, grand_total=0)
    _mock_exam(monkeypatch, [VerifierItemExam(item_no="1", agrees_with_pick=False,
                                              concerns=["voltage_grade far beyond catalog"],
                                              unfulfillable=True)])
    verdict = verifier.verify(catalog, [trap], tech, price)
    assert verdict.per_item[0].status == "flagged"
    assert verdict.overall == "recommend_no_bid"


def test_pricing_arithmetic_is_asserted(catalog, monkeypatch):
    tech = TechTable(items=[TechItem(item_no="1", description="d", quantity=100, unit="m",
                                     top3=[MatchResult(sku_id="SKU-GOOD", pct=100.0, evidence=[])],
                                     top_pick="SKU-GOOD", below_threshold=False)])
    _mock_exam(monkeypatch, [VerifierItemExam(item_no="1", agrees_with_pick=True,
                                              concerns=[], unfulfillable=False)])
    bad_price = _price(amount=49999.0)  # 100 x 500 must be 50000
    verdict = verifier.verify(catalog, [_item()], tech, bad_price)
    assert any("arithmetic check failed" in r for r in verdict.per_item[0].reasons)
