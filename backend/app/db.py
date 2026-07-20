"""PostgreSQL models — registry, catalog, runs, decisions, escalations.

Every pipeline run snapshots its full state to `runs.state` (JSONB) so any
bid can be reconstructed later. Every consequential human action has a row
in `decisions`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, date, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, String, Text, create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings, sources

JSONVariant = JSON().with_variant(JSONB(), "postgresql")

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    pass


RFP_STATUSES = ["new", "extracted", "drafting", "awaiting_review", "approved", "no_bid", "submitted", "closed"]


class RFP(Base):
    __tablename__ = "rfps"

    rfp_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="")
    issuer: Mapped[str] = mapped_column(Text, default="")
    reference_no: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="email")  # email | web
    source_detail: Mapped[str] = mapped_column(Text, default="")      # sender / portal URL
    dedupe_key: Mapped[str] = mapped_column(String(80), unique=True)
    doc_paths: Mapped[list] = mapped_column(JSONVariant, default=list)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RFPDatasetRow(Base):
    __tablename__ = "rfp_datasets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rfp_id: Mapped[str] = mapped_column(ForeignKey("rfps.rfp_id"), index=True)
    line_items: Mapped[list] = mapped_column(JSONVariant, default=list)
    tests: Mapped[list] = mapped_column(JSONVariant, default=list)
    special_conditions: Mapped[list] = mapped_column(JSONVariant, default=list)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SKU(Base):
    __tablename__ = "skus"

    sku_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, default="")
    specs: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    datasheet_source: Mapped[str] = mapped_column(Text, default="")
    embedding = mapped_column(Vector(sources.llm.embedding_dim), nullable=True)


class PriceMaterial(Base):
    __tablename__ = "price_materials"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.sku_id"), index=True)
    unit: Mapped[str] = mapped_column(String(20), default="m")
    unit_price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)


class PriceService(Base):
    __tablename__ = "price_services"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    test_name: Mapped[str] = mapped_column(Text)
    standard: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="INR")


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rfp_id: Mapped[str] = mapped_column(ForeignKey("rfps.rfp_id"), index=True)
    state: Mapped[dict] = mapped_column(JSONVariant, default=dict)  # full RFPState snapshot
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | awaiting_review | failed | decided
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    actor: Mapped[str] = mapped_column(Text)  # human user id — required
    action: Mapped[str] = mapped_column(String(30))  # approve | edit | no_bid | send_followup | mark_submitted
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rfp_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_agent: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low | medium | high
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | acked | resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Followup(Base):
    __tablename__ = "followups"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rfp_id: Mapped[str] = mapped_column(ForeignKey("rfps.rfp_id"), index=True)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | sent | discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeenEmail(Base):
    """Message-IDs already evaluated, so re-polls never reprocess mail."""
    __tablename__ = "seen_emails"

    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ingested: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
