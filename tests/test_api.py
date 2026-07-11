import pytest


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
# POST /api/persons/{id}/emails — rattachement manuel d'un email
# =============================================================

async def _seed_person(source="investigation"):
    from tests.conftest import session_test
    from app.db.models import Person
    async with session_test() as db:
        p = Person(first_name="Jean", last_name="Dupont", confidence_score=1.0, source=source)
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p.id


@pytest.mark.asyncio
async def test_add_email_requires_auth(client):
    resp = await client.post("/api/persons/1/emails", json={"email": "a@b.com"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_add_email_person_not_found(client, auth_headers):
    resp = await client.post("/api/persons/9999/emails", json={"email": "a@b.com"}, headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_email_invalid_format(client, auth_headers):
    pid = await _seed_person()
    resp = await client.post(f"/api/persons/{pid}/emails", json={"email": "pas-un-email"}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_add_email_success(client, auth_headers):
    pid = await _seed_person()
    resp = await client.post(
        f"/api/persons/{pid}/emails", json={"email": "Jean.Dupont@Example.com"}, headers=auth_headers
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "jean.dupont@example.com"  # normalisé en minuscules
    assert data["source"] == "manual"
    assert data["person_id"] == pid


@pytest.mark.asyncio
async def test_add_email_idempotent_same_person(client, auth_headers):
    pid = await _seed_person()
    r1 = await client.post(f"/api/persons/{pid}/emails", json={"email": "a@b.com"}, headers=auth_headers)
    r2 = await client.post(f"/api/persons/{pid}/emails", json={"email": "a@b.com"}, headers=auth_headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_add_email_conflict_different_person(client, auth_headers):
    pid1 = await _seed_person()
    pid2 = await _seed_person()
    await client.post(f"/api/persons/{pid1}/emails", json={"email": "a@b.com"}, headers=auth_headers)
    resp = await client.post(f"/api/persons/{pid2}/emails", json={"email": "a@b.com"}, headers=auth_headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_add_email_refused_on_lfw_dataset_person(client, auth_headers):
    pid = await _seed_person(source="lfw_dataset")
    resp = await client.post(f"/api/persons/{pid}/emails", json={"email": "a@b.com"}, headers=auth_headers)
    assert resp.status_code == 400


# =============================================================
# POST /api/persons/{id}/enrich — lancement manuel du pivot OSINT
# =============================================================

@pytest.mark.asyncio
async def test_enrich_requires_auth(client):
    resp = await client.post("/api/persons/1/enrich")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_enrich_person_not_found(client, auth_headers):
    resp = await client.post("/api/persons/9999/enrich", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_enrich_no_email_still_launches(client, auth_headers):
    # Le pipeline amorce la recherche par le NOM : un sujet d'enquête nommé n'a plus besoin
    # d'un email préalable (changement de contrat vs l'ancien pivot email-only).
    from unittest.mock import patch, ANY
    pid = await _seed_person()  # Jean Dupont, source=investigation, sans email
    with patch("app.api.routes._run_enrichment") as mock_enrich:
        resp = await client.post(f"/api/persons/{pid}/enrich", headers=auth_headers)
    assert resp.status_code == 200
    mock_enrich.assert_called_once_with(pid, ANY)  # (person_id, job_id)


@pytest.mark.asyncio
async def test_enrich_refused_on_lfw_dataset_person(client, auth_headers):
    pid = await _seed_person(source="lfw_dataset")
    resp = await client.post(f"/api/persons/{pid}/enrich", headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_enrich_refused_on_consented_demo_person(client, auth_headers):
    # Garde-fou central : un sujet consentant de démo n'est jamais profilé.
    pid = await _seed_person(source="consented_demo")
    resp = await client.post(f"/api/persons/{pid}/enrich", headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_enrich_refused_on_unknown_person(client, auth_headers):
    # Personne non identifiée (nom = "Inconnu") : rien à chercher, refus.
    from tests.conftest import session_test
    from app.db.models import Person
    async with session_test() as db:
        p = Person(first_name="Inconnu", last_name="#abcd1234",
                   confidence_score=1.0, source="investigation")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        pid = p.id
    resp = await client.post(f"/api/persons/{pid}/enrich", headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_enrich_launched_in_background(client, auth_headers):
    from unittest.mock import patch, ANY
    pid = await _seed_person()
    await client.post(f"/api/persons/{pid}/emails", json={"email": "a@b.com"}, headers=auth_headers)

    with patch("app.api.routes._run_enrichment") as mock_enrich:
        resp = await client.post(f"/api/persons/{pid}/enrich", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["person_id"] == pid
    mock_enrich.assert_called_once_with(pid, ANY)  # (person_id, job_id)


# =============================================================
# EnrichmentJob — endpoint de statut + cycle de vie (Étapes 3+4)
# =============================================================

@pytest.mark.asyncio
async def test_enrichment_status_404_when_no_job(client, auth_headers):
    pid = await _seed_person()
    resp = await client.get(f"/api/persons/{pid}/enrichment_status", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_enrich_creates_pending_job(client, auth_headers):
    from unittest.mock import patch
    pid = await _seed_person()
    with patch("app.api.routes._run_enrichment"):  # ne pas exécuter le pipeline réel
        resp = await client.post(f"/api/persons/{pid}/enrich", headers=auth_headers)
    assert resp.status_code == 200

    st = await client.get(f"/api/persons/{pid}/enrichment_status", headers=auth_headers)
    assert st.status_code == 200
    body = st.json()
    assert body["status"] == "pending"
    assert body["triggered_by"] == "manual"


@pytest.mark.asyncio
async def test_enrich_stream_trigger_recorded(client, auth_headers):
    from unittest.mock import patch
    pid = await _seed_person()
    with patch("app.api.routes._run_enrichment"):
        resp = await client.post(f"/api/persons/{pid}/enrich?trigger=stream", headers=auth_headers)
    assert resp.status_code == 200

    st = await client.get(f"/api/persons/{pid}/enrichment_status", headers=auth_headers)
    assert st.json()["triggered_by"] == "stream"


@pytest.mark.asyncio
async def test_enrich_invalid_trigger_rejected(client, auth_headers):
    pid = await _seed_person()
    resp = await client.post(f"/api/persons/{pid}/enrich?trigger=hack", headers=auth_headers)
    assert resp.status_code == 422  # pattern query param


@pytest.mark.asyncio
async def test_enrichment_status_completed_summary_and_duration(client, auth_headers):
    from datetime import datetime, timezone, timedelta
    from tests.conftest import session_test
    from app.db.models import EnrichmentJob
    pid = await _seed_person()
    start = datetime.now(timezone.utc)
    async with session_test() as db:
        db.add(EnrichmentJob(
            person_id=pid, status="completed", triggered_by="manual",
            started_at=start, completed_at=start + timedelta(seconds=12),
            summary={"emails_found": 2, "sites_found": 3,
                     "correlations_created": 1, "username_platforms_found": 4},
        ))
        await db.commit()

    resp = await client.get(f"/api/persons/{pid}/enrichment_status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["summary"]["emails_found"] == 2
    assert body["duration_seconds"] == 12.0


@pytest.mark.asyncio
async def test_enrichment_status_returns_latest_job(client, auth_headers):
    # L'endpoint renvoie le DERNIER job (id le plus élevé), pas le premier.
    from tests.conftest import session_test
    from app.db.models import EnrichmentJob
    pid = await _seed_person()
    async with session_test() as db:
        db.add(EnrichmentJob(person_id=pid, status="completed", triggered_by="manual"))
        db.add(EnrichmentJob(person_id=pid, status="running", triggered_by="stream"))
        await db.commit()
    resp = await client.get(f"/api/persons/{pid}/enrichment_status", headers=auth_headers)
    assert resp.json()["status"] == "running"
    assert resp.json()["triggered_by"] == "stream"


@pytest.mark.asyncio
async def test_enrichment_status_requires_auth(client):
    resp = await client.get("/api/persons/1/enrichment_status")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_pipeline_skip_marks_job_failed_not_stuck():
    # Garde-fou + sûreté : un sujet consentant est refusé, et le job atteint TOUJOURS un état
    # terminal ("failed") — jamais coincé en pending/running (sinon le polling tournerait à
    # l'infini). Chemin réseau-free (le pipeline retourne au garde-fou, avant tout appel OSINT).
    from tests.conftest import session_test
    from app.db.models import Person, EnrichmentJob
    from app.osint.pipeline import full_enrich_after_face_match
    async with session_test() as db:
        p = Person(first_name="Alice", last_name="Martin", confidence_score=1.0, source="consented_demo")
        db.add(p)
        await db.flush()
        job = EnrichmentJob(person_id=p.id, status="pending", triggered_by="manual")
        db.add(job)
        await db.commit()
        pid, jid = p.id, job.id

    async with session_test() as db:
        summary = await full_enrich_after_face_match(pid, db, job_id=jid)
        assert summary["skipped"]

    async with session_test() as db:
        job = await db.get(EnrichmentJob, jid)
        assert job.status == "failed"


@pytest.mark.asyncio
async def test_update_person_source_accepts_valid(client, auth_headers):
    # Reclasser un sujet en consented_demo (le protège du profilage).
    pid = await _seed_person()  # source=investigation au départ
    resp = await client.patch(
        f"/api/persons/{pid}", json={"source": "consented_demo"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "consented_demo"


@pytest.mark.asyncio
async def test_update_person_source_rejects_invalid(client, auth_headers):
    pid = await _seed_person()
    resp = await client.patch(
        f"/api/persons/{pid}", json={"source": "hacker"}, headers=auth_headers
    )
    assert resp.status_code == 422  # rejeté par la validation Pydantic (allowlist)


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
