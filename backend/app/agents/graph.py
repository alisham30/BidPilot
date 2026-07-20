"""Main Orchestrator — LangGraph run per RFP.

Fan-out: role summaries → (Technical || Pricing-tests) → join at material
pricing → MTO gaps → Verifier → human checkpoint. The graph NEVER proceeds
past the checkpoint on its own; every consequential action goes through
POST /runs/{id}/decision.

Every node's output is merged into `runs.state` (full snapshot) so any bid
can be reconstructed later, and pushed to the dashboard over WebSocket.
"""
from __future__ import annotations

import logging
import operator
import traceback
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import llm
from ..db import Run, RFP, SessionLocal
from ..dataset.builder import get_dataset
from ..schemas import (
    DraftResponse, PriceTable, RFPLineItem, RFPTest, RoleSummaries, TechTable, Verdict,
)
from ..tracking.escalations import escalate
from ..ws import manager
from . import mto, pricing, technical, verifier

log = logging.getLogger("bidpilot.graph")

SUMMARY_SYSTEM = """You are the orchestrator of a tender-response pipeline for a wires &
cables OEM. Produce two role-contextual summaries of the tender dataset provided:
- product_summary: everything the Technical Agent needs about the products required
  (items, constructions, standards, quantities).
- test_summary: everything the Pricing Agent needs about testing/acceptance
  requirements and commercial conditions.
Summarize faithfully; do not add requirements that are not in the dataset."""


class RunState(TypedDict, total=False):
    run_id: str
    rfp_id: str
    line_items: list[dict]
    tests: list[dict]
    product_summary: str
    test_summary: str
    tech: dict
    test_lines: list[dict]
    price: dict
    mto: list[dict]
    verdict: dict
    run_log: Annotated[list[str], operator.add]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _items(state: RunState) -> list[RFPLineItem]:
    return [RFPLineItem.model_validate(d) for d in state["line_items"]]


# ------------------------------ nodes ------------------------------

def node_summarize(state: RunState) -> dict:
    dataset_text = (
        "LINE ITEMS:\n" + "\n".join(str(d) for d in state["line_items"]) +
        "\n\nTESTS:\n" + "\n".join(str(t) for t in state["tests"])
    )
    summaries = llm.extract(RoleSummaries, SUMMARY_SYSTEM, dataset_text, max_tokens=4000)
    return {
        "product_summary": summaries.product_summary,
        "test_summary": summaries.test_summary,
        "run_log": [f"[{_now()}] orchestrator: role summaries built, dispatching technical + pricing in parallel"],
    }


def node_technical(state: RunState) -> dict:
    with SessionLocal() as session:
        table = technical.match_items(session, _items(state))
    gaps = sum(1 for i in table.items if i.below_threshold)
    return {
        "tech": table.model_dump(),
        "run_log": [f"[{_now()}] technical: matched {len(table.items)} items, {gaps} below threshold"],
    }


def node_price_tests(state: RunState) -> dict:
    tests = [RFPTest.model_validate(t) for t in state["tests"]]
    with SessionLocal() as session:
        lines = pricing.price_tests(session, tests)
    missing = sum(1 for l in lines if not l.priced)
    return {
        "test_lines": [l.model_dump() for l in lines],
        "run_log": [f"[{_now()}] pricing: {len(lines)} test lines priced immediately ({missing} missing from table)"],
    }


def node_join_pricing(state: RunState) -> dict:
    from ..schemas import TestPriceLine
    tech_table = TechTable.model_validate(state["tech"])
    test_lines = [TestPriceLine.model_validate(t) for t in state["test_lines"]]
    with SessionLocal() as session:
        lines = pricing.price_materials(session, tech_table)
        table = pricing.consolidate(lines, test_lines)
        for line in lines:
            if not line.priced:
                escalate(session, "pricing_agent",
                         f"item {line.item_no}: no usable price for SKU '{line.sku_id or 'none matched'}'",
                         rfp_id=state["rfp_id"])
        for t in test_lines:
            if not t.priced:
                escalate(session, "pricing_agent",
                         f"test '{t.test_name}' missing from services price table", rfp_id=state["rfp_id"])
    return {
        "price": table.model_dump(),
        "run_log": [f"[{_now()}] pricing: joined SKU table — grand total {table.grand_total} {table.currency}"],
    }


def node_mto(state: RunState) -> dict:
    tech_table = TechTable.model_validate(state["tech"])
    with SessionLocal() as session:
        requests = mto.draft_mto_requests(session, state["rfp_id"], tech_table.items)
    return {
        "mto": [r.model_dump() for r in requests],
        "run_log": [f"[{_now()}] mto: drafted {len(requests)} made-to-order request(s)"],
    }


def node_verify(state: RunState) -> dict:
    tech_table = TechTable.model_validate(state["tech"])
    price_table = PriceTable.model_validate(state["price"])
    with SessionLocal() as session:
        verdict = verifier.verify(session, _items(state), tech_table, price_table)
    return {
        "verdict": verdict.model_dump(),
        "run_log": [f"[{_now()}] verifier: {verdict.overall} "
                    f"({sum(1 for v in verdict.per_item if v.status == 'flagged')} flagged)"],
    }


def node_finalize(state: RunState) -> dict:
    return {"run_log": [f"[{_now()}] orchestrator: draft response posted to human checkpoint — awaiting review"]}


def build_graph():
    g = StateGraph(RunState)
    g.add_node("summarize", node_summarize)
    g.add_node("technical", node_technical)
    g.add_node("price_tests", node_price_tests)
    g.add_node("join_pricing", node_join_pricing)
    g.add_node("mto_gap", node_mto)
    g.add_node("verify", node_verify)
    g.add_node("finalize", node_finalize)

    g.add_edge(START, "summarize")
    g.add_edge("summarize", "technical")      # fan-out
    g.add_edge("summarize", "price_tests")    # fan-out
    g.add_edge(["technical", "price_tests"], "join_pricing")  # join
    g.add_edge("join_pricing", "mto_gap")
    g.add_edge("mto_gap", "verify")
    g.add_edge("verify", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ------------------------------ runner ------------------------------

def _persist(run_id: str, state: dict, status: str | None = None) -> None:
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        run.state = state
        if status:
            run.status = status
            if status in ("awaiting_review", "failed"):
                run.finished_at = datetime.now(timezone.utc)
        session.commit()


def execute_run(run_id: str) -> None:
    """Blocking pipeline execution — call from a worker thread."""
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        rfp = session.get(RFP, run.rfp_id) if run else None
        dataset = get_dataset(session, run.rfp_id) if run else None
        if run is None or rfp is None:
            return
        if dataset is None:
            escalate(session, "orchestrator", "no extracted dataset for this RFP — run POST /scan first",
                     rfp_id=run.rfp_id, severity="high")
            run.status = "failed"
            session.commit()
            manager.publish(run_id, {"type": "failed", "reason": "no dataset"})
            return
        rfp.status = "drafting"
        session.commit()
        state: dict = {
            "run_id": run_id,
            "rfp_id": run.rfp_id,
            "line_items": dataset.line_items,
            "tests": dataset.tests,
            "run_log": [f"[{_now()}] run started for {rfp.title or run.rfp_id}"],
        }
    _persist(run_id, state)
    manager.publish(run_id, {"type": "progress", "node": "start", "state": state})

    try:
        for update in get_graph().stream(state, stream_mode="updates"):
            for node_name, delta in update.items():
                for key, value in (delta or {}).items():
                    if key == "run_log":
                        state["run_log"] = state.get("run_log", []) + value
                    else:
                        state[key] = value
                _persist(run_id, state)
                manager.publish(run_id, {"type": "progress", "node": node_name, "state": state})
    except Exception as e:
        log.error("run %s failed: %s\n%s", run_id, e, traceback.format_exc())
        with SessionLocal() as session:
            escalate(session, "orchestrator", f"run failed: {e}", rfp_id=state.get("rfp_id"), severity="high")
        state["run_log"] = state.get("run_log", []) + [f"[{_now()}] RUN FAILED: {e}"]
        _persist(run_id, state, status="failed")
        manager.publish(run_id, {"type": "failed", "reason": str(e), "state": state})
        return

    _persist(run_id, state, status="awaiting_review")
    with SessionLocal() as session:
        rfp = session.get(RFP, state["rfp_id"])
        if rfp and rfp.status == "drafting":
            rfp.status = "awaiting_review"
            session.commit()
    manager.publish(run_id, {"type": "awaiting_review", "state": state})


def apply_edit_and_reverify(run_id: str, sku_overrides: dict[str, str]) -> None:
    """Human edit at the checkpoint: override top picks, then re-price and
    re-verify. (Edits re-trigger verification — never trusted blindly.)"""
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None or not run.state.get("tech"):
            return
        state = dict(run.state)

    tech_table = TechTable.model_validate(state["tech"])
    for item in tech_table.items:
        if item.item_no in sku_overrides:
            item.top_pick = sku_overrides[item.item_no] or None
    state["tech"] = tech_table.model_dump()
    state["run_log"] = state.get("run_log", []) + [
        f"[{_now()}] human edit applied to {len(sku_overrides)} item(s) — re-pricing and re-verifying"]
    _persist(run_id, state)
    manager.publish(run_id, {"type": "progress", "node": "edit", "state": state})

    # re-run pricing join and verification with merged logs
    for node in (node_join_pricing, node_verify):
        delta = node(state)
        for key, value in delta.items():
            if key == "run_log":
                state["run_log"] = state.get("run_log", []) + value
            else:
                state[key] = value
        _persist(run_id, state)
    _persist(run_id, state, status="awaiting_review")
    manager.publish(run_id, {"type": "awaiting_review", "state": state})


def draft_response_from_state(state: dict) -> DraftResponse | None:
    try:
        return DraftResponse(
            sku_table=TechTable.model_validate(state["tech"]),
            price_table=PriceTable.model_validate(state["price"]),
            mto_requests=state.get("mto", []),
            verifier_verdict=Verdict.model_validate(state["verdict"]),
            run_log=state.get("run_log", []),
        )
    except Exception:
        return None
