"""Deterministic Spec Match %. Equal weightage per required parameter.

Every number a customer could audit comes from here — plain Python over
extracted data. If an LLM ever emits a match percentage that gets used,
that is a bug.
"""
from __future__ import annotations

import re as _re

from ..schemas import Evidence, MatchResult, SpecParam
from .equivalence import equivalents
from .normalize import canonical_number, norm_text, numbers_comparable

REL_TOL = 1e-6  # numeric_exact: equal after unit normalization


def _numeric_actual(raw: str | None, req: SpecParam) -> tuple[float, str] | None:
    if raw is None:
        return None
    return canonical_number(raw, req.unit)


def _required_number(req: SpecParam) -> tuple[float, str] | None:
    # dual designations ("450/750") must be parsed from the stated value —
    # the extractor's numeric_value only carries the first number
    if "/" in (req.value or ""):
        parsed = canonical_number(req.value, req.unit)
        if parsed is not None:
            return parsed
    if req.numeric_value is not None:
        return canonical_number(req.numeric_value, req.unit)
    return canonical_number(req.value, req.unit)


def _categorical_score(required: str, actual: str) -> float:
    """Deterministic categorical comparison. Equality/equivalence only — plus
    three explicit, narrow rules (no fuzzy matching):
    1. '/' and ':' are interchangeable in standard refs (IS 694/2010 = IS 694:2010)
    2. a compound requirement ('IS 694 and IS 8130') matches on any component
    3. a MULTI-WORD catalog value contained whole in a longer requirement phrase
       matches ('Flat Cable' in 'flat cable with pvc insulation...') — single
       words never containment-match, so 'submarine' can never equal 'submersible'.
    """
    a = norm_text(actual)
    if a is None:
        return 0.0

    def variants(v: str) -> set[str]:
        return {v, v.replace("/", ":"), v.replace(":", "/")}

    req_components = [c for c in
                      (norm_text(p) for p in _re.split(r"\s+and\s+|\s*[,;&]\s*", required))
                      if c]
    for component in req_components or [norm_text(required)]:
        if component is None:
            continue
        for cv in variants(component):
            eq = equivalents(cv)
            if any(av in eq for av in variants(a)):
                return 1.0
        # rule 3: multi-word catalog value inside the requirement phrase
        if len(a.split()) >= 2 and f" {a} " in f" {component} ":
            return 1.0
    return 0.0


def spec_match(rfp_specs: list[SpecParam], sku_specs: dict[str, str]) -> MatchResult:
    """Equal weightage across all required parameters. Deterministic. Unit-tested."""
    scores: list[float] = []
    evidence: list[Evidence] = []
    lookup = {norm_text(k): v for k, v in sku_specs.items()}

    for req in rfp_specs:
        raw_actual = lookup.get(norm_text(req.name))
        actual_display: str | None = None if raw_actual is None else str(raw_actual)

        if raw_actual is None or str(raw_actual).strip() == "":
            s = 0.0  # missing spec = fail, never assume
            actual_display = None
        elif req.kind == "numeric_exact":
            need = _required_number(req)
            got = _numeric_actual(str(raw_actual), req)
            if need is None or got is None or not numbers_comparable(need, got):
                s = 0.0
            elif need[0] == 0:
                s = 1.0 if got[0] == 0 else 0.0
            elif abs(got[0] - need[0]) <= REL_TOL * abs(need[0]):
                s = 1.0
            else:
                s = max(0.0, 1 - abs(got[0] - need[0]) / abs(need[0]))
        elif req.kind == "numeric_min":  # meets-or-exceeds
            need = _required_number(req)
            got = _numeric_actual(str(raw_actual), req)
            if need is None or got is None or not numbers_comparable(need, got):
                s = 0.0
            elif need[0] <= 0:
                s = 1.0
            else:
                s = 1.0 if got[0] >= need[0] else max(0.0, got[0] / need[0])
        else:  # categorical
            s = _categorical_score(req.value, str(raw_actual))

        scores.append(s)
        evidence.append(Evidence(
            param=req.name, kind=req.kind, required=req.value,
            actual=actual_display, score=round(s, 4),
        ))

    pct = round(100 * sum(scores) / len(scores), 1) if scores else 0.0
    return MatchResult(pct=pct, evidence=evidence)
