"""Verifier Agent — independently re-derives every match and asserts pricing rules.

It sees the Technical Agent's CONCLUSIONS only (never its reasoning):
1. re-runs the deterministic scorer against the tender spec + SKU datasheet record
   and confirms every percentage;
2. asserts pricing: every line priced from a real table row, qty x rate arithmetic,
   no missing test costs;
3. fresh Claude cross-exam per item comparing tender spec vs SKU spec.

Unfulfillable items are flagged as gaps, never forced into a match. Output is a
recommendation with evidence — it has NO authority to act and NO side effects.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .. import llm
from ..config import sources
from ..db import SKU
from ..matching.scorer import spec_match
from ..agents.pricing import latest_material_price, _qty_in_price_unit
from ..schemas import (
    PriceTable, RFPLineItem, TechTable, Verdict, VerdictItem, VerifierExam,
)

log = logging.getLogger("bidpilot.verifier")

EXAM_SYSTEM = """You are an independent bid verifier for a wires & cables OEM.
For each tender line item you are given the tender's required specifications and
the datasheet record of the SKU proposed for it. You did NOT see how the proposal
was made — judge only from the two spec sets.
- agrees_with_pick: true only if the SKU plausibly satisfies the requirement.
- concerns: cite specific parameter names for any mismatch, ambiguity, or missing data.
- unfulfillable: true if the requirement looks impossible for ANY cable of this
  catalog's kind (e.g. voltage class far beyond the catalog, contradictory specs,
  or a product family the catalog does not contain). Trap tenders with impossible
  specs must be flagged, not matched."""


def verify(session: Session, line_items: list[RFPLineItem],
           tech: TechTable, price: PriceTable) -> Verdict:
    threshold = sources.matching.mto_threshold
    per_item: list[VerdictItem] = []
    evidence: list[str] = []
    items_by_no = {i.item_no: i for i in line_items}

    # ------- deterministic re-derivation (scores + pricing) -------
    flags: dict[str, list[str]] = {t.item_no: [] for t in tech.items}
    for t in tech.items:
        item = items_by_no.get(t.item_no)
        if item is None:
            flags[t.item_no].append("no matching tender line item for this row")
            continue
        if t.top_pick:
            sku = session.get(SKU, t.top_pick)
            if sku is None:
                flags[t.item_no].append(f"picked SKU {t.top_pick} not found in catalog")
            else:
                recomputed = spec_match(item.specs, sku.specs)
                claimed = t.top3[0].pct if t.top3 else None
                if claimed is None or abs(recomputed.pct - claimed) > 0.05:
                    flags[t.item_no].append(
                        f"score mismatch: technical agent claimed {claimed}%, verifier recomputed {recomputed.pct}%")
                else:
                    evidence.append(f"{t.item_no}: spec match {recomputed.pct}% independently reproduced")
                if recomputed.pct < threshold:
                    flags[t.item_no].append(
                        f"best match {recomputed.pct}% is below the {threshold}% threshold — gap, not a match")
        else:
            flags[t.item_no].append("no SKU could be matched — unfulfilled item")

    price_by_item = {l.item_no: l for l in price.lines}
    for t in tech.items:
        line = price_by_item.get(t.item_no)
        if line is None:
            flags[t.item_no].append("item missing from price table")
            continue
        if not line.priced:
            flags[t.item_no].append("no price-table entry — cost missing (escalated, never guessed)")
            continue
        row = latest_material_price(session, line.sku_id)
        if row is None:
            flags[t.item_no].append(f"price for {line.sku_id} not backed by a price_materials row")
            continue
        qty = _qty_in_price_unit(line.quantity, line.unit, row.unit)
        if qty is None or abs(round(qty * row.unit_price, 2) - line.amount) > 0.01:
            flags[t.item_no].append(
                f"arithmetic check failed: {line.quantity} {line.unit} x {row.unit_price}/{row.unit} != {line.amount}")
        else:
            evidence.append(f"{t.item_no}: price {line.amount} = {line.quantity} {line.unit} x {row.unit_price}/{row.unit} verified")

    unpriced_tests = [t.test_name for t in price.test_lines if not t.priced]
    if unpriced_tests:
        evidence.append("missing test costs: " + ", ".join(unpriced_tests))

    # ------- independent LLM cross-exam (conclusions only) -------
    unfulfillable: set[str] = set()
    exam_input_parts = []
    for t in tech.items:
        item = items_by_no.get(t.item_no)
        sku = session.get(SKU, t.top_pick) if t.top_pick else None
        req = "\n".join(f"  - {s.name} ({s.kind}): {s.value}" for s in (item.specs if item else []))
        got = "\n".join(f"  - {k}: {v}" for k, v in (sku.specs.items() if sku else [])) or "  (no SKU matched)"
        exam_input_parts.append(
            f"ITEM {t.item_no}: {t.description}\nTender requires:\n{req}\nProposed SKU "
            f"{t.top_pick or 'NONE'} datasheet:\n{got}")
    try:
        exam = llm.extract(VerifierExam, EXAM_SYSTEM, "\n\n".join(exam_input_parts))
        for ex in exam.items:
            if ex.item_no in flags:
                if not ex.agrees_with_pick:
                    flags[ex.item_no].append("verifier cross-exam disagrees: " + "; ".join(ex.concerns or ["unspecified concern"]))
                if ex.unfulfillable:
                    unfulfillable.add(ex.item_no)
                    flags[ex.item_no].append("verifier judges this item unfulfillable from the catalog")
        if exam.overall_comment:
            evidence.append(f"verifier comment: {exam.overall_comment}")
    except llm.LLMError as e:
        # cross-exam failure degrades to deterministic checks only — flagged, not hidden
        evidence.append(f"LLM cross-exam unavailable: {e}")
        for t in tech.items:
            flags[t.item_no].append("LLM cross-exam did not run — deterministic checks only")

    # ------- verdict (recommendation only; no side effects) -------
    for t in tech.items:
        reasons = flags.get(t.item_no, [])
        per_item.append(VerdictItem(
            item_no=t.item_no,
            status="flagged" if reasons else "verified",
            reasons=reasons,
        ))

    flagged = [v for v in per_item if v.status == "flagged"]
    hard_gaps = [v for v in per_item
                 if v.item_no in unfulfillable
                 or any("unfulfilled" in r or "below the" in r or "cost missing" in r for r in v.reasons)]
    if not flagged:
        overall = "proceed"
    elif hard_gaps and len(hard_gaps) * 2 >= max(len(per_item), 1):
        overall = "recommend_no_bid"
    elif unfulfillable:
        overall = "recommend_no_bid"
    else:
        overall = "proceed_with_deviations"

    return Verdict(per_item=per_item, overall=overall, evidence=evidence)
