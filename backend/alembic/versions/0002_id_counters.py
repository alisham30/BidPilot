"""id_counters table — human-readable ID sequences (RFP-2026-0001, DRAFT-0001)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20
"""
from alembic import op

from app.db import Counter

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Counter.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Counter.__table__.drop(bind=op.get_bind(), checkfirst=True)
