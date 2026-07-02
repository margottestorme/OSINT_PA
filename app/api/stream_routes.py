"""
Endpoints du flux webcam temps réel — OSINT Platform.

Le worker (singleton) démarre/s'arrête à la demande — il ne tourne jamais en
permanence. Aucune frame n'est écrite sur le disque.

JWT requis partout SAUF /feed : le flux est consommé par une balise <img src=…>
qui ne peut pas envoyer d'en-tête Authorization. Le /feed ne diffuse que si le
worker a été démarré au préalable par un utilisateur authentifié.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_token
from app.db.session import get_db
from app.db.models import Person
from app.facial.stream import get_camera_info
from app.facial.worker import stream_worker

stream_router = APIRouter(prefix="/api/stream", tags=["stream webcam"])


@stream_router.get("/info")
async def stream_info(token_data: dict = Depends(verify_token)) -> dict:
    """Caractéristiques de la webcam : {available, index, width, height, fps}."""
    return get_camera_info()


@stream_router.get("/start")
async def stream_start(
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
) -> dict:
    """Charge les visages connus depuis la DB puis démarre la capture."""
    result = await db.execute(
        select(Person.id, Person.first_name, Person.last_name, Person.embedding)
        .where(Person.embedding.is_not(None))
    )
    known = [(row[0], f"{row[1]} {row[2]}".strip(), row[3]) for row in result.all()]
    stream_worker.set_known_faces(known)

    started = stream_worker.start()
    # Laisse le thread ouvrir la webcam / détecter une éventuelle absence
    await asyncio.sleep(0.5)

    st = stream_worker.status()
    if st.get("error"):
        stream_worker.stop()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=st["error"],
        )
    return {"started": started, "known_faces": len(known), "status": st}


@stream_router.get("/stop")
async def stream_stop(token_data: dict = Depends(verify_token)) -> dict:
    """Arrête proprement la capture et libère la webcam."""
    stopped = stream_worker.stop()
    return {"stopped": stopped, "status": stream_worker.status()}


@stream_router.get("/status")
async def stream_status(token_data: dict = Depends(verify_token)) -> dict:
    """Statut + statistiques : running, fps, faces_detected, matches, last_match."""
    return stream_worker.status()


async def _mjpeg_generator():
    """Génère un flux MJPEG (multipart) à partir des derniers frames annotés."""
    boundary = b"--frame\r\n"
    while stream_worker.is_running():
        frame = stream_worker.get_frame()
        if frame is None:
            await asyncio.sleep(0.05)
            continue
        yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        await asyncio.sleep(1 / 30)  # plafonne le débit de sortie


@stream_router.get("/feed")
async def stream_feed():
    """Flux MJPEG (multipart/x-mixed-replace) — SANS JWT (consommé par <img src>)."""
    if not stream_worker.is_running():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Le flux n'est pas démarré",
        )
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
