"""RFP registry — dedupe and the due-within window, computed at query time."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import sources
from ..db import RFP, friendly_id


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def dedupe_key(reference_no: str, title: str, due_date: date | None, issuer: str) -> str:
    """Stable id = normalized ref no when present, else sha1(title|due|issuer)."""
    ref = re.sub(r"\s+", "", (reference_no or "")).lower()
    if ref:
        return f"ref:{ref}"[:80]
    basis = f"{(title or '').strip().lower()}|{due_date.isoformat() if due_date else ''}|{(issuer or '').strip().lower()}"
    return "sha:" + hashlib.sha1(basis.encode()).hexdigest()


def upsert_rfp(session: Session, *, title: str, issuer: str, reference_no: str,
               due_date: date | None, source: str, source_detail: str,
               doc_paths: list[str] | None = None) -> tuple[RFP, bool]:
    """Insert if unseen; returns (row, created). Re-scans never duplicate."""
    key = dedupe_key(reference_no, title, due_date, issuer)
    existing = session.scalar(select(RFP).where(RFP.dedupe_key == key))
    if existing is not None:
        if doc_paths:
            merged = list(dict.fromkeys([*existing.doc_paths, *doc_paths]))
            existing.doc_paths = merged
            session.commit()
        return existing, False
    row = RFP(
        rfp_id=friendly_id(session, "RFP", year=True), title=title, issuer=issuer, reference_no=reference_no,
        due_date=due_date, source=source, source_detail=source_detail,
        dedupe_key=key, doc_paths=doc_paths or [], status="new",
    )
    session.add(row)
    session.commit()
    return row, True


def rfps_in_window(session: Session, today: date | None = None) -> list[RFP]:
    """Due within `filters.due_within_days` (inclusive), plus unknown due dates
    (kept but flagged in the UI). Past-due excluded."""
    today = today or date.today()
    horizon = today + timedelta(days=sources.filters.due_within_days)
    stmt = select(RFP).where(
        or_(
            RFP.due_date.is_(None),
            (RFP.due_date >= today) & (RFP.due_date <= horizon),
        )
    ).order_by(RFP.due_date.nulls_last(), RFP.created_at.desc())
    return list(session.scalars(stmt))
