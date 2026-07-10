import base64
from unittest.mock import patch, MagicMock

import cv2
import numpy as np
import pytest

from app.facial import stream as stream_mod
from app.facial import engine as facial_engine
from app.db.models import Person

# base64 valide quelconque : le décodage réussit ; locate_and_encode est mocké dans
# les tests d'endpoint, donc le contenu réel des bytes n'a pas d'importance.
_VALID_B64 = base64.b64encode(b"fake-jpeg-bytes").decode()


def _fake_cap(opened=True, w=1280, h=720, fps=30.0):
    cap = MagicMock()
    cap.isOpened.return_value = opened
    cap.get.side_effect = lambda prop: {
        cv2.CAP_PROP_FRAME_WIDTH: w,
        cv2.CAP_PROP_FRAME_HEIGHT: h,
        cv2.CAP_PROP_FPS: fps,
    }.get(prop, 0)
    return cap


async def _add_person(embedding, first="Jean", last="Dupont", source="investigation") -> int:
    """Insère une personne (avec embedding) dans la base de test et renvoie son id."""
    from tests.conftest import session_test
    async with session_test() as s:
        p = Person(first_name=first, last_name=last, embedding=embedding,
                    confidence_score=1.0, source=source)
        s.add(p)
        await s.commit()
        return p.id


# =============================================================
# detect_camera / get_camera_info (cv2 mocké) — serveur, /info conservé
# =============================================================

def test_detect_camera_available():
    with patch("app.facial.stream.cv2.VideoCapture", return_value=_fake_cap(True)):
        assert stream_mod.detect_camera() is True


def test_detect_camera_unavailable():
    with patch("app.facial.stream.cv2.VideoCapture", return_value=_fake_cap(False)):
        assert stream_mod.detect_camera() is False


def test_get_camera_info_available():
    with patch("app.facial.stream.cv2.VideoCapture", return_value=_fake_cap(True, 1280, 720, 30.0)):
        info = stream_mod.get_camera_info()
    assert info == {"available": True, "index": 0, "width": 1280, "height": 720, "fps": 30.0}


def test_get_camera_info_unavailable():
    with patch("app.facial.stream.cv2.VideoCapture", return_value=_fake_cap(False)):
        info = stream_mod.get_camera_info()
    assert info["available"] is False
    assert info["width"] == 0


# =============================================================
# locate_and_encode (face_recognition + _load_image_array mockés)
# =============================================================

def test_locate_and_encode_multi():
    arr = np.zeros((10, 10, 3), dtype=np.uint8)
    with patch.object(facial_engine, "_load_image_array", return_value=arr), \
         patch("app.facial.engine.face_recognition.face_locations", return_value=[(10, 20, 30, 40)]), \
         patch("app.facial.engine.face_recognition.face_encodings", return_value=[np.array([0.2] * 128)]):
        faces = facial_engine.locate_and_encode(b"whatever")
    assert len(faces) == 1
    assert faces[0]["location"] == {"top": 10, "right": 20, "bottom": 30, "left": 40}
    assert faces[0]["embedding"] == [0.2] * 128


def test_locate_and_encode_no_face():
    arr = np.zeros((5, 5, 3), dtype=np.uint8)
    with patch.object(facial_engine, "_load_image_array", return_value=arr), \
         patch("app.facial.engine.face_recognition.face_locations", return_value=[]):
        assert facial_engine.locate_and_encode(b"x") == []


# =============================================================
# GET /api/stream/info
# =============================================================

@pytest.mark.asyncio
async def test_stream_info_requires_jwt(client):
    assert (await client.get("/api/stream/info")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_stream_info_ok(client, auth_headers):
    resp = await client.get("/api/stream/info", headers=auth_headers)
    assert resp.status_code == 200
    assert "available" in resp.json()


# =============================================================
# GET /api/stream/status (simplifié, sans worker)
# =============================================================

@pytest.mark.asyncio
async def test_stream_status_requires_jwt(client):
    assert (await client.get("/api/stream/status")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_stream_status_ok(client, auth_headers):
    resp = await client.get("/api/stream/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "client-side"
    assert "known_faces" in body


# =============================================================
# POST /api/stream/analyze (analyse d'un frame côté client)
# =============================================================

@pytest.mark.asyncio
async def test_analyze_requires_jwt(client):
    resp = await client.post("/api/stream/analyze", json={"frame": _VALID_B64})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_analyze_corrupt_base64(client, auth_headers):
    resp = await client.post("/api/stream/analyze", json={"frame": "!!!not-base64!!!"}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_analyze_no_face(client, auth_headers):
    with patch("app.api.stream_routes.locate_and_encode", return_value=[]):
        resp = await client.post("/api/stream/analyze", json={"frame": _VALID_B64}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["faces"] == [] and data["faces_count"] == 0
    assert isinstance(data["processing_ms"], (int, float))


@pytest.mark.asyncio
async def test_analyze_new_face(client, auth_headers):
    """Visage détecté mais aucun match en base → is_new=True."""
    faces = [{"location": {"top": 1, "right": 2, "bottom": 3, "left": 4}, "embedding": [0.5] * 128}]
    with patch("app.api.stream_routes.locate_and_encode", return_value=faces):
        resp = await client.post("/api/stream/analyze", json={"frame": _VALID_B64}, headers=auth_headers)
    assert resp.status_code == 200
    f = resp.json()["faces"][0]
    assert f["person_id"] is None and f["name"] is None
    assert f["confidence"] is None and f["is_new"] is True
    assert f["source"] is None
    assert f["location"] == {"top": 1, "right": 2, "bottom": 3, "left": 4}


@pytest.mark.asyncio
async def test_analyze_match(client, auth_headers):
    """Visage correspondant à une personne connue → person_id/name/confidence renseignés."""
    emb = [0.1] * 128
    pid = await _add_person(emb, "Jean", "Dupont")
    faces = [{"location": {"top": 10, "right": 50, "bottom": 60, "left": 20}, "embedding": emb}]
    with patch("app.api.stream_routes.locate_and_encode", return_value=faces):
        resp = await client.post("/api/stream/analyze", json={"frame": _VALID_B64}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["faces_count"] == 1
    f = data["faces"][0]
    assert f["person_id"] == pid
    assert f["name"] == "Jean Dupont"
    assert f["is_new"] is False
    assert f["confidence"] == 1.0  # distance nulle → confiance 1.0
    assert f["source"] == "investigation"


@pytest.mark.asyncio
async def test_analyze_match_lfw_dataset_source(client, auth_headers):
    """Visage correspondant à une entrée du jeu de données académique LFW → source distincte,
    pour que l'UI ne confonde jamais ça avec une vraie personne investiguée."""
    emb = [0.2] * 128
    pid = await _add_person(emb, "George W", "Bush", source="lfw_dataset")
    faces = [{"location": {"top": 10, "right": 50, "bottom": 60, "left": 20}, "embedding": emb}]
    with patch("app.api.stream_routes.locate_and_encode", return_value=faces):
        resp = await client.post("/api/stream/analyze", json={"frame": _VALID_B64}, headers=auth_headers)
    assert resp.status_code == 200
    f = resp.json()["faces"][0]
    assert f["person_id"] == pid
    assert f["source"] == "lfw_dataset"


@pytest.mark.asyncio
async def test_analyze_rate_limit(client, auth_headers):
    """Au-delà de 2 requêtes/seconde par utilisateur → 429."""
    with patch("app.api.stream_routes.locate_and_encode", return_value=[]):
        r1 = await client.post("/api/stream/analyze", json={"frame": _VALID_B64}, headers=auth_headers)
        r2 = await client.post("/api/stream/analyze", json={"frame": _VALID_B64}, headers=auth_headers)
        r3 = await client.post("/api/stream/analyze", json={"frame": _VALID_B64}, headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r3.status_code == 429
