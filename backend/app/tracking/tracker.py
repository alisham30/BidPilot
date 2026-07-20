"""Bid Tracker — persistent watcher for submitted bids.

Scheduled ticks check deadlines; IMAP polling classifies replies that
reference a bid; follow-ups are DRAFTED (never sent) and every anomaly —
approaching deadline, ambiguous reply, agent error, low confidence —
becomes an escalation for a human.
"""
from __future__ import annotations

import email
import imaplib
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import settings, sources
from ..db import RFP, Escalation, Followup, SessionLocal
from ..ingestion.email_scanner import _body_text, _decode
from ..ingestion.docparse import clean_text
from ..schemas import ReplyClassification
from .escalations import escalate
from .followups import draft_followup

log = logging.getLogger("bidpilot.tracker")

REPLY_SYSTEM = """You classify an email in the context of a submitted tender bid.
The bid's title and reference number are provided. Decide whether the email
references this bid and what the sender intends. Mark `ambiguous` true whenever
the intent is not clearly one of the categories."""


def _already_escalated(session: Session, rfp_id: str, marker: str) -> bool:
    rows = session.scalars(select(Escalation).where(
        Escalation.rfp_id == rfp_id, Escalation.status != "resolved")).all()
    return any(marker in r.reason for r in rows)


def _check_deadlines(session: Session, bids: list[RFP]) -> None:
    warn_days = sources.tracking.deadline_warn_days
    today = date.today()
    for rfp in bids:
        if rfp.due_date is None:
            continue
        days_left = (rfp.due_date - today).days
        marker = f"deadline {rfp.due_date.isoformat()}"
        if 0 <= days_left <= warn_days and not _already_escalated(session, rfp.rfp_id, marker):
            escalate(session, "bid_tracker",
                     f"{marker} is {days_left} day(s) away for '{rfp.title}' — review status",
                     rfp_id=rfp.rfp_id, severity="high" if days_left <= 1 else "medium")
            has_draft = session.scalar(select(Followup).where(
                Followup.rfp_id == rfp.rfp_id, Followup.status == "draft"))
            if not has_draft:
                draft_followup(session, rfp, f"Deadline {rfp.due_date.isoformat()} approaching; requesting status update")


def _poll_replies(session: Session, bids: list[RFP]) -> None:
    if not settings.gmail_user or not settings.gmail_app_password:
        return
    try:
        imap = imaplib.IMAP4_SSL(sources.email.imap_host)
        imap.login(settings.gmail_user, settings.gmail_app_password)
        imap.select("INBOX", readonly=True)
    except Exception as e:
        escalate(session, "bid_tracker", f"reply polling IMAP failure: {e}", severity="medium")
        return
    try:
        for rfp in bids:
            token = (rfp.reference_no or rfp.title or "").strip()
            if not token:
                continue
            try:
                ok, data = imap.uid("search", None, "TEXT", f'"{token[:60]}"')
            except Exception:
                continue
            if ok != "OK" or not data or not data[0]:
                continue
            for uid in data[0].split()[-3:]:  # newest few mentions
                ok, msg_data = imap.uid("fetch", uid.decode(), "(BODY.PEEK[])")
                if ok != "OK" or not msg_data or msg_data[0] is None:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                subject = _decode(msg.get("Subject"))
                marker = f"reply '{subject[:80]}'"
                if _already_escalated(session, rfp.rfp_id, marker):
                    continue
                try:
                    cls = llm.extract(
                        ReplyClassification, REPLY_SYSTEM,
                        f"Bid title: {rfp.title}\nReference: {rfp.reference_no}\n\n"
                        f"Email subject: {subject}\n\n{clean_text(_body_text(msg))[:8000]}",
                        max_tokens=1000,
                    )
                except llm.LLMError as e:
                    escalate(session, "bid_tracker", f"reply classification failed ({marker}): {e}",
                             rfp_id=rfp.rfp_id)
                    continue
                if not cls.references_bid:
                    continue
                if cls.ambiguous or cls.confidence < sources.tracking.confidence_threshold:
                    escalate(session, "bid_tracker",
                             f"ambiguous {marker}: {cls.summary} (confidence {cls.confidence:.2f})",
                             rfp_id=rfp.rfp_id, severity="medium")
                elif cls.intent in ("award", "rejection"):
                    escalate(session, "bid_tracker",
                             f"{cls.intent.upper()} — {marker}: {cls.summary}",
                             rfp_id=rfp.rfp_id, severity="high")
                elif cls.intent == "clarification_request":
                    escalate(session, "bid_tracker",
                             f"clarification requested — {marker}: {cls.summary}",
                             rfp_id=rfp.rfp_id, severity="medium")
                    draft_followup(session, rfp, f"Issuer asked for clarification: {cls.summary}")
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def tick() -> None:
    """One tracker cycle (scheduled). All failures escalate; none are silent."""
    with SessionLocal() as session:
        try:
            bids = list(session.scalars(select(RFP).where(RFP.status == "submitted")))
            if not bids:
                return
            _check_deadlines(session, bids)
            _poll_replies(session, bids)
        except Exception as e:
            log.exception("tracker tick failed")
            escalate(session, "bid_tracker", f"tracker tick crashed: {e}", severity="high")
