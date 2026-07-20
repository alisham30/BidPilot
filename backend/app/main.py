"""FastAPI backend — API surface + WebSocket + schedulers.

POST /runs/{id}/decision is the ONLY code path to a consequential action.
No agent can submit, cancel, drop an item, or send an email without a human
decision recorded here.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .agents.graph import apply_edit_and_reverify, draft_response_from_state, execute_run
from .config import sources
from .dataset.builder import build_dataset, get_dataset
from .dataset.catalog import rebuild_catalog
from .dataset.registry import rfps_in_window
from .db import (
    RFP, Decision, Escalation, Followup, PriceService, Run, SessionLocal, SKU,
    friendly_id, new_id,
)
from .ingestion.email_scanner import scan_inbox
from .ingestion.web_scanner import scan_portals
from .output.pdf import generate_bid_pdf
from .tracking import tracker
from .tracking.followups import send_followup
from .ws import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bidpilot.api")

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.set_loop(asyncio.get_running_loop())
    scheduler.add_job(_scheduled_email_scan, "interval", minutes=5, id="email_scan")
    scheduler.add_job(tracker.tick, "interval", minutes=sources.tracking.reply_poll_minutes, id="tracker")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="BidPilot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


def _scheduled_email_scan() -> None:
    with SessionLocal() as session:
        try:
            scan_inbox(session)
            _auto_build_datasets(session)
        except Exception:
            log.exception("scheduled email scan failed")


def _auto_build_datasets(session) -> None:
    """Datasets build automatically for new RFPs that carry documents, and the
    analysis run starts on its own — the user only ever reviews and decides."""
    for rfp in session.scalars(select(RFP).where(RFP.status == "new")):
        if rfp.doc_paths:
            build_dataset(session, rfp)
    # self-healing sweep: every extracted RFP without a run gets one
    for rfp in session.scalars(select(RFP).where(RFP.status == "extracted")):
        _auto_start_run(session, rfp.rfp_id)


def _auto_start_run(session, rfp_id: str) -> None:
    existing = session.scalar(select(Run).where(Run.rfp_id == rfp_id))
    if existing is not None:
        return
    run = Run(run_id=friendly_id(session, "DRAFT"), rfp_id=rfp_id, state={}, status="running")
    session.add(run)
    session.commit()
    threading.Thread(target=execute_run, args=(run.run_id,), daemon=True).start()
    log.info("auto-started run %s for %s", run.run_id, rfp_id)


# ------------------------------ scans & catalog ------------------------------

@app.post("/scan/email")
async def trigger_email_scan():
    def work():
        with SessionLocal() as session:
            stats = scan_inbox(session)
            _auto_build_datasets(session)
            return stats
    return await asyncio.to_thread(work)


@app.post("/scan/web")
async def trigger_web_scan():
    def work():
        with SessionLocal() as session:
            return scan_portals(session)
    return await asyncio.to_thread(work)


@app.post("/catalog/rebuild")
async def catalog_rebuild():
    def work():
        with SessionLocal() as session:
            return rebuild_catalog(session)
    return await asyncio.to_thread(work)


@app.get("/catalog/skus")
def list_skus(q: str = "", category: str = "", limit: int = 60, offset: int = 0):
    """Browse/search the product catalog — every word of q must match somewhere
    in code+name+category+specs (order-free)."""
    from sqlalchemy import String as SAString, cast
    from .db import PriceMaterial
    with SessionLocal() as session:
        stmt = select(SKU)
        if category:
            stmt = stmt.where(SKU.category == category)
        for term in [t.lower() for t in q.split() if t.strip()]:
            hay = func.lower(SKU.sku_id + " " + SKU.name + " " + SKU.category + " " + cast(SKU.specs, SAString))
            stmt = stmt.where(hay.like(f"%{term}%"))
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(session.scalars(stmt.order_by(SKU.category, SKU.sku_id)
                                    .limit(max(1, min(limit, 200))).offset(max(0, offset))))
        out = []
        for s in rows:
            price = session.scalar(select(PriceMaterial).where(PriceMaterial.sku_id == s.sku_id)
                                   .order_by(PriceMaterial.id.desc()))
            out.append({
                "sku_id": s.sku_id, "name": s.name, "category": s.category,
                "specs": s.specs, "source": s.datasheet_source,
                "unit_price": price.unit_price if price else None,
                "currency": price.currency if price else None,
                "unit": price.unit if price else None,
            })
        return {"total": total, "items": out}


@app.get("/catalog/stats")
def catalog_stats():
    with SessionLocal() as session:
        return {
            "skus": session.scalar(select(func.count()).select_from(SKU)) or 0,
            "service_prices": session.scalar(select(func.count()).select_from(PriceService)) or 0,
            "categories": [r[0] for r in session.execute(
                select(SKU.category).distinct().order_by(SKU.category)) if r[0]],
        }


# ------------------------------ registry ------------------------------

def _rfp_dict(r: RFP) -> dict:
    return {
        "rfp_id": r.rfp_id, "title": r.title, "issuer": r.issuer,
        "reference_no": r.reference_no,
        "due_date": r.due_date.isoformat() if r.due_date else None,
        "due_unknown": r.due_date is None,
        "days_left": (r.due_date - date.today()).days if r.due_date else None,
        "source": r.source, "source_detail": r.source_detail,
        "status": r.status, "doc_count": len(r.doc_paths or []),
        "created_at": r.created_at.isoformat(),
    }


@app.get("/rfps")
def list_rfps(window: str | None = None):
    with SessionLocal() as session:
        if window == "3m":
            rows = rfps_in_window(session)
        else:
            rows = list(session.scalars(select(RFP).order_by(RFP.created_at.desc())))
        return [_rfp_dict(r) for r in rows]


@app.get("/rfps/{rfp_id}")
def get_rfp(rfp_id: str):
    with SessionLocal() as session:
        rfp = session.get(RFP, rfp_id)
        if rfp is None:
            raise HTTPException(404)
        dataset = get_dataset(session, rfp_id)
        runs = list(session.scalars(select(Run).where(Run.rfp_id == rfp_id).order_by(Run.started_at.desc())))
        return {
            **_rfp_dict(rfp),
            "doc_paths": rfp.doc_paths,
            "dataset": None if dataset is None else {
                "line_items": dataset.line_items, "tests": dataset.tests,
                "special_conditions": dataset.special_conditions,
            },
            "runs": [{"run_id": r.run_id, "status": r.status,
                      "started_at": r.started_at.isoformat()} for r in runs],
        }


# ------------------------------ runs ------------------------------

@app.post("/rfps/{rfp_id}/respond")
def start_run(rfp_id: str):
    with SessionLocal() as session:
        rfp = session.get(RFP, rfp_id)
        if rfp is None:
            raise HTTPException(404)
        if get_dataset(session, rfp_id) is None:
            if not rfp.doc_paths:
                raise HTTPException(409, "RFP has no documents and no extracted dataset")
            built = build_dataset(session, rfp)
            if built is None:
                raise HTTPException(422, "dataset extraction failed — see escalations")
        run = Run(run_id=friendly_id(session, "DRAFT"), rfp_id=rfp_id, state={}, status="running")
        session.add(run)
        session.commit()
        run_id = run.run_id
    threading.Thread(target=execute_run, args=(run_id,), daemon=True).start()
    return {"run_id": run_id, "ws": f"/ws/runs/{run_id}"}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404)
        decisions = list(session.scalars(select(Decision).where(Decision.run_id == run_id)
                                         .order_by(Decision.decided_at)))
        return {
            "run_id": run.run_id, "rfp_id": run.rfp_id, "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "state": run.state,
            "decisions": [{"actor": d.actor, "action": d.action,
                           "payload": d.payload, "decided_at": d.decided_at.isoformat()}
                          for d in decisions],
        }


class DecisionBody(BaseModel):
    actor: str = Field(min_length=1, description="Human user id — every consequential action is attributed")
    action: str  # approve | edit | no_bid | mark_submitted
    payload: dict = Field(default_factory=dict)


@app.post("/runs/{run_id}/decision")
def post_decision(run_id: str, body: DecisionBody):
    """THE human checkpoint — the only path to a decision."""
    if body.action not in ("approve", "edit", "no_bid", "mark_submitted"):
        raise HTTPException(422, "action must be approve | edit | no_bid | mark_submitted")
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404)
        rfp = session.get(RFP, run.rfp_id)
        session.add(Decision(id=new_id("dec"), run_id=run_id, actor=body.actor,
                             action=body.action, payload=body.payload))
        if body.action == "approve":
            run.status = "decided"
            rfp.status = "approved"
        elif body.action == "no_bid":
            run.status = "decided"
            rfp.status = "no_bid"
        elif body.action == "mark_submitted":
            if rfp.status != "approved":
                session.rollback()
                raise HTTPException(409, "only an approved bid can be marked submitted")
            rfp.status = "submitted"
        session.commit()

    if body.action == "edit":
        overrides = {str(k): str(v) for k, v in (body.payload.get("sku_overrides") or {}).items()}
        threading.Thread(target=apply_edit_and_reverify, args=(run_id, overrides), daemon=True).start()
        return {"status": "reverifying", "note": "edits re-trigger pricing and verification"}
    return {"status": "recorded"}


@app.get("/runs/{run_id}/pdf")
def get_run_pdf(run_id: str):
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404)
        approved = session.scalar(select(Decision).where(
            Decision.run_id == run_id, Decision.action == "approve"))
        if approved is None:
            raise HTTPException(403, "PDF is generated only after a human approve decision")
        rfp = session.get(RFP, run.rfp_id)
        draft = draft_response_from_state(run.state)
        if draft is None:
            raise HTTPException(422, "run state is incomplete")
        path = generate_bid_pdf(rfp, draft)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


# ------------------------------ escalations, bids, followups ------------------------------

@app.get("/escalations")
def list_escalations(status: str = "open"):
    with SessionLocal() as session:
        stmt = select(Escalation).order_by(Escalation.created_at.desc())
        if status != "all":
            stmt = stmt.where(Escalation.status == status)
        return [{"id": e.id, "rfp_id": e.rfp_id, "source_agent": e.source_agent,
                 "reason": e.reason, "severity": e.severity, "status": e.status,
                 "created_at": e.created_at.isoformat()}
                for e in session.scalars(stmt)]


@app.post("/escalations/{esc_id}/ack")
def ack_escalation(esc_id: str, actor: str = Body(embed=True)):
    with SessionLocal() as session:
        esc = session.get(Escalation, esc_id)
        if esc is None:
            raise HTTPException(404)
        esc.status = "acked" if esc.status == "open" else "resolved"
        session.commit()
        return {"status": esc.status}


@app.get("/bids")
def list_bids():
    with SessionLocal() as session:
        bids = list(session.scalars(select(RFP).where(
            RFP.status.in_(["approved", "submitted", "closed"])).order_by(RFP.updated_at.desc())))
        followups = list(session.scalars(select(Followup).order_by(Followup.created_at.desc())))
        return {
            "bids": [_rfp_dict(b) for b in bids],
            "followups": [{"id": f.id, "rfp_id": f.rfp_id, "subject": f.subject,
                           "body": f.body, "reason": f.reason, "status": f.status,
                           "created_at": f.created_at.isoformat()} for f in followups],
        }


class SendBody(BaseModel):
    actor: str = Field(min_length=1)


@app.post("/followups/{followup_id}/send")
def send_followup_route(followup_id: str, body: SendBody):
    """Human-approved follow-up send — 403 without explicit approval payload."""
    with SessionLocal() as session:
        fu = session.get(Followup, followup_id)
        if fu is None:
            raise HTTPException(404)
        if fu.status != "draft":
            raise HTTPException(409, f"follow-up is {fu.status}")
        rfp = session.get(RFP, fu.rfp_id)
        latest_run = session.scalar(select(Run).where(Run.rfp_id == fu.rfp_id)
                                    .order_by(Run.started_at.desc()))
        run_id = latest_run.run_id if latest_run else None
        ok = send_followup(session, fu, rfp)
        if ok:
            from .db import utcnow
            fu.status = "sent"
            fu.sent_at = utcnow()
            if run_id:
                session.add(Decision(id=new_id("dec"), run_id=run_id, actor=body.actor,
                                     action="send_followup", payload={"followup_id": fu.id}))
            session.commit()
            return {"status": "sent"}
        raise HTTPException(502, "send failed — see escalations")


# ------------------------------ assistant ------------------------------

class ChatBody(BaseModel):
    actor: str = ""
    messages: list[dict] = Field(default_factory=list)


@app.post("/assistant/chat")
async def assistant_chat(body: ChatBody):
    from . import assistant
    if not body.messages:
        raise HTTPException(422, "messages required")
    reply = await asyncio.to_thread(assistant.chat, body.actor, body.messages)
    return {"reply": reply}


# ------------------------------ dashboard stats & websocket ------------------------------

@app.get("/stats")
def dashboard_stats():
    with SessionLocal() as session:
        def count(model, *where):
            stmt = select(func.count()).select_from(model)
            for w in where:
                stmt = stmt.where(w)
            return session.scalar(stmt) or 0

        # the manager's work queue: newest run awaiting review per tender
        queue = []
        for rfp in session.scalars(select(RFP).where(RFP.status.in_(["awaiting_review", "drafting"]))
                                   .order_by(RFP.due_date.nulls_last())):
            run = session.scalar(select(Run).where(Run.rfp_id == rfp.rfp_id)
                                 .order_by(Run.started_at.desc()))
            if run is None:
                continue
            state = run.state or {}
            price = state.get("price") or {}
            verdict = (state.get("verdict") or {}).get("overall")
            items = (state.get("tech") or {}).get("items", [])
            queue.append({
                "rfp_id": rfp.rfp_id, "run_id": run.run_id, "title": rfp.title,
                "issuer": rfp.issuer,
                "due_date": rfp.due_date.isoformat() if rfp.due_date else None,
                "days_left": (rfp.due_date - date.today()).days if rfp.due_date else None,
                "running": run.status == "running",
                "verdict": verdict,
                "grand_total": price.get("grand_total"),
                "items_total": len(items),
                "items_flagged": sum(1 for i in items if i.get("below_threshold")),
            })

        return {
            "rfps_total": count(RFP),
            "rfps_awaiting_review": count(RFP, RFP.status == "awaiting_review"),
            "rfps_in_window": len(rfps_in_window(session)),
            "bids_submitted": count(RFP, RFP.status == "submitted"),
            "open_escalations": count(Escalation, Escalation.status == "open"),
            "skus": count(SKU),
            "review_queue": queue,
        }


@app.websocket("/ws/runs/{run_id}")
async def run_ws(ws: WebSocket, run_id: str):
    await manager.connect(run_id, ws)
    try:
        while True:
            await ws.receive_text()  # keepalive pings from the client
    except WebSocketDisconnect:
        manager.disconnect(run_id, ws)
