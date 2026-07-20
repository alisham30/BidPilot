"""The decision gate: PDF is 403 until an approve decision row exists, and
every consequential action requires an attributed human actor."""
import pytest
from fastapi.testclient import TestClient

from app.db import RFP, Run
from app.main import app

client = TestClient(app)  # no lifespan → no schedulers in tests


VALID_STATE = {
    "tech": {"items": [{"item_no": "1", "description": "d", "quantity": 1, "unit": "m",
                        "top3": [{"sku_id": "S", "pct": 100.0, "evidence": []}],
                        "top_pick": "S", "below_threshold": False}]},
    "price": {"lines": [], "test_lines": [], "material_total": 0,
              "test_total": 0, "grand_total": 0, "currency": "INR"},
    "verdict": {"per_item": [], "overall": "proceed", "evidence": []},
    "mto": [],
    "run_log": ["test"],
}


@pytest.fixture()
def run(session):
    rfp = RFP(rfp_id="rfp_test1", title="T", dedupe_key="k1", status="awaiting_review")
    run = Run(run_id="run_test1", rfp_id="rfp_test1", state=VALID_STATE, status="awaiting_review")
    session.add_all([rfp, run])
    session.commit()
    return run


def test_pdf_403_without_approve_decision(run):
    resp = client.get("/runs/run_test1/pdf")
    assert resp.status_code == 403


def test_decision_requires_actor(run):
    resp = client.post("/runs/run_test1/decision", json={"actor": "", "action": "approve"})
    assert resp.status_code == 422
    resp = client.post("/runs/run_test1/decision", json={"action": "approve"})
    assert resp.status_code == 422


def test_pdf_after_approve_decision(run):
    resp = client.post("/runs/run_test1/decision",
                       json={"actor": "alisha", "action": "approve", "payload": {}})
    assert resp.status_code == 200
    resp = client.get("/runs/run_test1/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_unknown_action_rejected(run):
    resp = client.post("/runs/run_test1/decision",
                       json={"actor": "alisha", "action": "auto_submit"})
    assert resp.status_code == 422


def test_mark_submitted_requires_prior_approval(run):
    resp = client.post("/runs/run_test1/decision",
                       json={"actor": "alisha", "action": "mark_submitted"})
    assert resp.status_code == 409


def test_followup_send_requires_actor(run):
    resp = client.post("/followups/nonexistent/send", json={})
    assert resp.status_code == 422  # actor is mandatory before anything is looked up
