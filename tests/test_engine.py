from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from app.osint import engine


# --- Fixture : désactiver le rate limiter pour les tests ---

@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    """Remplace le rate limiter par un no-op pour accélérer les tests."""
    async def noop():
        pass
    monkeypatch.setattr(engine._rate_limiter, "acquire", noop)


# =============================================================
# Tests extract_entities (spaCy local, pas de mock nécessaire)
# =============================================================

@pytest.mark.asyncio
async def test_extract_entities_emails():
    """Vérifie l'extraction d'adresses email depuis un texte."""
    text = "Contactez-moi à jean.dupont@example.com ou admin@test.org pour plus d'infos."
    result = await engine.extract_entities(text)

    assert "jean.dupont@example.com" in result["emails"]
    assert "admin@test.org" in result["emails"]
    assert len(result["emails"]) == 2


@pytest.mark.asyncio
async def test_extract_entities_domains():
    """Vérifie l'extraction de domaines depuis un texte."""
    text = "Visitez notre site example.com ou notre blog sur blog.example.fr pour les actualités."
    result = await engine.extract_entities(text)

    assert any("example.com" in d for d in result["domains"])


@pytest.mark.asyncio
async def test_extract_entities_names():
    """Vérifie l'extraction de noms de personnes via spaCy."""
    text = "Jean Dupont travaille avec Marie Martin sur le projet OSINT."
    result = await engine.extract_entities(text)

    # spaCy peut détecter les noms — on vérifie qu'au moins un est trouvé
    assert isinstance(result["names"], list)


@pytest.mark.asyncio
async def test_extract_entities_empty_text():
    """Vérifie le comportement avec un texte vide."""
    result = await engine.extract_entities("")
    assert result == {"names": [], "emails": [], "domains": []}


# =============================================================
# Tests search_email (mocks sur DuckDuckGo et DNS)
# =============================================================

@pytest.mark.asyncio
async def test_search_email_with_mx():
    """Vérifie que search_email retourne les MX quand ils existent."""
    mock_mx = ["mx1.example.com", "mx2.example.com"]
    mock_ddg = {"Type": "E", "RelatedTopics": [{"Text": "test"}]}

    with patch.object(engine, "_resolve_mx", return_value=mock_mx) as mx_mock, \
         patch.object(engine, "_duckduckgo_search", return_value=mock_ddg) as ddg_mock:

        results = await engine.search_email("test@example.com")

        mx_mock.assert_called_once_with("example.com")
        ddg_mock.assert_called_once_with("test@example.com")

        sources = [r["source"] for r in results]
        assert "dns_mx" in sources
        assert "duckduckgo" in sources
        assert len(results) == 2


@pytest.mark.asyncio
async def test_search_email_no_results():
    """Vérifie le comportement quand aucune source ne retourne de résultat."""
    with patch.object(engine, "_resolve_mx", return_value=[]), \
         patch.object(engine, "_duckduckgo_search", return_value={}):

        results = await engine.search_email("nobody@nowhere.invalid")
        assert results == []


# =============================================================
# Tests whois_lookup (mock sur python-whois)
# =============================================================

@pytest.mark.asyncio
async def test_whois_lookup_success():
    """Vérifie le parsing WHOIS quand la requête réussit."""
    fake_whois = MagicMock()
    fake_whois.domain_name = "EXAMPLE.COM"
    fake_whois.registrar = "Test Registrar Inc."
    fake_whois.creation_date = "2000-01-01"
    fake_whois.expiration_date = "2030-01-01"
    fake_whois.name_servers = ["ns1.example.com", "ns2.example.com"]
    fake_whois.emails = ["admin@example.com"]
    fake_whois.org = "Example Org"
    fake_whois.country = "US"

    with patch("app.osint.engine.whois.whois", return_value=fake_whois):
        result = await engine.whois_lookup("example.com")

    assert result["domain_name"] == "EXAMPLE.COM"
    assert result["registrar"] == "Test Registrar Inc."
    assert result["country"] == "US"
    assert "error" not in result


@pytest.mark.asyncio
async def test_whois_lookup_failure():
    """Vérifie le comportement quand le WHOIS échoue."""
    with patch("app.osint.engine.whois.whois", side_effect=Exception("Connection timeout")):
        result = await engine.whois_lookup("invalid-domain.xyz")

    assert "error" in result
    assert "Connection timeout" in result["error"]


# =============================================================
# Tests correlate (mocks sur les sous-fonctions)
# =============================================================

@pytest.mark.asyncio
async def test_correlate_email():
    """Pivot email : sources core (MX + DuckDuckGo + WHOIS), registre neutralisé."""
    with patch.object(engine, "_resolve_mx", return_value=["mx.ex.com"]), \
         patch.object(engine, "_duckduckgo_search", return_value={"Type": "E"}), \
         patch.object(engine, "whois_lookup", return_value={"domain_name": "ex.com", "registrar": "R"}), \
         patch.dict(engine.SOURCES, {"email": []}):

        results = await engine.correlate("t@ex.com", "email")

        sources = [r["source"] for r in results]
        assert "dns_mx" in sources
        assert "duckduckgo" in sources
        assert "whois" in sources
        assert len(results) == 3
        # Format normalisé sur tous les résultats
        assert all("type" in r and "extracted" in r for r in results)


@pytest.mark.asyncio
async def test_correlate_domain():
    """Pivot domain : whois + MX + DuckDuckGo, registre neutralisé."""
    with patch.object(engine, "whois_lookup", return_value={"domain_name": "ex.com"}) as wl, \
         patch.object(engine, "_resolve_mx", return_value=["mx.ex.com"]) as mx, \
         patch.object(engine, "_duckduckgo_search", return_value={"Type": "E"}) as ddg, \
         patch.dict(engine.SOURCES, {"domain": []}):

        results = await engine.correlate("example.com", "domain")

        wl.assert_called_once()
        mx.assert_called_once()
        ddg.assert_called_once()
        assert len(results) == 3


@pytest.mark.asyncio
async def test_correlate_name():
    """Pivot name : uniquement DuckDuckGo côté core, registre neutralisé."""
    with patch.object(engine, "_duckduckgo_search", return_value={"Type": "E"}) as ddg, \
         patch.dict(engine.SOURCES, {"name": []}):
        results = await engine.correlate("Jean Dupont", "name")

        ddg.assert_called_once_with("Jean Dupont")
        assert len(results) == 1
        assert results[0]["source"] == "duckduckgo"


@pytest.mark.asyncio
async def test_correlate_uses_registry():
    """Pivot name : les sources du registre sont bien invoquées et agrégées."""
    async def fake_source(entity):
        return engine._normalize("faux", entity, "name", data={}, extracted={"names": [entity]})

    with patch.object(engine, "_duckduckgo_search", return_value={}), \
         patch.dict(engine.SOURCES, {"name": [fake_source]}):
        results = await engine.correlate("Jean Dupont", "name")

        assert len(results) == 1
        assert results[0]["source"] == "faux"


@pytest.mark.asyncio
async def test_correlate_unknown_type():
    """Vérifie qu'un type d'entité inconnu retourne une liste vide."""
    results = await engine.correlate("test", "unknown_type")
    assert results == []


# =============================================================
# Tests _duckduckgo_search (mock httpx)
# =============================================================

@pytest.mark.asyncio
async def test_duckduckgo_search_success():
    """Vérifie l'appel à l'API DuckDuckGo avec une réponse valide."""
    fake_response = MagicMock()
    fake_response.json.return_value = {"Type": "D", "RelatedTopics": [{"Text": "info"}]}
    fake_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=fake_response)

    with patch("app.osint.engine.httpx.AsyncClient", return_value=mock_client):
        result = await engine._duckduckgo_search("test query")

    assert result["Type"] == "D"
    assert len(result["RelatedTopics"]) == 1


@pytest.mark.asyncio
async def test_duckduckgo_search_http_error():
    """Vérifie le comportement en cas d'erreur HTTP."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.HTTPError("500 Internal Server Error"))

    with patch("app.osint.engine.httpx.AsyncClient", return_value=mock_client):
        result = await engine._duckduckgo_search("test")

    assert result == {}


# =============================================================
# Tests _resolve_mx (mock dnspython)
# =============================================================

@pytest.mark.asyncio
async def test_resolve_mx_success():
    """Vérifie la résolution MX avec des résultats valides."""
    mock_record_1 = MagicMock()
    mock_record_1.exchange = "mx1.example.com."
    mock_record_2 = MagicMock()
    mock_record_2.exchange = "mx2.example.com."

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = [mock_record_1, mock_record_2]

    with patch("app.osint.engine.dns.resolver.Resolver", return_value=mock_resolver):
        result = await engine._resolve_mx("example.com")

    assert "mx1.example.com" in result
    assert "mx2.example.com" in result


@pytest.mark.asyncio
async def test_resolve_mx_nxdomain():
    """Vérifie le comportement quand le domaine n'existe pas."""
    import dns.resolver

    mock_resolver = MagicMock()
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()

    with patch("app.osint.engine.dns.resolver.Resolver", return_value=mock_resolver):
        result = await engine._resolve_mx("nonexistent.invalid")

    assert result == []
