from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.db.session import Base
from app.db.models import Person, Email
from app.osint.correlator import enrich_person

TEST_DB_URL = "sqlite+aiosqlite://"
engine_test = create_async_engine(TEST_DB_URL, echo=False)
session_test = async_sessionmaker(engine_test, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db():
    async with session_test() as s:
        yield s


# =============================================================
# enrich_person — personne inexistante
# =============================================================

@pytest.mark.asyncio
async def test_enrich_person_not_found(db):
    result = await enrich_person(9999, db)
    assert result["person_id"] == 9999
    assert result["new_emails"] == 0
    assert result["new_sites"] == 0
    assert result["new_correlations"] == 0


# =============================================================
# enrich_person — personne sans emails
# =============================================================

@pytest.mark.asyncio
async def test_enrich_person_no_emails(db):
    person = Person(first_name="Test", last_name="User", confidence_score=1.0)
    db.add(person)
    await db.flush()

    result = await enrich_person(person.id, db)
    assert result["new_emails"] == 0
    assert result["new_correlations"] == 0


# =============================================================
# enrich_person — enrichissement avec nouvelles entités
# =============================================================

@pytest.mark.asyncio
async def test_enrich_person_discovers_new_entities(db):
    person = Person(first_name="Jean", last_name="Dupont", confidence_score=0.9)
    db.add(person)
    await db.flush()

    email = Email(email="jean@example.com", source="manual", person_id=person.id)
    db.add(email)
    await db.commit()

    fake_correlate = [
        {
            "source": "duckduckgo",
            "entity": "jean@example.com",
            "data": {"RelatedTopics": [{"Text": "Contact: jean.pro@company.org sur company.org"}]},
        },
        {
            "source": "whois",
            "entity": "example.com",
            "data": {"domain_name": "example.com", "org": "Example Inc", "emails": "admin@example.com"},
        },
    ]

    fake_entities = {
        "emails": ["jean.pro@company.org"],
        "domains": ["company.org"],
        "names": ["Jean"],
    }

    with patch("app.osint.correlator.correlate", return_value=fake_correlate), \
         patch("app.osint.correlator.extract_entities", return_value=fake_entities):
        result = await enrich_person(person.id, db)

    assert result["person_id"] == person.id
    assert result["new_emails"] >= 1
    assert result["new_correlations"] >= 1


# =============================================================
# enrich_person — déduplication (même email pas retraité)
# =============================================================

@pytest.mark.asyncio
async def test_enrich_person_deduplication(db):
    person = Person(first_name="Dup", last_name="Test", confidence_score=1.0)
    db.add(person)
    await db.flush()

    email = Email(email="dup@example.com", source="manual", person_id=person.id)
    db.add(email)
    await db.commit()

    call_count = 0

    async def counting_correlate(entity, entity_type):
        nonlocal call_count
        call_count += 1
        # Retourne l'email déjà connu pour forcer la boucle de déduplication
        return [{
            "source": "duckduckgo",
            "entity": entity,
            "data": {"RelatedTopics": [{"Text": f"Info sur {entity}"}]},
        }]

    fake_entities = {"emails": ["dup@example.com"], "domains": [], "names": []}

    with patch("app.osint.correlator.correlate", side_effect=counting_correlate), \
         patch("app.osint.correlator.extract_entities", return_value=fake_entities):
        await enrich_person(person.id, db)

    # correlate() ne doit être appelé qu'une fois pour "dup@example.com"
    assert call_count == 1


# =============================================================
# enrich_person — profondeur max respectée
# =============================================================

@pytest.mark.asyncio
async def test_enrich_person_max_depth(db):
    person = Person(first_name="Deep", last_name="Test", confidence_score=1.0)
    db.add(person)
    await db.flush()

    email = Email(email="level0@example.com", source="manual", person_id=person.id)
    db.add(email)
    await db.commit()

    depth_tracker = []

    async def chained_correlate(entity, entity_type):
        depth_tracker.append(entity)
        return [{
            "source": "duckduckgo",
            "entity": entity,
            "data": {"RelatedTopics": [{"Text": f"Contact: next-{len(depth_tracker)}@example.com"}]},
        }]

    async def chained_entities(text):
        # Chaque appel découvre un nouvel email qui sera dans la queue suivante
        import re
        found = re.findall(r"next-\d+@example\.com", text)
        return {"emails": found, "domains": [], "names": []}

    with patch("app.osint.correlator.correlate", side_effect=chained_correlate), \
         patch("app.osint.correlator.extract_entities", side_effect=chained_entities):
        await enrich_person(person.id, db)

    # Avec MAX_DEPTH=3 : profondeur 0 (level0), 1 (next-1), 2 (next-2)
    # next-3 est découvert à profondeur 2 mais jamais traité (profondeur 3 non atteinte)
    assert len(depth_tracker) <= 3


# =============================================================
# enrich_person — erreur correlate() gérée proprement
# =============================================================

@pytest.mark.asyncio
async def test_enrich_person_correlate_error(db):
    person = Person(first_name="Err", last_name="Test", confidence_score=1.0)
    db.add(person)
    await db.flush()

    email = Email(email="err@example.com", source="manual", person_id=person.id)
    db.add(email)
    await db.commit()

    with patch("app.osint.correlator.correlate", side_effect=Exception("Network timeout")):
        result = await enrich_person(person.id, db)

    # Pas de crash, retourne un résumé vide
    assert result["new_correlations"] == 0
