import time
from unittest.mock import patch, MagicMock

import cv2
import numpy as np
import pytest

from app.facial import stream as stream_mod
from app.facial.worker import StreamWorker, stream_worker


@pytest.fixture(autouse=True)
def _cleanup_worker():
    """Garantit que le worker singleton est arrêté après chaque test."""
    yield
    stream_worker.stop()


def _fake_cap(opened=True, w=1280, h=720, fps=30.0):
    cap = MagicMock()
    cap.isOpened.return_value = opened
    cap.get.side_effect = lambda prop: {
        cv2.CAP_PROP_FRAME_WIDTH: w,
        cv2.CAP_PROP_FRAME_HEIGHT: h,
        cv2.CAP_PROP_FPS: fps,
    }.get(prop, 0)
    return cap


# =============================================================
# detect_camera / get_camera_info (cv2 mocké)
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
# StreamWorker (cv2 + face_recognition mockés)
# =============================================================

class _FakeCap:
    def isOpened(self):
        return True

    def read(self):
        return True, np.zeros((120, 160, 3), dtype=np.uint8)

    def release(self):
        pass


def test_worker_start_stop_status():
    with patch("app.facial.worker.cv2.VideoCapture", return_value=_FakeCap()), \
         patch("app.facial.worker.face_recognition.face_locations", return_value=[]):
        w = StreamWorker(max_fps=10)
        assert w.is_running() is False
        assert w.start() is True
        time.sleep(0.4)

        assert w.is_running() is True
        st = w.status()
        assert st["running"] is True
        assert st["error"] is None
        assert w.get_frame() is not None  # JPEG produit

        assert w.start() is False  # déjà démarré
        assert w.stop() is True
        assert w.is_running() is False
        assert w.get_frame() is None  # frame libérée
        assert w.stop() is False  # déjà arrêté


def test_worker_no_webcam():
    cap = MagicMock()
    cap.isOpened.return_value = False
    with patch("app.facial.worker.cv2.VideoCapture", return_value=cap):
        w = StreamWorker()
        w.start()
        time.sleep(0.3)
        assert w.is_running() is False
        assert w.status()["error"] == "Webcam indisponible"


# =============================================================
# Endpoints /api/stream/*
# =============================================================

@pytest.mark.asyncio
async def test_stream_info_requires_jwt(client):
    assert (await client.get("/api/stream/info")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_stream_info_ok(client, auth_headers):
    resp = await client.get("/api/stream/info", headers=auth_headers)
    assert resp.status_code == 200
    assert "available" in resp.json()


@pytest.mark.asyncio
async def test_stream_start_requires_jwt(client):
    assert (await client.get("/api/stream/start")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_stream_start_no_webcam(client, auth_headers):
    with patch("app.facial.worker.cv2.VideoCapture") as mock_cap:
        mock_cap.return_value.isOpened.return_value = False
        resp = await client.get("/api/stream/start", headers=auth_headers)
    assert resp.status_code == 503
    assert "webcam" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_stream_stop_requires_jwt(client):
    assert (await client.get("/api/stream/stop")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_stream_stop_ok(client, auth_headers):
    resp = await client.get("/api/stream/stop", headers=auth_headers)
    assert resp.status_code == 200
    assert "stopped" in resp.json()


@pytest.mark.asyncio
async def test_stream_status_requires_jwt(client):
    assert (await client.get("/api/stream/status")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_stream_status_ok(client, auth_headers):
    resp = await client.get("/api/stream/status", headers=auth_headers)
    assert resp.status_code == 200
    assert "running" in resp.json()


@pytest.mark.asyncio
async def test_stream_feed_not_started(client):
    stream_worker.stop()
    resp = await client.get("/api/stream/feed")
    assert resp.status_code == 409


# =============================================================
# Générateur MJPEG — format multipart d'un chunk
# =============================================================

@pytest.mark.asyncio
async def test_mjpeg_generator_format():
    from app.api.stream_routes import _mjpeg_generator

    fake = b"\xff\xd8FAKEJPEG\xff\xd9"
    with patch.object(stream_worker, "is_running", return_value=True), \
         patch.object(stream_worker, "get_frame", return_value=fake):
        gen = _mjpeg_generator()
        chunk = await gen.__anext__()
        await gen.aclose()

    assert b"--frame" in chunk
    assert b"Content-Type: image/jpeg" in chunk
    assert fake in chunk
