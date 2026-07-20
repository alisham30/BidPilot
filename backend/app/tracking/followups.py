"""Follow-up drafts — the tracker NEVER auto-sends. Sending requires an
explicit human decision through POST /followups/{id}/send, which records a
decisions row before anything leaves the system."""
from __future__ import annotations

import email.message
import logging
import re
import smtplib

from sqlalchemy.orm import Session

from .. import llm
from ..config import settings, sources
from ..db import RFP, Followup, friendly_id
from ..schemas import FollowupDraft
from .escalations import escalate

log = logging.getLogger("bidpilot.followups")

DRAFT_SYSTEM = """You draft a short, professional follow-up email from a wires & cables
OEM's bid desk to a tender issuer. Be factual and courteous; reference the tender
title and reference number provided. Do not invent commitments, prices or dates."""


def draft_followup(session: Session, rfp: RFP, reason: str) -> Followup:
    try:
        draft = llm.extract(
            FollowupDraft, DRAFT_SYSTEM,
            f"Tender: {rfp.title}\nReference: {rfp.reference_no or 'n/a'}\n"
            f"Issuer: {rfp.issuer or 'n/a'}\nDue date: {rfp.due_date or 'n/a'}\n"
            f"Reason for follow-up: {reason}",
            max_tokens=1500,
        )
        subject, body = draft.subject, draft.body
    except llm.LLMError as e:
        escalate(session, "bid_tracker", f"follow-up drafting failed: {e}", rfp_id=rfp.rfp_id)
        subject = f"Follow-up: {rfp.title} ({rfp.reference_no})"
        body = f"[drafting failed — write manually]\nReason: {reason}"
    row = Followup(id=friendly_id(session, "FU"), rfp_id=rfp.rfp_id, subject=subject, body=body, reason=reason)
    session.add(row)
    session.commit()
    return row


def _extract_address(source_detail: str) -> str | None:
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", source_detail or "")
    return m.group(0) if m else None


def send_followup(session: Session, followup: Followup, rfp: RFP) -> bool:
    """Actually send — only ever called after a recorded human decision."""
    to_addr = _extract_address(rfp.source_detail)
    if not to_addr:
        escalate(session, "bid_tracker", "cannot send follow-up: no recipient address on record",
                 rfp_id=rfp.rfp_id, severity="high")
        return False
    if not settings.gmail_user or not settings.gmail_app_password:
        escalate(session, "bid_tracker", "cannot send follow-up: Gmail credentials not configured",
                 rfp_id=rfp.rfp_id, severity="high")
        return False
    msg = email.message.EmailMessage()
    msg["From"] = settings.gmail_user
    msg["To"] = to_addr
    msg["Subject"] = followup.subject
    msg.set_content(followup.body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(settings.gmail_user, settings.gmail_app_password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        escalate(session, "bid_tracker", f"follow-up send failed: {e}", rfp_id=rfp.rfp_id, severity="high")
        return False
