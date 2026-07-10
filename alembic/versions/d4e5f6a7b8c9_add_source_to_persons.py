"""Ajout de source sur persons (reconnaissance faciale — jeu de données académique LFW)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "persons",
        sa.Column("source", sa.String(20), nullable=False, server_default="investigation"),
    )


def downgrade() -> None:
    op.drop_column("persons", "source")
