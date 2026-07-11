"""
Tests du pipeline d'enrichissement OSINT post-identification faciale (app/osint/pipeline.py).

Toutes les briques réseau sont mockées au niveau du module pipeline (correlate, enrich_person,
check_all_platforms) : on teste la LOGIQUE d'orchestration (garde-fous, extraction emails/
domaines, slug d'énumération, cycle de vie du job), jamais les sources OSINT réelles. DB SQLite
en mémoire dédiée par test.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Email, EnrichmentJob, Person, SearchHistory, Site
from app.db.session import Base
from app.osint.pipeline import full_enrich_after_face_match


@pytest_asyncio.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(db_factory, *, first="Jean", last="Dupont", source="investigation", with_job=True):
    """Crée une personne (+ un job pending optionnel) ; renvoie (person_id, job_id)."""
    async with db_factory() as db:
        p = Person(first_name=first, last_name=last, confidence_score=1.0, source=source)
        db.add(p)
        await db.flush()
        jid = None
        if with_job:
            job = EnrichmentJob(person_id=p.id, status="pending", triggered_by="manual")
            db.add(job)
            await db.flush()
            jid = job.id
        await db.commit()
        return p.id, jid


def _name_results(emails):
    """Simule le retour de correlate(name) : un OsintResult avec des emails extraits."""
    return [{
        "source": "ddg", "entity": "Jean Dupont", "type": "name",
        "data": {}, "extracted": {"emails": emails, "domains": [], "urls": []},
    }]


def _mock_pipeline(*, name_emails, found_urls, enrich_counts, captured):
    """Contextes de patch des dépendances réseau du pipeline."""
    async def fake_check_all_platforms(slug):
        captured["slug"] = slug
        for u in found_urls:
            yield {"found": True, "url": u, "platform": "P"}
        yield {"found": False, "url": "https://none/x", "platform": "Q"}  # ignoré (found=False)

    return [
        patch("app.osint.pipeline.correlate", AsyncMock(return_value=_name_results(name_emails))),
        patch("app.osint.pipeline.enrich_person", AsyncMock(return_value=enrich_counts)),
        patch("app.osint.pipeline.check_all_platforms", fake_check_all_platforms),
    ]


# =============================================================
# Pipeline complet — name → emails → domains → username enum
# =============================================================

@pytest.mark.asyncio
async def test_pipeline_full_flow(db_factory):
    pid, jid = await _seed(db_factory)
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=["jean@acme.com", "jean@perso.fr"],
            found_urls=["https://x.com/jean", "https://gh.com/jean"],
            enrich_counts={"new_correlations": 2, "new_sites": 3},
            captured=captured,
        ):
            st.enter_context(p)
        async with db_factory() as db:
            summary = await full_enrich_after_face_match(pid, db, job_id=jid)

    assert summary["skipped"] is None
    assert summary["name_searched"] == "Jean Dupont"
    assert summary["emails_found"] == ["jean@acme.com", "jean@perso.fr"]
    assert summary["domains_searched"] == ["acme.com", "perso.fr"]
    assert summary["correlations_created"] == 2
    assert summary["sites_found"] == 5           # 3 (enrich) + 2 (username enum)
    assert summary["username_platforms_found"] == 2

    # Le job a été mené jusqu'à completed avec le résumé compteurs.
    async with db_factory() as db:
        job = await db.get(EnrichmentJob, jid)
        assert job.status == "completed"
        assert job.summary == {
            "emails_found": 2, "sites_found": 5,
            "correlations_created": 2, "username_platforms_found": 2,
        }


@pytest.mark.asyncio
async def test_pipeline_seeds_emails_from_name_results(db_factory):
    pid, jid = await _seed(db_factory)
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=["a@acme.com", "b@acme.com"],
            found_urls=[], enrich_counts={}, captured=captured,
        ):
            st.enter_context(p)
        async with db_factory() as db:
            await full_enrich_after_face_match(pid, db, job_id=jid)

    # Les emails extraits de la recherche par nom sont rattachés à la personne (graines de pivot).
    async with db_factory() as db:
        emails = (await db.execute(
            select(Email).where(Email.person_id == pid)
        )).scalars().all()
    assert {e.email for e in emails} == {"a@acme.com", "b@acme.com"}


@pytest.mark.asyncio
async def test_pipeline_domains_extracted_from_emails(db_factory):
    pid, jid = await _seed(db_factory)
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=["x@corp.io", "y@corp.io", "z@autre.net"],
            found_urls=[], enrich_counts={}, captured=captured,
        ):
            st.enter_context(p)
        async with db_factory() as db:
            summary = await full_enrich_after_face_match(pid, db, job_id=jid)

    assert summary["domains_searched"] == ["autre.net", "corp.io"]  # dédupliqués + triés


@pytest.mark.asyncio
async def test_pipeline_username_enum_uses_correct_slug(db_factory):
    pid, jid = await _seed(db_factory, first="Marie", last="Curie")
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=[], found_urls=["https://x.com/mariecurie"],
            enrich_counts={}, captured=captured,
        ):
            st.enter_context(p)
        async with db_factory() as db:
            summary = await full_enrich_after_face_match(pid, db, job_id=jid)

    assert captured["slug"] == "Marie.Curie"
    assert summary["username_platforms_found"] == 1
    # Le profil trouvé est persisté en Site.
    async with db_factory() as db:
        sites = (await db.execute(select(Site))).scalars().all()
    assert any(s.url == "https://x.com/mariecurie" for s in sites)


@pytest.mark.asyncio
async def test_pipeline_persists_name_search_history(db_factory):
    pid, jid = await _seed(db_factory)
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=["a@b.com"], found_urls=[], enrich_counts={}, captured=captured,
        ):
            st.enter_context(p)
        async with db_factory() as db:
            await full_enrich_after_face_match(pid, db, job_id=jid)

    async with db_factory() as db:
        hist = (await db.execute(select(SearchHistory))).scalars().all()
    assert any(h.query == "Jean Dupont" and h.query_type == "name" for h in hist)


# =============================================================
# Garde-fous : le pipeline refuse tout ce qui n'est pas un sujet d'enquête identifié
# =============================================================

@pytest.mark.asyncio
async def test_pipeline_skips_non_investigation_source(db_factory):
    pid, jid = await _seed(db_factory, source="consented_demo")
    with patch("app.osint.pipeline.correlate", AsyncMock()) as mock_correlate:
        async with db_factory() as db:
            summary = await full_enrich_after_face_match(pid, db, job_id=jid)

    assert summary["skipped"] and "non enrichissable" in summary["skipped"]
    mock_correlate.assert_not_called()  # aucun appel OSINT sur un sujet consentant
    async with db_factory() as db:
        job = await db.get(EnrichmentJob, jid)
        assert job.status == "failed"  # état terminal, jamais coincé


@pytest.mark.asyncio
async def test_pipeline_skips_unknown_person(db_factory):
    pid, jid = await _seed(db_factory, first="Inconnu", last="#abcd1234")
    with patch("app.osint.pipeline.correlate", AsyncMock()) as mock_correlate:
        async with db_factory() as db:
            summary = await full_enrich_after_face_match(pid, db, job_id=jid)

    assert summary["skipped"] == "personne non identifiée"
    mock_correlate.assert_not_called()
    async with db_factory() as db:
        job = await db.get(EnrichmentJob, jid)
        assert job.status == "failed"


@pytest.mark.asyncio
async def test_pipeline_skips_missing_person(db_factory):
    _, jid = await _seed(db_factory, with_job=True)
    async with db_factory() as db:
        summary = await full_enrich_after_face_match(999999, db, job_id=jid)
    assert summary["skipped"] == "personne introuvable"


# =============================================================
# Cycle de vie du job : pending → running → completed / failed
# =============================================================

@pytest.mark.asyncio
async def test_pipeline_job_lifecycle_completed(db_factory):
    pid, jid = await _seed(db_factory)
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=["a@b.com"], found_urls=[], enrich_counts={}, captured=captured,
        ):
            st.enter_context(p)
        async with db_factory() as db:
            await full_enrich_after_face_match(pid, db, job_id=jid)

    async with db_factory() as db:
        job = await db.get(EnrichmentJob, jid)
    assert job.status == "completed"
    assert job.started_at is not None     # passé par "running"
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_pipeline_job_failed_on_unexpected_exception(db_factory):
    pid, jid = await _seed(db_factory)
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=["a@b.com"], found_urls=[], enrich_counts={}, captured=captured,
        ):
            st.enter_context(p)
        # Erreur hors des try/except par étape (filet de sécurité outer) → job "failed".
        st.enter_context(patch(
            "app.osint.pipeline._emails_from_results", side_effect=RuntimeError("boom")
        ))
        async with db_factory() as db:
            summary = await full_enrich_after_face_match(pid, db, job_id=jid)

    assert summary["skipped"] is None
    async with db_factory() as db:
        job = await db.get(EnrichmentJob, jid)
    assert job.status == "failed"
    assert "boom" in (job.error_message or "")


@pytest.mark.asyncio
async def test_pipeline_runs_without_job_id(db_factory):
    # job_id=None : le pipeline tourne sans suivi (aucune erreur, pas de job créé).
    pid, _ = await _seed(db_factory, with_job=False)
    captured = {}
    with ExitStack() as st:
        for p in _mock_pipeline(
            name_emails=["a@b.com"], found_urls=[], enrich_counts={}, captured=captured,
        ):
            st.enter_context(p)
        async with db_factory() as db:
            summary = await full_enrich_after_face_match(pid, db, job_id=None)
    assert summary["skipped"] is None
    async with db_factory() as db:
        jobs = (await db.execute(select(EnrichmentJob))).scalars().all()
    assert jobs == []
