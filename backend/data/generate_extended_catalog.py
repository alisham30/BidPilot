"""Generate EY_dataset_extended.csv — a catalog extension derived from the real
EY dataset.

Two sources of new rows:
1. INTERPOLATED SIZES: for each existing product family, standard cross-sections
   that fall between the family's smallest and largest listed size, priced by
   linear interpolation of that family's own price curve. No extrapolation.
2. MARINE/DEFENCE FAMILY: HFFR ship-wiring cables to IS 14855 (armoured and
   unarmoured, small sections) — demo entries with clearly-estimated prices.

⚠ All generated prices are ESTIMATES for demo/matching purposes.
   Replace with real rates before quoting. Source column marks every row.

Run:  .venv/Scripts/python data/generate_extended_catalog.py
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "datasheets" / "EY_dataset.csv"
OUT = HERE / "datasheets" / "EY_dataset_extended.csv"

STANDARD_SIZES = [1.0, 1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400]

HEADERS = ["Product Category", "Cable Type", "Voltage Grade", "Conductor Material",
           "Core Count", "Cross Section (sq.mm)", "Armouring", "Insulation Type",
           "Standard", "Price (Rupees/m)", "Source"]


def interpolate_families(rows: list[dict]) -> list[dict]:
    families: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        try:
            cs = float(r["Cross Section (sq.mm)"])
            price = float(r["Price (Rupees/m)"])
        except (ValueError, KeyError):
            continue
        key = (r["Product Category"], r["Cable Type"], r["Voltage Grade"],
               r["Conductor Material"], r["Core Count"], r["Armouring"],
               r["Insulation Type"], r["Standard"])
        families[key].append((cs, price))

    out = []
    for key, points in families.items():
        pts = sorted(set(points))
        if len(pts) < 2:
            continue
        existing = {cs for cs, _ in pts}
        lo, hi = pts[0][0], pts[-1][0]
        for size in STANDARD_SIZES:
            if size in existing or not (lo < size < hi):
                continue
            # linear interpolation between the two nearest listed sizes
            below = max(p for p in pts if p[0] < size)
            above = min(p for p in pts if p[0] > size)
            frac = (size - below[0]) / (above[0] - below[0])
            price = round(below[1] + frac * (above[1] - below[1]))
            cat, ctype, volt, cond, cores, arm, ins, std = key
            out.append(dict(zip(HEADERS, [
                cat, ctype, volt, cond, cores,
                f"{size:g}", arm, ins, std, str(price),
                "Extended: interpolated from EY price curve — verify before quoting",
            ])))
    return out


def marine_family() -> list[dict]:
    out = []
    for voltage in ["250 V", "1.1 kV"]:
        for cores in [1, 2, 3]:
            for cs in [1.0, 1.5, 2.5, 4, 6, 10, 16]:
                for armouring in ["Armoured", "Unarmoured"]:
                    base = 45 + 38 * cs                       # small Cu cable base curve
                    price = base * (1 + 0.65 * (cores - 1))   # per-core addition
                    price *= 1.25 if armouring == "Armoured" else 1.0
                    price *= 1.5                              # marine-grade factor
                    if voltage == "1.1 kV":
                        price *= 1.15
                    out.append(dict(zip(HEADERS, [
                        "Marine & Defence", "HFFR Ship Wiring Cable", voltage, "Copper",
                        str(cores), f"{cs:g}", armouring, "HFFR",
                        "IS 14855", str(round(price)),
                        "Extended: demo estimate — replace with real marine-cable rates",
                    ])))
    return out


def flat_and_wire_families() -> list[dict]:
    """Families tenders commonly ask for that the EY sheet lacks: flat PVC
    cables (IS 694, 450/750 V class) and FR single-core building wires."""
    out = []
    # Flat cables — aluminium & copper, 2/3/4 core, PVC, IS 694
    for conductor, cond_factor in [("Aluminium", 0.75), ("Copper", 1.0)]:
        for cores in [2, 3, 4]:
            for cs in [1.5, 2.5, 4, 6, 10, 16]:
                price = (30 + 30 * cs) * (1 + 0.55 * (cores - 1)) * cond_factor * 1.1
                out.append(dict(zip(HEADERS, [
                    "Flat & Building Wire", "Flat Cable", "450/750 V", conductor,
                    str(cores), f"{cs:g}", "Unarmoured", "PVC", "IS 694:2010",
                    str(round(price)),
                    "Extended: demo estimate — replace with real rates",
                ])))
    # FR PVC single-core building wires — copper, IS 694
    for cs in [0.75, 1.0, 1.5, 2.5, 4, 6]:
        price = 20 + 28 * cs
        out.append(dict(zip(HEADERS, [
            "Flat & Building Wire", "FR PVC Insulated Wire", "450/750 V", "Copper",
            "1", f"{cs:g}", "Unarmoured", "PVC", "IS 694:2010",
            str(round(price)),
            "Extended: demo estimate — replace with real rates",
        ])))
    return out


def ht_family() -> list[dict]:
    """HT XLPE power cables above the EY sheet's 3.3 kV ceiling — 6.6 to 33 kV.
    Prices scale the EY 3.3 kV armoured-aluminium curve by voltage class."""
    out = []
    voltage_factor = {"6.6 kV": 1.35, "11 kV": 1.7, "22 kV": 2.4, "33 kV": 3.2}
    for voltage, vf in voltage_factor.items():
        for conductor, cf in [("Aluminium", 1.0), ("Copper", 1.6)]:
            for cores, kf in [("1", 1.0), ("3", 2.6)]:
                for cs in [35, 50, 70, 95, 120, 150, 185, 240, 300, 400]:
                    base_33kv = 690 + 4.0 * cs   # EY 3.3 kV 1C Al curve anchor
                    price = base_33kv * vf * cf * kf
                    out.append(dict(zip(HEADERS, [
                        "HT Power", "A2XWaY" if conductor == "Aluminium" else "2XWaY",
                        voltage, conductor, cores, f"{cs:g}", "Armoured", "XLPE",
                        "IS 7098 (Part 2):2011", str(round(price)),
                        "Extended: demo estimate — replace with real rates",
                    ])))
    return out


def abc_solar_instrumentation() -> list[dict]:
    out = []
    # Aerial Bunched Cables (LT distribution, IS 14255)
    for main_cs, price in [(16, 95), (25, 120), (35, 150), (50, 190), (70, 245), (95, 310)]:
        out.append(dict(zip(HEADERS, [
            "LT Distribution", "Aerial Bunched Cable (ABC)", "1.1 kV", "Aluminium",
            "4", f"{main_cs:g}", "Unarmoured", "XLPE", "IS 14255", str(price),
            "Extended: demo estimate — replace with real rates",
        ])))
    # Solar DC cables (IS 17293, tinned copper, XLPO)
    for cs, price in [(2.5, 55), (4, 78), (6, 108), (10, 168)]:
        out.append(dict(zip(HEADERS, [
            "Solar", "Solar DC Cable", "1.5 kV", "Copper", "1", f"{cs:g}",
            "Unarmoured", "XLPO", "IS 17293", str(price),
            "Extended: demo estimate — replace with real rates",
        ])))
    # Instrumentation cables (shielded pairs, IS 1554 / BS 5308)
    for pairs in [1, 2, 5, 10]:
        for cs in [0.5, 0.75, 1.5]:
            price = (28 + 40 * cs) * (1 + 0.8 * (pairs - 1))
            out.append(dict(zip(HEADERS, [
                "Instrumentation Cable", f"Shielded Instrumentation Cable {pairs} Pair",
                "1.1 kV", "Copper", str(pairs * 2), f"{cs:g}", "Armoured", "PVC",
                "BS 5308", str(round(price)),
                "Extended: demo estimate — replace with real rates",
            ])))
    # FRLS building wires (fire-retardant low-smoke variants)
    for cs in [1.0, 1.5, 2.5, 4, 6]:
        out.append(dict(zip(HEADERS, [
            "Flat & Building Wire", "FRLS PVC Insulated Wire", "450/750 V", "Copper",
            "1", f"{cs:g}", "Unarmoured", "FRLS PVC", "IS 694:2010",
            str(round((20 + 28 * cs) * 1.15)),
            "Extended: demo estimate — replace with real rates",
        ])))
    return out


def overhead_and_special_families() -> list[dict]:
    out = []

    def row(category, ctype, voltage, conductor, cores, cs, armouring, insulation, standard, price):
        out.append(dict(zip(HEADERS, [
            category, ctype, voltage, conductor, str(cores), f"{cs:g}",
            armouring, insulation, standard, str(round(price)),
            "Extended: demo estimate — replace with real rates",
        ])))

    # Bare overhead conductors — ACSR (IS 398 Part 2) and AAAC (IS 398 Part 4)
    for name, cs, price in [("ACSR Squirrel", 20, 42), ("ACSR Weasel", 30, 58),
                            ("ACSR Rabbit", 50, 92), ("ACSR Dog", 100, 178),
                            ("ACSR Wolf", 150, 262), ("ACSR Panther", 200, 348)]:
        row("Overhead Conductor", name, "33 kV", "Aluminium", 1, cs,
            "Unarmoured", "Bare", "IS 398 (Part 2)", price)
    for cs, price in [(34, 60), (55, 95), (100, 172), (148, 255), (232, 392)]:
        row("Overhead Conductor", "AAAC", "33 kV", "Aluminium", 1, cs,
            "Unarmoured", "Bare", "IS 398 (Part 4)", price)

    # Fire survival cables (circuit integrity, BS 6387 / IS 17505)
    for cores in [2, 3, 4]:
        for cs in [1.5, 2.5, 4]:
            row("Fire Survival", "Fire Survival Cable", "1.1 kV", "Copper", cores, cs,
                "Armoured", "XLPE", "BS 6387", (95 + 52 * cs) * (1 + 0.6 * (cores - 1)))

    # Rubber trailing cables for mining / cranes (IS 9968)
    for cs, price in [(25, 520), (35, 690), (50, 930), (70, 1240), (95, 1580)]:
        row("Mining & Crane", "Rubber Trailing Cable", "1.1 kV", "Copper", 3, cs,
            "Unarmoured", "EPR", "IS 9968", price)

    # EV charging cables (IEC 62893)
    for cs, price in [(2.5, 240), (4, 320), (6, 430)]:
        row("EV Charging", "EV Charging Cable", "1 kV", "Copper", 5, cs,
            "Unarmoured", "TPU", "IEC 62893", price)

    # Multicore control cables — high core counts (IS 1554 Part 1)
    for cores in [5, 7, 10, 12, 16, 19, 24]:
        for cs in [1.5, 2.5]:
            row("LT Control", "Multicore Control Cable", "1.1 kV", "Copper", cores, cs,
                "Armoured", "PVC", "IS 1554 (Part 1):1988", (34 + 30 * cs) * (1 + 0.32 * (cores - 1)))

    # Railway signalling cables (IRS:S 63)
    for cores, price in [(2, 68), (4, 110), (6, 152), (10, 235)]:
        row("Railway Signalling", "Signalling Cable", "1.1 kV", "Copper", cores, 1.0,
            "Armoured", "PVC", "IRS:S 63", price)

    return out


def armouring_mirrors(rows: list[dict]) -> list[dict]:
    """For every real EY row, generate the missing armoured/unarmoured
    counterpart (tenders ask for both; the sheet often lists only one).
    Armoured→unarmoured priced at 0.87x, unarmoured→armoured at 1.15x."""
    seen = set()
    parsed = []
    for r in rows:
        try:
            price = float(r["Price (Rupees/m)"])
        except (ValueError, KeyError):
            continue
        key = (r["Product Category"], r["Cable Type"], r["Voltage Grade"],
               r["Conductor Material"], r["Core Count"], r["Cross Section (sq.mm)"],
               r["Armouring"], r["Insulation Type"], r["Standard"])
        seen.add(key)
        parsed.append((key, price))
    out = []
    for key, price in parsed:
        cat, ctype, volt, cond, cores, cs, arm, ins, std = key
        arm_l = (arm or "").strip().lower()
        if arm_l == "armoured":
            flipped, factor = "Unarmoured", 0.87
        elif arm_l == "unarmoured":
            flipped, factor = "Armoured", 1.15
        else:
            continue
        mirror_key = (cat, ctype, volt, cond, cores, cs, flipped, ins, std)
        if mirror_key in seen:
            continue
        seen.add(mirror_key)
        out.append(dict(zip(HEADERS, [
            cat, ctype, volt, cond, cores, cs, flipped, ins, std,
            str(round(price * factor)),
            "Extended: armouring mirror of EY row — verify before quoting",
        ])))
    return out


def lt_35_core_family() -> list[dict]:
    """3.5-core LT aluminium XLPE power cables — a staple of LT distribution
    tenders that the EY sheet lacks."""
    out = []
    for cs in [25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400]:
        out.append(dict(zip(HEADERS, [
            "LT Power", "A2XWY", "1.1 kV", "Aluminium", "3.5", f"{cs:g}",
            "Armoured", "XLPE", "IS 7098 (Part 1):1988",
            str(round(90 + 3.4 * cs)),
            "Extended: demo estimate — replace with real rates",
        ])))
    return out


def main() -> None:
    with SRC.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    generated = (interpolate_families(rows) + marine_family() + flat_and_wire_families()
                 + ht_family() + abc_solar_instrumentation() + overhead_and_special_families()
                 + armouring_mirrors(rows) + lt_35_core_family())
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(generated)
    print(f"wrote {len(generated)} extended rows -> {OUT.name}")


if __name__ == "__main__":
    main()
