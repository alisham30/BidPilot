"""Pricing Agent — all arithmetic in code, all prices from real table rows.

Test/acceptance costs are priced immediately (parallel branch); material
pricing joins when the SKU table arrives. Missing price-table entries are
escalations, not guesses.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import PriceMaterial, PriceService
from ..matching.normalize import canonical_number, norm_text
from ..schemas import PriceLine, PriceTable, RFPTest, TechTable, TestPriceLine

log = logging.getLogger("bidpilot.pricing")

_UNIT_TO_METERS = {"m": 1.0, "mtr": 1.0, "meter": 1.0, "metre": 1.0, "km": 1000.0}


def _qty_in_price_unit(quantity: float, line_unit: str, price_unit: str) -> float | None:
    """Convert the RFP quantity into the price table's unit (km→m guard)."""
    lu, pu = (norm_text(line_unit) or ""), (norm_text(price_unit) or "")
    if lu == pu:
        return quantity
    if lu in _UNIT_TO_METERS and pu in _UNIT_TO_METERS:
        return quantity * _UNIT_TO_METERS[lu] / _UNIT_TO_METERS[pu]
    return None  # incomparable units — fail closed, escalate upstream


def price_tests(session: Session, tests: list[RFPTest]) -> list[TestPriceLine]:
    table = list(session.scalars(select(PriceService)))
    lines: list[TestPriceLine] = []
    for test in tests:
        tn = norm_text(test.name) or ""
        ts = norm_text(test.standard) or ""
        row = None
        for candidate in table:
            cn = norm_text(candidate.test_name) or ""
            cs = norm_text(candidate.standard) or ""
            if cn == tn or (cn and (cn in tn or tn in cn)):
                if not ts or not cs or cs in ts or ts in cs:
                    row = candidate
                    break
        if row is None:
            lines.append(TestPriceLine(test_name=test.name, standard=test.standard,
                                       price=0.0, currency="INR", priced=False))
        else:
            lines.append(TestPriceLine(test_name=test.name, standard=test.standard,
                                       price=row.price, currency=row.currency, priced=True))
    return lines


def latest_material_price(session: Session, sku_id: str) -> PriceMaterial | None:
    return session.scalar(
        select(PriceMaterial).where(PriceMaterial.sku_id == sku_id)
        .order_by(PriceMaterial.valid_from.desc().nulls_last(), PriceMaterial.id.desc())
    )


def price_materials(session: Session, tech: TechTable) -> list[PriceLine]:
    lines: list[PriceLine] = []
    for item in tech.items:
        if not item.top_pick:
            lines.append(PriceLine(item_no=item.item_no, sku_id="", description=item.description,
                                   quantity=item.quantity, unit=item.unit, unit_price=0.0,
                                   currency="INR", amount=0.0, priced=False))
            continue
        row = latest_material_price(session, item.top_pick)
        if row is None:
            lines.append(PriceLine(item_no=item.item_no, sku_id=item.top_pick,
                                   description=item.description, quantity=item.quantity,
                                   unit=item.unit, unit_price=0.0, currency="INR",
                                   amount=0.0, priced=False))
            continue
        qty = _qty_in_price_unit(item.quantity, item.unit, row.unit)
        if qty is None:
            lines.append(PriceLine(item_no=item.item_no, sku_id=item.top_pick,
                                   description=item.description, quantity=item.quantity,
                                   unit=item.unit, unit_price=row.unit_price, currency=row.currency,
                                   amount=0.0, priced=False))
            continue
        lines.append(PriceLine(item_no=item.item_no, sku_id=item.top_pick,
                               description=item.description, quantity=item.quantity,
                               unit=item.unit, unit_price=row.unit_price, currency=row.currency,
                               amount=round(qty * row.unit_price, 2), priced=True))
    return lines


def consolidate(lines: list[PriceLine], test_lines: list[TestPriceLine]) -> PriceTable:
    material_total = round(sum(l.amount for l in lines if l.priced), 2)
    test_total = round(sum(t.price for t in test_lines if t.priced), 2)
    return PriceTable(lines=lines, test_lines=test_lines,
                      material_total=material_total, test_total=test_total,
                      grand_total=round(material_total + test_total, 2))
