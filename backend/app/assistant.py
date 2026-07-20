"""BidPilot assistant — chat/voice interface over the live system.

A tool-calling loop (OpenAI) over read tools + ONE consequential tool
(record_decision). The consequential tool still writes through the same
decisions audit trail, requires the human's name, and the assistant must
obtain explicit confirmation in chat before calling it.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import String, cast, func, or_, select

from .config import settings, sources
from .db import (
    RFP, Decision, Escalation, PriceMaterial, Run, SessionLocal, SKU, new_id,
)

log = logging.getLogger("bidpilot.assistant")

SYSTEM = """You are the BidPilot assistant for a wires & cables OEM's tender desk.
You answer questions about tenders (RFPs), agent runs, catalog products, prices and
escalations using your tools — never from memory. Keep answers short, concrete and
speakable (they may be read aloud). Amounts are INR.

Decisions (approve / no_bid / mark_submitted) are consequential:
1. First tell the user exactly what would be decided (tender title, run, amount).
2. Only after the user explicitly confirms in a FOLLOW-UP message, call
   record_decision with confirm=true. Never confirm on the user's behalf.
The user's name is attached to every decision for the audit trail.

Routing: company/organization names and abbreviations (HPCL, HPL, DAE, NTPC,
railways, metro, navy...) are tender ISSUERS — use get_tender_details for them,
not search_products. search_products is for cable/wire products only. If one
tool finds nothing and the term could plausibly be the other kind, try the
other tool before answering "not found". The lookup tolerates typos and
abbreviations — pass the user's words as-is.

If a tool returns nothing useful, say so plainly. Today is {today}."""


# ------------------------------ tool implementations ------------------------------

def _rfp_brief(r: RFP) -> dict:
    return {"rfp_id": r.rfp_id, "title": r.title, "issuer": r.issuer,
            "reference_no": r.reference_no,
            "due_date": r.due_date.isoformat() if r.due_date else None,
            "status": r.status}


def _latest_run(session, rfp_id: str) -> Run | None:
    return session.scalar(select(Run).where(Run.rfp_id == rfp_id)
                          .order_by(Run.started_at.desc()))


def _run_summary(run: Run) -> dict:
    s = run.state or {}
    tech = (s.get("tech") or {}).get("items", [])
    price = s.get("price") or {}
    verdict = s.get("verdict") or {}
    return {
        "run_id": run.run_id, "run_status": run.status,
        "verdict": verdict.get("overall"),
        "grand_total_inr": price.get("grand_total"),
        "items": [{
            "item_no": t.get("item_no"), "description": t.get("description"),
            "quantity": f"{t.get('quantity')} {t.get('unit')}",
            "top_pick": t.get("top_pick"),
            "match_pct": (t.get("top3") or [{}])[0].get("pct"),
            "below_threshold": t.get("below_threshold"),
            "gaps": [f"{e['param']}: needs {e['required']}, offered {e['actual']}"
                     for e in (t.get("top3") or [{}])[0].get("evidence", []) if e.get("score", 1) < 1],
        } for t in tech],
    }


def tool_list_tenders() -> list[dict]:
    with SessionLocal() as session:
        return [_rfp_brief(r) for r in
                session.scalars(select(RFP).order_by(RFP.created_at.desc()).limit(25))]


_STOPWORDS = {"of", "the", "for", "and", "a", "an", "what", "about", "status",
              "tender", "bid", "rfp", "show", "me", "is", "on", "in"}


def _tokens(text: str) -> list[str]:
    import re
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _fuzzy_pick_tender(session, query: str):
    """Typo- and abbreviation-tolerant tender lookup, scored in Python.

    'hpl' matches Hindustan Petroleum ... Limited via its acronym; 'railays'
    matches Railways via edit similarity. Returns (rfp, score, runners_up).
    """
    import difflib
    terms = [t for t in _tokens(query) if t not in _STOPWORDS] or _tokens(query)
    if not terms:
        return None, 0.0, []
    scored = []
    for rfp in session.scalars(select(RFP).order_by(RFP.created_at.desc()).limit(100)):
        words = _tokens(f"{rfp.title} {rfp.issuer} {rfp.reference_no} {rfp.rfp_id}")
        candidates = set(words)
        for source in (rfp.issuer, rfp.title):
            src_words = [w for w in _tokens(source) if w not in _STOPWORDS]
            if len(src_words) >= 2:
                candidates.add("".join(w[0] for w in src_words))  # acronym: hpcl, dae…
        total = 0.0
        for t in terms:
            best = 0.0
            for c in candidates:
                if t == c or (len(t) > 3 and t in c):
                    best = 1.0
                    break
                r = difflib.SequenceMatcher(None, t, c).ratio()
                if r > best:
                    best = r
            total += best
        scored.append((total / len(terms), rfp))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_rfp = scored[0]
    runners = [r for s, r in scored[1:4] if s >= best_score - 0.1 and s >= 0.6]
    return (best_rfp if best_score >= 0.6 else None), best_score, runners


def tool_get_tender_details(query: str) -> dict:
    with SessionLocal() as session:
        rfp, score, runners = _fuzzy_pick_tender(session, query)
        if rfp is None:
            return {"error": f"no tender confidently matches '{query}'",
                    "available_tenders": [_rfp_brief(r) for r in
                                          session.scalars(select(RFP).order_by(RFP.created_at.desc()).limit(15))]}
        out = _rfp_brief(rfp)
        if runners:
            out["also_similar"] = [_rfp_brief(r) for r in runners]
        run = _latest_run(session, rfp.rfp_id)
        if run is not None:
            out["latest_run"] = _run_summary(run)
            decisions = session.scalars(select(Decision).where(Decision.run_id == run.run_id)).all()
            out["decisions"] = [{"actor": d.actor, "action": d.action,
                                 "at": d.decided_at.isoformat()} for d in decisions]
        return out


def tool_search_products(query: str, limit: int = 8) -> list[dict]:
    # every word must appear somewhere in name+category+specs (order-free)
    terms = [t for t in (query or "").split() if t.strip()]
    if not terms:
        return [{"note": "empty query"}]
    with SessionLocal() as session:
        haystack = func.lower(SKU.name + " " + SKU.category + " " + cast(SKU.specs, String))
        conditions = [haystack.like(f"%{t.lower()}%") for t in terms]
        stmt = select(SKU)
        for c in conditions:
            stmt = stmt.where(c)
        skus = session.scalars(stmt.limit(max(1, min(limit, 20)))).all()
        out = []
        for s in skus:
            price = session.scalar(select(PriceMaterial).where(PriceMaterial.sku_id == s.sku_id)
                                   .order_by(PriceMaterial.id.desc()))
            out.append({"sku_id": s.sku_id, "name": s.name, "category": s.category,
                        "specs": s.specs,
                        "unit_price": f"{price.unit_price} {price.currency}/{price.unit}" if price else "no price"})
        return out or [{"note": f"no products match '{query}'"}]


def tool_open_escalations() -> list[dict]:
    with SessionLocal() as session:
        return [{"severity": e.severity, "agent": e.source_agent, "reason": e.reason,
                 "rfp_id": e.rfp_id}
                for e in session.scalars(select(Escalation).where(Escalation.status == "open")
                                         .order_by(Escalation.created_at.desc()).limit(20))]


def tool_record_decision(actor: str, run_id: str, action: str, reason: str, confirm: bool) -> dict:
    if not confirm:
        return {"error": "not confirmed — ask the user to confirm first"}
    if not actor.strip():
        return {"error": "no user name set in the chat widget — decisions must be attributed"}
    if action not in ("approve", "no_bid", "mark_submitted"):
        return {"error": "action must be approve | no_bid | mark_submitted"}
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            return {"error": f"run {run_id} not found"}
        rfp = session.get(RFP, run.rfp_id)
        if action == "mark_submitted" and rfp.status != "approved":
            return {"error": "only an approved bid can be marked submitted"}
        session.add(Decision(id=new_id("dec"), run_id=run_id, actor=actor.strip(),
                             action=action, payload={"reason": reason, "via": "assistant"}))
        if action == "approve":
            run.status = "decided"
            rfp.status = "approved"
        elif action == "no_bid":
            run.status = "decided"
            rfp.status = "no_bid"
        elif action == "mark_submitted":
            rfp.status = "submitted"
        session.commit()
        return {"ok": True, "recorded": action, "tender": rfp.title, "actor": actor.strip(),
                "note": "PDF is now available from the run page" if action == "approve" else ""}


TOOLS = [
    {"type": "function", "function": {
        "name": "list_tenders",
        "description": "List recent tenders (RFPs) with status and due dates.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_tender_details",
        "description": "Full detail for one tender by id, reference no, or (partial) title: specs, latest run matches with Spec Match % and gaps, pricing, verdict, decisions.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "rfp id, reference number, or part of the title, e.g. 'metro'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "search_products",
        "description": "Search the product catalog (names, categories, specs). Returns SKUs with specs and unit prices.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "open_escalations",
        "description": "List open escalations (agent failures, pricing gaps, deadline warnings).",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "record_decision",
        "description": "Record a human decision on a run. ONLY call after the user explicitly confirmed in a follow-up message. action: approve | no_bid | mark_submitted.",
        "parameters": {"type": "object", "properties": {
            "run_id": {"type": "string"},
            "action": {"type": "string", "enum": ["approve", "no_bid", "mark_submitted"]},
            "reason": {"type": "string"},
            "confirm": {"type": "boolean", "description": "true only after explicit user confirmation"}},
            "required": ["run_id", "action", "confirm"]}}},
]


def chat(actor: str, messages: list[dict]) -> str:
    """One assistant turn: tool loop until a text reply."""
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key or None, timeout=60)

    convo: list[dict] = [{"role": "system", "content": SYSTEM.format(today=date.today().isoformat())}]
    convo += [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:4000]}
              for m in messages[-16:]]

    for _ in range(6):
        resp = client.chat.completions.create(
            model=sources.llm.openai_model, messages=convo,
            tools=TOOLS, tool_choice="auto", max_tokens=1200,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or "…"
        convo.append({"role": "assistant", "content": msg.content,
                      "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                if tc.function.name == "list_tenders":
                    result = tool_list_tenders()
                elif tc.function.name == "get_tender_details":
                    result = tool_get_tender_details(args.get("query", ""))
                elif tc.function.name == "search_products":
                    result = tool_search_products(args.get("query", ""), args.get("limit", 8))
                elif tc.function.name == "open_escalations":
                    result = tool_open_escalations()
                elif tc.function.name == "record_decision":
                    result = tool_record_decision(actor, args.get("run_id", ""),
                                                  args.get("action", ""), args.get("reason", ""),
                                                  bool(args.get("confirm")))
                else:
                    result = {"error": f"unknown tool {tc.function.name}"}
            except Exception as e:
                log.exception("assistant tool %s failed", tc.function.name)
                result = {"error": str(e)[:300]}
            convo.append({"role": "tool", "tool_call_id": tc.id,
                          "content": json.dumps(result, default=str)[:12000]})
    return "I hit my tool-call limit for this question — try asking it more specifically."
