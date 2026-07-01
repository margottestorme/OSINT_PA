"""Ajout d'une colonne notes (TEXT) sur persons

Revision ID: e7b5c3d2f1a4
Revises: d6a4b9c1e2f0
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "e7b5c3d2f1a4"
down_revision = "d6a4b9c1e2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("persons", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("persons", "notes")
