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
async def test_check_username_found_text():
    # Symétrique de not_found_text : shell générique 200 sans marqueur d'absence stable, mais
    # le profil réel contient une preuve de présence (ex : Instagram, Bluesky, Telegram).
    platform = {"name": "Instagram", "url": "https://www.instagram.com/{username}/",
                "category": "social", "check_method": "found_text",
                "found_text": "• Instagram photos and videos"}
    # 200 avec le marqueur de présence -> found
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="<title>John (@john) • Instagram photos and videos</title>"))
    async with httpx.AsyncClient(transport=transport) as s:
        r = await uc.check_username("john", platform, s)
    assert r["found"] is True
    assert r["error"] is None

    # 200 sans le marqueur (shell générique, pseudo inexistant) -> not found
    transport2 = httpx.MockTransport(lambda req: httpx.Response(200, text="<title>Instagram</title>"))
    async with httpx.AsyncClient(transport=transport2) as s:
        r2 = await uc.check_username("john", platform, s)
    assert r2["found"] is False
    assert r2["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_check_username_ambiguous_status_reported_as_error(status):
    # Anti-bot/rate-limit (401/403/429) ou erreur serveur (5xx) : jamais un faux "pas trouvé"
    # silencieux -> remonté en `error`. Découvert sur Instagram (401 "Please wait a few
    # minutes" masquant un compte réel, cf. commentaire sur PLATFORMS).
    platform = {"name": "GitLab", "url": "https://gitlab.com/{username}", "category": "dev",
                "check_method": "status_code"}
    transport = httpx.MockTransport(lambda req: httpx.Response(status))
    async with httpx.AsyncClient(transport=transport) as s:
        r = await uc.check_username("john", platform, s)
    assert r["found"] is False
    assert r["error"] == f"HTTP {status}"


@pytest.mark.asyncio
async def test_check_username_check_url_used_for_request_url_for_display():
    # Mécanisme générique (plus utilisé par aucune plateforme active depuis Instagram
    # le 2026-07-07, cf. son commentaire dans PLATFORMS) : l'API interrogée (check_url)
    # peut différer de la page affichée (url).
    platform = {"name": "ExempleAPI", "url": "https://www.example.com/{username}/",
                "check_url": "https://api.example.com/v1/users/?username={username}",
                "category": "social", "check_method": "status_code"}
    requested_urls = []

    def handler(req):
        requested_urls.append(str(req.url))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as s:
        r = await uc.check_username("john", platform, s)

    assert requested_urls == ["https://api.example.com/v1/users/?username=john"]
    assert r["url"] == "https://www.example.com/john/"  # page humaine, pas l'API
    assert r["found"] is True


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
# Sources à API officielle (clé optionnelle) — YouTube, Twitch
# =============================================================

def _settings(**kw):
    from app.core.config import Settings
    base = {"YOUTUBE_API_KEY": "", "TWITCH_CLIENT_ID": "", "TWITCH_CLIENT_SECRET": ""}
    base.update(kw)
    return Settings(**base)


def test_build_dynamic_platforms_empty_without_keys():
    with patch("app.core.config.get_settings", return_value=_settings()):
        assert uc._build_dynamic_platforms() == []


def test_build_dynamic_platforms_with_keys():
    with patch("app.core.config.get_settings",
               return_value=_settings(YOUTUBE_API_KEY="yk", TWITCH_CLIENT_ID="ci", TWITCH_CLIENT_SECRET="cs")):
        dynamic = uc._build_dynamic_platforms()
    names = {p["name"] for p in dynamic}
    assert names == {"YouTube", "Twitch"}


@pytest.mark.asyncio
async def test_check_youtube_found():
    def handler(req):
        assert "forHandle=johndoe" in str(req.url)
        assert "key=yk" in str(req.url)
        return httpx.Response(200, json={"items": [{"id": "abc"}]})

    with patch("app.core.config.get_settings", return_value=_settings(YOUTUBE_API_KEY="yk")):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as s:
            r = await uc._check_youtube("johndoe", {}, s)
    assert r["found"] is True
    assert r["platform"] == "YouTube"
    assert r["url"] == "https://www.youtube.com/@johndoe"


@pytest.mark.asyncio
async def test_check_youtube_not_found_empty_items():
    def handler(req):
        return httpx.Response(200, json={"items": []})

    with patch("app.core.config.get_settings", return_value=_settings(YOUTUBE_API_KEY="yk")):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as s:
            r = await uc._check_youtube("zqxvwk9284nonexistent", {}, s)
    assert r["found"] is False
    assert r["error"] is None


@pytest.mark.asyncio
async def test_check_twitch_found_fetches_and_caches_token():
    uc._twitch_token_cache["token"] = None
    uc._twitch_token_cache["expires_at"] = 0.0
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if "id.twitch.tv" in str(req.url):
            return httpx.Response(200, json={"access_token": "tok123", "expires_in": 3600})
        assert req.headers["Authorization"] == "Bearer tok123"
        assert req.headers["Client-Id"] == "ci"
        return httpx.Response(200, json={"data": [{"id": "1", "login": "johndoe"}]})

    settings = _settings(TWITCH_CLIENT_ID="ci", TWITCH_CLIENT_SECRET="cs")
    with patch("app.core.config.get_settings", return_value=settings):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as s:
            r = await uc._check_twitch("johndoe", {}, s)
            # 2e appel : le token déjà en cache ne doit pas être re-demandé.
            r2 = await uc._check_twitch("johndoe", {}, s)

    assert r["found"] is True
    assert r2["found"] is True
    assert sum("id.twitch.tv" in c for c in calls) == 1  # un seul appel au token, pas deux


@pytest.mark.asyncio
async def test_check_twitch_not_found_empty_data():
    uc._twitch_token_cache["token"] = "tok123"
    uc._twitch_token_cache["expires_at"] = 9_999_999_999.0

    def handler(req):
        return httpx.Response(200, json={"data": []})

    settings = _settings(TWITCH_CLIENT_ID="ci", TWITCH_CLIENT_SECRET="cs")
    with patch("app.core.config.get_settings", return_value=settings):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as s:
            r = await uc._check_twitch("zqxvwk9284nonexistent", {}, s)
    assert r["found"] is False
    assert r["error"] is None


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

    # Neutralise les sources dynamiques (YouTube/Twitch) : leur activation dépend du vrai
    # .env, indépendant de ce test — jamais d'appel réseau réel en test (cf. conftest).
    with patch.object(uc, "check_username", side_effect=fake_check), \
         patch.object(uc, "_build_dynamic_platforms", return_value=[]):
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
    # 157 définies au total (voir ALL_PLATFORM_DEFINITIONS) ; 59 désactivées (reliable=False :
    # audit du 2026-07-07 en 2 vagues + RubyGems/NameMC détectés par
    # scripts/audit_username_platforms.py) ; Instagram réparée via found_text (page de profil
    # server-rendue pour le SEO) après l'abandon de l'API check_url, trop vite rate-limitée
    # -> 98 actives.
    assert uc.PLATFORM_COUNT == 98
    assert len(uc.ALL_PLATFORM_DEFINITIONS) == 157
    names = [p["name"] for p in uc.PLATFORMS]
    assert len(set(names)) == len(names)  # noms uniques
    assert all("{username}" in p["url"] for p in uc.PLATFORMS)
    assert set(uc.platform_categories()) == {
        "dev", "social", "gaming", "forum", "creative",
        "professional", "dating", "crypto", "news",
    }


# =============================================================
# Moteur — generate_username_variants (set élargi)
# =============================================================

def test_generate_variants_two_words_covers_expected_forms():
    variants = uc.generate_username_variants("margotte.storme")
    assert variants[0] == "margotte.storme"  # l'original toujours en premier
    assert "margottestorme" in variants        # sans séparateur
    assert "margotte_storme" in variants        # underscore
    assert "margotte-storme" in variants        # tiret
    assert "storme.margotte" in variants        # ordre inversé
    assert "m.storme" in variants               # initiale prénom
    assert "margotte.storme1" in variants        # suffixe numérique sur l'original
    assert "margottestorme123" in variants        # suffixe numérique sur la forme sans séparateur
    # Pas de doublon (comparaison insensible à la casse)
    assert len(variants) == len({v.lower() for v in variants})
    assert len(variants) <= uc.MAX_VARIANTS


def test_generate_variants_single_word_includes_split_insertions_and_suffixes():
    # "johndoe" (aucun séparateur) doit aussi tenter d'insérer un séparateur à des positions
    # plausibles (préfixe court en tête), pas seulement des suffixes numériques — sinon un
    # compte réel "john_doe" ne serait jamais retrouvé en cherchant juste "johndoe".
    variants = uc.generate_username_variants("johndoe")
    assert variants[0] == "johndoe"
    assert "john_doe" in variants
    assert "john.doe" in variants
    assert "johndoe1" in variants
    assert "johndoe123" in variants


def test_generate_variants_single_word_real_world_case():
    # Cas réel rapporté : chercher "somargotte" doit permettre de retrouver un compte
    # "so_margotte" (préfixe court "so" = diminutif/initiales de prénom).
    variants = uc.generate_username_variants("somargotte")
    assert "so_margotte" in variants
    assert "so.margotte" in variants


def test_generate_variants_empty_or_blank():
    assert uc.generate_username_variants("") == []
    assert uc.generate_username_variants("   ") == []


def test_generate_variants_never_exceeds_url_charset():
    # Toute variante générée doit rester compatible avec la validation username (3-30, [A-Za-z0-9._-])
    for seed in ("margotte.storme", "jean-pierre.dupont", "a.b", "johndoe"):
        for v in uc.generate_username_variants(seed):
            assert uc._VARIANT_RE.match(v), f"variante invalide générée : {v}"


# =============================================================
# Moteur — _check_platform_variants (toutes les variantes testées, plusieurs comptes possibles)
# =============================================================

@pytest.mark.asyncio
async def test_check_platform_variants_single_match_returns_one_entry():
    platform = {"name": "X", "url": "https://x.example/{username}", "category": "social",
                "check_method": "status_code"}
    variants = ["margotte.storme", "margottestorme", "margotte_storme"]

    async def fake_check(v, p, s):
        return {"platform": "X", "url": p["url"].format(username=v), "category": "social",
                "found": v == "margottestorme", "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check):
        results = await uc._check_platform_variants(variants, platform, None, asyncio.Semaphore(5))

    assert len(results) == 1
    assert results[0]["found"] is True
    assert results[0]["variant"] == "margottestorme"
    assert results[0]["platform"] == "X"
    assert results[0]["is_primary"] is True


@pytest.mark.asyncio
async def test_check_platform_variants_multiple_matches_returns_all_in_variant_order():
    # Deux vrais comptes distincts sous deux variantes différentes (ex : mstorme ET
    # margottestorme tous les deux réels sur GitHub) — les DEUX doivent remonter.
    platform = {"name": "X", "url": "https://x.example/{username}", "category": "social",
                "check_method": "status_code"}
    variants = ["margotte.storme", "margottestorme", "mstorme", "margotte_storme"]

    async def fake_check(v, p, s):
        return {"platform": "X", "url": p["url"].format(username=v), "category": "social",
                "found": v in ("margottestorme", "mstorme"), "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check):
        results = await uc._check_platform_variants(variants, platform, None, asyncio.Semaphore(5))

    assert len(results) == 2
    # Ordre = ordre des variantes (margottestorme avant mstorme dans la liste `variants`)
    assert [r["variant"] for r in results] == ["margottestorme", "mstorme"]
    assert results[0]["is_primary"] is True
    assert results[1]["is_primary"] is False
    assert all(r["found"] for r in results)


@pytest.mark.asyncio
async def test_check_platform_variants_none_found_falls_back_to_original():
    platform = {"name": "X", "url": "https://x.example/{username}", "category": "social",
                "check_method": "status_code"}
    variants = ["margotte.storme", "margottestorme"]

    async def fake_check(v, p, s):
        return {"platform": "X", "url": p["url"].format(username=v), "category": "social",
                "found": False, "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check):
        results = await uc._check_platform_variants(variants, platform, None, asyncio.Semaphore(5))

    assert len(results) == 1
    assert results[0]["found"] is False
    assert results[0]["variant"] == "margotte.storme"  # repli sur l'originale
    assert results[0]["is_primary"] is True


@pytest.mark.asyncio
async def test_check_platform_variants_retry_sweep_recovers_intermittent_miss():
    # Instagram sert de façon intermittente un shell générique même pour un compte réel
    # (cf. commentaire sur PLATFORMS) : retry_sweep=3 relance jusqu'à 3 passages complets
    # supplémentaires si rien n'est trouvé, pour rattraper ce type de faux négatif.
    platform = {"name": "Instagram", "url": "https://instagram.example/{username}",
                "category": "social", "check_method": "found_text", "retry_sweep": 3}
    variants = ["steven.gausset", "stevengausset"]
    call_count = {"stevengausset": 0}

    async def fake_check(v, p, s):
        found = False
        if v == "stevengausset":
            call_count["stevengausset"] += 1
            found = call_count["stevengausset"] >= 3  # raté 2 fois, trouvé au 3e passage
        return {"platform": "Instagram", "url": p["url"].format(username=v), "category": "social",
                "found": found, "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check):
        results = await uc._check_platform_variants(variants, platform, None, asyncio.Semaphore(5))

    assert len(results) == 1
    assert results[0]["found"] is True
    assert results[0]["variant"] == "stevengausset"
    assert call_count["stevengausset"] == 3  # bien trois passages effectués


@pytest.mark.asyncio
async def test_check_platform_variants_retry_sweep_stops_after_exhausting_attempts():
    # Si le compte n'existe vraiment nulle part : épuise les 3 passages supplémentaires
    # (4 tentatives au total) puis conclut à l'absence, sans boucler indéfiniment.
    platform = {"name": "Instagram", "url": "https://instagram.example/{username}",
                "category": "social", "check_method": "found_text", "retry_sweep": 3}
    variants = ["ghost.user"]
    calls = []

    async def fake_check(v, p, s):
        calls.append(v)
        return {"platform": "Instagram", "url": p["url"].format(username=v), "category": "social",
                "found": False, "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check):
        results = await uc._check_platform_variants(variants, platform, None, asyncio.Semaphore(5))

    assert len(calls) == 4  # 1 passage initial + 3 relances
    assert results[0]["found"] is False


@pytest.mark.asyncio
async def test_check_platform_variants_no_retry_sweep_by_default():
    # Sans le flag retry_sweep sur la plateforme : un seul passage, pas de seconde chance.
    platform = {"name": "X", "url": "https://x.example/{username}", "category": "social",
                "check_method": "status_code"}
    variants = ["margotte.storme", "margottestorme"]
    calls = []

    async def fake_check(v, p, s):
        calls.append(v)
        return {"platform": "X", "url": p["url"].format(username=v), "category": "social",
                "found": False, "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check):
        await uc._check_platform_variants(variants, platform, None, asyncio.Semaphore(5))

    assert len(calls) == len(variants)  # un seul passage, pas de doublon


@pytest.mark.asyncio
async def test_check_all_platforms_result_carries_variant_field():
    async def fake_check(v, p, s):
        return {"platform": p["name"], "url": p["url"].format(username=v),
                "category": p["category"], "found": False, "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check), \
         patch.object(uc, "_build_dynamic_platforms", return_value=[]):
        results = [r async for r in uc.check_all_platforms("margotte.storme", max_concurrent=20)]

    assert len(results) == uc.PLATFORM_COUNT  # un résultat par plateforme (aucun match ici)
    assert all("variant" in r for r in results)
    assert all(r["is_primary"] is True for r in results)


@pytest.mark.asyncio
async def test_check_all_platforms_multiple_matches_yield_more_than_platform_count():
    # GitHub matche 2 variantes -> 2 événements pour GitHub -> total > PLATFORM_COUNT
    async def fake_check(v, p, s):
        found = p["name"] == "GitHub" and v in ("margottestorme", "mstorme")
        return {"platform": p["name"], "url": p["url"].format(username=v),
                "category": p["category"], "found": found, "response_time_ms": 1.0, "error": None}

    with patch.object(uc, "check_username", side_effect=fake_check), \
         patch.object(uc, "_build_dynamic_platforms", return_value=[]):
        results = [r async for r in uc.check_all_platforms("margotte.storme", max_concurrent=20)]

    assert len(results) == uc.PLATFORM_COUNT + 1
    github_results = [r for r in results if r["platform"] == "GitHub"]
    assert len(github_results) == 2
    assert {r["variant"] for r in github_results} == {"margottestorme", "mstorme"}
    assert sum(1 for r in github_results if r["is_primary"]) == 1


# =============================================================
# Endpoint — /api/username/platforms
# =============================================================

@pytest.mark.asyncio
async def test_platforms_endpoint(client, auth_headers):
    # Neutralise les sources dynamiques : leur activation dépend du vrai .env (YouTube/Twitch),
    # indépendant de ce test — le total attendu ici est celui du registre statique seul.
    with patch("app.api.username_routes._build_dynamic_platforms", return_value=[]):
        r = await client.get("/api/username/platforms", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 98
    assert len(data["categories"]) == 9


@pytest.mark.asyncio
async def test_platforms_endpoint_includes_dynamic_sources_when_configured(client, auth_headers):
    dynamic = [{"name": "YouTube", "category": "creative", "checker": None}]
    with patch("app.api.username_routes._build_dynamic_platforms", return_value=dynamic):
        r = await client.get("/api/username/platforms", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total"] == 99


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
