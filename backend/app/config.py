"""Configuration loader — the ONLY module that touches the environment.

All sources, keywords, categories, thresholds and URLs live in config/sources.yaml.
All credentials live in .env. Everything else imports `settings` / `sources` from here.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parent.parent
SOURCES_PATH = Path(os.environ.get("BIDPILOT_SOURCES", BACKEND_DIR / "config" / "sources.yaml"))
DATA_DIR = Path(os.environ.get("BIDPILOT_DATA_DIR", BACKEND_DIR / "data"))
DATASHEETS_DIR = DATA_DIR / "datasheets"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
PDF_OUT_DIR = DATA_DIR / "bids"

load_dotenv(BACKEND_DIR / ".env")


class EmailConfig(BaseModel):
    imap_host: str = "imap.gmail.com"
    folders: list[str] = ["INBOX"]
    lookback_days: int = 90
    keywords: list[str] = []
    attachment_types: list[str] = [".pdf", ".docx", ".xlsx", ".zip"]
    max_ingests_per_poll: int = 5
    peek_newest: int = 50


class WebConfig(BaseModel):
    urls: list[str] = []
    request_timeout: int = 30


class FiltersConfig(BaseModel):
    due_within_days: int = 92
    product_categories: list[str] = []


class MatchingConfig(BaseModel):
    mto_threshold: float = 80.0
    top_k: int = 3
    shortlist_size: int = 10
    equivalences: dict[str, list[str]] = {}


class CatalogConfig(BaseModel):
    header_aliases: dict[str, list[str]] = {}
    price_headers: list[str] = ["price"]
    price_unit: str = "m"
    currency: str = "INR"
    derived_specs: dict[str, dict[str, str]] = {}


class LLMConfig(BaseModel):
    provider: str = "anthropic"  # anthropic | openai
    model: str = "claude-sonnet-5"
    openai_model: str = "gpt-4o-mini"
    max_input_chars: int = 60000
    max_retries: int = 2
    timeout_seconds: int = 180
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536


class TrackingConfig(BaseModel):
    deadline_warn_days: int = 5
    reply_poll_minutes: int = 30
    confidence_threshold: float = 0.6


class Sources(BaseModel):
    email: EmailConfig = Field(default_factory=EmailConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)


class Settings(BaseModel):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    database_url: str = "postgresql+psycopg://bidpilot:bidpilot@localhost:5433/bidpilot"
    gmail_user: str = ""
    gmail_app_password: str = ""


@lru_cache
def get_sources() -> Sources:
    raw: dict[str, Any] = {}
    if SOURCES_PATH.exists():
        raw = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8")) or {}
    return Sources.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        database_url=os.environ.get("DATABASE_URL", Settings().database_url),
        gmail_user=os.environ.get("GMAIL_USER", ""),
        gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
    )


sources = get_sources()
settings = get_settings()
