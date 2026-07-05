"""Tests du chiffrement pgcrypto (Module 12) — nécessitent PostgreSQL.

pgcrypto n'existe pas sur SQLite : ces tests sont donc SKIPPÉS par défaut. Pour les
lancer, pointer la base de test sur PostgreSQL :

  TEST_DATABASE_URL=postgresql+asyncpg://osint_user:osint2024@localhost:5432/osint_test \\
    python -m pytest tests/test_crypto.py -v
"""
import os

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.crypto import decrypt_value, enable_pgcrypto, encrypt_value
from app.db.models import Email, Person, hash_email

pytestmark = pytest.mark.skipif(
    "postgresql" not in os.getenv("TEST_DATABASE_URL", ""),
    reason="Chiffrement pgcrypto : PostgreSQL requis (TEST_DATABASE_URL=postgresql+asyncpg://…)",
)


@pytest_asyncio.fixture(autouse=True)
async def _ensure_pgcrypto():
    """Active pgcrypto sur la base de test et isole le moteur applicatif entre tests.

    encrypt_value/decrypt_value utilisent le moteur global (osint_dev, avec pool) ;
    pytest-asyncio crée une boucle d'événements par test, ce qui invalide les connexions
    poolées d'un test à l'autre. On dispose donc le moteur applicatif avant/après chaque
    test pour repartir de connexions fraîches (artefact de test uniquement).
    """
    from tests.conftest import engine_test
    from app.db.session import engine as app_engine

    await app_engine.dispose()
    async with engine_test.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    yield
    await app_engine.dispose()


# =============================================================
# Fonctions crypto (app/db/crypto.py)
# =============================================================

@pytest.mark.asyncio
async def test_enable_pgcrypto_extension_active():
    await enable_pgcrypto()
    from app.db.session import engine
    async with engine.connect() as conn:
        active = await conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname='pgcrypto'"))
    assert active == 1


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip():
    enc = await encrypt_value("données sensibles")
    assert "données" not in enc                      # chiffré (aucun clair)
    assert await decrypt_value(enc) == "données sensibles"


@pytest.mark.asyncio
async def test_decrypt_wrong_key_raises():
    enc = await encrypt_value("secret", key="bonne-cle")
    with pytest.raises(Exception):
        await decrypt_value(enc, key="mauvaise-cle")


# =============================================================
# TypeDecorator — chiffrement transparent via l'ORM
# =============================================================

@pytest.mark.asyncio
async def test_email_encrypted_at_rest_decrypted_via_orm():
    from tests.conftest import engine_test, session_test
    async with session_test() as s:
        s.add(Email(email="secret@example.com", source="test"))
        await s.commit()

    # Lecture via l'ORM → déchiffrée (column_expression)
    async with session_test() as s:
        e = (await s.execute(select(Email).where(Email.source == "test"))).scalar_one()
        assert e.email == "secret@example.com"
        assert e.email_hash == hash_email("secret@example.com")
        eid = e.id

    # Lecture brute → chiffrée (base64), déchiffrable uniquement avec la clé
    async with engine_test.connect() as conn:
        raw = await conn.scalar(text("SELECT email FROM emails WHERE id=:i"), {"i": eid})
        assert "secret@example.com" not in raw
        dec = await conn.scalar(
            text("SELECT pgp_sym_decrypt(decode(email, 'base64'), :k) FROM emails WHERE id=:i"),
            {"k": get_settings().DB_ENCRYPTION_KEY, "i": eid},
        )
        assert dec == "secret@example.com"


@pytest.mark.asyncio
async def test_email_decrypted_in_api(client, auth_headers):
    from tests.conftest import session_test
    async with session_test() as s:
        s.add(Email(email="api@example.com", source="apitest"))
        await s.commit()
    resp = await client.get("/api/emails", headers=auth_headers)
    assert resp.status_code == 200
    assert "api@example.com" in [e["email"] for e in resp.json()]  # déchiffré dans la réponse


@pytest.mark.asyncio
async def test_notes_encrypted_at_rest():
    from tests.conftest import engine_test, session_test
    async with session_test() as s:
        p = Person(first_name="A", last_name="B", notes="note confidentielle")
        s.add(p)
        await s.commit()
        pid = p.id

    async with session_test() as s:
        assert (await s.get(Person, pid)).notes == "note confidentielle"  # ORM déchiffre

    async with engine_test.connect() as conn:
        raw = await conn.scalar(text("SELECT notes FROM persons WHERE id=:i"), {"i": pid})
    assert raw is not None and "confidentielle" not in raw               # chiffré au repos


# =============================================================
# Recherche par hash (l'email en clair n'est jamais requêté)
# =============================================================

@pytest.mark.asyncio
async def test_hash_email_migration_matches_validates():
    """Le hash SQL (comme dans la migration) == hash Python (@validates) → recherche cohérente
    entre données migrées et nouvelles données."""
    from tests.conftest import engine_test
    email = "Jean.Dupont@Example.COM"
    async with engine_test.connect() as conn:
        sql_hash = await conn.scalar(
            text("SELECT encode(digest(lower(trim(:e)), 'sha256'), 'hex')"), {"e": email}
        )
    assert sql_hash == hash_email(email)


@pytest.mark.asyncio
async def test_find_email_via_hash():
    from tests.conftest import session_test
    from app.osint.correlator import _find_email
    async with session_test() as s:
        s.add(Email(email="findme@example.com", source="t"))
        await s.commit()

    async with session_test() as s:
        found = await _find_email(s, "findme@example.com")
        assert found is not None and found.email == "findme@example.com"
        # normalisation casse/espaces via le hash
        assert await _find_email(s, "  FINDME@Example.com  ") is not None
        assert await _find_email(s, "absent@example.com") is None
