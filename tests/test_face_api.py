import io
import hashlib
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from app.facial.exceptions import (
    ImageTooLargeError,
    InvalidImageError,
    MultipleFacesError,
    NoFaceDetectedError,
)


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

    # Deuxième upload → match avec la personne créée
    result2 = {**FAKE_PROCESS_RESULT, "hash": hashlib.sha256(jpeg2).hexdigest()}
    with patch("app.api.face_routes.process_image", return_value=result2), \
         patch("app.api.face_routes.compare_embedding", return_value=(person_id, 0.35)):
        resp2 = await client.post(
            "/api/faces/upload",
            files={"file": ("face2.jpg", jpeg2, "image/jpeg")},
            headers=auth_headers,
        )

    assert resp2.status_code == 200
    data = resp2.json()
    assert data["is_new"] is False
    assert data["person_id"] == person_id
    assert data["confidence_score"] == 0.65  # 1.0 - 0.35


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
