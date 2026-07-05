import hashlib
from contextlib import contextmanager
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest

import app.osint.engine as engine
from app.core.config import Settings


# =============================================================
# Helpers de mock (aucun appel réseau réel)
# =============================================================

@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Neutralise les rate limiters (pas d'attente 2s/5s dans les tests)."""
    with patch.object(engine._rate_limiter, "acquire", new_callable=AsyncMock), \
         patch.object(engine._leakcheck_limiter, "acquire", new_callable=AsyncMock):
        yield


@contextmanager
def mock_httpx(status=200, json_data=None):
    """Patche httpx.AsyncClient pour renvoyer une réponse contrôlée."""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=json_data if json_data is not None else {})
    resp.raise_for_status = MagicMock()

    client = MagicMock()

    async def _get(*a, **k):
        return resp

    client.get = _get
    cm = MagicMock()

    async def _aenter(*a):
        return client

    async def _aexit(*a):
        return False

    cm.__aenter__ = _aenter
    cm.__aexit__ = _aexit
    with patch("app.osint.engine.httpx.AsyncClient", return_value=cm):
        yield


@contextmanager
def mock_httpx_error():
    """Simule une panne réseau : httpx.AsyncClient lève à l'instanciation."""
    def boom(*a, **k):
        raise RuntimeError("network down")
    with patch("app.osint.engine.httpx.AsyncClient", side_effect=boom):
        yield


@contextmanager
def mock_hibp(pwned_text="", pwned_status=200, leak_json=None, leak_status=200,
              pwned_exc=None, leak_exc=None, calls=None):
    """Patche httpx pour les deux endpoints HIBP, dispatch selon l'URL.

    - pwned_text/status : réponse TEXTE de api.pwnedpasswords.com/range/{prefix}
    - leak_json/status  : réponse JSON de leakcheck.io/api/public
    - pwned_exc/leak_exc : exception à lever pour simuler une panne réseau ciblée
    - calls : si fourni, liste où sont consignés les (url, kwargs) appelés (k-anonymity)
    """
    def make_resp(text_val, json_val, status):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text_val
        resp.json = MagicMock(return_value=json_val or {})

        def rfs():
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

        resp.raise_for_status = MagicMock(side_effect=rfs)
        return resp

    async def _get(url, *a, **k):
        if calls is not None:
            calls.append((url, k))
        if "pwnedpasswords" in url:
            if pwned_exc:
                raise pwned_exc
            return make_resp(pwned_text, None, pwned_status)
        if "leakcheck" in url:
            if leak_exc:
                raise leak_exc
            return make_resp("", leak_json, leak_status)
        return make_resp("", {}, 404)

    client = MagicMock()
    client.get = _get
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("app.osint.engine.httpx.AsyncClient", return_value=cm):
        yield


def settings_with(**kw):
    """Patche get_settings avec des clés API contrôlées."""
    base = {"VIRUSTOTAL_API_KEY": "", "OTX_API_KEY": "", "HUNTER_API_KEY": "", "GITHUB_TOKEN": ""}
    base.update(kw)
    return patch("app.osint.engine.get_settings", return_value=Settings(**base))


def mock_resolve(mapping):
    """Patche engine._resolve avec une table {(domain, rdtype): [valeurs]}."""
    async def _fake(domain, rdtype):
        return mapping.get((domain, rdtype), [])
    return patch("app.osint.engine._resolve", side_effect=_fake)


# =============================================================
# Gravatar
# =============================================================

@pytest.mark.asyncio
async def test_gravatar_found():
    data = {"entry": [{
        "displayName": "Jean D",
        "name": {"formatted": "Jean Dupont"},
        "profileUrl": "https://gravatar.com/jd",
        "urls": [{"value": "https://blog.jd.fr"}],
        "accounts": [{"url": "https://twitter.com/jd"}],
    }]}
    with mock_httpx(200, data):
        r = await engine._source_gravatar("jean@example.com")
    assert r["source"] == "gravatar"
    assert r["extracted"]["names"] == ["Jean D", "Jean Dupont"]
    assert "https://twitter.com/jd" in r["extracted"]["urls"]


@pytest.mark.asyncio
async def test_gravatar_404():
    with mock_httpx(404):
        assert await engine._source_gravatar("inconnu@example.com") is None


@pytest.mark.asyncio
async def test_gravatar_error():
    with mock_httpx_error():
        assert await engine._source_gravatar("x@y.fr") is None


# =============================================================
# Wikipedia
# =============================================================

@pytest.mark.asyncio
async def test_wikipedia_found():
    data = {"query": {"search": [{"title": "Jean Dupont"}, {"title": "Autre"}]}}
    with mock_httpx(200, data):
        r = await engine._source_wikipedia("Jean Dupont")
    assert r["extracted"]["names"] == ["Jean Dupont", "Autre"]
    assert r["extracted"]["urls"][0] == "https://fr.wikipedia.org/wiki/Jean_Dupont"


@pytest.mark.asyncio
async def test_wikipedia_empty():
    with mock_httpx(200, {"query": {"search": []}}):
        assert await engine._source_wikipedia("zzzz") is None


@pytest.mark.asyncio
async def test_wikipedia_error():
    with mock_httpx_error():
        assert await engine._source_wikipedia("x") is None


# =============================================================
# GitHub
# =============================================================

@pytest.mark.asyncio
async def test_github_found():
    data = {"items": [{"login": "jdupont", "html_url": "https://github.com/jdupont"}]}
    with settings_with(), mock_httpx(200, data):
        r = await engine._source_github("Jean Dupont")
    assert r["extracted"]["names"] == ["jdupont"]
    assert r["extracted"]["urls"] == ["https://github.com/jdupont"]


@pytest.mark.asyncio
async def test_github_empty():
    with settings_with(), mock_httpx(200, {"items": []}):
        assert await engine._source_github("zzzz") is None


@pytest.mark.asyncio
async def test_github_error():
    with settings_with(), mock_httpx_error():
        assert await engine._source_github("x") is None


# =============================================================
# crt.sh
# =============================================================

@pytest.mark.asyncio
async def test_crtsh_found():
    certs = [{"name_value": "a.example.com\n*.example.com"}, {"name_value": "b.example.com\nother.org"}]
    with mock_httpx(200, certs):
        r = await engine._source_crtsh("example.com")
    assert r["extracted"]["domains"] == ["a.example.com", "b.example.com", "example.com"]


@pytest.mark.asyncio
async def test_crtsh_empty():
    with mock_httpx(200, []):
        assert await engine._source_crtsh("example.com") is None


@pytest.mark.asyncio
async def test_crtsh_error():
    with mock_httpx_error():
        assert await engine._source_crtsh("example.com") is None


# =============================================================
# DNS records
# =============================================================

@pytest.mark.asyncio
async def test_dns_records_all_types():
    mapping = {
        ("example.com", "A"): ["1.2.3.4"],
        ("example.com", "AAAA"): ["::1"],
        ("example.com", "NS"): ["ns1.example.com."],
        ("example.com", "CNAME"): [],
        ("example.com", "TXT"): ['"v=spf1 include:_spf.google.com ~all"', '"autre"'],
        ("_dmarc.example.com", "TXT"): ['"v=DMARC1; p=none"'],
    }
    with mock_resolve(mapping):
        r = await engine._source_dns_records("example.com")
    assert r["data"]["SPF"].startswith("v=spf1")
    assert r["data"]["DMARC"].startswith("v=DMARC1")
    assert r["extracted"]["ips"] == ["1.2.3.4", "::1"]
    assert r["extracted"]["domains"] == ["ns1.example.com"]


@pytest.mark.asyncio
async def test_dns_records_nxdomain():
    with mock_resolve({}):  # tout vide
        assert await engine._source_dns_records("inexistant.invalid") is None


# =============================================================
# RDAP
# =============================================================

@pytest.mark.asyncio
async def test_rdap_complete():
    obj = {
        "ldhName": "example.com",
        "status": ["active"],
        "events": [],
        "nameservers": [{"ldhName": "NS1.EXAMPLE.COM."}],
        "entities": [{"vcardArray": ["vcard", [
            ["version", {}, "text", "4.0"],
            ["email", {}, "text", "abuse@example.com"],
        ]]}],
    }
    with mock_httpx(200, obj):
        r = await engine._source_rdap("example.com")
    assert r["extracted"]["domains"] == ["ns1.example.com"]
    assert r["extracted"]["emails"] == ["abuse@example.com"]


@pytest.mark.asyncio
async def test_rdap_404():
    with mock_httpx(404):
        assert await engine._source_rdap("inexistant.invalid") is None


@pytest.mark.asyncio
async def test_rdap_error():
    with mock_httpx_error():
        assert await engine._source_rdap("example.com") is None


# =============================================================
# Shodan InternetDB
# =============================================================

@pytest.mark.asyncio
async def test_shodan_ip_with_data():
    idb = {"ip": "9.9.9.9", "ports": [80, 443], "cpes": ["cpe:/a:nginx"],
           "vulns": ["CVE-2021-1"], "hostnames": ["web.example.com"], "tags": ["cdn"]}
    with mock_resolve({("example.com", "A"): ["9.9.9.9"]}), mock_httpx(200, idb):
        r = await engine._source_shodan_internetdb("example.com")
    assert r["data"]["9.9.9.9"]["ports"] == [80, 443]
    assert r["extracted"]["domains"] == ["web.example.com"]
    assert r["extracted"]["ips"] == ["9.9.9.9"]


@pytest.mark.asyncio
async def test_shodan_404_ip():
    with mock_resolve({("example.com", "A"): ["9.9.9.9"]}), mock_httpx(404):
        assert await engine._source_shodan_internetdb("example.com") is None


@pytest.mark.asyncio
async def test_shodan_no_ip():
    with mock_resolve({}):  # aucune IP résolue
        assert await engine._source_shodan_internetdb("example.com") is None


@pytest.mark.asyncio
async def test_shodan_error():
    with mock_resolve({("example.com", "A"): ["9.9.9.9"]}), mock_httpx_error():
        assert await engine._source_shodan_internetdb("example.com") is None


# =============================================================
# Freemium — dormant (sans clé) → None
# =============================================================

@pytest.mark.asyncio
async def test_virustotal_no_key():
    with settings_with():
        assert await engine._source_virustotal("example.com") is None


@pytest.mark.asyncio
async def test_otx_no_key():
    with settings_with():
        assert await engine._source_otx("example.com") is None


@pytest.mark.asyncio
async def test_hunter_no_key():
    with settings_with():
        assert await engine._source_hunter("a@b.fr") is None


# =============================================================
# Freemium — avec clé mockée → extracted correct
# =============================================================

@pytest.mark.asyncio
async def test_virustotal_with_key():
    vt = {"data": {"attributes": {
        "reputation": -5,
        "last_analysis_stats": {"malicious": 2},
        "categories": {"x": "y"},
        "last_dns_records": [{"type": "A", "value": "1.2.3.4"}, {"type": "NS", "value": "ns1.example.com."}],
    }}}
    with settings_with(VIRUSTOTAL_API_KEY="K"), mock_httpx(200, vt):
        r = await engine._source_virustotal("example.com")
    assert r["data"]["reputation"] == -5
    assert r["extracted"]["ips"] == ["1.2.3.4"]
    assert r["extracted"]["domains"] == ["ns1.example.com"]


@pytest.mark.asyncio
async def test_otx_with_key():
    otx = {"passive_dns": [
        {"hostname": "a.example.com.", "address": "1.2.3.4"},
        {"hostname": "b.example.com", "address": "5.6.7.8"},
    ]}
    with settings_with(OTX_API_KEY="K"), mock_httpx(200, otx):
        r = await engine._source_otx("example.com")
    assert r["extracted"]["domains"] == ["a.example.com", "b.example.com"]
    assert r["extracted"]["ips"] == ["1.2.3.4", "5.6.7.8"]


@pytest.mark.asyncio
async def test_hunter_with_key():
    hun = {"data": {"status": "valid", "result": "deliverable", "score": 95,
                    "sources": [{"uri": "http://x.fr/p", "domain": "x.fr"}]}}
    with settings_with(HUNTER_API_KEY="K"), mock_httpx(200, hun):
        r = await engine._source_hunter("jean@x.fr")
    assert r["data"]["result"] == "deliverable"
    assert r["extracted"]["urls"] == ["http://x.fr/p"]
    assert r["extracted"]["domains"] == ["x.fr"]


# =============================================================
# Source scraping (_source_scraper) — intégration au registre (BUG 2)
# =============================================================

@pytest.mark.asyncio
async def test_source_scraper_registered_passes_entity_type():
    """Régression BUG 2 : le scraper est enregistré via lambda → appelable avec un seul
    argument (entity), en transmettant le bon entity_type. Enregistré nu, il levait
    'missing 1 required positional argument: entity_type'."""
    with patch.object(engine, "_source_scraper", new=AsyncMock(return_value=None)) as m:
        for etype in ("email", "domain", "name"):
            # On repère les lambdas déléguant à _source_scraper (via leur closure), sans
            # dépendre de leur position dans le registre (d'autres sources peuvent suivre).
            for src in engine.SOURCES[etype]:
                if getattr(src, "__name__", "") == "<lambda>" and "_source_scraper" in src.__code__.co_names:
                    await src("cible")
    assert m.await_args_list == [
        call("cible", "email"), call("cible", "domain"), call("cible", "name"),
    ]


@pytest.mark.asyncio
async def test_all_registered_sources_take_single_arg():
    """Garde-fou : toute source du registre doit être appelable avec (entity,) seul."""
    import inspect
    for etype, sources in engine.SOURCES.items():
        for src in sources:
            required = [
                p for p in inspect.signature(src).parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
            ]
            assert len(required) == 1, f"{etype} : {src} n'accepte pas exactement 1 argument"


@pytest.mark.asyncio
async def test_source_scraper_domain_aggregates():
    """domain : scrape https://<domaine> et agrège les entités des pages."""
    scraped = [{
        "url": "https://example.com",
        "emails": ["a@example.com"], "domains": ["cdn.example.com"],
        "urls": ["https://example.com/x"], "names": ["Example"], "phones": ["+33 1 23 45"],
    }]
    with patch.object(engine, "scrape_batch", new=AsyncMock(return_value=scraped)) as sb:
        r = await engine._source_scraper("example.com", "domain")
    sb.assert_awaited_once_with(["https://example.com"], max_pages=3)
    assert r["source"] == "scraper" and r["type"] == "domain"
    assert r["extracted"]["emails"] == ["a@example.com"]
    assert r["extracted"]["domains"] == ["cdn.example.com"]
    assert r["data"]["phones"] == ["+33 1 23 45"]


@pytest.mark.asyncio
async def test_source_scraper_email_no_urls_returns_none():
    """email sans URL DuckDuckGo exploitable → None (scrape_batch jamais appelé)."""
    with patch.object(engine, "_duckduckgo_search", new=AsyncMock(return_value={})), \
         patch.object(engine, "scrape_batch", new=AsyncMock()) as sb:
        r = await engine._source_scraper("a@b.fr", "email")
    assert r is None
    sb.assert_not_awaited()


# =============================================================
# HIBP — vérification de fuites de données (Module 13)
# =============================================================

def _sha1_upper(email: str) -> str:
    return hashlib.sha1(email.strip().lower().encode()).hexdigest().upper()


@pytest.mark.asyncio
async def test_hibp_pwned_password_found():
    """Email dont le SHA1 apparaît dans Pwned Passwords → compte > 0."""
    email = "pwned@example.com"
    suffix = _sha1_upper(email)[5:]
    pwned_text = f"{suffix}:42\r\nFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:7"
    with mock_hibp(pwned_text=pwned_text, leak_json={"found": 0}):
        r = await engine._source_hibp(email)
    assert r["source"] == "hibp" and r["type"] == "email"
    assert r["data"]["pwned_password_count"] == 42
    assert r["extracted"]["emails"] == []  # enveloppe extracted vide


@pytest.mark.asyncio
async def test_hibp_pwned_password_clean():
    """Email absent de la réponse (préfixe partagé, suffixe absent) → compte 0."""
    with mock_hibp(pwned_text="FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:3", leak_json={"found": 0}):
        r = await engine._source_hibp("clean@example.com")
    assert r["data"]["pwned_password_count"] == 0
    assert r["data"]["leakcheck_found"] is False


@pytest.mark.asyncio
async def test_hibp_leakcheck_found():
    """LeakCheck trouve l'email → leakcheck_found True + sources listées."""
    leak = {"found": 2, "sources": [{"name": "Collection1"}, {"name": "Breach2"}]}
    with mock_hibp(pwned_text="", leak_json=leak):
        r = await engine._source_hibp("leaked@example.com")
    assert r["data"]["leakcheck_found"] is True
    assert r["data"]["leakcheck_sources"] == ["Collection1", "Breach2"]


@pytest.mark.asyncio
async def test_hibp_both_fail_returns_none():
    """Les deux sources en erreur réseau → None (dégradation propre)."""
    with mock_hibp(pwned_exc=RuntimeError("net"), leak_exc=RuntimeError("net")):
        assert await engine._source_hibp("x@y.fr") is None


@pytest.mark.asyncio
async def test_hibp_kanonymity_email_never_sent():
    """K-anonymity : seuls les 5 premiers chars du SHA1 sont envoyés à HIBP."""
    email = "victim@example.com"
    sha1 = _sha1_upper(email)
    calls = []
    with mock_hibp(pwned_text="FFFF:1", leak_json={"found": 0}, calls=calls):
        await engine._source_hibp(email)

    pwned_urls = [url for url, _ in calls if "pwnedpasswords" in url]
    assert pwned_urls, "l'endpoint Pwned Passwords doit être interrogé"
    url = pwned_urls[0]
    assert url.endswith("/range/" + sha1[:5])          # uniquement les 5 premiers chars
    assert email not in url                             # jamais l'email en clair
    assert sha1 not in url and sha1[5:] not in url      # ni le hash complet ni le suffixe


# =============================================================
# correlate() avec le registre
# =============================================================

@pytest.mark.asyncio
async def test_correlate_empty_registry():
    """Registre vide → uniquement les sources historiques."""
    with patch.object(engine, "_duckduckgo_search", return_value={"Type": "E"}), \
         patch.dict(engine.SOURCES, {"name": []}):
        results = await engine.correlate("Jean Dupont", "name")
    assert len(results) == 1
    assert results[0]["source"] == "duckduckgo"


@pytest.mark.asyncio
async def test_correlate_source_returns_none_ignored():
    """Une source du registre qui renvoie None est ignorée proprement."""
    async def none_source(entity):
        return None

    with patch.object(engine, "_duckduckgo_search", return_value={"Type": "E"}), \
         patch.dict(engine.SOURCES, {"name": [none_source]}):
        results = await engine.correlate("Jean Dupont", "name")
    assert len(results) == 1  # seulement DDG, la source None est écartée


@pytest.mark.asyncio
async def test_correlate_multiple_sources_aggregated():
    """Plusieurs sources du registre sont agrégées avec l'historique."""
    async def src_a(entity):
        return engine._normalize("a", entity, "name", data={}, extracted={"names": [entity]})

    async def src_b(entity):
        return engine._normalize("b", entity, "name", data={}, extracted={"urls": ["http://b"]})

    with patch.object(engine, "_duckduckgo_search", return_value={"Type": "E"}), \
         patch.dict(engine.SOURCES, {"name": [src_a, src_b]}):
        results = await engine.correlate("Jean Dupont", "name")
    sources = [r["source"] for r in results]
    assert sources == ["duckduckgo", "a", "b"]
