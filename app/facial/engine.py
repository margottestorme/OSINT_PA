import io

import numpy as np
import face_recognition
from PIL import Image
from loguru import logger

from app.facial.exceptions import (
    ImageTooLargeError,
    InvalidImageError,
    MultipleFacesError,
    NoFaceDetectedError,
)

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 Mo
ALLOWED_FORMATS = {"JPEG", "PNG"}


def _load_image_array(image_bytes: bytes) -> np.ndarray:
    """Charge des bytes bruts en tableau numpy RGB sans écriture disque."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        arr = np.array(img)
        img.close()
        return arr
    except Exception as e:
        raise InvalidImageError(f"Impossible de décoder l'image : {e}")


def detect_faces(image_bytes: bytes) -> list[dict]:
    """Détecte les visages et retourne leurs coordonnées (top, right, bottom, left)."""
    logger.info(f"[detect_faces] Analyse de {len(image_bytes)} octets")

    img_array = _load_image_array(image_bytes)
    locations = face_recognition.face_locations(img_array)

    del img_array

    if not locations:
        logger.warning("[detect_faces] Aucun visage détecté")
        raise NoFaceDetectedError("Aucun visage détecté dans l'image")

    faces = [
        {"top": top, "right": right, "bottom": bottom, "left": left}
        for top, right, bottom, left in locations
    ]

    logger.info(f"[detect_faces] {len(faces)} visage(s) détecté(s)")
    return faces


def extract_embedding(image_bytes: bytes) -> list[float]:
    """Extrait l'embedding facial (128 floats) d'une image contenant exactement un visage."""
    logger.info(f"[extract_embedding] Extraction depuis {len(image_bytes)} octets")

    img_array = _load_image_array(image_bytes)
    encodings = face_recognition.face_encodings(img_array)

    del img_array

    if len(encodings) == 0:
        raise NoFaceDetectedError("Aucun visage détecté pour l'extraction d'embedding")

    if len(encodings) > 1:
        raise MultipleFacesError(
            f"{len(encodings)} visages détectés — impossible de déterminer l'identité"
        )

    embedding = encodings[0].tolist()
    logger.info(f"[extract_embedding] Embedding extrait ({len(embedding)} dimensions)")
    return embedding


def compare_embedding(
    embedding: list[float],
    db_embeddings: list[tuple[int, list[float]]],
    threshold: float = 0.6,
) -> tuple[int, float] | None:
    """Compare un embedding contre ceux en base via distance euclidienne.

    Args:
        embedding: vecteur 128 floats du visage à identifier
        db_embeddings: liste de (person_id, embedding) depuis la DB
        threshold: distance maximale pour un match (plus bas = plus strict)

    Returns:
        (person_id, score) du meilleur match, ou None si aucun match.
    """
    if not db_embeddings:
        logger.info("[compare_embedding] Aucun embedding en base — pas de comparaison")
        return None

    target = np.array(embedding)
    best_match: tuple[int, float] | None = None

    for person_id, db_emb in db_embeddings:
        distance = float(np.linalg.norm(target - np.array(db_emb)))

        if distance < threshold:
            if best_match is None or distance < best_match[1]:
                best_match = (person_id, round(distance, 4))

    if best_match:
        logger.info(
            f"[compare_embedding] Match trouvé — person_id={best_match[0]}, distance={best_match[1]}"
        )
    else:
        logger.info(f"[compare_embedding] Aucun match sous le seuil {threshold}")

    return best_match


def process_image(image_bytes: bytes, image_hash: str) -> dict:
    """Orchestre la validation, détection et extraction d'embedding.

    Vérifie le format (JPEG/PNG), la taille (5 Mo max), détecte les visages,
    puis extrait l'embedding. Aucune image n'est écrite sur le disque.
    """
    logger.info(f"[process_image] Traitement — hash={image_hash[:16]}..., taille={len(image_bytes)}")

    # Vérification de la taille
    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise ImageTooLargeError(
            f"Image trop volumineuse : {len(image_bytes)} octets (max {MAX_IMAGE_SIZE})"
        )

    # Vérification du format via Pillow
    try:
        img = Image.open(io.BytesIO(image_bytes))
        fmt = img.format
        img.close()
    except Exception as e:
        raise InvalidImageError(f"Format d'image non reconnu : {e}")

    if fmt not in ALLOWED_FORMATS:
        raise InvalidImageError(f"Format '{fmt}' non supporté — acceptés : {ALLOWED_FORMATS}")

    logger.debug(f"[process_image] Format validé : {fmt}")

    # Détection des visages
    faces = detect_faces(image_bytes)

    # Extraction de l'embedding
    embedding = extract_embedding(image_bytes)

    result = {
        "embedding": embedding,
        "faces_count": len(faces),
        "hash": image_hash,
    }

    logger.info(
        f"[process_image] Terminé — {result['faces_count']} visage(s), "
        f"embedding {len(embedding)}d"
    )
    return result
