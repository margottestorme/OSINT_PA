import io
from unittest.mock import patch

import pytest
from jose import jwt
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AuditLog, User
from app.core.security import hash_password


FAKE_EMBEDDING = [0.1] * 128
FAKE_PROCESS_RESULT = {
    "embedding": FAKE_EMBEDDING,
    "faces_count": 1,
    "hash": "abc123",
}


def _make_jpeg() -> bytes:
    img = Image.new("RGB", (100, 100), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# =============================================================
# 1. Multi-utilisateurs
# =============================================================

@pytest.mark.asyncio
async def test_login_db_success(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "osint2024"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_db_wrong_password(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "wrongpass"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_db_user_inactive(client):
    from tests.conftest import session_test

    async with session_test() as db:
        user = User(
            username="inactive_user",
            hashed_password=hash_password("validpass123"),
            role="analyst",
            is_active=False,
        )
        db.add(user)
        await db.commit()

    resp = await client.post("/api/auth/login", json={"username": "inactive_user", "password": "validpass123"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_token_contains_role(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "osint2024"})
    token = resp.json()["access_token"]
    settings = get_settings()
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"


@pytest.mark.asyncio
async def test_login_updates_last_login(client):
    from tests.conftest import session_test

    await client.post("/api/auth/login", json={"username": "admin", "password": "osint2024"})

    async with session_test() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        user = result.scalar_one()
        assert user.last_login is not None


# =============================================================
# 2. Logs d'audit
# =============================================================

@pytest.mark.asyncio
async def test_audit_login_success(client):
    from tests.conftest import session_test

    await client.post("/api/auth/login", json={"username": "admin", "password": "osint2024"})

    async with session_test() as db:
        result = await db.execute(select(AuditLog).where(AuditLog.action == "login_success"))
        log = result.scalar_one()
        assert log.target == "admin"
        assert log.user_id is not None


@pytest.mark.asyncio
async def test_audit_login_failed(client):
    from tests.conftest import session_test

    await client.post("/api/auth/login", json={"username": "admin", "password": "wrongpass"})

    async with session_test() as db:
        result = await db.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
        log = result.scalar_one()
        assert log.target == "admin"
        assert log.user_id is None


@pytest.mark.asyncio
async def test_audit_face_upload(client, auth_headers):
    from tests.conftest import session_test

    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    async with session_test() as db:
        result = await db.execute(select(AuditLog).where(AuditLog.action == "face_upload"))
        log = result.scalar_one()
        assert "person_id=" in log.target
        assert "is_new=true" in log.target


@pytest.mark.asyncio
async def test_audit_search_launched(client, auth_headers):
    from tests.conftest import session_test

    await client.post(
        "/api/search",
        json={"query": "test@example.com", "query_type": "email"},
        headers=auth_headers,
    )

    async with session_test() as db:
        result = await db.execute(select(AuditLog).where(AuditLog.action == "search_launched"))
        log = result.scalar_one()
        assert "email:test@example.com" in log.target


@pytest.mark.asyncio
async def test_audit_report_viewed(client, auth_headers):
    from tests.conftest import session_test

    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        upload_resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )
    person_id = upload_resp.json()["person_id"]

    await client.get(f"/api/persons/{person_id}/report", headers=auth_headers)

    async with session_test() as db:
        result = await db.execute(select(AuditLog).where(AuditLog.action == "report_viewed"))
        log = result.scalar_one()
        assert log.target == str(person_id)


# =============================================================
# 3. En-têtes de sécurité
# =============================================================

from app.api.middleware import _CSP

EXPECTED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": _CSP,
}


@pytest.mark.asyncio
async def test_security_headers_on_health(client):
    resp = await client.get("/health")
    for header, value in EXPECTED_HEADERS.items():
        assert resp.headers.get(header) == value, f"{header} manquant ou incorrect"


@pytest.mark.asyncio
async def test_security_headers_on_login(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "osint2024"})
    for header, value in EXPECTED_HEADERS.items():
        assert resp.headers.get(header) == value, f"{header} manquant ou incorrect"


@pytest.mark.asyncio
async def test_security_headers_on_dashboard(client):
    resp = await client.get("/dashboard")
    for header, value in EXPECTED_HEADERS.items():
        assert resp.headers.get(header) == value, f"{header} manquant ou incorrect"


# =============================================================
# RBAC (S6) — endpoints sensibles réservés à admin
# =============================================================

ADMIN_ONLY_ENDPOINTS = [
    "/api/persons",
    "/api/correlations",
    "/api/emails",
    "/api/sites",
    "/api/history",
    "/api/faces/persons",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ADMIN_ONLY_ENDPOINTS)
async def test_rbac_analyst_forbidden(client, analyst_headers, endpoint):
    resp = await client.get(endpoint, headers=analyst_headers)
    assert resp.status_code == 403
    assert "privilèges" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ADMIN_ONLY_ENDPOINTS)
async def test_rbac_admin_allowed(client, auth_headers, endpoint):
    resp = await client.get(endpoint, headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rbac_analyst_can_search(client, analyst_headers):
    """L'analyst conserve l'accès à la recherche OSINT."""
    resp = await client.post(
        "/api/search",
        json={"query": "test@example.com", "query_type": "email"},
        headers=analyst_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rbac_analyst_can_upload(client, analyst_headers):
    """L'analyst conserve l'accès à l'upload facial."""
    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=analyst_headers,
        )
    assert resp.status_code == 200


# =============================================================
# 4. Rate limiting global
# =============================================================

@pytest.mark.asyncio
async def test_global_rate_limit_blocks_at_limit():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from httpx import AsyncClient, ASGITransport
    from app.api.middleware import GlobalRateLimitMiddleware

    async def ok(request):
        return PlainTextResponse("ok")

    mini_app = Starlette(routes=[Route("/ping", ok)])
    mini_app.add_middleware(GlobalRateLimitMiddleware, max_requests=5, window=60)

    transport = ASGITransport(app=mini_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for i in range(5):
            resp = await c.get("/ping")
            assert resp.status_code == 200, f"Requête {i+1} bloquée trop tôt"

        resp = await c.get("/ping")
        assert resp.status_code == 429
        assert "Trop de requêtes" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_global_rate_limit_different_ip_not_blocked():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from httpx import AsyncClient, ASGITransport
    from app.api.middleware import GlobalRateLimitMiddleware

    async def ok(request):
        return PlainTextResponse("ok")

    mini_app = Starlette(routes=[Route("/ping", ok)])
    mini_app.add_middleware(GlobalRateLimitMiddleware, max_requests=3, window=60)

    transport = ASGITransport(app=mini_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for _ in range(3):
            await c.get("/ping")

        resp = await c.get("/ping")
        assert resp.status_code == 429

        resp = await c.get("/ping", headers={"X-Forwarded-For": "10.0.0.99"})
        assert resp.status_code == 200


# =============================================================
# Les 5 en-têtes de sécurité sont présents sur une réponse 429 (régression M2)
# =============================================================

@pytest.mark.asyncio
async def test_security_headers_on_429(client):
    """Une réponse 429 du rate-limiter global doit porter les 5 en-têtes.

    C'est le test qui aurait attrapé M2 : avant correction, SecurityHeaders
    était interne à GlobalRateLimit, donc les 429 sortaient sans en-têtes.
    Exerce la vraie app (ordre réel des middlewares de main.py).
    """
    from tests.conftest import global_limiter

    assert global_limiter is not None, "rate-limiter global introuvable dans le stack"
    original_max = global_limiter.max_requests
    global_limiter.max_requests = 2
    global_limiter._requests.clear()
    try:
        last = None
        for _ in range(3):
            last = await client.get("/health")
        assert last.status_code == 429
        for header, value in EXPECTED_HEADERS.items():
            assert last.headers.get(header) == value, f"{header} manquant sur le 429"
    finally:
        global_limiter.max_requests = original_max
        global_limiter._requests.clear()
