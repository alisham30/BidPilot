"""Fail loudly, escalate cleanly — any agent exception or low-confidence result
creates an escalation row and surfaces in the dashboard. Silent failure or
silent fallback to fake data is forbidden.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..db import Escalation, friendly_id

log = logging.getLogger("bidpilot.escalations")


def escalate(session: Session, source_agent: str, reason: str,
             rfp_id: str | None = None, severity: str = "medium") -> Escalation:
    row = Escalation(
        id=friendly_id(session, "ALERT"), rfp_id=rfp_id, source_agent=source_agent,
        reason=reason[:4000], severity=severity,
    )
    session.add(row)
    session.commit()
    log.warning("ESCALATION [%s] %s: %s", severity, source_agent, reason)
    return row
