"""Tests du Module 15 — énumération de pseudos (moteur, SSE, /save, intégration Module 14).

Aucune requête réseau réelle : tout est mocké (httpx.MockTransport pour le moteur,
patch de check_all_platforms / _source_github pour les endpoints).
"""
import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from unittest.mock import patch

from app.osint import username_checker as uc
from app.db.models import Site, UsernameResult, UsernameSearch


# --------------------------------------------------------------------------- #
# Faux générateur partagé (résultats déterministes, sans réseau)              #
# --------------------------------------------------------------------------- #

# Trois cas distincts : trouvé / non trouvé (propre) / erreur réseau
FAKE_RESULTS = [
    {"platform": "GitHub", "url": "https://github.com/john", "category": "dev",
     "found": True, "response_time_ms": 100.0, "error": None},
    {"platform": "GitLab", "url": "https://gitlab.com/john", "category": "dev",
     "found": False, "response_time_ms": 80.0, "error": None},
    {"platform": "Steam", "url": "https://steamcommunity.com/id/john", "category": "gaming",
     "found": False, "response_time_ms": 0.0, "error": "ConnectError"},
]


async def _fake_gen(username, max_concurrent=20):
    for r in FAKE_RESULTS:
        yield r


# =============================================================
# Moteur — check_username (httpx.MockTransport)
# =============================================================

@pytest.mark.asyncio
async def test_check_username_found_200():
    platform = {"name": "GitHub", "url": "https://github.com/{username}", "category": "dev",
                "check_method": "status_code"}
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as s:
        r = await uc.check_username("john", platform, s)
    assert r["found"] is True
    assert r["error"] is None
    assert r["platform"] == "GitHub"
    assert r["url"] == "https://github.com/john"
    assert r["response_time_ms"] >= 0


@pytest.mark.asyncio
async def test_check_username_not_found_404():
    platform = {"name": "GitLab", "url": "https://gitlab.com/{username}", "category": "dev",
                "check_method": "status_code"}
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as s:
        r = await uc.check_username("john", platform, s)
    assert r["found"] is False
    assert r["error"] is None


@pytest.mark.asyncio
async def test_check_username_soft404_not_found_text():
    platform = {"name": "Steam", "url": "https://steamcommunity.com/id/{username}",
                "category": "gaming", "check_method": "not_found_text",
                "not_found_text": "The specified profile could not be found."}
    # 200 mais contient le marqueur d'absence -> not found
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="The specified profile could not be found."))
    async with httpx.AsyncClient(transport=transport) as s:
        r = await uc.check_username("john", platform, s)
    assert r["found"] is False
    assert r["error"] is None

    # 200 sans le marqueur -> found
    transport2 = httpx.MockTransport(lambda req: httpx.Response(200, text="<html>profile</html>"))
    async with httpx.AsyncClient(transport=transport2) as s:
        r2 = await uc.check_username("john", platform, s)
    assert r2["found"] is True


@pytest.mark.asyncio
async def test_check_username_network_error():
    platform = {"name": "GitHub", "url": "https://github.com/{username}", "category": "dev",
                "check_method": "status_code"}

    def boom(req):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as s:
        r = await uc.check_username("john", platform, s)
    assert r["found"] is False
    assert r["error"] == "ConnectError"


# =============================================================
# Moteur — check_all_platforms (yield + concurrence)
# =============================================================

@pytest.mark.asyncio
async def test_check_all_platforms_yields_all_with_bounded_concurrency():
    tracker = {"cur": 0, "max": 0}

    async def fake_check(username, platform, session):
        tracker["cur"] += 1
        tracker["max"] = max(tracker["max"], tracker["cur"])
        await asyncio.sleep(0.001)
        tracker["cur"] -= 1
        return {"platform": platform["name"], "url": platform["url"],
                "category": platform["category"], "found": False,
                "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check):
        results = [r async for r in uc.check_all_platforms("john", max_concurrent=20)]

    assert len(results) == uc.PLATFORM_COUNT
    assert tracker["max"] <= 20  # le semaphore borne la concurrence


# =============================================================
# Moteur — normalize_username, registre
# =============================================================

def test_normalize_username_strip_and_case_preserved():
    assert uc.normalize_username("  John_Doe  ") == "John_Doe"  # strip, casse préservée
    assert uc.normalize_username("MixedCase") == "MixedCase"
    assert uc.normalize_username("") == ""
    assert uc.normalize_username(None) == ""


def test_platform_registry():
    assert uc.PLATFORM_COUNT == 157
    names = [p["name"] for p in uc.PLATFORMS]
    assert len(set(names)) == len(names)  # noms uniques
    assert all("{username}" in p["url"] for p in uc.PLATFORMS)
    assert set(uc.platform_categories()) == {
        "dev", "social", "gaming", "forum", "creative",
        "professional", "dating", "crypto", "news",
    }


# =============================================================
# Endpoint — /api/username/platforms
# =============================================================

@pytest.mark.asyncio
async def test_platforms_endpoint(client, auth_headers):
    r = await client.get("/api/username/platforms", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 157
    assert len(data["categories"]) == 9


@pytest.mark.asyncio
async def test_platforms_no_jwt_401(client):
    r = await client.get("/api/username/platforms")
    assert r.status_code == 401


# =============================================================
# Endpoint SSE — /api/username/check
# =============================================================

@pytest.mark.asyncio
async def test_check_sse_stream(client, auth_headers):
    from tests.conftest import session_test
    with patch("app.api.username_routes.check_all_platforms", _fake_gen), \
         patch("app.db.session.async_session", session_test):
        r = await client.get("/api/username/check?username=john", headers=auth_headers)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    import json
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    # 3 plateformes + 1 event "complete"
    assert len(events) == 4
    complete = events[-1]
    assert complete["type"] == "complete"
    assert complete["found"] == 1
    assert complete["not_found"] == 1
    assert complete["errors"] == 1


@pytest.mark.asyncio
async def test_check_no_jwt_401(client):
    r = await client.get("/api/username/check?username=john")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_check_username_too_short_422(client, auth_headers):
    r = await client.get("/api/username/check?username=ab", headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_check_username_invalid_chars_422(client, auth_headers):
    r = await client.get("/api/username/check?username=bad name!", headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_check_rate_limit_429(client, auth_headers):
    from tests.conftest import session_test
    with patch("app.api.username_routes.check_all_platforms", _fake_gen), \
         patch("app.db.session.async_session", session_test):
        r1 = await client.get("/api/username/check?username=johndoe", headers=auth_headers)
        r2 = await client.get("/api/username/check?username=johndoe", headers=auth_headers)
    assert r1.status_code == 200
    assert r2.status_code == 429  # 1 recherche / 30s par utilisateur


# =============================================================
# Endpoint — /api/username/save
# =============================================================

@pytest.mark.asyncio
async def test_save_persists(client, auth_headers):
    from tests.conftest import session_test
    from sqlalchemy import select, func
    # Sans GitHub -> pas d'enrichissement de fond (aucun réseau)
    results = [
        {"platform": "Twitter/X", "url": "https://x.com/john", "category": "social",
         "found": True, "response_time_ms": 90.0, "error": None},
        {"platform": "Steam", "url": "https://steamcommunity.com/id/john", "category": "gaming",
         "found": False, "response_time_ms": 0.0, "error": "ConnectError"},
    ]
    r = await client.post("/api/username/save",
                          json={"username": "john", "results": results}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed"
    assert data["total_platforms"] == 2
    search_id = data["search_id"]

    async with session_test() as s:
        search = await s.get(UsernameSearch, search_id)
        assert search.found_count == 1
        n_results = await s.scalar(
            select(func.count()).select_from(UsernameResult).where(UsernameResult.search_id == search_id))
        assert n_results == 2
        # Une seule URL trouvée -> un seul Site créé
        n_sites = await s.scalar(select(func.count()).select_from(Site))
        assert n_sites == 1
        site_url = await s.scalar(select(Site.url))
        assert site_url == "https://x.com/john"


@pytest.mark.asyncio
async def test_save_invalid_username_422(client, auth_headers):
    r = await client.post("/api/username/save",
                          json={"username": "ab", "results": []}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_save_no_jwt_401(client):
    r = await client.post("/api/username/save", json={"username": "john", "results": []})
    assert r.status_code == 401


# =============================================================
# Intégration Module 14 — rattachement, timeline, PDF
# =============================================================

async def _seed_username_search(username="john", found=2, total=3):
    from tests.conftest import session_test
    async with session_test() as s:
        us = UsernameSearch(username=username, found_count=found,
                            total_platforms=total, status="completed")
        s.add(us)
        await s.commit()
        return us.id


async def _create_investigation(client, headers, title="Enquête pseudo"):
    r = await client.post("/api/investigations", json={"title": title}, headers=headers)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_attach_username_search(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    us_id = await _seed_username_search()
    r = await client.post(f"/api/investigations/{inv}/entities",
                          json={"entity_type": "username_search", "entity_id": us_id},
                          headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["entity_type"] == "username_search"


@pytest.mark.asyncio
async def test_attach_nonexistent_username_search_404(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    r = await client.post(f"/api/investigations/{inv}/entities",
                          json={"entity_type": "username_search", "entity_id": 9999},
                          headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_timeline_contains_username_search(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    us_id = await _seed_username_search(username="john", found=2, total=3)
    await client.post(f"/api/investigations/{inv}/entities",
                      json={"entity_type": "username_search", "entity_id": us_id},
                      headers=auth_headers)
    r = await client.get(f"/api/investigations/{inv}/timeline", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["entity_type"] == "username_search"
    assert "@john" in items[0]["label"]
    assert "2/3" in items[0]["label"]


@pytest.mark.asyncio
async def test_pdf_includes_username_section(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    us_id = await _seed_username_search()
    await client.post(f"/api/investigations/{inv}/entities",
                      json={"entity_type": "username_search", "entity_id": us_id},
                      headers=auth_headers)
    r = await client.get(f"/api/investigations/{inv}/export/pdf", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert b"%PDF-" == r.content[:5]
    # La section « Recherches de pseudos » figure dans le PDF (pageCompression=0 -> texte lisible)
    assert b"pseudos" in r.content


# =============================================================
# Intégration OSINT — persistance Sites & enrichissement GitHub
# =============================================================

@pytest.mark.asyncio
async def test_persist_found_sites_dedup(client, auth_headers):
    from tests.conftest import session_test
    from sqlalchemy import select, func
    from app.api.username_routes import _persist_found_sites
    results = [
        {"platform": "GitHub", "url": "https://github.com/john", "found": True},
        {"platform": "X", "url": "https://x.com/john", "found": True},
        {"platform": "Steam", "url": "https://steam/john", "found": False},  # non trouvé -> ignoré
    ]
    async with session_test() as s:
        added = await _persist_found_sites(s, results)
        await s.commit()
        # 2ᵉ passe : mêmes URLs -> aucun doublon
        added2 = await _persist_found_sites(s, results)
        await s.commit()
        n = await s.scalar(select(func.count()).select_from(Site))
    assert added == 2
    assert added2 == 0
    assert n == 2


@pytest.mark.asyncio
async def test_enrich_github_creates_sites():
    from tests.conftest import session_test
    from sqlalchemy import select, func
    from app.api.username_routes import _enrich_github

    async def fake_gh(name):
        return {"source": "github", "entity": name, "type": "name", "data": [],
                "extracted": {"names": [name], "urls": ["https://github.com/enriched"]}}

    with patch("app.osint.engine._source_github", fake_gh), \
         patch("app.db.session.async_session", session_test):
        await _enrich_github("john")

    async with session_test() as s:
        urls = (await s.execute(select(Site.url))).scalars().all()
    assert "https://github.com/enriched" in urls
