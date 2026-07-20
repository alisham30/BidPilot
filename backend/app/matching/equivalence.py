"""Categorical equivalence classes, read from config — extendable without code changes."""
from __future__ import annotations

from functools import lru_cache

from ..config import sources
from .normalize import norm_text


@lru_cache
def _classes() -> dict[str, frozenset[str]]:
    classes: dict[str, frozenset[str]] = {}
    for canonical, aliases in sources.matching.equivalences.items():
        members = {norm_text(canonical)} | {norm_text(a) for a in aliases}
        members.discard(None)
        fs = frozenset(members)  # type: ignore[arg-type]
        for m in fs:
            classes[m] = fs
    return classes


def equivalents(value: str) -> frozenset[str]:
    """All values considered equal to `value` (always includes itself)."""
    v = norm_text(value)
    if v is None:
        return frozenset()
    return _classes().get(v, frozenset({v}))
