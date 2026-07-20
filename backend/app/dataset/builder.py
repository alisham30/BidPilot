"""Dataset builder — tender documents → normalized RFP JSON (rfp_datasets)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import sources
from ..db import RFP, RFPDatasetRow, new_id
from ..ingestion.docparse import clean_text, extract_file
from ..schemas import RFPDataset
from ..tracking.escalations import escalate
from .registry import parse_iso_date

log = logging.getLogger("bidpilot.builder")

EXTRACT_SYSTEM = """You extract structured data from tender/RFP documents for a wires & cables OEM.
Normalize every line item's technical requirements into spec parameters using these
canonical snake_case names where applicable: voltage_grade, conductor_material,
core_count, cross_section_sqmm, insulation_type, armouring, standard, cable_type,
temp_rating. Choose `kind` carefully:
- numeric_exact for construction values that must match (cores, cross-section, voltage grade)
- numeric_min for meets-or-exceeds ratings (temperature, short-circuit rating)
- categorical for materials, insulation, armouring, standards

STRICT RULES for specs:
- Specs are PHYSICAL/TECHNICAL parameters of the product only. Commercial,
  administrative or eligibility conditions (local supplier preference, delivery
  period, EMD, inspection clauses, payment terms) go in special_conditions —
  NEVER as a spec parameter.
- Every numeric spec MUST carry its unit: numeric_value=26, unit="V" (never a
  bare number). Voltages in V or kV, sections in sqmm, temperatures in deg_c.
- cable_type is the construction family (e.g. XLPE power cable, HFFR ship cable,
  control cable, flexible) — not the end-use application. Put the application
  (e.g. "for rectifier starting on ships") in the item description instead.
- If a line item is NOT a wire/cable/conductor product at all (e.g. a rectifier,
  panel, transformer), still extract it faithfully with whatever technical specs
  it states — downstream matching will flag it as out-of-catalog.
Extract testing/acceptance requirements separately.
Documents may be wholly or partly in Hindi or other Indian languages — read them in
their language but ALWAYS output titles, descriptions and spec values in English
(translate faithfully; keep units and standard references exactly as printed).
Only extract what the documents state. Never invent items, quantities or dates."""


import re as _re


def _clean(value: str) -> str:
    """Strip structural-JSON artifacts an LLM can leak into text values."""
    return _re.sub(r'[{}\[\]"\\]+', "", value or "").strip(" ,;:|").strip()


def _sanitize(dataset: RFPDataset) -> None:
    for item in dataset.line_items:
        item.description = _clean(item.description)
        for spec in item.specs:
            spec.name = _clean(spec.name)
            spec.value = _clean(spec.value)
            spec.unit = _clean(spec.unit)


def get_dataset(session: Session, rfp_id: str) -> RFPDatasetRow | None:
    return session.scalar(
        select(RFPDatasetRow).where(RFPDatasetRow.rfp_id == rfp_id)
        .order_by(RFPDatasetRow.extracted_at.desc())
    )


def build_dataset(session: Session, rfp: RFP) -> RFPDatasetRow | None:
    """Extract the normalized dataset for one RFP. Returns None on failure (escalated)."""
    texts = []
    for path in rfp.doc_paths:
        text = extract_file(path)
        if text.strip():
            texts.append(f"### Document: {path}\n{clean_text(text)}")
    if not texts:
        escalate(session, "dataset_builder", "no readable documents attached", rfp_id=rfp.rfp_id, severity="high")
        return None

    try:
        dataset = llm.extract(RFPDataset, EXTRACT_SYSTEM, "\n\n".join(texts))
    except llm.LLMError as e:
        escalate(session, "dataset_builder", f"extraction failed: {e}", rfp_id=rfp.rfp_id, severity="high")
        return None

    _sanitize(dataset)

    if not dataset.line_items:
        escalate(session, "dataset_builder", "extraction produced zero line items — document may be unreadable",
                 rfp_id=rfp.rfp_id, severity="high")
        return None

    row = RFPDatasetRow(
        id=new_id("ds"), rfp_id=rfp.rfp_id,
        line_items=[li.model_dump() for li in dataset.line_items],
        tests=[t.model_dump() for t in dataset.tests],
        special_conditions=dataset.special_conditions,
    )
    session.add(row)

    # backfill registry metadata the scanner couldn't see
    if dataset.title and not rfp.title:
        rfp.title = dataset.title
    if dataset.issuer and not rfp.issuer:
        rfp.issuer = dataset.issuer
    if dataset.reference_no and not rfp.reference_no:
        rfp.reference_no = dataset.reference_no
    if dataset.due_date and rfp.due_date is None:
        rfp.due_date = parse_iso_date(dataset.due_date)
    rfp.status = "extracted"
    session.commit()
    log.info("dataset built for %s: %d items, %d tests", rfp.rfp_id, len(dataset.line_items), len(dataset.tests))
    return row
