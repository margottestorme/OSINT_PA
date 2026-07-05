"""
Chiffrement des données au repos via pgcrypto (Module 12).

pgcrypto est une extension PostgreSQL : `pgp_sym_encrypt`/`pgp_sym_decrypt` chiffrent
symétriquement avec une clé secrète (DB_ENCRYPTION_KEY, lue depuis `.env`). La valeur
chiffrée (bytea) est encodée en base64 pour tenir dans une colonne TEXT — d'où un
contrat `str` → `str` pratique côté modèles (Module 12 / ÉTAPE 2).

Confidentialité (contrainte CDC) : la clé n'est JAMAIS journalisée ni renvoyée par
l'API — elle ne transite que via les paramètres liés des requêtes SQL.

⚠️ PostgreSQL uniquement : ces fonctions échouent sur SQLite (pgcrypto absent).
"""
from loguru import logger
from sqlalchemy import Text, bindparam, func, text
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings
from app.db.session import engine


def _encryption_key(key: str | None = None) -> str:
    """Clé de chiffrement : argument explicite, sinon DB_ENCRYPTION_KEY du `.env`.

    Jamais journalisée ni exposée.
    """
    return key if key is not None else get_settings().DB_ENCRYPTION_KEY


async def enable_pgcrypto() -> None:
    """Active l'extension pgcrypto (idempotent). PostgreSQL uniquement."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    logger.info("[crypto] Extension pgcrypto activée")  # aucune clé loggée


async def encrypt_value(value: str, key: str | None = None) -> str:
    """Chiffre `value` (pgp_sym_encrypt) et renvoie une chaîne base64 stockable en TEXT."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT encode(pgp_sym_encrypt(:v, :k), 'base64')"),
            {"v": value, "k": _encryption_key(key)},
        )
        return result.scalar_one()


async def decrypt_value(encrypted: str, key: str | None = None) -> str:
    """Déchiffre une valeur base64 produite par `encrypt_value` → texte clair."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT pgp_sym_decrypt(decode(:e, 'base64'), :k)"),
            {"e": encrypted, "k": _encryption_key(key)},
        )
        return result.scalar_one()


def _key_param():
    """Clé de chiffrement passée en paramètre lié (jamais inlinée dans le SQL compilé)."""
    return bindparam(None, get_settings().DB_ENCRYPTION_KEY)


class _PgpText(TypeDecorator):
    """Implémentation PostgreSQL de `EncryptedText` : la colonne TEXT stocke le chiffré
    base64, et le (dé)chiffrement pgcrypto est réalisé DANS le SQL via bind/column
    expression — totalement transparent pour l'ORM (on lit/écrit du texte clair côté
    Python). La clé ne transite que comme paramètre lié.
    """
    impl = Text
    cache_ok = True

    def bind_expression(self, bindvalue):
        # Écriture : encode(pgp_sym_encrypt(:val, :key), 'base64')
        return func.encode(func.pgp_sym_encrypt(bindvalue, _key_param()), "base64")

    def column_expression(self, col):
        # Lecture : pgp_sym_decrypt(decode(col, 'base64'), :key)
        return func.pgp_sym_decrypt(func.decode(col, "base64"), _key_param())


# Type dialect-aware (Option A) : chiffré via pgcrypto sur PostgreSQL, colonne TEXT en
# clair sur les autres dialectes (SQLite en tests) → aucune dépendance pgcrypto en test.
EncryptedText = Text().with_variant(_PgpText(), "postgresql")
