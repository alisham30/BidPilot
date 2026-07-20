"""Technical Agent — per line item: shortlist → deterministic spec_match →
rank → top-3 with per-parameter comparison. Every percentage is reproducible.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import sources
from ..db import SKU
from ..matching.scorer import spec_match
from ..schemas import MatchResult, RFPLineItem, SpecParam, TechItem, TechTable

log = logging.getLogger("bidpilot.technical")


def _shortlist(session: Session, item: RFPLineItem) -> list[SKU]:
    """pgvector shortlist when embeddings exist; falls back to the full catalog."""
    size = sources.matching.shortlist_size
    has_embeddings = session.scalar(select(SKU.sku_id).where(SKU.embedding.is_not(None)).limit(1))
    if has_embeddings:
        try:
            query_text = item.description + " " + " ".join(f"{s.name}={s.value}" for s in item.specs)
            vec = llm.embed([query_text])[0]
            stmt = select(SKU).where(SKU.embedding.is_not(None)).order_by(
                SKU.embedding.cosine_distance(vec)).limit(size)
            return list(session.scalars(stmt))
        except Exception as e:
            log.warning("vector shortlist failed (%s) — scanning full catalog", e)
    return list(session.scalars(select(SKU)))


def _score_candidates(specs: list[SpecParam], candidates: list[SKU]) -> list[MatchResult]:
    results = []
    for sku in candidates:
        r = spec_match(specs, sku.specs)
        r.sku_id = sku.sku_id
        results.append(r)
    results.sort(key=lambda r: r.pct, reverse=True)
    return results


def match_items(session: Session, line_items: list[RFPLineItem]) -> TechTable:
    threshold = sources.matching.mto_threshold
    top_k = sources.matching.top_k
    items: list[TechItem] = []

    for item in line_items:
        candidates = _shortlist(session, item)
        ranked = _score_candidates(item.specs, candidates)

        # deterministic guard: if the shortlist can't clear the threshold,
        # re-score the FULL catalog before declaring a gap
        if (not ranked or ranked[0].pct < threshold) and len(candidates) < _catalog_size(session):
            ranked = _score_candidates(item.specs, list(session.scalars(select(SKU))))

        top3 = ranked[:top_k]
        best = top3[0] if top3 else None
        items.append(TechItem(
            item_no=item.item_no,
            description=item.description,
            quantity=item.quantity,
            unit=item.unit,
            top3=top3,
            top_pick=best.sku_id if best and best.pct > 0 else None,
            below_threshold=(best is None or best.pct < threshold),
        ))
    return TechTable(items=items)


def _catalog_size(session: Session) -> int:
    from sqlalchemy import func
    return session.scalar(select(func.count()).select_from(SKU)) or 0
