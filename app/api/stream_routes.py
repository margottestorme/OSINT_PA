"""
Endpoints du flux webcam — OSINT Platform (architecture CLIENT-SIDE).

La webcam est capturée CÔTÉ NAVIGATEUR (getUserMedia) : le navigateur envoie des
frames JPEG encodés en base64 à POST /api/stream/analyze pour détection + reconnaissance.
Le serveur ne capture rien lui-même → fonctionne depuis n'importe quel appareil, sans
webcam serveur.

Confidentialité : aucune frame n'est jamais stockée. Chaque frame est traité en mémoire
puis ses bytes sont supprimés immédiatement (aucune écriture disque, aucune conservation).
"""
import base64
import binascii
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import stream_rate_limiter
from app.core.security import verify_token
from app.db.session import get_db
from app.db.models import Person
from app.facial.engine import compare_embedding, locate_and_encode
from app.facial.exceptions import InvalidImageError
from app.facial.stream import get_camera_info
from app.schemas.schemas import StreamAnalyzeResponse, StreamFrameRequest

stream_router = APIRouter(prefix="/api/stream", tags=["stream webcam"])

MAX_FRAME_SIZE = 3 * 1024 * 1024  # 3 Mo : un frame 640x480 JPEG q0.7 fait ~30-60 Ko


@stream_router.get("/info")
async def stream_info(token_data: dict = Depends(verify_token)) -> dict:
    """Caractéristiques de la webcam SERVEUR (informatif — la capture réelle est côté client)."""
    return get_camera_info()


@stream_router.get("/status")
async def stream_status(
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
) -> dict:
    """Statut simplifié (architecture client-side) : nombre de visages connus comparables."""
    known = await db.scalar(
        select(func.count()).select_from(Person).where(Person.embedding.is_not(None))
    )
    return {"mode": "client-side", "known_faces": known or 0}


def _decode_frame(frame_b64: str) -> bytes:
    """Décode le base64 (data URL acceptée) → bytes JPEG. 422 si invalide/vide/trop gros."""
    if "," in frame_b64:  # retire un éventuel préfixe « data:image/jpeg;base64, »
        frame_b64 = frame_b64.split(",", 1)[1]
    try:
        data = base64.b64decode(frame_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Frame base64 invalide")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Frame vide")
    if len(data) > MAX_FRAME_SIZE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Frame trop volumineux")
    return data


@stream_router.post("/analyze", response_model=StreamAnalyzeResponse)
async def stream_analyze(
    body: StreamFrameRequest,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
) -> StreamAnalyzeResponse:
    """Analyse un frame webcam (base64) envoyé par le navigateur : détecte les visages
    et les compare à la base. Le frame n'est jamais stocké (bytes supprimés aussitôt).

    Rate limit : 2 requêtes/seconde par utilisateur (429 au-delà).
    """
    username = token_data.get("sub", "unknown")
    stream_rate_limiter.check(username)

    t0 = time.perf_counter()
    frame_bytes = _decode_frame(body.frame)
    try:
        faces = locate_and_encode(frame_bytes)  # [{location, embedding}, ...]
    except InvalidImageError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Frame illisible (JPEG attendu)")
    finally:
        del frame_bytes  # suppression immédiate des bytes — jamais stockés

    # Charge les visages connus seulement s'il y a des visages à identifier
    name_by_id: dict[int, str] = {}
    source_by_id: dict[int, str] = {}
    db_embeddings: list[tuple[int, list[float]]] = []
    if faces:
        rows = (await db.execute(
            select(Person.id, Person.first_name, Person.last_name, Person.embedding, Person.source)
            .where(Person.embedding.is_not(None))
        )).all()
        for pid, first, last, emb, source in rows:
            name_by_id[pid] = f"{first} {last}".strip()
            source_by_id[pid] = source
            db_embeddings.append((pid, emb))

    from app.core.config import get_settings
    settings = get_settings()

    results: list[dict] = []
    for face in faces:
        match = compare_embedding(
            face["embedding"], db_embeddings,
            threshold=settings.FACE_MATCH_THRESHOLD, min_margin=settings.FACE_MATCH_MIN_MARGIN,
        )
        if match:
            pid, distance = match
            results.append({
                "location": face["location"],
                "person_id": pid,
                "name": name_by_id.get(pid),
                "confidence": round(1.0 - distance, 4),
                "is_new": False,
                "source": source_by_id.get(pid),
            })
        else:
            results.append({
                "location": face["location"],
                "person_id": None, "name": None, "confidence": None, "is_new": True,
                "source": None,
            })

    processing_ms = round((time.perf_counter() - t0) * 1000, 2)
    return StreamAnalyzeResponse(faces=results, faces_count=len(results), processing_ms=processing_ms)
