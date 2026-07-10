"""Tests du Module 11 — Elasticsearch (client, indexer, recherche full-text).

Aucun ES réel : AsyncElasticsearch est mocké. Le singleton `elastic._client` est
réinitialisé entre chaque test (fixture autouse) pour l'isolation. Couvre le client
(connect/disconnect/is_available/create_indices), l'indexation unitaire (sans fuite
biométrique, extracted filtré), bulk_reindex, l'endpoint /api/search/fulltext (auth,
q vide, type invalide, résultats mockés), /api/search/status et la dégradation propre.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import Email, Person, SearchResult, Site
from app.search import elastic as elastic_mod
from app.search.elastic import ElasticClient, INDEX_MAPPINGS
from app.search.indexer import (
    bulk_reindex, index_email, index_person, index_search_result, index_site,
)

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)


# =============================================================
# Helpers / fixtures
# =============================================================

def make_fake_es(ping=True, exists=False):
    """Faux client AsyncElasticsearch (méthodes async mockées)."""
    client = MagicMock()
    client.ping = AsyncMock(return_value=ping)
    client.close = AsyncMock()
    client.index = AsyncMock()
    client.search = AsyncMock()
    client.indices = MagicMock()
    client.indices.exists = AsyncMock(return_value=exists)
    client.indices.create = AsyncMock()
    client.indices.put_mapping = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def _reset_elastic():
    """Isole l'état du singleton ES entre les tests."""
    elastic_mod.elastic._client = None
    yield
    elastic_mod.elastic._client = None


@pytest.fixture
def es_client():
    """Injecte un faux client ES (disponible) dans le singleton et le renvoie."""
    fake = make_fake_es(ping=True)
    elastic_mod.elastic._client = fake
    return fake


# =============================================================
# ElasticClient
# =============================================================

@pytest.mark.asyncio
async def test_connect_is_idempotent_and_disconnect():
    ec = ElasticClient()
    fake = make_fake_es()
    with patch("app.search.elastic.AsyncElasticsearch", return_value=fake) as ctor:
        await ec.connect()
        assert ec.client is fake
        await ec.connect()  # 2e appel : ne recrée pas le client
        ctor.assert_called_once()
    await ec.disconnect()
    assert ec.client is None
    fake.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_available_not_connected():
    assert await ElasticClient().is_available() is False


@pytest.mark.asyncio
async def test_is_available_true_on_ping():
    ec = ElasticClient()
    ec._client = make_fake_es(ping=True)
    assert await ec.is_available() is True


@pytest.mark.asyncio
async def test_is_available_false_on_ping_error():
    ec = ElasticClient()
    client = make_fake_es()
    client.ping = AsyncMock(side_effect=RuntimeError("down"))
    ec._client = client
    assert await ec.is_available() is False


@pytest.mark.asyncio
async def test_create_indices_skipped_when_unavailable():
    ec = ElasticClient()
    ec._client = make_fake_es(ping=False)
    await ec.create_indices()
    ec._client.indices.create.assert_not_called()


@pytest.mark.asyncio
async def test_create_indices_creates_missing():
    ec = ElasticClient()
    ec._client = make_fake_es(ping=True, exists=False)
    await ec.create_indices()
    assert ec._client.indices.create.await_count == len(INDEX_MAPPINGS)


@pytest.mark.asyncio
async def test_create_indices_skips_existing():
    ec = ElasticClient()
    ec._client = make_fake_es(ping=True, exists=True)
    await ec.create_indices()
    ec._client.indices.create.assert_not_called()
    # Mapping complété (ex : nouveau champ `source`) même si l'index existe déjà.
    assert ec._client.indices.put_mapping.await_count == len(INDEX_MAPPINGS)


# =============================================================
# Indexation unitaire
# =============================================================

@pytest.mark.asyncio
async def test_index_person_document_without_biometrics(es_client):
    person = SimpleNamespace(
        id=7, first_name="Jean", last_name="Dupont", notes="vip",
        confidence_score=0.8, source="investigation", created_at=NOW,
        embedding=[0.1] * 128, image_hash="deadbeef" * 8,  # NE doivent PAS fuiter
    )
    assert await index_person(person) is True
    kwargs = es_client.index.await_args.kwargs
    assert kwargs["index"] == "osint_persons"
    assert kwargs["id"] == "7"
    doc = kwargs["document"]
    assert "embedding" not in doc and "image_hash" not in doc
    assert doc == {
        "id": "7", "first_name": "Jean", "last_name": "Dupont", "notes": "vip",
        "confidence_score": 0.8, "source": "investigation", "created_at": NOW.isoformat(),
    }


@pytest.mark.asyncio
async def test_index_search_result_extracted_filtered(es_client):
    sr = SimpleNamespace(
        id=3, search_id=9, source="scraper", entity="ex.com", entity_type="domain",
        created_at=NOW, extracted={"emails": ["a@b.fr"], "phones": ["+33"], "names": ["X"]},
    )
    assert await index_search_result(sr) is True
    kwargs = es_client.index.await_args.kwargs
    assert kwargs["index"] == "osint_search_results" and kwargs["id"] == "3"
    # 'phones' n'est pas une sous-clé nested autorisée → filtrée
    assert kwargs["document"]["extracted"] == {"emails": ["a@b.fr"], "names": ["X"]}
    assert kwargs["document"]["search_id"] == "9"


@pytest.mark.asyncio
async def test_index_email(es_client):
    em = SimpleNamespace(id=5, email="a@b.fr", source="hunter", person_id=2, created_at=NOW)
    assert await index_email(em) is True
    kwargs = es_client.index.await_args.kwargs
    assert kwargs["index"] == "osint_emails" and kwargs["id"] == "5"
    assert kwargs["document"] == {
        "email": "a@b.fr", "source": "hunter", "person_id": "2", "created_at": NOW.isoformat(),
    }


@pytest.mark.asyncio
async def test_index_site_maps_discovered_at(es_client):
    site = SimpleNamespace(id=8, url="https://x.fr", server="nginx", discovered_at=NOW)
    assert await index_site(site) is True
    kwargs = es_client.index.await_args.kwargs
    assert kwargs["index"] == "osint_sites" and kwargs["id"] == "8"
    # Site.discovered_at → champ ES created_at
    assert kwargs["document"] == {"url": "https://x.fr", "server": "nginx", "created_at": NOW.isoformat()}


@pytest.mark.asyncio
async def test_index_person_es_down_returns_false():
    # _client déjà None (fixture autouse) → dégradation propre
    person = SimpleNamespace(id=1, first_name="A", last_name="B", notes=None,
                             confidence_score=1.0, source="investigation", created_at=NOW)
    assert await index_person(person) is False


@pytest.mark.asyncio
async def test_index_error_returns_false(es_client):
    es_client.index = AsyncMock(side_effect=RuntimeError("boom"))
    person = SimpleNamespace(id=1, first_name="A", last_name="B", notes=None,
                             confidence_score=1.0, source="investigation", created_at=NOW)
    assert await index_person(person) is False


# =============================================================
# bulk_reindex
# =============================================================

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    """Session async factice : renvoie des lignes selon le modèle du select()."""
    def __init__(self, mapping):
        self._mapping = mapping

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        model = stmt.column_descriptions[0]["entity"]
        return _FakeResult(self._mapping.get(model, []))


@pytest.mark.asyncio
async def test_bulk_reindex_es_down_returns_unavailable():
    # _client None (fixture autouse)
    assert await bulk_reindex() == {
        "available": False, "persons": 0, "search_results": 0, "emails": 0, "sites": 0,
    }


@pytest.mark.asyncio
async def test_bulk_reindex_indexes_all_types(es_client):
    persons = [
        SimpleNamespace(id=1, first_name="A", last_name="B", notes=None, confidence_score=1.0,
                        source="investigation", created_at=NOW),
        SimpleNamespace(id=2, first_name="C", last_name="D", notes="n", confidence_score=0.5,
                        source="lfw_dataset", created_at=NOW),
    ]
    results = [SimpleNamespace(id=1, search_id=1, source="s", entity="e",
                              entity_type="domain", extracted={"emails": ["a@b"]}, created_at=NOW)]
    sites = [SimpleNamespace(id=1, url="https://x", server=None, discovered_at=NOW)]
    mapping = {Person: persons, SearchResult: results, Email: [], Site: sites}

    with patch("app.search.indexer.async_session", return_value=_FakeSession(mapping)):
        result = await bulk_reindex()

    assert result == {"available": True, "persons": 2, "search_results": 1, "emails": 0, "sites": 1}
    assert es_client.index.await_count == 4  # 2 + 1 + 0 + 1


# =============================================================
# fulltext_search (logique interne)
# =============================================================

@pytest.mark.asyncio
async def test_fulltext_search_es_down_graceful():
    from app.search.fulltext import fulltext_search
    # _client None (fixture autouse) → dégradation propre
    assert await fulltext_search("jean", "all", 20, 0) == {"total": 0, "hits": []}


@pytest.mark.asyncio
async def test_fulltext_search_parses_merges_sorts(es_client):
    from app.search.fulltext import fulltext_search

    def _search_side(*args, **kwargs):
        data = {
            "osint_persons": {"hits": {"total": {"value": 1}, "hits": [
                {"_index": "osint_persons", "_id": "1", "_score": 2.0,
                 "_source": {"first_name": "Jean", "last_name": "D"},
                 "highlight": {"first_name": ["<mark>Jean</mark>"]}},
            ]}},
            "osint_emails": {"hits": {"total": {"value": 1}, "hits": [
                {"_index": "osint_emails", "_id": "5", "_score": 3.0,
                 "_source": {"email": "jean@b.fr"}},  # pas de clé highlight
            ]}},
        }
        return data.get(kwargs["index"], {"hits": {"total": {"value": 0}, "hits": []}})

    es_client.search = AsyncMock(side_effect=_search_side)
    result = await fulltext_search("jean", "all", 10, 0)

    assert result["total"] == 2
    # Tri par score décroissant : email (3.0) avant person (2.0)
    assert [h["index"] for h in result["hits"]] == ["osint_emails", "osint_persons"]
    assert result["hits"][0]["id"] == "5"
    assert result["hits"][1]["highlight"] == {"first_name": ["<mark>Jean</mark>"]}


# =============================================================
# Endpoint GET /api/search/fulltext
# =============================================================

_FAKE_HITS = {
    "total": 2,
    "hits": [
        {"index": "osint_persons", "id": "1", "score": 1.5,
         "highlight": {"first_name": ["<mark>Jean</mark>"]},
         "source": {"first_name": "Jean", "last_name": "Dupont"}},
        {"index": "osint_emails", "id": "3", "score": 0.9,
         "highlight": {}, "source": {"email": "a@b.fr"}},
    ],
}


@pytest.mark.asyncio
async def test_fulltext_endpoint_200_with_results(client, auth_headers):
    with patch("app.search.fulltext.fulltext_search",
               new=AsyncMock(return_value=_FAKE_HITS)):
        r = await client.get("/api/search/fulltext?q=jean&type=all", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert len(data["hits"]) == 2
    assert data["hits"][0]["index"] == "osint_persons"
    assert data["hits"][0]["highlight"]["first_name"] == ["<mark>Jean</mark>"]


@pytest.mark.asyncio
async def test_fulltext_endpoint_400_empty_query(client, auth_headers):
    r = await client.get("/api/search/fulltext?q=%20", headers=auth_headers)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_fulltext_endpoint_401_without_jwt(client):
    r = await client.get("/api/search/fulltext?q=jean")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_fulltext_endpoint_403_analyst(client, analyst_headers):
    r = await client.get("/api/search/fulltext?q=jean", headers=analyst_headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_fulltext_endpoint_422_invalid_type(client, auth_headers):
    r = await client.get("/api/search/fulltext?q=jean&type=bogus", headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_fulltext_endpoint_degrades_when_es_down(client, auth_headers):
    """ES down : l'endpoint reste 200 avec {total:0, hits:[]} (pas de crash)."""
    r = await client.get("/api/search/fulltext?q=jean", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"total": 0, "hits": []}


# =============================================================
# Endpoint GET /api/search/status
# =============================================================

@pytest.mark.asyncio
async def test_search_status_available(client, auth_headers):
    with patch("app.search.elastic.elastic.is_available", new=AsyncMock(return_value=True)):
        r = await client.get("/api/search/status", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"available": True}


@pytest.mark.asyncio
async def test_search_status_unavailable(client, auth_headers):
    # _client None (fixture autouse) → is_available False
    r = await client.get("/api/search/status", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"available": False}


@pytest.mark.asyncio
async def test_search_status_401_without_jwt(client):
    r = await client.get("/api/search/status")
    assert r.status_code == 401
