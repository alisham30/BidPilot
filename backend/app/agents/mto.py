"""MTO Gap Agent — drafts made-to-order engineering requests for sub-threshold
items. Stored and surfaced in the dashboard; a human raises it internally."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .. import llm
from ..db import SKU
from ..schemas import MTODraft, MTORequest, TechItem
from ..tracking.escalations import escalate

log = logging.getLogger("bidpilot.mto")

MTO_SYSTEM = """You draft an internal made-to-order (MTO) engineering request for a wires
& cables OEM. You are given a tender line item, the closest base SKU, and the exact
per-parameter gaps (required vs actual, computed deterministically). Write a concise,
factual request: which parameters fall short, by how much, and which base SKU to start
from. Do not invent numbers — use only the gap data provided."""


def draft_mto_requests(session: Session, rfp_id: str, items: list[TechItem]) -> list[MTORequest]:
    requests: list[MTORequest] = []
    for item in items:
        if not item.below_threshold:
            continue
        best = item.top3[0] if item.top3 else None
        gaps = [e for e in (best.evidence if best else []) if e.score < 1.0]
        closest = best.sku_id if best else ""
        sku = session.get(SKU, closest) if closest else None
        gap_text = "\n".join(
            f"- {g.param}: required {g.required}, closest SKU has {g.actual or 'nothing'} (score {g.score})"
            for g in gaps) or "- no catalog SKU resembles this item at all"
        try:
            draft = llm.extract(
                MTODraft, MTO_SYSTEM,
                f"Tender item {item.item_no}: {item.description}\n"
                f"Quantity: {item.quantity} {item.unit}\n"
                f"Closest base SKU: {closest or 'none'} ({sku.name if sku else 'n/a'})\n"
                f"Best deterministic match: {best.pct if best else 0}%\n"
                f"Parameter gaps:\n{gap_text}",
                max_tokens=2000,
            )
            subject, body = draft.subject, draft.body
        except llm.LLMError as e:
            escalate(session, "mto_agent", f"MTO draft failed for item {item.item_no}: {e}", rfp_id=rfp_id)
            subject = f"MTO request needed: tender item {item.item_no}"
            body = f"Automatic drafting failed. Deterministic gap data:\n{gap_text}"
        requests.append(MTORequest(
            item_no=item.item_no, closest_sku=closest, gaps=gaps,
            draft_subject=subject, draft_body=body,
        ))
    return requests
