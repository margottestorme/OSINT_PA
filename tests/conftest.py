import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.db.session import Base, get_db
from app.db.models import User
from app.core.security import create_access_token, hash_password
from app.main import app

# Base de test : SQLite en mémoire par défaut (rapide, isolée). Peut être pointée
# vers PostgreSQL via TEST_DATABASE_URL pour valider la compatibilité PG.
#   ex: TEST_DATABASE_URL=postgresql+asyncpg://osint_user:<password>@localhost:5432/osint_test
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite://")

if TEST_DB_URL.startswith("sqlite"):
    # SQLite en mémoire : pool par défaut (une seule connexion partagée) — indispensable,
    # car avec NullPool chaque nouvelle connexion serait une base vide distincte.
    engine_test = create_async_engine(TEST_DB_URL, echo=False)
else:
    # PostgreSQL/asyncpg : NullPool évite le partage de connexions entre boucles
    # d'événements (pytest-asyncio crée une boucle par test) qui casserait le loop.
    engine_test = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)

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


# Construit le stack de middlewares une fois et récupère l'instance du rate-limiter
# global, afin de pouvoir réinitialiser son compteur entre les tests (sinon les
# requêtes s'accumulent par IP et finissent par déclencher des 429 parasites).
app.middleware_stack = app.build_middleware_stack()


def _find_global_limiter():
    from app.api.middleware import GlobalRateLimitMiddleware
    mw = app.middleware_stack
    while mw is not None:
        if isinstance(mw, GlobalRateLimitMiddleware):
            return mw
        mw = getattr(mw, "app", None)
    return None


global_limiter = _find_global_limiter()


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
    """Réinitialise les rate limiters API et le rate-limiter global entre chaque test."""
    from app.api.rate_limit import search_rate_limiter, login_rate_limiter
    search_rate_limiter._requests.clear()
    login_rate_limiter._requests.clear()
    if global_limiter is not None:
        global_limiter._requests.clear()
    yield
    search_rate_limiter._requests.clear()
    login_rate_limiter._requests.clear()
    if global_limiter is not None:
        global_limiter._requests.clear()


@pytest_asyncio.fixture(autouse=True)
def _mock_background_search():
    """Remplace _run_search par un no-op pour éviter les appels réseau dans les tests."""
    async def noop(query, query_type):
        pass
    with patch("app.api.routes._run_search", side_effect=noop):
        yield


@pytest_asyncio.fixture(autouse=True)
def _mock_background_enrich():
    """Remplace _background_enrich par un no-op (T6) — symétrique à _run_search.

    Évite qu'un test de match facial déclenche une vraie tâche de fond
    enrich_person -> correlate() (réseau réel + rate-limiter 2s).
    """
    async def noop(person_id):
        pass
    with patch("app.api.face_routes._background_enrich", side_effect=noop):
        yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_headers():
    """Headers avec un token JWT valide de rôle admin (accès à tous les endpoints)."""
    token = create_access_token({"sub": "testuser", "role": "admin"}, expires_minutes=15)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def analyst_headers():
    """Headers avec un token JWT valide de rôle analyst (accès restreint)."""
    token = create_access_token({"sub": "analyst1", "role": "analyst"}, expires_minutes=15)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def expired_headers():
    """Retourne des headers avec un token JWT expiré."""
    token = create_access_token({"sub": "testuser", "role": "admin"}, expires_minutes=-1)
    return {"Authorization": f"Bearer {token}"}
