from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import SearchHistory, SearchResult


@pytest_asyncio.fixture(autouse=True)
def _mock_background_search():
    """Override du fixture conftest : ici on veut tester le VRAI _run_search."""
    yield


# =============================================================
# Persistance : _run_search écrit SearchHistory + un SearchResult par OsintResult
# =============================================================

@pytest.mark.asyncio
async def test_run_search_persists_results():
    from app.api.routes import _run_search
    from tests.conftest import session_test

    fake_results = [
        {
            "source": "dns", "entity": "github.com", "type": "domain",
            "data": {"A": ["140.82.121.4"]},
            "extracted": {"ips": ["140.82.121.4"], "domains": ["ns1.example.com"]},
        },
        {
            "source": "rdap", "entity": "github.com", "type": "domain",
            "data": {"ldhName": "github.com"},
            "extracted": {"domains": ["ns1.example.com", "ns2.example.com"]},
        },
    ]

    async def fake_correlate(query, query_type):
        return fake_results

    # Le vrai _run_search utilise async_session (prod) : on le pointe vers la DB de test
    with patch("app.osint.engine.correlate", side_effect=fake_correlate), \
         patch("app.db.session.async_session", session_test):
        await _run_search("github.com", "domain")

    async with session_test() as db:
        histories = (await db.execute(
            select(SearchHistory).where(SearchHistory.query == "github.com")
        )).scalars().all()
        assert len(histories) == 1
        assert histories[0].result_count == 2

        results = (await db.execute(
            select(SearchResult).where(SearchResult.search_id == histories[0].id)
        )).scalars().all()
        assert len(results) == 2
        sources = {r.source for r in results}
        assert sources == {"dns", "rdap"}
        # Le champ extracted est bien persisté (JSON)
        dns_row = next(r for r in results if r.source == "dns")
        assert dns_row.extracted["ips"] == ["140.82.121.4"]


# =============================================================
# Endpoint GET /api/history/{id}/results — 200 (contenu) et 404
# =============================================================

@pytest.mark.asyncio
async def test_get_search_results_endpoint(client, auth_headers):
    from tests.conftest import session_test

    async with session_test() as db:
        sh = SearchHistory(query="example.com", query_type="domain", result_count=2)
        db.add(sh)
        await db.flush()
        db.add(SearchResult(
            search_id=sh.id, source="dns", entity="example.com", entity_type="domain",
            data={"A": ["1.2.3.4"]}, extracted={"ips": ["1.2.3.4"]},
        ))
        db.add(SearchResult(
            search_id=sh.id, source="rdap", entity="example.com", entity_type="domain",
            data={}, extracted={"domains": ["ns.example.com"]},
        ))
        await db.commit()
        sid = sh.id

    # 200 — contenu complet
    resp = await client.get(f"/api/history/{sid}/results", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert {r["source"] for r in data} == {"dns", "rdap"}
    dns_row = next(r for r in data if r["source"] == "dns")
    assert dns_row["extracted"]["ips"] == ["1.2.3.4"]
    assert dns_row["search_id"] == sid

    # 404 — recherche inexistante
    resp404 = await client.get("/api/history/99999/results", headers=auth_headers)
    assert resp404.status_code == 404
    assert "introuvable" in resp404.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_search_results_with_list_data(client, auth_headers):
    """Régression audit : les sources github/wikipedia stockent `data` en LISTE.
    L'endpoint doit renvoyer 200 (avant : 500 ResponseValidationError car data était dict|None)."""
    from tests.conftest import session_test

    async with session_test() as db:
        sh = SearchHistory(query="Linus Torvalds", query_type="name", result_count=2)
        db.add(sh)
        await db.flush()
        db.add(SearchResult(
            search_id=sh.id, source="github", entity="Linus Torvalds", entity_type="name",
            data=[{"login": "torvalds", "id": 1024025}],   # LISTE (réponse GitHub)
            extracted={"names": ["torvalds"], "urls": ["https://github.com/torvalds"]},
        ))
        db.add(SearchResult(
            search_id=sh.id, source="wikipedia", entity="Linus Torvalds", entity_type="name",
            data=[{"title": "Linus Torvalds"}],            # LISTE (réponse Wikipedia)
            extracted={"names": ["Linus Torvalds"]},
        ))
        await db.commit()
        sid = sh.id

    resp = await client.get(f"/api/history/{sid}/results", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    gh = next(r for r in data if r["source"] == "github")
    assert isinstance(gh["data"], list)                    # la liste est préservée
    assert gh["data"][0]["login"] == "torvalds"
