"""Ajout de matched_variant sur username_results (Module 15 — variantes de pseudo)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "username_results",
        sa.Column("matched_variant", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("username_results", "matched_variant")
