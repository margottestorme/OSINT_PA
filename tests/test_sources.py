from contextlib import contextmanager
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import app.osint.engine as engine
from app.core.config import Settings


# =============================================================
# Helpers de mock (aucun appel réseau réel)
# =============================================================

@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Neutralise le rate limiter (pas d'attente 2s dans les tests)."""
    with patch.object(engine._rate_limiter, "acquire", new_callable=AsyncMock):
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
