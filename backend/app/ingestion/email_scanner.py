"""Gmail IMAP scanner.

Cheap keyword prefilter runs before any LLM spend; Claude then classifies
against the strict EmailClassification schema. Attachments are saved and a
registry row is created for relevant tenders. Nothing is ever sent, deleted
or marked read — the scanner only PEEKs.
"""
from __future__ import annotations

import email
import email.header
import imaplib
import logging
import threading
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from .. import llm
from ..config import ATTACHMENTS_DIR, settings, sources
from ..db import SeenEmail
from ..dataset.registry import parse_iso_date, upsert_rfp
from ..schemas import EmailClassification
from ..tracking.escalations import escalate
from .docparse import clean_text, extract_bytes

log = logging.getLogger("bidpilot.email")

CLASSIFY_SYSTEM = """You classify emails for a wires & cables OEM's tender desk.
Decide whether the email is a tender/RFP/bid opportunity, and whether it concerns
these product categories: {categories}.
Extract the tender title, issuer, reference number and due date only from what is
explicitly stated in the email text. Content may be in Hindi or other Indian
languages — output the title in English. Never invent values; leave them
empty/null when not stated. Dates must be ISO YYYY-MM-DD."""


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out = ""
    for text, enc in parts:
        out += text.decode(enc or "utf-8", errors="replace") if isinstance(text, bytes) else text
    return out


def _body_text(msg: email.message.Message) -> str:
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    if not parts:
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    import re
                    parts.append(re.sub(r"<[^>]+>", " ", payload.decode(part.get_content_charset() or "utf-8", errors="replace")))
    return "\n".join(parts)


def _keyword_hit(text: str) -> bool:
    lower = text.lower()
    return any(k.lower() in lower for k in sources.email.keywords)


def _attachment_preview(msg: email.message.Message, body_len: int) -> str:
    """Attachment names always; extracted attachment text when the body is thin.
    Tender mails are often a bare 'PFA' with the whole tender in a PDF."""
    names, texts = [], []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode(filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in sources.email.attachment_types:
            continue
        names.append(filename)
        if body_len < 500 and len(texts) < 2:
            payload = part.get_payload(decode=True)
            if payload:
                text = clean_text(extract_bytes(payload, suffix), cap_chars=8000)
                if text.strip():
                    texts.append(f"--- attachment {filename} (excerpt) ---\n{text}")
    out = ""
    if names:
        out += "\nAttachments: " + ", ".join(names)
    if texts:
        out += "\n\n" + "\n\n".join(texts)
    return out


def _save_attachments(msg: email.message.Message, rfp_dir: Path) -> list[str]:
    saved: list[str] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode(filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in sources.email.attachment_types:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        rfp_dir.mkdir(parents=True, exist_ok=True)
        target = rfp_dir / Path(filename).name
        target.write_bytes(payload)
        saved.append(str(target))
    return saved


_scan_lock = threading.Lock()  # scheduled + manual scans must never run concurrently
                               # (parallel IMAP sessions make Gmail drop the socket)


def scan_inbox(session: Session) -> dict:
    """One poll: peek newest N messages, prefilter, classify, ingest. Returns stats."""
    if not _scan_lock.acquire(blocking=False):
        return {"status": "a scan is already running — try again in a minute"}
    try:
        return _scan_inbox_locked(session)
    finally:
        _scan_lock.release()


def _scan_inbox_locked(session: Session) -> dict:
    cfg = sources.email
    stats = {"checked": 0, "prefiltered": 0, "classified": 0, "ingested": 0, "skipped_seen": 0}

    if not settings.gmail_user or not settings.gmail_app_password:
        escalate(session, "sales_agent", "Gmail credentials not configured (.env GMAIL_USER / GMAIL_APP_PASSWORD)", severity="high")
        return stats

    try:
        imap = imaplib.IMAP4_SSL(cfg.imap_host)
        imap.login(settings.gmail_user, settings.gmail_app_password)
    except Exception as e:
        escalate(session, "sales_agent", f"IMAP login failed: {e}", severity="high")
        return stats

    try:
        for folder in cfg.folders:
            imap.select(folder, readonly=True)
            ok, data = imap.uid("search", None, "ALL")
            if ok != "OK":
                continue
            uids = data[0].split()
            for uid in reversed(uids[-cfg.peek_newest:]):
                if stats["ingested"] >= cfg.max_ingests_per_poll:
                    break
                uid_s = uid.decode()
                ok, msg_data = imap.uid("fetch", uid_s, "(BODY.PEEK[])")
                if ok != "OK" or not msg_data or msg_data[0] is None:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                message_id = msg.get("Message-ID", f"uid:{folder}:{uid_s}").strip()
                if session.get(SeenEmail, message_id):
                    stats["skipped_seen"] += 1
                    continue
                stats["checked"] += 1
                subject = _decode(msg.get("Subject"))
                sender = _decode(msg.get("From"))
                body = _body_text(msg)

                seen = SeenEmail(message_id=message_id, ingested=False)
                session.add(seen)
                session.commit()

                preview = _attachment_preview(msg, len(body.strip()))
                if not _keyword_hit(subject + "\n" + body[:4000] + "\n" + preview):
                    continue
                stats["prefiltered"] += 1

                try:
                    cls = llm.extract(
                        EmailClassification,
                        CLASSIFY_SYSTEM.format(categories=", ".join(sources.filters.product_categories)),
                        f"From: {sender}\nSubject: {subject}\n\n{clean_text(body)}{preview}",
                        max_tokens=1000,
                    )
                except llm.LLMError as e:
                    escalate(session, "sales_agent", f"classification failed for email '{subject}': {e}")
                    continue
                stats["classified"] += 1

                if not (cls.is_tender and cls.relevant_to_categories):
                    continue
                if cls.confidence < sources.tracking.confidence_threshold:
                    escalate(session, "sales_agent",
                             f"low-confidence tender classification ({cls.confidence:.2f}) for '{subject}' — review manually")
                    continue

                due = parse_iso_date(cls.due_date)
                rfp, created = upsert_rfp(
                    session,
                    title=cls.title or subject, issuer=cls.issuer, reference_no=cls.reference_no,
                    due_date=due, source="email", source_detail=sender,
                )
                docs = _save_attachments(msg, ATTACHMENTS_DIR / rfp.rfp_id)
                body_path = ATTACHMENTS_DIR / rfp.rfp_id / "email_body.txt"
                body_path.parent.mkdir(parents=True, exist_ok=True)
                body_path.write_text(f"Subject: {subject}\nFrom: {sender}\n\n{body}", encoding="utf-8")
                rfp.doc_paths = list(dict.fromkeys([*rfp.doc_paths, *docs, str(body_path)]))
                seen.ingested = True
                session.commit()
                if created:
                    stats["ingested"] += 1

                if due is not None and due < date.today():
                    escalate(session, "sales_agent",
                             f"tender '{rfp.title}' has an expired due date {due.isoformat()} — blocked from pipeline",
                             rfp_id=rfp.rfp_id, severity="low")
                    rfp.status = "closed"
                    session.commit()
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as e:
        # Gmail dropped the connection mid-scan — report partial results, never a 500
        escalate(session, "sales_agent",
                 f"IMAP connection dropped mid-scan ({e}) — partial results {stats}; will retry on next poll",
                 severity="low")
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    log.info("inbox scan: %s", stats)
    return stats
