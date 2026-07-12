"""
Scraping web léger pour l'enrichissement OSINT — BeautifulSoup + lxml.

Sources publiques uniquement, respect strict des robots.txt, blacklist des
réseaux sociaux, rate limiting 2s entre pages, taille max 1 Mo, timeout 10s.
Jamais de scraping agressif.
"""
import re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.core.config import get_settings
from app.osint.rate_limiter import RateLimiter

# User-Agent descriptif (URL de contact = dépôt public, aucune donnée personnelle)
_UA = {"User-Agent": "OSINT-Platform/0.1 (+https://github.com/margottestorme/OSINT_PA)"}

_timeout = get_settings().EXTERNAL_REQUEST_TIMEOUT
_rate_limiter = RateLimiter(min_interval=2.0)  # 2s entre chaque page scrapée

MAX_PAGE_SIZE = 1 * 1024 * 1024  # 1 Mo

# Réseaux sociaux exclus du scraping (ToS / robots stricts)
SOCIAL_BLACKLIST = (
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "tiktok.com",
)

# Infrastructure Wikimedia : présente sur TOUTE page Wikipédia (barre de liens interlangues,
# projets sœurs) quel que soit son contenu — aucun signal sur le sujet recherché, contrairement
# à SOCIAL_BLACKLIST qui bloque le scraping, ceux-ci sont seulement exclus des entités EXTRAITES
# (la page Wikipédia elle-même reste scrapée normalement, seuls ses liens de navigation le sont pas).
_WIKIMEDIA_FAMILY = (
    "wikipedia.org", "wikimedia.org", "wikidata.org", "wiktionary.org",
    "wikibooks.org", "wikiquote.org", "wikisource.org", "wikinews.org",
    "wikiversity.org", "mediawiki.org", "wikimediafoundation.org",
)


def _is_wikimedia_noise(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in _WIKIMEDIA_FAMILY)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
# Téléphones au format international (préfixe + obligatoire pour limiter le bruit)
_PHONE_RE = re.compile(r"\+\d[\d\s().\-]{7,}\d")


def _is_blacklisted(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(social in host for social in SOCIAL_BLACKLIST)


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    """Vérifie le robots.txt de l'hôte. Absent/illisible → autorisé (permissif standard)."""
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        resp = await client.get(robots_url)
        if resp.status_code != 200 or not resp.text.strip():
            return True
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(_UA["User-Agent"], url)
    except Exception:
        return True


async def fetch_page(url: str) -> str | None:
    """Récupère le HTML d'une URL (robots.txt respecté, taille max, timeout). None si refusé/erreur."""
    if _is_blacklisted(url):
        logger.info(f"[scraper] URL blacklistée (réseau social) : {url}")
        return None

    try:
        async with httpx.AsyncClient(timeout=_timeout, headers=_UA, follow_redirects=True) as client:
            if not await _robots_allows(client, url):
                logger.info(f"[scraper] robots.txt interdit le scraping de {url}")
                return None

            resp = await client.get(url)
            resp.raise_for_status()

            if len(resp.content) > MAX_PAGE_SIZE:
                logger.warning(f"[scraper] Page trop volumineuse ({len(resp.content)} octets) : {url}")
                return None

            return resp.text
    except Exception as e:
        logger.warning(f"[scraper] Échec récupération {url} : {e}")
        return None


async def scrape_entities(url: str) -> dict | None:
    """Scrape et extrait les entités d'une page. None si la page n'a pu être récupérée."""
    html = await fetch_page(url)
    if html is None:
        return None

    soup = BeautifulSoup(html, "lxml")
    # Retirer le code non pertinent avant extraction
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)
    base_host = urlparse(url).netloc.lower()

    emails = sorted(set(_EMAIL_RE.findall(text)))
    phones = sorted(set(m.strip() for m in _PHONE_RE.findall(text)))

    domains: set[str] = set()
    urls: set[str] = set()
    for a in soup.find_all("a", href=True):
        parsed = urlparse(a["href"])
        if parsed.scheme in ("http", "https") and parsed.netloc:
            host = parsed.netloc.lower()
            if _is_wikimedia_noise(host):
                continue  # lien de navigation Wikimedia, pas une entité liée au sujet
            urls.add(a["href"])
            if host != base_host and not _is_blacklisted(a["href"]):
                domains.add(host)

    names: list[str] = []
    if soup.title and soup.title.get_text(strip=True):
        names.append(soup.title.get_text(strip=True))
    for heading in soup.find_all(["h1", "h2"]):
        txt = heading.get_text(strip=True)
        if txt:
            names.append(txt)

    description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        description = meta["content"].strip()

    logger.info(
        f"[scraper] {url} → {len(emails)} email(s), {len(domains)} domaine(s), "
        f"{len(names)} nom(s), {len(phones)} téléphone(s)"
    )
    return {
        "emails": emails,
        "domains": sorted(domains),
        "urls": sorted(urls)[:20],
        "names": names[:10],
        "phones": phones,
        "description": description,
    }


async def scrape_batch(urls: list[str], max_pages: int = 3) -> list[dict]:
    """Scrape jusqu'à max_pages URLs (2s entre chaque). Ignore les erreurs individuelles."""
    results: list[dict] = []
    for url in urls[:max_pages]:
        await _rate_limiter.acquire()  # rate limiting 2s entre pages
        try:
            data = await scrape_entities(url)
            if data:
                results.append({"url": url, **data})
        except Exception as e:
            logger.warning(f"[scraper] Erreur lot pour {url} : {e}")
            continue
    return results
