import hashlib

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, File, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.security import require_role, verify_token
from app.core.audit import create_audit_log, get_client_ip, resolve_user_id
from app.db.session import get_db
from app.db.models import Person
from app.facial.engine import compare_embedding, process_image
from app.facial.exceptions import FacialError
from app.schemas.schemas import FacePersonResponse, FaceUploadResponse

face_router = APIRouter(prefix="/api/faces", tags=["reconnaissance faciale"])

MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 Mo


async def _background_enrich(person_id: int) -> None:
    """Tâche de fond : enrichit une personne via le moteur OSINT."""
    from app.osint.correlator import enrich_person
    from app.db.session import async_session

    try:
        async with async_session() as db:
            result = await enrich_person(person_id, db)
            logger.info(f"[faces/upload] Enrichissement terminé : {result}")
    except Exception as e:
        logger.error(f"[faces/upload] Erreur enrichissement person_id={person_id} : {e}")


@face_router.post("/upload", response_model=FaceUploadResponse)
async def upload_face(
    request: Request,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    username = token_data.get("sub", "unknown")

    # Lecture du fichier en mémoire
    image_bytes = await file.read()

    # Vérification de la taille avant tout traitement
    if len(image_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Fichier trop volumineux ({len(image_bytes)} octets, max {MAX_UPLOAD_SIZE})",
        )

    # Calcul du SHA256
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    logger.info(f"[faces/upload] user={username} hash={image_hash[:16]}... taille={len(image_bytes)}")

    user_id = await resolve_user_id(db, username)
    ip = get_client_ip(request)

    # Anti-doublon : vérifier si ce hash existe déjà en DB
    existing = await db.execute(
        select(Person).where(Person.image_hash == image_hash)
    )
    duplicate = existing.scalar_one_or_none()
    if duplicate:
        logger.info(f"[faces/upload] Doublon détecté — person_id={duplicate.id}")
        await create_audit_log(db, "face_upload", target=f"person_id={duplicate.id},is_new=false,duplicate=true", user_id=user_id, ip_address=ip)
        return FaceUploadResponse(
            person_id=duplicate.id,
            is_new=False,
            confidence_score=duplicate.confidence_score,
            faces_count=1,
            source=duplicate.source,
        )

    # Traitement de l'image (validation format + détection + embedding)
    try:
        result = process_image(image_bytes, image_hash)
    except FacialError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    # Comparaison avec les embeddings existants
    db_result = await db.execute(
        select(Person.id, Person.embedding).where(Person.embedding.is_not(None))
    )
    db_embeddings = [(row[0], row[1]) for row in db_result.all()]

    from app.core.config import get_settings
    settings = get_settings()
    match = compare_embedding(
        result["embedding"], db_embeddings,
        threshold=settings.FACE_MATCH_THRESHOLD, min_margin=settings.FACE_MATCH_MIN_MARGIN,
    )

    # Indexation ES de la personne (créée ou mise à jour) en tâche de fond
    from app.search.indexer import index_person

    if match:
        person_id, score = match
        confidence = round(1.0 - score, 4)

        # Mise à jour du score de confiance
        person_result = await db.execute(select(Person).where(Person.id == person_id))
        person = person_result.scalar_one()
        person.confidence_score = max(person.confidence_score, confidence)
        await db.flush()

        logger.info(
            f"[faces/upload] Match trouvé — person_id={person_id}, "
            f"distance={score}, confidence={confidence}, source={person.source}"
        )
        await create_audit_log(db, "face_upload", target=f"person_id={person_id},is_new=false", user_id=user_id, ip_address=ip)
        if background_tasks:
            # Enrichissement OSINT réservé aux personnes réellement investiguées : une entrée
            # lfw_dataset est une personnalité publique du jeu de données académique, pas le
            # sujet d'une enquête — la lancer dans le moteur OSINT n'a pas de sens ici.
            if person.source == "investigation":
                background_tasks.add_task(_background_enrich, person_id)
            background_tasks.add_task(index_person, person)  # confidence_score mis à jour
        return FaceUploadResponse(
            person_id=person_id,
            is_new=False,
            confidence_score=confidence,
            faces_count=result["faces_count"],
            source=person.source,
        )

    # Pas de match → nouvelle personne
    new_person = Person(
        first_name="Inconnu",
        last_name=f"#{image_hash[:8]}",
        confidence_score=1.0,
        embedding=result["embedding"],
        image_hash=image_hash,
    )
    db.add(new_person)
    await db.flush()

    logger.info(f"[faces/upload] Nouvelle personne créée — person_id={new_person.id}")
    await create_audit_log(db, "face_upload", target=f"person_id={new_person.id},is_new=true", user_id=user_id, ip_address=ip)
    if background_tasks:
        background_tasks.add_task(index_person, new_person)
    return FaceUploadResponse(
        person_id=new_person.id,
        is_new=True,
        confidence_score=1.0,
        faces_count=result["faces_count"],
    )


@face_router.get("/persons", response_model=list[FacePersonResponse])
async def list_face_persons(
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin")),
):
    result = await db.execute(
        select(Person)
        .where(Person.embedding.is_not(None))
        .order_by(Person.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    persons = result.scalars().all()

    return [
        FacePersonResponse(
            id=p.id,
            first_name=p.first_name,
            last_name=p.last_name,
            confidence_score=p.confidence_score,
            has_embedding=True,
            notes=p.notes,
            source=p.source,
            created_at=p.created_at,
        )
        for p in persons
    ]
