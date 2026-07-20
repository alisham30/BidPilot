"""One-off migration: renumber existing rows to the uniform human-readable IDs.

rfp_xxxx → RFP-2026-0001 (by creation order)
run_xxxx → DRAFT-0001    (by start order; run.state JSON references updated too)
esc_xxxx → ALERT-0001    · fu_xxxx → FU-0001

Foreign keys are handled by clone-insert → repoint children → delete old.
Run once:  .venv/Scripts/python scripts/renumber_ids.py
"""
from sqlalchemy import select, update

from app.db import (
    Base, Decision, Escalation, Followup, RFP, RFPDatasetRow, Run,
    SessionLocal, engine, friendly_id,
)

Base.metadata.create_all(engine)  # ensures id_counters exists


def main() -> None:
    rfp_map: dict[str, str] = {}
    run_map: dict[str, str] = {}

    with SessionLocal() as s:
        # ---- RFPs ----
        for rfp in s.scalars(select(RFP).order_by(RFP.created_at)).all():
            if rfp.rfp_id.startswith("RFP-"):
                continue
            old = rfp.rfp_id
            new = friendly_id(s, "RFP", year=True)
            rfp_map[old] = new
            rfp.dedupe_key = rfp.dedupe_key + "~renaming"  # free the unique key
            s.flush()
            clone = RFP(rfp_id=new, title=rfp.title, issuer=rfp.issuer,
                        reference_no=rfp.reference_no, due_date=rfp.due_date,
                        source=rfp.source, source_detail=rfp.source_detail,
                        dedupe_key=rfp.dedupe_key.removesuffix("~renaming"),
                        doc_paths=rfp.doc_paths, status=rfp.status,
                        created_at=rfp.created_at, updated_at=rfp.updated_at)
            s.add(clone)
            s.flush()
            s.execute(update(RFPDatasetRow).where(RFPDatasetRow.rfp_id == old).values(rfp_id=new))
            s.execute(update(Run).where(Run.rfp_id == old).values(rfp_id=new))
            s.execute(update(Escalation).where(Escalation.rfp_id == old).values(rfp_id=new))
            s.execute(update(Followup).where(Followup.rfp_id == old).values(rfp_id=new))
            s.delete(rfp)
            s.flush()

        # ---- Runs ----
        for run in s.scalars(select(Run).order_by(Run.started_at)).all():
            if run.run_id.startswith("DRAFT-"):
                continue
            old = run.run_id
            new = friendly_id(s, "DRAFT")
            run_map[old] = new
            state = dict(run.state or {})
            if state.get("run_id"):
                state["run_id"] = new
            if state.get("rfp_id") in rfp_map:
                state["rfp_id"] = rfp_map[state["rfp_id"]]
            clone = Run(run_id=new, rfp_id=run.rfp_id, state=state, status=run.status,
                        started_at=run.started_at, finished_at=run.finished_at)
            s.add(clone)
            s.flush()
            s.execute(update(Decision).where(Decision.run_id == old).values(run_id=new))
            s.delete(run)
            s.flush()

        # ---- Escalations & follow-ups (no children) ----
        for esc in s.scalars(select(Escalation).order_by(Escalation.created_at)).all():
            if not esc.id.startswith("ALERT-"):
                esc.id = friendly_id(s, "ALERT")
        for fu in s.scalars(select(Followup).order_by(Followup.created_at)).all():
            if not fu.id.startswith("FU-"):
                fu.id = friendly_id(s, "FU")

        s.commit()
    print(f"renumbered: {len(rfp_map)} RFPs, {len(run_map)} drafts")
    for old, new in rfp_map.items():
        print(f"  {old} -> {new}")


if __name__ == "__main__":
    main()
