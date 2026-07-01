"""Ajout de la table search_results (persistance des recherches manuelles, Approche B)

Revision ID: d6a4b9c1e2f0
Revises: c5f3a1b2d8e9
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "d6a4b9c1e2f0"
down_revision = "c5f3a1b2d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("search_id", sa.Integer(), sa.ForeignKey("search_history.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("extracted", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_search_results_search_id", "search_results", ["search_id"])


def downgrade() -> None:
    op.drop_index("ix_search_results_search_id")
    op.drop_table("search_results")
