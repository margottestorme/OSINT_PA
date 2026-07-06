"""Ajout des tables username_searches et username_results (Module 15 — énumération de pseudos)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "username_searches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(30), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("total_platforms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_username_searches_username", "username_searches", ["username"])

    op.create_table(
        "username_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("search_id", sa.Integer(),
                  sa.ForeignKey("username_searches.id"), nullable=False),
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("found", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("response_time_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.String(100), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_username_results_search_id", "username_results", ["search_id"])


def downgrade() -> None:
    op.drop_index("ix_username_results_search_id")
    op.drop_table("username_results")
    op.drop_index("ix_username_searches_username")
    op.drop_table("username_searches")
