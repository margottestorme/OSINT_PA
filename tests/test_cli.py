import sys

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import app.cli as cli
from app.db.session import Base
from app.db.models import User


@pytest_asyncio.fixture
async def cli_db(tmp_path, monkeypatch):
    """Redirige le CLI vers une DB SQLite fichier isolée, schéma déjà créé (simule alembic)."""
    db_file = tmp_path / "cli_test.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    test_engine = create_async_engine(url)
    # Le CLI ne crée plus les tables (m10) : on simule la migration appliquée
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    test_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(cli, "engine", test_engine)
    monkeypatch.setattr(cli, "async_session", test_session)
    yield test_session
    await test_engine.dispose()


# =============================================================
# Création nominale
# =============================================================

@pytest.mark.asyncio
async def test_cli_create_user_nominal(cli_db):
    await cli.create_user("newadmin", "validpass123", "admin")

    async with cli_db() as db:
        user = (await db.execute(select(User).where(User.username == "newadmin"))).scalar_one()
        assert user.role == "admin"
        assert user.is_active
        # Le mot de passe est haché, jamais stocké en clair
        assert user.hashed_password != "validpass123"
        assert user.hashed_password.startswith("$2b$")


# =============================================================
# Doublon — même username
# =============================================================

@pytest.mark.asyncio
async def test_cli_create_user_duplicate(cli_db, capsys):
    await cli.create_user("dupe", "validpass123", "analyst")

    with pytest.raises(SystemExit) as exc:
        await cli.create_user("dupe", "validpass123", "analyst")

    assert exc.value.code == 1
    assert "existe déjà" in capsys.readouterr().out


# =============================================================
# Mot de passe trop court (< 6) — via main()
# =============================================================

def test_cli_short_password(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv",
        ["cli", "create-user", "--username", "bob", "--password", "abc"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "6 caractères" in capsys.readouterr().out


# =============================================================
# Rôle invalide — rejeté par argparse (choices)
# =============================================================

def test_cli_invalid_role(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv",
        ["cli", "create-user", "--username", "bob", "--password", "validpass123", "--role", "superuser"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()

    # argparse sort avec le code 2 pour une valeur hors choices
    assert exc.value.code == 2
    assert "superuser" in capsys.readouterr().err
