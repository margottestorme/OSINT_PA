"""Chiffrement pgcrypto des colonnes sensibles (emails.email, persons.notes) + index SHA256

Revision ID: f1a2b3c4d5e6
Revises: e7b5c3d2f1a4
Create Date: 2026-07-05

- emails.email : VARCHAR(255) -> TEXT, chiffré au repos (pgp_sym_encrypt, base64)
- emails.email_hash : nouvelle colonne SHA256(email en clair), unique -> recherche/dédup
- persons.notes : TEXT (inchangé), chiffré au repos
- Migration des données existantes (chiffrement + backfill du hash)

Les opérations pgcrypto ne s'exécutent que sur PostgreSQL (Option A) ; sur un autre
dialecte, seules les modifications de schéma sont appliquées (les données restent en clair).
"""
from alembic import op
import sqlalchemy as sa

from app.core.config import get_settings

revision = "f1a2b3c4d5e6"
down_revision = "e7b5c3d2f1a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    key = get_settings().DB_ENCRYPTION_KEY

    # Schéma : colonne de hash (nullable le temps du backfill) + email en TEXT
    op.add_column("emails", sa.Column("email_hash", sa.String(64), nullable=True))
    op.execute("ALTER TABLE emails DROP CONSTRAINT IF EXISTS emails_email_key")
    op.alter_column("emails", "email", type_=sa.Text(), existing_type=sa.String(255))

    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        # Backfill du hash (depuis le clair) ET chiffrement de l'email, en une passe :
        # les deux clauses SET lisent la valeur d'origine (avant écriture).
        bind.execute(sa.text(
            "UPDATE emails SET "
            "email_hash = encode(digest(lower(trim(email)), 'sha256'), 'hex'), "
            "email = encode(pgp_sym_encrypt(email, :k), 'base64')"
        ), {"k": key})
        bind.execute(sa.text(
            "UPDATE persons SET notes = encode(pgp_sym_encrypt(notes, :k), 'base64') "
            "WHERE notes IS NOT NULL"
        ), {"k": key})

    # Contrainte : unicité désormais portée par le hash (l'email chiffré n'est pas déterministe)
    op.alter_column("emails", "email_hash", existing_type=sa.String(64), nullable=False)
    op.create_index("ix_emails_email_hash", "emails", ["email_hash"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    key = get_settings().DB_ENCRYPTION_KEY

    op.drop_index("ix_emails_email_hash", table_name="emails")

    if is_pg:
        # Déchiffrement inverse tant que la colonne est encore TEXT
        bind.execute(sa.text(
            "UPDATE emails SET email = pgp_sym_decrypt(decode(email, 'base64'), :k)"
        ), {"k": key})
        bind.execute(sa.text(
            "UPDATE persons SET notes = pgp_sym_decrypt(decode(notes, 'base64'), :k) "
            "WHERE notes IS NOT NULL"
        ), {"k": key})

    op.drop_column("emails", "email_hash")
    op.alter_column("emails", "email", type_=sa.String(255), existing_type=sa.Text())
    op.create_unique_constraint("emails_email_key", "emails", ["email"])
