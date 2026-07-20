"""Clause-level deviation statement — rendered directly from scorer evidence,
so it can never disagree with the Spec Match %."""
from __future__ import annotations

from ..schemas import TechTable


def deviation_clauses(tech: TechTable) -> list[str]:
    clauses: list[str] = []
    for item in tech.items:
        best = item.top3[0] if item.top3 else None
        if best is None or item.top_pick is None:
            clauses.append(
                f"Item {item.item_no} ({item.description}): no catalog product is offered; "
                f"item is excluded pending made-to-order feasibility.")
            continue
        gaps = [e for e in best.evidence if e.score < 1.0]
        if not gaps:
            continue
        for g in gaps:
            actual = g.actual if g.actual is not None else "not specified on the offered product"
            clauses.append(
                f"Item {item.item_no}, parameter '{g.param}': tender requires {g.required}; "
                f"offered product ({item.top_pick}) provides {actual} "
                f"(parameter score {g.score:.2f}).")
    if not clauses:
        clauses.append("No deviations: all offered products fully comply with the stated specifications.")
    return clauses
