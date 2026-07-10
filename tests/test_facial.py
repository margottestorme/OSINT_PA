import io
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from app.facial.engine import (
    compare_embedding,
    detect_faces,
    extract_embedding,
    process_image,
)
from app.facial.exceptions import (
    ImageTooLargeError,
    InvalidImageError,
    MultipleFacesError,
    NoFaceDetectedError,
)


def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    """Génère une image JPEG valide en mémoire pour les tests."""
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png(width: int = 100, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


FAKE_EMBEDDING = [0.1] * 128
FAKE_LOCATIONS = [(10, 110, 110, 10)]


# =============================================================
# detect_faces
# =============================================================

def test_detect_faces_one_face():
    jpeg = _make_jpeg()
    with patch("app.facial.engine.face_recognition.face_locations", return_value=FAKE_LOCATIONS):
        faces = detect_faces(jpeg)

    assert len(faces) == 1
    assert faces[0] == {"top": 10, "right": 110, "bottom": 110, "left": 10}


def test_detect_faces_multiple():
    jpeg = _make_jpeg()
    locs = [(10, 50, 50, 10), (60, 110, 110, 60)]
    with patch("app.facial.engine.face_recognition.face_locations", return_value=locs):
        faces = detect_faces(jpeg)

    assert len(faces) == 2


def test_detect_faces_none_raises():
    jpeg = _make_jpeg()
    with patch("app.facial.engine.face_recognition.face_locations", return_value=[]):
        with pytest.raises(NoFaceDetectedError):
            detect_faces(jpeg)


def test_detect_faces_invalid_bytes():
    with pytest.raises(InvalidImageError):
        detect_faces(b"not an image")


# =============================================================
# extract_embedding
# =============================================================

def test_extract_embedding_success():
    jpeg = _make_jpeg()
    fake_enc = [np.array(FAKE_EMBEDDING)]

    with patch("app.facial.engine.face_recognition.face_encodings", return_value=fake_enc):
        emb = extract_embedding(jpeg)

    assert len(emb) == 128
    assert isinstance(emb, list)
    assert all(isinstance(v, float) for v in emb)


def test_extract_embedding_no_face():
    jpeg = _make_jpeg()
    with patch("app.facial.engine.face_recognition.face_encodings", return_value=[]):
        with pytest.raises(NoFaceDetectedError):
            extract_embedding(jpeg)


def test_extract_embedding_multiple_faces():
    jpeg = _make_jpeg()
    fake_enc = [np.array(FAKE_EMBEDDING), np.array(FAKE_EMBEDDING)]

    with patch("app.facial.engine.face_recognition.face_encodings", return_value=fake_enc):
        with pytest.raises(MultipleFacesError):
            extract_embedding(jpeg)


# =============================================================
# compare_embedding
# =============================================================

def test_compare_embedding_match():
    target = [0.1] * 128
    db = [
        (1, [0.5] * 128),
        (2, [0.11] * 128),  # très proche du target
    ]
    result = compare_embedding(target, db, threshold=0.6)

    assert result is not None
    person_id, score = result
    assert person_id == 2
    assert score < 0.6


def test_compare_embedding_no_match():
    target = [0.1] * 128
    db = [(1, [0.9] * 128)]  # distance > 0.6
    result = compare_embedding(target, db, threshold=0.6)

    assert result is None


def test_compare_embedding_empty_db():
    result = compare_embedding([0.1] * 128, [], threshold=0.6)
    assert result is None


def test_compare_embedding_picks_closest():
    target = [0.0] * 128
    db = [
        (1, [0.05] * 128),
        (2, [0.02] * 128),
        (3, [0.04] * 128),
    ]
    result = compare_embedding(target, db, threshold=0.6)

    assert result is not None
    assert result[0] == 2  # person_id 2 est le plus proche


def _offset_vector(offset: float) -> list[float]:
    """Vecteur nul sauf la 1re dimension : distance euclidienne au vecteur nul = `offset`
    exactement, ce qui permet de calibrer précisément des écarts pour tester la marge."""
    return [offset] + [0.0] * 127


def test_compare_embedding_ambiguous_match_rejected():
    # Deux personnes différentes presque aussi proches l'une que l'autre (écart < marge) :
    # mieux vaut "inconnu" qu'un faux positif — voir le commentaire sur `min_margin` dans
    # app/facial/engine.py (collisions mesurées empiriquement sur le jeu LFW, 2026-07-09).
    target = [0.0] * 128
    db = [(1, _offset_vector(0.30)), (2, _offset_vector(0.31))]  # écart de 0.01 < marge
    result = compare_embedding(target, db, threshold=0.6, min_margin=0.03)
    assert result is None


def test_compare_embedding_clear_winner_not_rejected_by_margin():
    # Même si un 2e candidat existe, un écart suffisant avec le meilleur n'est pas ambigu.
    target = [0.0] * 128
    db = [(1, _offset_vector(0.20)), (2, _offset_vector(0.55))]  # écart de 0.35 >> marge
    result = compare_embedding(target, db, threshold=0.6, min_margin=0.03)
    assert result is not None
    assert result[0] == 1


def test_compare_embedding_margin_ignores_same_person_multiple_embeddings():
    # Plusieurs embeddings de la MÊME personne (ex : plusieurs photos) ne doivent jamais
    # se faire concurrence entre eux pour le calcul de la marge d'ambiguïté.
    target = [0.0] * 128
    db = [(1, _offset_vector(0.20)), (1, _offset_vector(0.21)), (1, _offset_vector(0.22))]
    result = compare_embedding(target, db, threshold=0.6, min_margin=0.03)
    assert result is not None
    assert result[0] == 1


# =============================================================
# process_image
# =============================================================

def test_process_image_jpeg_success():
    jpeg = _make_jpeg()
    fake_enc = [np.array(FAKE_EMBEDDING)]

    with patch("app.facial.engine.face_recognition.face_locations", return_value=FAKE_LOCATIONS), \
         patch("app.facial.engine.face_recognition.face_encodings", return_value=fake_enc):
        result = process_image(jpeg, "abc123")

    assert result["faces_count"] == 1
    assert len(result["embedding"]) == 128
    assert result["hash"] == "abc123"


def test_process_image_png_success():
    png = _make_png()
    fake_enc = [np.array(FAKE_EMBEDDING)]

    with patch("app.facial.engine.face_recognition.face_locations", return_value=FAKE_LOCATIONS), \
         patch("app.facial.engine.face_recognition.face_encodings", return_value=fake_enc):
        result = process_image(png, "def456")

    assert result["faces_count"] == 1
    assert result["hash"] == "def456"


def test_process_image_too_large():
    oversized = b"\x00" * (5 * 1024 * 1024 + 1)
    with pytest.raises(ImageTooLargeError):
        process_image(oversized, "hash")


def test_process_image_invalid_format_bmp():
    """Un BMP valide mais format refusé."""
    img = Image.new("RGB", (10, 10))
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    bmp_bytes = buf.getvalue()

    with pytest.raises(InvalidImageError, match="non supporté"):
        process_image(bmp_bytes, "hash")


def test_process_image_corrupted_bytes():
    with pytest.raises(InvalidImageError):
        process_image(b"corrupted data", "hash")


def test_process_image_no_face():
    jpeg = _make_jpeg()
    with patch("app.facial.engine.face_recognition.face_locations", return_value=[]):
        with pytest.raises(NoFaceDetectedError):
            process_image(jpeg, "hash")
