"""Ajout d'une colonne JSON data sur correlations (métadonnées sources : IPs, etc.)

Revision ID: c5f3a1b2d8e9
Revises: b4e2f3a5c6d7
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "c5f3a1b2d8e9"
down_revision = "b4e2f3a5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("correlations", sa.Column("data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("correlations", "data")
