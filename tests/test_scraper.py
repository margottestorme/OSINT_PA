"""Tests du module de scraping web (app/osint/scraper.py).

Aucun appel réseau réel : httpx.AsyncClient est patché et le rate limiter
neutralisé (pas d'attente 2s). Couvre la blacklist réseaux sociaux, le respect
de robots.txt, les garde-fous de fetch_page (taille max, erreurs HTTP/réseau),
l'extraction d'entités et l'agrégation par lot.
"""
from unittest.mock import patch, MagicMock, AsyncMock

import httpx
import pytest

import app.osint.scraper as scraper


# =============================================================
# Helpers de mock (aucun appel réseau réel)
# =============================================================

@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Neutralise le rate limiter du scraper (pas d'attente 2s dans les tests)."""
    with patch.object(scraper._rate_limiter, "acquire", new_callable=AsyncMock):
        yield


def mock_client(page_html="<html></html>", page_status=200,
                robots_text="", robots_status=404, page_content=None,
                robots_raise=False):
    """Patche scraper.httpx.AsyncClient ; dispatch selon l'URL (robots.txt vs page).

    - robots_status/robots_text : contrôlent la réponse à /robots.txt
    - page_status/page_html/page_content : contrôlent la réponse à la page
    - robots_raise : la requête robots.txt lève (hôte injoignable)
    """
    def make_resp(text, status, content=None):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        resp.content = content if content is not None else text.encode()

        def raise_for_status():
            if status >= 400:
                raise httpx.HTTPStatusError(
                    "err", request=MagicMock(),
                    response=MagicMock(status_code=status),
                )

        resp.raise_for_status = MagicMock(side_effect=raise_for_status)
        return resp

    async def _get(url, *a, **k):
        if url.endswith("/robots.txt"):
            if robots_raise:
                raise httpx.ConnectError("robots injoignable")
            return make_resp(robots_text, robots_status)
        return make_resp(page_html, page_status, page_content)

    client = MagicMock()
    client.get = _get
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.osint.scraper.httpx.AsyncClient", return_value=cm)


def mock_client_boom():
    """Simule une panne réseau : httpx.AsyncClient lève à l'instanciation."""
    def boom(*a, **k):
        raise RuntimeError("network down")
    return patch("app.osint.scraper.httpx.AsyncClient", side_effect=boom)


# =============================================================
# _is_blacklisted
# =============================================================

@pytest.mark.parametrize("url", [
    "https://www.facebook.com/jean",
    "https://twitter.com/jean",
    "https://x.com/jean",
    "https://www.instagram.com/jean",
    "https://fr.linkedin.com/in/jean",
    "https://www.tiktok.com/@jean",
])
def test_is_blacklisted_social(url):
    assert scraper._is_blacklisted(url) is True


@pytest.mark.parametrize("url", [
    "https://example.com/page",
    "https://blog.example.fr/article",
    "https://mon-site.org",
])
def test_is_blacklisted_regular(url):
    assert scraper._is_blacklisted(url) is False


@pytest.mark.parametrize("host", [
    "fr.wikipedia.org", "ar.wikipedia.org", "wikipedia.org",
    "www.wikidata.org", "wikimedia.org", "commons.wikimedia.org",
    "www.mediawiki.org", "wikimediafoundation.org",
])
def test_is_wikimedia_noise_true(host):
    assert scraper._is_wikimedia_noise(host) is True


@pytest.mark.parametrize("host", [
    "example.com", "not-wikipedia.org.evil.com", "www.jeuxvideo.com",
])
def test_is_wikimedia_noise_false(host):
    assert scraper._is_wikimedia_noise(host) is False


# =============================================================
# fetch_page
# =============================================================

@pytest.mark.asyncio
async def test_fetch_page_success():
    with mock_client(page_html="<html>OK</html>"):
        assert await scraper.fetch_page("https://example.com") == "<html>OK</html>"


@pytest.mark.asyncio
async def test_fetch_page_blacklisted_no_network():
    """URL blacklistée : refusée sans même tenter de requête réseau."""
    with mock_client_boom():  # lèverait si le réseau était sollicité
        assert await scraper.fetch_page("https://facebook.com/jean") is None


@pytest.mark.asyncio
async def test_fetch_page_robots_disallow():
    with mock_client(robots_text="User-agent: *\nDisallow: /", robots_status=200):
        assert await scraper.fetch_page("https://example.com/secret") is None


@pytest.mark.asyncio
async def test_fetch_page_robots_allow():
    with mock_client(page_html="<html>Y</html>",
                     robots_text="User-agent: *\nAllow: /", robots_status=200):
        assert await scraper.fetch_page("https://example.com/ok") == "<html>Y</html>"


@pytest.mark.asyncio
async def test_fetch_page_robots_absent_permissive():
    """robots.txt absent (404) → autorisé par défaut (permissif standard)."""
    with mock_client(page_html="<html>Z</html>", robots_status=404):
        assert await scraper.fetch_page("https://example.com") == "<html>Z</html>"


@pytest.mark.asyncio
async def test_fetch_page_robots_unreachable_permissive():
    """robots.txt injoignable → autorisé (l'exception ne bloque pas le scraping)."""
    with mock_client(page_html="<html>W</html>", robots_raise=True):
        assert await scraper.fetch_page("https://example.com") == "<html>W</html>"


@pytest.mark.asyncio
async def test_fetch_page_too_large():
    big = b"x" * (scraper.MAX_PAGE_SIZE + 1)
    with mock_client(page_html="<html>big</html>", page_content=big):
        assert await scraper.fetch_page("https://example.com/big") is None


@pytest.mark.asyncio
async def test_fetch_page_http_error():
    with mock_client(page_status=500):
        assert await scraper.fetch_page("https://example.com/err") is None


@pytest.mark.asyncio
async def test_fetch_page_network_error():
    with mock_client_boom():
        assert await scraper.fetch_page("https://example.com") is None


# =============================================================
# scrape_entities (fetch_page mocké → extraction en isolation)
# =============================================================

_SAMPLE_HTML = """<html><head>
<title>Jean Dupont - Page</title>
<meta name="description" content="Description de test">
<script>var x = "hidden@script.com";</script>
<style>.c{color:red}</style>
</head><body>
<h1>Titre Principal</h1><h2>Sous-titre</h2>
<p>Contact: jean.dupont@example.com, jean.dupont@example.com,
   alice@example.com ou +33 1 23 45 67 89</p>
<a href="https://autre-domaine.fr/page">ext</a>
<a href="https://example.com/interne">int</a>
<a href="https://facebook.com/jean">fb</a>
<a href="/relatif">rel</a>
</body></html>"""


@pytest.mark.asyncio
async def test_scrape_entities_extraction():
    with patch("app.osint.scraper.fetch_page", AsyncMock(return_value=_SAMPLE_HTML)):
        r = await scraper.scrape_entities("https://example.com")

    # Emails : triés, dédupliqués, hors <script>
    assert r["emails"] == ["alice@example.com", "jean.dupont@example.com"]
    assert "hidden@script.com" not in r["emails"]
    # Domaines : uniquement les hôtes externes non blacklistés (pas example.com ni facebook)
    assert r["domains"] == ["autre-domaine.fr"]
    # URLs absolues collectées (facebook conservé dans urls, exclu des domains)
    assert r["urls"] == [
        "https://autre-domaine.fr/page",
        "https://example.com/interne",
        "https://facebook.com/jean",
    ]
    # Noms : title + h1 + h2 dans l'ordre du document
    assert r["names"] == ["Jean Dupont - Page", "Titre Principal", "Sous-titre"]
    assert r["phones"] == ["+33 1 23 45 67 89"]
    assert r["description"] == "Description de test"


@pytest.mark.asyncio
async def test_scrape_entities_excludes_wikimedia_navigation_links():
    """Une page Wikipédia liste ~80 liens interlangues/projets sœurs sur CHAQUE article —
    aucun rapport avec le sujet recherché. Ils ne doivent polluer ni domains ni urls."""
    html = """<html><body>
    <a href="https://ar.wikipedia.org/wiki/Sujet">ar</a>
    <a href="https://www.wikidata.org/wiki/Q123">wikidata</a>
    <a href="https://commons.wikimedia.org/wiki/File:x">commons</a>
    <a href="https://vraie-source.fr/article">source pertinente</a>
    </body></html>"""
    with patch("app.osint.scraper.fetch_page", AsyncMock(return_value=html)):
        r = await scraper.scrape_entities("https://fr.wikipedia.org/wiki/Sujet")
    assert r["domains"] == ["vraie-source.fr"]
    assert r["urls"] == ["https://vraie-source.fr/article"]


@pytest.mark.asyncio
async def test_scrape_entities_removes_script_style():
    html = '<html><body><script>evil@hidden.com</script>Visible: real@site.fr</body></html>'
    with patch("app.osint.scraper.fetch_page", AsyncMock(return_value=html)):
        r = await scraper.scrape_entities("https://example.com")
    assert r["emails"] == ["real@site.fr"]


@pytest.mark.asyncio
async def test_scrape_entities_empty_page():
    """Page minimale : dict renvoyé avec des champs vides, sans erreur."""
    with patch("app.osint.scraper.fetch_page", AsyncMock(return_value="<html></html>")):
        r = await scraper.scrape_entities("https://example.com")
    assert r == {"emails": [], "domains": [], "urls": [],
                 "names": [], "phones": [], "description": ""}


@pytest.mark.asyncio
async def test_scrape_entities_fetch_failed():
    """fetch_page renvoie None (refus/erreur) → scrape_entities renvoie None."""
    with patch("app.osint.scraper.fetch_page", AsyncMock(return_value=None)):
        assert await scraper.scrape_entities("https://example.com") is None


# =============================================================
# scrape_batch (scrape_entities mocké → agrégation en isolation)
# =============================================================

@pytest.mark.asyncio
async def test_scrape_batch_aggregates_url_key():
    pages = [{"emails": ["a@x.fr"]}, {"emails": ["b@y.fr"]}]
    with patch("app.osint.scraper.scrape_entities", AsyncMock(side_effect=pages)):
        r = await scraper.scrape_batch(["https://u1", "https://u2"])
    assert r == [
        {"url": "https://u1", "emails": ["a@x.fr"]},
        {"url": "https://u2", "emails": ["b@y.fr"]},
    ]


@pytest.mark.asyncio
async def test_scrape_batch_respects_max_pages():
    urls = [f"https://u{i}" for i in range(5)]
    mock = AsyncMock(return_value={"emails": []})
    with patch("app.osint.scraper.scrape_entities", mock):
        r = await scraper.scrape_batch(urls, max_pages=2)
    assert mock.await_count == 2
    assert len(r) == 2


@pytest.mark.asyncio
async def test_scrape_batch_ignores_individual_errors():
    """Une page qui lève est ignorée ; les autres sont conservées."""
    effects = [{"emails": ["a@x.fr"]}, RuntimeError("boom"), {"emails": ["c@z.fr"]}]
    with patch("app.osint.scraper.scrape_entities", AsyncMock(side_effect=effects)):
        r = await scraper.scrape_batch(["https://u1", "https://u2", "https://u3"])
    assert [p["url"] for p in r] == ["https://u1", "https://u3"]


@pytest.mark.asyncio
async def test_scrape_batch_skips_none_results():
    """scrape_entities renvoyant None → page non incluse dans le résultat."""
    effects = [None, {"emails": ["b@y.fr"]}]
    with patch("app.osint.scraper.scrape_entities", AsyncMock(side_effect=effects)):
        r = await scraper.scrape_batch(["https://u1", "https://u2"])
    assert r == [{"url": "https://u2", "emails": ["b@y.fr"]}]


@pytest.mark.asyncio
async def test_scrape_batch_rate_limited_per_page():
    """Le rate limiter est sollicité une fois par page traitée."""
    urls = ["https://u1", "https://u2", "https://u3"]
    with patch("app.osint.scraper.scrape_entities", AsyncMock(return_value={"emails": []})), \
         patch.object(scraper._rate_limiter, "acquire", new_callable=AsyncMock) as acquire:
        await scraper.scrape_batch(urls, max_pages=3)
    assert acquire.await_count == 3


@pytest.mark.asyncio
async def test_scrape_batch_empty_input():
    with patch("app.osint.scraper.scrape_entities", AsyncMock()) as mock:
        r = await scraper.scrape_batch([])
    assert r == []
    mock.assert_not_awaited()
