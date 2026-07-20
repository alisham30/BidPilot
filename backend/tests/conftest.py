"""Offline test setup — SQLite, no API key, no network.

LLM calls are mocked at the app.llm boundary; fixtures live only here.
"""
import os
import tempfile

# must run before any app import so the engine binds to SQLite
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "bidpilot_test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-never-used")

import pytest  # noqa: E402

from app.db import Base, engine, SessionLocal  # noqa: E402


@pytest.fixture()
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        yield s
