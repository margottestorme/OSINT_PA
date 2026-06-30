import re
import asyncio
from typing import Any

import httpx
import spacy
import whois
import dns.resolver
from loguru import logger

from app.core.config import get_settings
from app.osint.rate_limiter import RateLimiter

_nlp = spacy.load("fr_core_news_sm")
_rate_limiter = RateLimiter(min_interval=2.0)
_timeout = get_settings().EXTERNAL_REQUEST_TIMEOUT

# Regex pour l'extraction d'emails et de domaines
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")


async def search_email(email: str) -> list[dict]:
    """Recherche les occurrences publiques d'un email via DuckDuckGo et DNS MX."""
    logger.info(f"[search_email] Lancement pour : {email}")
    results: list[dict] = []

    # Vérification MX du domaine de l'email
    domain = email.split("@")[-1]
    mx_records = await _resolve_mx(domain)
    if mx_records:
        results.append({
            "source": "dns_mx",
            "entity": email,
            "data": {"domain": domain, "mx_records": mx_records},
        })
        logger.info(f"[search_email] MX trouvés pour {domain} : {mx_records}")

    # Recherche via DuckDuckGo Instant Answer API (source publique, pas de scraping)
    ddg_results = await _duckduckgo_search(email)
    if ddg_results:
        results.append({
            "source": "duckduckgo",
            "entity": email,
            "data": ddg_results,
        })
        logger.info(f"[search_email] Résultats DuckDuckGo pour {email} : {len(ddg_results.get('RelatedTopics', []))} topics")

    logger.info(f"[search_email] Terminé — {len(results)} source(s) trouvée(s)")
    return results


async def extract_entities(text: str) -> dict[str, list[str]]:
    """Extrait emails, domaines et noms de personnes depuis un texte via spaCy + regex."""
    logger.info(f"[extract_entities] Analyse de {len(text)} caractères")

    # spaCy tourne en synchrone — on le lance dans un thread pour ne pas bloquer la boucle
    loop = asyncio.get_event_loop()
    doc = await loop.run_in_executor(None, _nlp, text)

    names = list({ent.text.strip() for ent in doc.ents if ent.label_ == "PER"})
    emails = list(set(_EMAIL_RE.findall(text)))
    domains = list(set(_DOMAIN_RE.findall(text)) - set(emails))

    logger.info(
        f"[extract_entities] Trouvé : {len(names)} nom(s), "
        f"{len(emails)} email(s), {len(domains)} domaine(s)"
    )
    return {"names": names, "emails": emails, "domains": domains}


async def whois_lookup(domain: str) -> dict[str, Any]:
    """Effectue un lookup WHOIS sur un domaine via python-whois."""
    logger.info(f"[whois_lookup] Requête WHOIS pour : {domain}")
    await _rate_limiter.acquire()

    loop = asyncio.get_event_loop()
    try:
        w = await loop.run_in_executor(None, whois.whois, domain)
        result = {
            "domain_name": w.domain_name,
            "registrar": w.registrar,
            "creation_date": str(w.creation_date) if w.creation_date else None,
            "expiration_date": str(w.expiration_date) if w.expiration_date else None,
            "name_servers": w.name_servers,
            "emails": w.emails,
            "org": w.org,
            "country": w.country,
        }
        logger.info(f"[whois_lookup] WHOIS OK pour {domain} — registrar={w.registrar}")
        return result
    except Exception as e:
        logger.warning(f"[whois_lookup] Échec WHOIS pour {domain} : {e}")
        return {"error": str(e), "domain": domain}


async def correlate(entity: str, entity_type: str) -> list[dict]:
    """Lance le pivot OSINT selon le type d'entité et agrège les résultats."""
    logger.info(f"[correlate] Pivot {entity_type} pour : {entity}")
    results: list[dict] = []

    if entity_type == "email":
        search_results = await search_email(entity)
        results.extend(search_results)

        domain = entity.split("@")[-1]
        whois_data = await whois_lookup(domain)
        if "error" not in whois_data:
            results.append({
                "source": "whois",
                "entity": domain,
                "data": whois_data,
            })

    elif entity_type == "domain":
        whois_data = await whois_lookup(entity)
        if "error" not in whois_data:
            results.append({
                "source": "whois",
                "entity": entity,
                "data": whois_data,
            })

        mx_records = await _resolve_mx(entity)
        if mx_records:
            results.append({
                "source": "dns_mx",
                "entity": entity,
                "data": {"mx_records": mx_records},
            })

        ddg_results = await _duckduckgo_search(entity)
        if ddg_results:
            results.append({
                "source": "duckduckgo",
                "entity": entity,
                "data": ddg_results,
            })

    elif entity_type == "name":
        ddg_results = await _duckduckgo_search(entity)
        if ddg_results:
            results.append({
                "source": "duckduckgo",
                "entity": entity,
                "data": ddg_results,
            })

    else:
        logger.warning(f"[correlate] Type d'entité inconnu : {entity_type}")

    logger.info(f"[correlate] Pivot terminé — {len(results)} résultat(s)")
    return results


# --- Fonctions internes ---

async def _duckduckgo_search(query: str) -> dict:
    """Interroge l'API DuckDuckGo Instant Answer (publique, légale, pas de scraping)."""
    await _rate_limiter.acquire()
    logger.debug(f"[duckduckgo] Recherche : {query}")

    try:
        async with httpx.AsyncClient(timeout=float(_timeout)) as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_redirect": "1"},
            )
            response.raise_for_status()
            data = response.json()
            logger.debug(f"[duckduckgo] Réponse reçue — type={data.get('Type', 'N/A')}")
            return data
    except httpx.HTTPError as e:
        logger.warning(f"[duckduckgo] Erreur HTTP : {e}")
        return {}


async def _resolve_mx(domain: str) -> list[str]:
    """Résout les enregistrements MX d'un domaine via dnspython."""
    await _rate_limiter.acquire()
    logger.debug(f"[dns_mx] Résolution MX pour : {domain}")

    loop = asyncio.get_event_loop()
    try:
        resolver = dns.resolver.Resolver()
        answers = await loop.run_in_executor(None, resolver.resolve, domain, "MX")
        mx_list = sorted(
            [str(r.exchange).rstrip(".") for r in answers],
            key=lambda x: x,
        )
        logger.debug(f"[dns_mx] MX trouvés : {mx_list}")
        return mx_list
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers) as e:
        logger.warning(f"[dns_mx] Pas de MX pour {domain} : {e}")
        return []
    except Exception as e:
        logger.warning(f"[dns_mx] Erreur DNS pour {domain} : {e}")
        return []
