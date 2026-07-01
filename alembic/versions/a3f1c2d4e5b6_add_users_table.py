"""Ajout de la table users pour la gestion multi-utilisateurs

Revision ID: a3f1c2d4e5b6
Revises: d926ec61bde4
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa

revision = "a3f1c2d4e5b6"
down_revision = "d926ec61bde4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="analyst"),
        # server_default portable : "true" est accepté par PostgreSQL ET SQLite
        # (PostgreSQL rejette un défaut entier "1" sur une colonne booléenne).
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("users")
