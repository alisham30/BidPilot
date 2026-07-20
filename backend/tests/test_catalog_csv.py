"""CSV datasheet ingestion must land on the same canonical spec names the RFP
extractor emits — that alignment is what makes scorer lookups work."""
from pathlib import Path

from sqlalchemy import select

from app.dataset.catalog import _ingest_csv, _ingest_service_prices
from app.db import SKU, PriceMaterial, PriceService

EY_STYLE = """Product Category,Cable Type,Voltage Grade,Conductor Material,Core Count,Cross Section (sq.mm),Armouring,Insulation Type,Standard,Price (Rupees/m),Source
HT Power,A2XWaY,3.3 kV,Aluminium,1,95,Armoured,XLPE,IS 7098 (Part 2):2011,1066,HT Cables
"""

SERVICES = """test_name,standard,price,currency
High voltage test,IS 7098,15000,INR
"""


def test_ey_headers_map_to_canonical_specs(session, tmp_path: Path):
    csv_path = tmp_path / "ey.csv"
    csv_path.write_text(EY_STYLE, encoding="utf-8")
    count = _ingest_csv(session, csv_path)
    session.commit()
    assert count == 1

    sku = session.scalars(select(SKU)).one()
    assert sku.category == "HT Power"
    assert sku.specs["voltage_grade"] == "3.3 kV"
    assert sku.specs["cross_section_sqmm"] == "95"
    assert sku.specs["conductor_material"] == "Aluminium"
    assert "source" not in sku.specs           # metadata never becomes a spec
    assert "price_rupees_m" not in sku.specs   # price never becomes a spec
    assert sku.specs["temp_rating"] == "90 deg_c"   # derived from XLPE insulation
    assert sku.specs["sheath_type"] == "PVC ST2"

    price = session.scalars(select(PriceMaterial)).one()
    assert price.sku_id == sku.sku_id
    assert price.unit_price == 1066.0
    assert price.unit == "m"


def test_service_price_table_ingestion(session, tmp_path: Path):
    p = tmp_path / "price_services.csv"
    p.write_text(SERVICES, encoding="utf-8")
    count = _ingest_service_prices(session, p)
    session.commit()
    assert count == 1
    row = session.scalars(select(PriceService)).one()
    assert row.test_name == "High voltage test"
    assert row.price == 15000.0
