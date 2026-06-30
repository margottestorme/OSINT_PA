import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.security import verify_token
from app.db.session import get_db
from app.db.models import Person
from app.facial.engine import compare_embedding, process_image
from app.facial.exceptions import FacialError
from app.schemas.schemas import FacePersonResponse, FaceUploadResponse

face_router = APIRouter(prefix="/api/faces", tags=["reconnaissance faciale"])

MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 Mo


@face_router.post("/upload", response_model=FaceUploadResponse)
async def upload_face(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    user = token_data.get("sub", "unknown")

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
    logger.info(f"[faces/upload] user={user} hash={image_hash[:16]}... taille={len(image_bytes)}")

    # Anti-doublon : vérifier si ce hash existe déjà en DB
    existing = await db.execute(
        select(Person).where(Person.image_hash == image_hash)
    )
    duplicate = existing.scalar_one_or_none()
    if duplicate:
        logger.info(f"[faces/upload] Doublon détecté — person_id={duplicate.id}")
        return FaceUploadResponse(
            person_id=duplicate.id,
            is_new=False,
            confidence_score=duplicate.confidence_score,
            faces_count=1,
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

    match = compare_embedding(result["embedding"], db_embeddings)

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
            f"distance={score}, confidence={confidence}"
        )
        return FaceUploadResponse(
            person_id=person_id,
            is_new=False,
            confidence_score=confidence,
            faces_count=result["faces_count"],
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
    return FaceUploadResponse(
        person_id=new_person.id,
        is_new=True,
        confidence_score=1.0,
        faces_count=result["faces_count"],
    )


@face_router.get("/persons", response_model=list[FacePersonResponse])
async def list_face_persons(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
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
            created_at=p.created_at,
        )
        for p in persons
    ]
