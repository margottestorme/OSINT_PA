import pytest
from unittest.mock import patch, AsyncMock

from app.db.models import Person, Correlation, SearchHistory


# =============================================================
# GET /health — public, pas de JWT
# =============================================================

@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_health_no_auth_required(client):
    """Le healthcheck ne doit pas exiger de JWT."""
    resp = await client.get("/health")
    assert resp.status_code == 200


# =============================================================
# POST /api/auth/login
# =============================================================

@pytest.mark.asyncio
async def test_login_success(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "osint2024"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "wrongpass"})
    assert resp.status_code == 401
    assert "Identifiants invalides" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_unknown_user(client):
    resp = await client.post("/api/auth/login", json={"username": "nobody", "password": "whatever1"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_validation_short_username(client):
    """Username trop court → erreur de validation Pydantic."""
    resp = await client.post("/api/auth/login", json={"username": "ab", "password": "validpass"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_validation_short_password(client):
    """Mot de passe trop court → erreur de validation Pydantic."""
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "short"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_validation_missing_fields(client):
    resp = await client.post("/api/auth/login", json={})
    assert resp.status_code == 422


# =============================================================
# POST /api/search — JWT requis + rate limiting
# =============================================================

@pytest.mark.asyncio
async def test_search_requires_auth(client):
    resp = await client.post("/api/search", json={"query": "test@test.com", "query_type": "email"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_search_with_expired_token(client, expired_headers):
    resp = await client.post(
        "/api/search",
        json={"query": "test@test.com", "query_type": "email"},
        headers=expired_headers,
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_with_invalid_token(client):
    headers = {"Authorization": "Bearer fakeinvalidtoken123"}
    resp = await client.post(
        "/api/search",
        json={"query": "test@test.com", "query_type": "email"},
        headers=headers,
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_success(client, auth_headers):
    resp = await client.post(
        "/api/search",
        json={"query": "test@example.com", "query_type": "email"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "test@example.com"
    assert data["query_type"] == "email"
    assert "message" in data


@pytest.mark.asyncio
async def test_search_domain_type(client, auth_headers):
    resp = await client.post(
        "/api/search",
        json={"query": "example.com", "query_type": "domain"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["query_type"] == "domain"


@pytest.mark.asyncio
async def test_search_name_type(client, auth_headers):
    resp = await client.post(
        "/api/search",
        json={"query": "Jean Dupont", "query_type": "name"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["query_type"] == "name"


@pytest.mark.asyncio
async def test_search_invalid_query_type(client, auth_headers):
    """Un query_type non autorisé doit être rejeté par l'enum Pydantic."""
    resp = await client.post(
        "/api/search",
        json={"query": "test", "query_type": "sql_injection"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_empty_query(client, auth_headers):
    resp = await client.post(
        "/api/search",
        json={"query": "", "query_type": "email"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_missing_body(client, auth_headers):
    resp = await client.post("/api/search", headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_rate_limit(client, auth_headers):
    """Vérifie que le rate limiter bloque après 10 requêtes."""
    for i in range(10):
        resp = await client.post(
            "/api/search",
            json={"query": f"test{i}@example.com", "query_type": "email"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Requête {i+1} devrait passer"

    # La 11ème doit être bloquée
    resp = await client.post(
        "/api/search",
        json={"query": "overflow@example.com", "query_type": "email"},
        headers=auth_headers,
    )
    assert resp.status_code == 429


# =============================================================
# GET /api/correlations — JWT requis + pagination
# =============================================================

@pytest.mark.asyncio
async def test_correlations_requires_auth(client):
    resp = await client.get("/api/correlations")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_correlations_empty(client, auth_headers):
    resp = await client.get("/api/correlations", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_correlations_pagination_params(client, auth_headers):
    resp = await client.get("/api/correlations?limit=5&offset=0", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_correlations_invalid_limit(client, auth_headers):
    resp = await client.get("/api/correlations?limit=0", headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_correlations_invalid_offset(client, auth_headers):
    resp = await client.get("/api/correlations?offset=-1", headers=auth_headers)
    assert resp.status_code == 422


# =============================================================
# GET /api/persons — JWT requis + pagination
# =============================================================

@pytest.mark.asyncio
async def test_persons_requires_auth(client):
    resp = await client.get("/api/persons")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_persons_empty(client, auth_headers):
    resp = await client.get("/api/persons", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_persons_pagination(client, auth_headers):
    resp = await client.get("/api/persons?limit=10&offset=0", headers=auth_headers)
    assert resp.status_code == 200


# =============================================================
# GET /api/history — JWT requis + pagination
# =============================================================

@pytest.mark.asyncio
async def test_history_requires_auth(client):
    resp = await client.get("/api/history")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_history_empty(client, auth_headers):
    resp = await client.get("/api/history", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_history_pagination(client, auth_headers):
    resp = await client.get("/api/history?limit=5&offset=0", headers=auth_headers)
    assert resp.status_code == 200
