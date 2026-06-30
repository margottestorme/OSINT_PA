import io
from unittest.mock import patch

import pytest
from PIL import Image

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
# GET /api/persons/{id}/report — sans JWT → 401
# =============================================================

@pytest.mark.asyncio
async def test_report_no_auth(client):
    resp = await client.get("/api/persons/1/report")
    assert resp.status_code in (401, 403)


# =============================================================
# GET /api/persons/{id}/report — personne inexistante → 404
# =============================================================

@pytest.mark.asyncio
async def test_report_person_not_found(client, auth_headers):
    resp = await client.get("/api/persons/9999/report", headers=auth_headers)
    assert resp.status_code == 404
    assert "introuvable" in resp.json()["detail"].lower()


# =============================================================
# GET /api/persons/{id}/report — personne sans données
# =============================================================

@pytest.mark.asyncio
async def test_report_person_empty_data(client, auth_headers):
    # Créer une personne via upload
    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        upload_resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )
    person_id = upload_resp.json()["person_id"]

    resp = await client.get(f"/api/persons/{person_id}/report", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["person"]["id"] == person_id
    assert data["emails"] == []
    assert data["sites"] == []
    assert data["correlations"] == []


# =============================================================
# GET /api/persons/{id}/report — personne avec données complètes
# =============================================================

@pytest.mark.asyncio
async def test_report_person_with_data(client, auth_headers):
    # Créer une personne via upload
    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        upload_resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )
    person_id = upload_resp.json()["person_id"]

    # Ajouter des données via la DB de test directement
    from tests.conftest import session_test
    from app.db.models import Email, Site, Correlation

    async with session_test() as db:
        email = Email(email="test@report.com", source="test", person_id=person_id)
        db.add(email)
        await db.flush()

        site = Site(url="https://report-test.com", server="nginx")
        db.add(site)
        await db.flush()

        corr = Correlation(
            person_id=person_id,
            email_id=email.id,
            site_id=site.id,
            score=0.8,
        )
        db.add(corr)
        await db.commit()

    resp = await client.get(f"/api/persons/{person_id}/report", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["person"]["id"] == person_id
    assert len(data["emails"]) == 1
    assert data["emails"][0]["email"] == "test@report.com"
    assert len(data["sites"]) == 1
    assert data["sites"][0]["url"] == "https://report-test.com"
    assert len(data["correlations"]) == 1
    assert data["correlations"][0]["score"] == 0.8


# =============================================================
# GET /api/persons/{id}/report — pagination
# =============================================================

@pytest.mark.asyncio
async def test_report_pagination(client, auth_headers):
    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        upload_resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )
    person_id = upload_resp.json()["person_id"]

    resp = await client.get(
        f"/api/persons/{person_id}/report?limit=5&offset=0",
        headers=auth_headers,
    )
    assert resp.status_code == 200


# =============================================================
# GET /report — les sites enrichis apparaissent (régression M1)
# =============================================================

@pytest.mark.asyncio
async def test_report_shows_enriched_sites(client, auth_headers):
    """Après enrich_person(), un site découvert doit apparaître dans le rapport.

    C'est le test qui aurait attrapé M1 : avant correction, la corrélation
    était créée sans site_id et le rapport ne remontait jamais les sites.
    """
    from tests.conftest import session_test
    from app.db.models import Email
    from app.osint import correlator

    # 1) Créer une personne via upload
    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        upload_resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )
    person_id = upload_resp.json()["person_id"]

    # 2) Lui associer un email connu
    async with session_test() as db:
        db.add(Email(email="cible@example.com", source="manual", person_id=person_id))
        await db.commit()

    # 3) Lancer l'enrichissement avec un résultat WHOIS mocké → crée un Site lié
    fake_correlate = [{
        "source": "whois",
        "entity": "example.com",
        "data": {"domain_name": "example.com", "org": "Example Inc"},
    }]
    fake_entities = {"emails": [], "domains": [], "names": []}

    with patch("app.osint.correlator.correlate", return_value=fake_correlate), \
         patch("app.osint.correlator.extract_entities", return_value=fake_entities):
        async with session_test() as db:
            await correlator.enrich_person(person_id, db)

    # 4) Le rapport doit maintenant contenir le site découvert
    resp = await client.get(f"/api/persons/{person_id}/report", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["sites"]) >= 1
    urls = [s["url"] for s in data["sites"]]
    assert "https://example.com" in urls
    # La corrélation doit bien référencer ce site
    assert any(c["site_id"] is not None for c in data["correlations"])
