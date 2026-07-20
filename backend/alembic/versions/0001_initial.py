"""initial schema — pgvector extension + all tables from app.db metadata

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""
from alembic import op

from app.db import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
