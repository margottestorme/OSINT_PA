"""
Indexation PostgreSQL → Elasticsearch (Module 11).

Chaque fonction `index_*` prend un objet ORM et écrit un document dans l'index ES
correspondant, en *upsert* (l'`_id` ES = clé primaire PostgreSQL → idempotent). Tout
est tolérant aux pannes : si ES est indisponible, on renvoie `False`/compte nul sans
jamais lever — l'app continue de fonctionner sur PostgreSQL seul (dégradation propre).

Confidentialité : les documents ne contiennent QUE des métadonnées publiques
recherchables (jamais d'embedding facial ni de hash d'image — cf. mappings stricts).
"""
from sqlalchemy import select
from loguru import logger

from app.db.models import Email, Person, SearchResult, Site
from app.db.session import async_session
from app.search.elastic import (
    INDEX_EMAILS,
    INDEX_PERSONS,
    INDEX_SEARCH_RESULTS,
    INDEX_SITES,
    elastic,
)

# Sous-champs autorisés du bloc `extracted` (mapping nested strict) : on filtre pour
# ne jamais transmettre une clé imprévue qui ferait échouer l'indexation stricte.
_EXTRACTED_KEYS = ("emails", "domains", "urls", "ips", "names")


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _clean_extracted(extracted: dict | None) -> dict:
    """Ne conserve que les sous-clés connues du mapping nested `extracted`."""
    if not extracted:
        return {}
    return {k: extracted[k] for k in _EXTRACTED_KEYS if k in extracted}


# --- Constructeurs de documents (purs, réutilisés par l'indexation unitaire et le bulk) ---

def _person_doc(person: Person) -> dict:
    # JAMAIS d'embedding ni d'image_hash : données biométriques exclues de l'index.
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "notes": person.notes,
        "confidence_score": person.confidence_score,
        "source": person.source,
        "created_at": _iso(person.created_at),
    }


def _search_result_doc(result: SearchResult) -> dict:
    return {
        "search_id": str(result.search_id),
        "source": result.source,
        "entity": result.entity,
        "entity_type": result.entity_type,
        "extracted": _clean_extracted(result.extracted),
        "created_at": _iso(result.created_at),
    }


def _email_doc(email: Email) -> dict:
    return {
        "email": email.email,
        "source": email.source,
        "person_id": str(email.person_id) if email.person_id is not None else None,
        "created_at": _iso(email.created_at),
    }


def _site_doc(site: Site) -> dict:
    return {
        "url": site.url,
        "server": site.server,
        "created_at": _iso(site.discovered_at),  # Site.discovered_at → champ ES created_at
    }


async def _index_doc(index: str, doc_id: str, document: dict) -> bool:
    """Écrit un document (upsert par _id). False si ES indisponible ou en cas d'erreur.

    Ne pingue pas : si ES est absent, `index()` échoue vite (connexion refusée) et
    l'exception est absorbée — on ne veut aucun surcoût quand ES est présent.
    """
    client = elastic.client
    if client is None:
        return False
    try:
        await client.index(index=index, id=doc_id, document=document)
        return True
    except Exception as e:
        logger.warning(f"[indexer] Échec indexation {index}/{doc_id} : {e}")
        return False


# --- Indexation unitaire ---

async def index_person(person: Person) -> bool:
    return await _index_doc(INDEX_PERSONS, str(person.id), _person_doc(person))


async def index_search_result(result: SearchResult) -> bool:
    return await _index_doc(INDEX_SEARCH_RESULTS, str(result.id), _search_result_doc(result))


async def index_email(email: Email) -> bool:
    return await _index_doc(INDEX_EMAILS, str(email.id), _email_doc(email))


async def index_site(site: Site) -> bool:
    return await _index_doc(INDEX_SITES, str(site.id), _site_doc(site))


# --- Réindexation complète depuis PostgreSQL ---

async def bulk_reindex() -> dict:
    """Réindexe toute la base PostgreSQL vers Elasticsearch (idempotent, upsert par _id).

    Renvoie un compte par type. Si ES est indisponible, ne fait rien et le signale
    via `available: False` (aucune exception levée).
    """
    counts = {"persons": 0, "search_results": 0, "emails": 0, "sites": 0}
    if not await elastic.is_available():
        logger.warning("[indexer] bulk_reindex ignoré : ES indisponible")
        return {"available": False, **counts}

    async with async_session() as session:
        for person in (await session.execute(select(Person))).scalars().all():
            if await index_person(person):
                counts["persons"] += 1
        for result in (await session.execute(select(SearchResult))).scalars().all():
            if await index_search_result(result):
                counts["search_results"] += 1
        for email in (await session.execute(select(Email))).scalars().all():
            if await index_email(email):
                counts["emails"] += 1
        for site in (await session.execute(select(Site))).scalars().all():
            if await index_site(site):
                counts["sites"] += 1

    logger.info(f"[indexer] bulk_reindex terminé : {counts}")
    return {"available": True, **counts}
