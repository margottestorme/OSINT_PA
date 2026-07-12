import io
import hashlib
from unittest.mock import patch

import pytest
from PIL import Image

from app.db.models import Person
from app.facial.exceptions import (
    InvalidImageError,
    MultipleFacesError,
    NoFaceDetectedError,
)
from tests.conftest import session_test


def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png(width: int = 100, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=(64, 64, 64))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


FAKE_EMBEDDING = [0.1] * 128
FAKE_PROCESS_RESULT = {
    "embedding": FAKE_EMBEDDING,
    "faces_count": 1,
    "hash": "abc123",
}


# =============================================================
# POST /api/faces/upload — sans JWT → 401/403
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_no_auth(client):
    jpeg = _make_jpeg()
    resp = await client.post(
        "/api/faces/upload",
        files={"file": ("test.jpg", jpeg, "image/jpeg")},
    )
    assert resp.status_code in (401, 403)


# =============================================================
# POST /api/faces/upload — upload valide, pas de match → nouvelle personne
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_new_person(client, auth_headers):
    jpeg = _make_jpeg()

    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):

        resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_new"] is True
    assert data["confidence_score"] == 1.0
    assert data["faces_count"] == 1
    assert "person_id" in data
    assert data["source"] == "investigation"


# =============================================================
# POST /api/faces/upload — upload valide, match existant
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_match_existing(client, auth_headers):
    jpeg1 = _make_jpeg(80, 80)
    jpeg2 = _make_jpeg(90, 90)

    # Premier upload → crée une personne
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        resp1 = await client.post(
            "/api/faces/upload",
            files={"file": ("face1.jpg", jpeg1, "image/jpeg")},
            headers=auth_headers,
        )
    assert resp1.status_code == 200
    person_id = resp1.json()["person_id"]

    # La personne est auto-créée "Inconnu" : on la nomme, sinon elle n'est pas enrichissable
    # (le pipeline pivote sur le nom — un visage encore anonyme n'est jamais profilé).
    from tests.conftest import session_test
    from app.db.models import Person
    async with session_test() as db:
        p = await db.get(Person, person_id)
        p.first_name, p.last_name = "Jean", "Dupont"
        await db.commit()

    # Deuxième upload → match avec la personne (nommée, investigation)
    result2 = {**FAKE_PROCESS_RESULT, "hash": hashlib.sha256(jpeg2).hexdigest()}
    with patch("app.api.face_routes.process_image", return_value=result2), \
         patch("app.api.face_routes.compare_embedding", return_value=(person_id, 0.35)), \
         patch("app.api.face_routes._background_enrich") as mock_enrich:
        resp2 = await client.post(
            "/api/faces/upload",
            files={"file": ("face2.jpg", jpeg2, "image/jpeg")},
            headers=auth_headers,
        )
    # Match sur un sujet d'enquête NOMMÉ : enrichissement déclenché avec (person_id, job_id).
    from unittest.mock import ANY
    mock_enrich.assert_called_once_with(person_id, ANY)

    assert resp2.status_code == 200
    data = resp2.json()
    assert data["is_new"] is False
    assert data["person_id"] == person_id
    assert data["confidence_score"] == 0.65  # 1.0 - 0.35
    assert data["source"] == "investigation"


@pytest.mark.asyncio
async def test_faces_upload_match_unknown_no_enrich(client, auth_headers):
    # Un match sur un visage encore "Inconnu" (jamais nommé) ne déclenche PAS d'enrichissement
    # (rien à chercher) — évite des jobs "failed" à chaque ré-identification d'un inconnu.
    jpeg1 = _make_jpeg(70, 70)
    jpeg2 = _make_jpeg(75, 75)
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        resp1 = await client.post(
            "/api/faces/upload",
            files={"file": ("f1.jpg", jpeg1, "image/jpeg")}, headers=auth_headers,
        )
    person_id = resp1.json()["person_id"]  # créée "Inconnu"

    result2 = {**FAKE_PROCESS_RESULT, "hash": hashlib.sha256(jpeg2).hexdigest()}
    with patch("app.api.face_routes.process_image", return_value=result2), \
         patch("app.api.face_routes.compare_embedding", return_value=(person_id, 0.35)), \
         patch("app.api.face_routes._background_enrich") as mock_enrich:
        await client.post(
            "/api/faces/upload",
            files={"file": ("f2.jpg", jpeg2, "image/jpeg")}, headers=auth_headers,
        )
    mock_enrich.assert_not_called()


# =============================================================
# POST /api/faces/upload — match sur une personne du jeu de données académique LFW
# (source="lfw_dataset") : la source est bien remontée, et l'enrichissement OSINT
# n'est PAS déclenché (une entrée LFW n'est pas le sujet d'une enquête réelle).
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_match_lfw_dataset_person_no_enrich(client, auth_headers):
    async with session_test() as db:
        person = Person(
            first_name="George W", last_name="Bush", confidence_score=1.0,
            embedding=[0.1] * 128, image_hash="lfw" + "0" * 61, source="lfw_dataset",
        )
        db.add(person)
        await db.commit()
        await db.refresh(person)
        person_id = person.id

    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=(person_id, 0.2)), \
         patch("app.api.face_routes._background_enrich") as mock_enrich:
        resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["person_id"] == person_id
    assert data["source"] == "lfw_dataset"
    mock_enrich.assert_not_called()


# =============================================================
# POST /api/faces/upload — doublon hash → réponse immédiate
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_duplicate_hash(client, auth_headers):
    jpeg = _make_jpeg()

    # Premier upload
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        resp1 = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )
    assert resp1.status_code == 200
    person_id = resp1.json()["person_id"]

    # Même image → détecté comme doublon via hash, process_image jamais appelé
    with patch("app.api.face_routes.process_image") as mock_process:
        resp2 = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    assert resp2.status_code == 200
    data = resp2.json()
    assert data["person_id"] == person_id
    assert data["is_new"] is False
    mock_process.assert_not_called()


# =============================================================
# POST /api/faces/upload — format invalide → 422
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_invalid_format(client, auth_headers):
    bmp = Image.new("RGB", (10, 10))
    buf = io.BytesIO()
    bmp.save(buf, format="BMP")
    bmp_bytes = buf.getvalue()

    with patch("app.api.face_routes.process_image", side_effect=InvalidImageError("Format 'BMP' non supporté")):
        resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.bmp", bmp_bytes, "image/bmp")},
            headers=auth_headers,
        )

    assert resp.status_code == 422
    assert "non supporté" in resp.json()["detail"]


# =============================================================
# POST /api/faces/upload — taille dépassée → 422
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_too_large(client, auth_headers):
    oversized = b"\xff\xd8\xff\xe0" + b"\x00" * (5 * 1024 * 1024 + 100)

    resp = await client.post(
        "/api/faces/upload",
        files={"file": ("big.jpg", oversized, "image/jpeg")},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "volumineux" in resp.json()["detail"]


# =============================================================
# POST /api/faces/upload — aucun visage → 422
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_no_face(client, auth_headers):
    jpeg = _make_jpeg()

    with patch("app.api.face_routes.process_image", side_effect=NoFaceDetectedError("Aucun visage")):
        resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    assert resp.status_code == 422
    assert "visage" in resp.json()["detail"].lower()


# =============================================================
# POST /api/faces/upload — multiples visages → 422
# =============================================================

@pytest.mark.asyncio
async def test_faces_upload_multiple_faces(client, auth_headers):
    jpeg = _make_jpeg()

    with patch("app.api.face_routes.process_image", side_effect=MultipleFacesError("2 visages")):
        resp = await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    assert resp.status_code == 422


# =============================================================
# GET /api/faces/persons — sans JWT → 401/403
# =============================================================

@pytest.mark.asyncio
async def test_faces_persons_no_auth(client):
    resp = await client.get("/api/faces/persons")
    assert resp.status_code in (401, 403)


# =============================================================
# GET /api/faces/persons — liste vide
# =============================================================

@pytest.mark.asyncio
async def test_faces_persons_empty(client, auth_headers):
    resp = await client.get("/api/faces/persons", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


# =============================================================
# GET /api/faces/persons — liste après upload, sans embedding exposé
# =============================================================

@pytest.mark.asyncio
async def test_faces_persons_after_upload(client, auth_headers):
    jpeg = _make_jpeg()

    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    resp = await client.get("/api/faces/persons", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1

    person = data[0]
    assert "id" in person
    assert "first_name" in person
    assert "confidence_score" in person
    assert person["has_embedding"] is True
    assert person["source"] == "investigation"
    # L'embedding brut ne doit PAS être exposé
    assert "embedding" not in person


# =============================================================
# GET /api/faces/persons — pagination
# =============================================================

@pytest.mark.asyncio
async def test_faces_persons_pagination(client, auth_headers):
    resp = await client.get("/api/faces/persons?limit=5&offset=0", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_faces_persons_invalid_limit(client, auth_headers):
    resp = await client.get("/api/faces/persons?limit=0", headers=auth_headers)
    assert resp.status_code == 422


# =============================================================
# GET /api/faces/persons — en-tête X-Total-Count (pagination : numéros de page, dernière page)
# =============================================================

@pytest.mark.asyncio
async def test_faces_persons_total_count_header_empty(client, auth_headers):
    resp = await client.get("/api/faces/persons", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_faces_persons_total_count_header_reflects_total_not_page_size(client, auth_headers):
    jpeg = _make_jpeg()
    with patch("app.api.face_routes.process_image", return_value=FAKE_PROCESS_RESULT), \
         patch("app.api.face_routes.compare_embedding", return_value=None):
        await client.post(
            "/api/faces/upload",
            files={"file": ("test.jpg", jpeg, "image/jpeg")},
            headers=auth_headers,
        )

    resp = await client.get("/api/faces/persons?limit=1&offset=0", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.headers["X-Total-Count"] == "1"  # total réel, pas la taille de la page renvoyée
