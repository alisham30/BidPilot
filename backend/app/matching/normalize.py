"""Unit and text normalization for spec comparison. Pure functions, unit-tested."""
from __future__ import annotations

import re

# canonical unit per family, with conversion factors into the canonical unit
_UNIT_FAMILIES: dict[str, tuple[str, float]] = {
    # voltage → kV
    "v": ("kv", 0.001),
    "kv": ("kv", 1.0),
    "volt": ("kv", 0.001),
    "volts": ("kv", 0.001),
    # area → sqmm
    "sqmm": ("sqmm", 1.0),
    "sq.mm": ("sqmm", 1.0),
    "sq mm": ("sqmm", 1.0),
    "mm2": ("sqmm", 1.0),
    "mm²": ("sqmm", 1.0),
    # length → m
    "m": ("m", 1.0),
    "km": ("m", 1000.0),
    "mtr": ("m", 1.0),
    "meter": ("m", 1.0),
    "metre": ("m", 1.0),
    # temperature → deg_c
    "c": ("deg_c", 1.0),
    "°c": ("deg_c", 1.0),
    "deg_c": ("deg_c", 1.0),
    "degc": ("deg_c", 1.0),
}

_NUM_RE = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*(.*?)\s*$")


def norm_text(value: str | None) -> str | None:
    """Canonical categorical form: lowercase, trimmed, collapsed whitespace/punct.
    Stray structural punctuation at the edges (extraction artifacts like
    'Aluminium},{') is stripped so it can never break a legitimate match."""
    if value is None:
        return None
    s = str(value).strip().lower()
    s = s.strip(" \t'\"{}[],;|\\")
    s = re.sub(r"[\s_]+", " ", s).strip()
    return s or None


_DUAL_VOLTAGE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)\s*(.*?)\s*$")


def canonical_number(value: str | float | None, unit: str = "") -> tuple[float, str] | None:
    """Parse a value (possibly with an embedded unit) into (number, canonical unit).

    "11 kV" -> (11.0, "kv"); "11000V" -> (11.0, "kv"); "70" + unit "sqmm" -> (70.0, "sqmm").
    Returns None when no number can be parsed.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        num, raw_unit = float(value), unit
    else:
        s = str(value)
        dual = _DUAL_VOLTAGE_RE.match(s)
        if dual:
            # voltage designation like "450/750" or "0.6/1 kV" — compare on the
            # upper (line) voltage; unitless dual designations are volts
            num = float(dual.group(2).replace(",", "."))
            raw_unit = dual.group(3) or unit or "v"
        else:
            m = _NUM_RE.match(s)
            if not m:
                return None
            num = float(m.group(1).replace(",", "."))
            raw_unit = m.group(2) or unit
    key = (raw_unit or "").strip().lower().replace(".", "").replace(" ", "")
    # try a few spellings against the family table
    for candidate in (raw_unit.strip().lower(), key):
        if candidate in _UNIT_FAMILIES:
            canon, factor = _UNIT_FAMILIES[candidate]
            return (num * factor, canon)
    return (num, key)  # unknown unit — compare as-is


def numbers_comparable(a: tuple[float, str], b: tuple[float, str]) -> bool:
    """Same canonical unit family, or one side unitless (bare number in a table)."""
    return a[1] == b[1] or a[1] == "" or b[1] == ""
