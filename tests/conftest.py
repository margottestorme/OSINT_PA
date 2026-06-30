import asyncio
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.db.session import Base, get_db
from app.db.models import User
from app.core.security import create_access_token, hash_password
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite://"

engine_test = create_async_engine(TEST_DB_URL, echo=False)
session_test = async_sessionmaker(engine_test, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with session_test() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise

app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Crée les tables et l'utilisateur de test avant chaque test."""
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_test() as db:
        admin = User(
            username="admin",
            hashed_password=hash_password("osint2024"),
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()

    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Réinitialise le rate limiter API entre chaque test."""
    from app.api.rate_limit import search_rate_limiter
    search_rate_limiter._requests.clear()
    yield
    search_rate_limiter._requests.clear()


@pytest_asyncio.fixture(autouse=True)
def _mock_background_search():
    """Remplace _run_search par un no-op pour éviter les appels réseau dans les tests."""
    async def noop(query, query_type):
        pass
    with patch("app.api.routes._run_search", side_effect=noop):
        yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_headers():
    """Retourne des headers avec un token JWT valide."""
    token = create_access_token({"sub": "testuser"}, expires_minutes=15)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def expired_headers():
    """Retourne des headers avec un token JWT expiré."""
    token = create_access_token({"sub": "testuser"}, expires_minutes=-1)
    return {"Authorization": f"Bearer {token}"}
