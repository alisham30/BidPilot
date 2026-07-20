"""Catalog builder — product data enters ONLY through this datasheet path.

data/datasheets/*.csv       structured datasheets → parsed deterministically
data/datasheets/*.pdf|docx  unstructured datasheets → Claude extraction (DatasheetSKUs)
data/datasheets/price_services.csv   test/acceptance price table (test_name,standard,price[,currency])

Prices found on datasheets populate price_materials; nothing is ever guessed.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .. import llm
from ..config import DATASHEETS_DIR, sources
from ..db import SKU, PriceMaterial, PriceService, new_id
from ..ingestion.docparse import clean_text, extract_file
from ..matching.normalize import norm_text
from ..schemas import DatasheetSKUs
from ..tracking.escalations import escalate

log = logging.getLogger("bidpilot.catalog")

PDF_SYSTEM = """You extract product SKUs from a wires & cables datasheet.
Each distinct product variant (unique combination of construction parameters) is one SKU.
Use canonical snake_case spec names: voltage_grade, conductor_material, core_count,
cross_section_sqmm, insulation_type, armouring, standard, cable_type, temp_rating.
Only extract what the datasheet states; leave unit_price null unless a price is printed."""


def _slug(header: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", header.strip().lower())).strip("_")


def _canonical_header(header: str) -> str | None:
    """Resolve a CSV header to a canonical spec name via config aliases, else slug."""
    h = norm_text(header) or ""
    for canonical, aliases in sources.catalog.header_aliases.items():
        if h == canonical or h in [norm_text(a) for a in aliases]:
            return canonical
    return _slug(header) or None


def _is_price_header(header: str) -> bool:
    h = norm_text(header) or ""
    return h in [norm_text(p) for p in sources.catalog.price_headers]


def _stable_sku_id(basis: str) -> str:
    return "SKU-" + hashlib.sha1(basis.encode()).hexdigest()[:10].upper()


def _apply_derived(specs: dict[str, str]) -> None:
    """Fill standard insulation-implied properties (temp rating, sheath) from
    config when the datasheet doesn't state them — never overwrites."""
    ins = norm_text(specs.get("insulation_type") or "") or ""
    for key, value in sources.catalog.derived_specs.get(ins, {}).items():
        specs.setdefault(key, value)


def _sku_text(name: str, category: str, specs: dict) -> str:
    parts = [name, category] + [f"{k}: {v}" for k, v in sorted(specs.items())]
    return " | ".join(p for p in parts if p)


def _ingest_csv(session: Session, path: Path) -> int:
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            specs: dict[str, str] = {}
            price: float | None = None
            category = ""
            for header, value in row.items():
                if header is None or value is None or str(value).strip() == "":
                    continue
                value = str(value).strip()
                if _is_price_header(header):
                    try:
                        price = float(value.replace(",", ""))
                    except ValueError:
                        pass
                    continue
                canon = _canonical_header(header)
                if canon in (None, "source"):
                    continue
                if canon == "product_category":
                    category = value
                    continue
                specs[canon] = value

            if not specs:
                continue
            _apply_derived(specs)
            name_bits = [category, specs.get("cable_type", ""),
                         specs.get("core_count", ""), specs.get("cross_section_sqmm", ""),
                         specs.get("voltage_grade", "")]
            name = " ".join(b for b in name_bits if b) or f"{path.stem} row {count + 1}"
            sku_id = _stable_sku_id(f"{category}|{sorted(specs.items())}")
            session.merge(SKU(sku_id=sku_id, name=name, category=category,
                              specs=specs, datasheet_source=path.name))
            session.flush()  # SKU row must exist before its price row (FK)
            if price is not None:
                session.add(PriceMaterial(id=new_id("pm"), sku_id=sku_id,
                                          unit=sources.catalog.price_unit,
                                          unit_price=price, currency=sources.catalog.currency))
            count += 1
    return count


def _ingest_unstructured(session: Session, path: Path) -> int:
    text = clean_text(extract_file(path))
    if not text.strip():
        escalate(session, "catalog_builder", f"unreadable datasheet {path.name}")
        return 0
    try:
        extracted = llm.extract(DatasheetSKUs, PDF_SYSTEM, text)
    except llm.LLMError as e:
        escalate(session, "catalog_builder", f"datasheet extraction failed for {path.name}: {e}")
        return 0
    count = 0
    for sku in extracted.skus:
        specs = {kv.name: kv.value for kv in sku.specs}
        if not specs:
            continue
        _apply_derived(specs)
        sku_id = _stable_sku_id(f"{sku.category}|{sorted(specs.items())}")
        session.merge(SKU(sku_id=sku_id, name=sku.name, category=sku.category,
                          specs=specs, datasheet_source=path.name))
        session.flush()  # SKU row must exist before its price row (FK)
        if sku.unit_price is not None:
            session.add(PriceMaterial(id=new_id("pm"), sku_id=sku_id,
                                      unit=sku.price_unit or sources.catalog.price_unit,
                                      unit_price=sku.unit_price, currency=sources.catalog.currency))
        count += 1
    return count


def _ingest_service_prices(session: Session, path: Path) -> int:
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            norm = {(_slug(k or "")): (v or "").strip() for k, v in row.items()}
            name = norm.get("test_name") or norm.get("test") or norm.get("name")
            raw_price = norm.get("price", "")
            if not name or not raw_price:
                continue
            try:
                price = float(raw_price.replace(",", ""))
            except ValueError:
                continue
            session.add(PriceService(id=new_id("ps"), test_name=name,
                                     standard=norm.get("standard", ""), price=price,
                                     currency=norm.get("currency") or sources.catalog.currency))
            count += 1
    return count


def rebuild_catalog(session: Session) -> dict:
    """Full re-ingest of data/datasheets/ → skus, price_materials, price_services."""
    DATASHEETS_DIR.mkdir(parents=True, exist_ok=True)
    session.execute(delete(PriceMaterial))
    session.execute(delete(PriceService))
    session.execute(delete(SKU))
    session.commit()

    stats = {"files": 0, "skus": 0, "service_prices": 0}
    for path in sorted(DATASHEETS_DIR.iterdir()):
        if path.is_dir():
            continue
        stats["files"] += 1
        if path.name.lower() == "price_services.csv":
            stats["service_prices"] += _ingest_service_prices(session, path)
        elif path.suffix.lower() == ".csv":
            stats["skus"] += _ingest_csv(session, path)
        elif path.suffix.lower() in (".pdf", ".docx", ".xlsx"):
            stats["skus"] += _ingest_unstructured(session, path)
    session.commit()

    if stats["skus"] == 0:
        escalate(session, "catalog_builder", "catalog rebuild produced zero SKUs — drop datasheets into data/datasheets/",
                 severity="high")
        return stats

    _embed_catalog(session)
    log.info("catalog rebuilt: %s", stats)
    return stats


def _embed_catalog(session: Session) -> None:
    skus = session.query(SKU).all()
    texts = [_sku_text(s.name, s.category, s.specs) for s in skus]
    try:
        batch = 128
        for i in range(0, len(skus), batch):
            vectors = llm.embed(texts[i:i + batch])
            for sku, vec in zip(skus[i:i + batch], vectors):
                sku.embedding = vec
        session.commit()
    except Exception as e:
        session.rollback()
        escalate(session, "catalog_builder",
                 f"embedding failed ({e}) — shortlisting will fall back to full catalog scan")
