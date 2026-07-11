"""Ajout de la table enrichment_jobs (suivi du pipeline d'enrichissement OSINT post-facial)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "enrichment_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("triggered_by", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_enrichment_jobs_person_id", "enrichment_jobs", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_enrichment_jobs_person_id", table_name="enrichment_jobs")
    op.drop_table("enrichment_jobs")
