"""
Tests de scripts/index_local_faces.py — indexation locale de sujets consentants.

face_recognition est mocké (comme dans test_facial.py) : on ne teste pas dlib, mais la
logique d'indexation (source=consented_demo, trace de consentement chiffrée, dédoublonnage
par image_hash, parsing des noms). DB SQLite en mémoire dédiée à chaque test.
"""
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import patch

from app.db.models import Person
from app.db.session import Base
from app.facial.exceptions import NoFaceDetectedError
from scripts.index_local_faces import (
    DEMO_SOURCE,
    IndexStats,
    _consent_note,
    _parse_name,
    index_directory,
)

FAKE_EMBEDDING = [0.1] * 128


def _make_person_dir(base, folder_name, colors):
    """Crée `base/folder_name/imgN.jpg` — couleurs distinctes → hashes distincts."""
    d = base / folder_name
    d.mkdir()
    for i, color in enumerate(colors):
        Image.new("RGB", (100, 100), color=color).save(d / f"img{i}.jpg", format="JPEG")
    return d


@pytest_asyncio.fixture
async def db_factory():
    """Fabrique de session sur une base SQLite en mémoire isolée (tables créées)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


# =============================================================
# index_directory
# =============================================================

@pytest.mark.asyncio
async def test_index_directory_adds_consenting_persons(tmp_path, db_factory):
    _make_person_dir(tmp_path, "Alice_Martin", [(10, 20, 30), (40, 50, 60)])
    _make_person_dir(tmp_path, "Bob_Durand", [(70, 80, 90)])

    with patch("scripts.index_local_faces.extract_embedding", return_value=FAKE_EMBEDDING):
        stats = await index_directory(
            tmp_path.as_posix(), session_factory=db_factory, consent_ref="form-2026"
        )

    assert stats.persons == 2
    assert stats.added == 3          # 2 images Alice + 1 Bob
    assert stats.skipped_dup == 0
    assert stats.skipped_noface == 0

    async with db_factory() as db:
        rows = (await db.execute(select(Person))).scalars().all()

    assert len(rows) == 3
    assert all(p.source == DEMO_SOURCE for p in rows)
    assert all(p.embedding == FAKE_EMBEDDING for p in rows)
    # Trace de consentement horodatée + référence, stockée (en clair sur SQLite) dans notes.
    assert all("Consentement" in (p.notes or "") for p in rows)
    assert all("form-2026" in (p.notes or "") for p in rows)

    names = {(p.first_name, p.last_name) for p in rows}
    assert ("Alice", "Martin") in names
    assert ("Bob", "Durand") in names


@pytest.mark.asyncio
async def test_index_directory_is_idempotent(tmp_path, db_factory):
    _make_person_dir(tmp_path, "Alice_Martin", [(10, 20, 30)])

    with patch("scripts.index_local_faces.extract_embedding", return_value=FAKE_EMBEDDING):
        first = await index_directory(tmp_path.as_posix(), session_factory=db_factory)
        second = await index_directory(tmp_path.as_posix(), session_factory=db_factory)

    assert first.added == 1
    assert second.added == 0
    assert second.skipped_dup == 1

    async with db_factory() as db:
        count = len((await db.execute(select(Person))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_index_directory_respects_images_per_person(tmp_path, db_factory):
    _make_person_dir(tmp_path, "Alice_Martin", [(i, i, i) for i in range(10, 60, 10)])  # 5 images

    with patch("scripts.index_local_faces.extract_embedding", return_value=FAKE_EMBEDDING):
        stats = await index_directory(
            tmp_path.as_posix(), session_factory=db_factory, images_per_person=2
        )

    assert stats.persons == 1
    assert stats.added == 2  # tronqué à images_per_person


@pytest.mark.asyncio
async def test_index_directory_skips_images_without_face(tmp_path, db_factory):
    _make_person_dir(tmp_path, "Alice_Martin", [(10, 20, 30)])

    with patch(
        "scripts.index_local_faces.extract_embedding",
        side_effect=NoFaceDetectedError("aucun visage"),
    ):
        stats = await index_directory(tmp_path.as_posix(), session_factory=db_factory)

    assert stats.added == 0
    assert stats.persons == 0
    assert stats.skipped_noface == 1

    async with db_factory() as db:
        count = len((await db.execute(select(Person))).scalars().all())
    assert count == 0


@pytest.mark.asyncio
async def test_index_directory_empty_dir(tmp_path, db_factory):
    with patch("scripts.index_local_faces.extract_embedding", return_value=FAKE_EMBEDDING):
        stats = await index_directory(tmp_path.as_posix(), session_factory=db_factory)

    assert stats == IndexStats()  # tous les compteurs à zéro


@pytest.mark.asyncio
async def test_index_directory_ignores_loose_files(tmp_path, db_factory):
    """Un fichier à la racine (hors sous-dossier personne) est ignoré."""
    Image.new("RGB", (100, 100), color=(1, 2, 3)).save(tmp_path / "orphan.jpg", format="JPEG")
    _make_person_dir(tmp_path, "Alice_Martin", [(10, 20, 30)])

    with patch("scripts.index_local_faces.extract_embedding", return_value=FAKE_EMBEDDING):
        stats = await index_directory(tmp_path.as_posix(), session_factory=db_factory)

    assert stats.persons == 1
    assert stats.added == 1


# =============================================================
# helpers purs
# =============================================================

def test_parse_name_multi():
    assert _parse_name("Alice_Martin") == ("Alice", "Martin")


def test_parse_name_compound_first_name():
    assert _parse_name("Jean_Michel_Dupont") == ("Jean Michel", "Dupont")


def test_parse_name_single_token():
    assert _parse_name("Cher") == ("Cher", "?")


def test_consent_note_without_ref():
    note = _consent_note(None)
    assert "Consentement" in note
    assert DEMO_SOURCE in note
    assert "Référence" not in note


def test_consent_note_with_ref():
    note = _consent_note("formulaires-demo-2026")
    assert "formulaires-demo-2026" in note
    assert "Référence" in note
