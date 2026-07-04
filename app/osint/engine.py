import re
import asyncio
import hashlib
from typing import Any
from urllib.parse import quote

import httpx
import spacy
import whois
import dns.resolver
from loguru import logger

from app.core.config import get_settings
from app.osint.scraper import scrape_batch
from app.osint.rate_limiter import RateLimiter

_nlp = spacy.load("fr_core_news_sm")
_rate_limiter = RateLimiter(min_interval=2.0)
_timeout = get_settings().EXTERNAL_REQUEST_TIMEOUT

# User-Agent descriptif et poli (crt.sh / Wikipedia / GitHub le recommandent).
# On expose une URL de contact (le dépôt public), pas de donnée personnelle.
_UA = {"User-Agent": "OSINT-Platform/0.1 (+https://github.com/margottestorme/OSINT_PA)"}

# --- Format de résultat normalisé ---
# Toute source renvoie cette enveloppe commune (ou None si échec/vide).
OsintResult = dict[str, Any]

# Clés du sous-dictionnaire "extracted" : vocabulaire commun d'entités normalisées.
_EXTRACTED_KEYS = ("emails", "domains", "urls", "ips", "names")


def _normalize(
    source: str,
    entity: str,
    entity_type: str,
    data: Any,
    extracted: dict[str, list] | None = None,
) -> OsintResult:
    """Construit l'enveloppe normalisée commune à toutes les sources OSINT.

    - `data`      : payload brut spécifique à la source (rétrocompatible).
    - `extracted` : entités normalisées ; toutes les clés de _EXTRACTED_KEYS sont
                    garanties présentes, listes dédupliquées en conservant l'ordre.
    """
    extracted = extracted or {}
    normalized: dict[str, list] = {}
    for key in _EXTRACTED_KEYS:
        seen: list = []
        for value in extracted.get(key, []) or []:
            if value and value not in seen:
                seen.append(value)
        normalized[key] = seen

    return {
        "source": source,
        "entity": entity,
        "type": entity_type,
        "data": data,
        "extracted": normalized,
    }


# --- Registre déclaratif des sources ---
# Rempli au fur et à mesure de l'implémentation. correlate() itère sur ces listes
# selon le type d'entité ; chaque source est auto-protégée (log + None, jamais d'exception).
SOURCES: dict[str, list] = {
    "email": [],
    "domain": [],
    "name": [],
}

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


def _whois_extracted(whois_data: dict) -> dict[str, list]:
    """Extrait emails et nameservers d'un résultat WHOIS vers le format normalisé."""
    emails = whois_data.get("emails")
    if isinstance(emails, str):
        emails = [emails]
    elif not isinstance(emails, list):
        emails = []

    ns = whois_data.get("name_servers") or []
    if isinstance(ns, str):
        ns = [ns]
    domains = [str(n).rstrip(".").lower() for n in ns]
    return {"emails": emails, "domains": domains}


async def correlate(entity: str, entity_type: str) -> list[OsintResult]:
    """Pivot OSINT : agrège les sources historiques (normalisées) + le registre SOURCES."""
    logger.info(f"[correlate] Pivot {entity_type} pour : {entity}")
    results: list[OsintResult] = []

    # --- Sources historiques (WHOIS / MX / DuckDuckGo), normalisées ---
    if entity_type == "email":
        domain = entity.split("@")[-1]
        mx_records = await _resolve_mx(domain)
        if mx_records:
            results.append(_normalize(
                "dns_mx", entity, "email",
                data={"domain": domain, "mx_records": mx_records},
                extracted={"domains": [m.lower() for m in mx_records]},
            ))
        ddg = await _duckduckgo_search(entity)
        if ddg:
            results.append(_normalize("duckduckgo", entity, "email", data=ddg))
        whois_data = await whois_lookup(domain)
        if "error" not in whois_data:
            results.append(_normalize(
                "whois", domain, "email",
                data=whois_data, extracted=_whois_extracted(whois_data),
            ))

    elif entity_type == "domain":
        whois_data = await whois_lookup(entity)
        if "error" not in whois_data:
            results.append(_normalize(
                "whois", entity, "domain",
                data=whois_data, extracted=_whois_extracted(whois_data),
            ))
        mx_records = await _resolve_mx(entity)
        if mx_records:
            results.append(_normalize(
                "dns_mx", entity, "domain",
                data={"mx_records": mx_records},
                extracted={"domains": [m.lower() for m in mx_records]},
            ))
        ddg = await _duckduckgo_search(entity)
        if ddg:
            results.append(_normalize("duckduckgo", entity, "domain", data=ddg))

    elif entity_type == "name":
        ddg = await _duckduckgo_search(entity)
        if ddg:
            results.append(_normalize("duckduckgo", entity, "name", data=ddg))

    else:
        logger.warning(f"[correlate] Type d'entité inconnu : {entity_type}")
        return results

    # --- Sources du registre (Lot Vert + Freemium dormantes) ---
    for source in SOURCES.get(entity_type, []):
        res = await source(entity)
        if res:
            results.append(res)

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


# =============================================================
# Sources OSINT — Lot Vert (sans clé API)
# Chaque source : rate limiter + User-Agent, ne lève jamais (log + None).
# =============================================================

async def _source_gravatar(email: str) -> OsintResult | None:
    """Profil public Gravatar associé à un email (hash MD5 de l'email normalisé)."""
    await _rate_limiter.acquire()
    email_hash = hashlib.md5(email.strip().lower().encode()).hexdigest()
    logger.info(f"[gravatar] Lookup profil pour {email} (hash {email_hash[:8]}...)")

    try:
        async with httpx.AsyncClient(timeout=_timeout, headers=_UA) as client:
            resp = await client.get(f"https://www.gravatar.com/{email_hash}.json")

        if resp.status_code == 404:
            logger.debug(f"[gravatar] Aucun profil pour {email}")
            return None
        resp.raise_for_status()
        payload = resp.json()

        entries = payload.get("entry", [])
        if not entries:
            return None
        entry = entries[0]

        names = [entry.get("displayName"), (entry.get("name") or {}).get("formatted")]
        urls = [entry.get("profileUrl")]
        urls += [u.get("value") for u in entry.get("urls", []) if isinstance(u, dict)]
        urls += [a.get("url") for a in entry.get("accounts", []) if isinstance(a, dict)]

        logger.info(f"[gravatar] Profil trouvé pour {email}")
        return _normalize(
            "gravatar", email, "email",
            data=entry,
            extracted={"names": names, "urls": urls},
        )
    except Exception as e:
        logger.warning(f"[gravatar] Échec pour {email} : {e}")
        return None


async def _source_wikipedia(name: str) -> OsintResult | None:
    """Recherche d'une personne sur Wikipédia (fr) via l'API MediaWiki."""
    await _rate_limiter.acquire()
    logger.info(f"[wikipedia] Recherche : {name}")

    try:
        async with httpx.AsyncClient(timeout=_timeout, headers=_UA) as client:
            resp = await client.get(
                "https://fr.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": name,
                    "srlimit": 5,
                    "format": "json",
                },
            )
        resp.raise_for_status()
        results = resp.json().get("query", {}).get("search", [])
        if not results:
            logger.debug(f"[wikipedia] Aucun résultat pour {name}")
            return None

        titles = [r.get("title") for r in results if r.get("title")]
        urls = [
            "https://fr.wikipedia.org/wiki/" + quote(t.replace(" ", "_"))
            for t in titles
        ]

        logger.info(f"[wikipedia] {len(titles)} page(s) pour {name}")
        return _normalize(
            "wikipedia", name, "name",
            data=results,
            extracted={"names": titles, "urls": urls},
        )
    except Exception as e:
        logger.warning(f"[wikipedia] Échec pour {name} : {e}")
        return None


async def _source_github(name: str) -> OsintResult | None:
    """Recherche d'utilisateurs GitHub publics par nom (token optionnel pour le quota)."""
    await _rate_limiter.acquire()
    logger.info(f"[github] Recherche users : {name}")

    headers = dict(_UA)
    headers["Accept"] = "application/vnd.github+json"
    token = get_settings().GITHUB_TOKEN
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=_timeout, headers=headers) as client:
            resp = await client.get(
                "https://api.github.com/search/users",
                params={"q": name, "per_page": 5},
            )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            logger.debug(f"[github] Aucun user pour {name}")
            return None

        logins = [i.get("login") for i in items if i.get("login")]
        urls = [i.get("html_url") for i in items if i.get("html_url")]

        logger.info(f"[github] {len(logins)} user(s) pour {name}")
        return _normalize(
            "github", name, "name",
            data=items,
            extracted={"names": logins, "urls": urls},
        )
    except Exception as e:
        logger.warning(f"[github] Échec pour {name} : {e}")
        return None


# --- Enregistrement dans le registre (actif au refactor de correlate(), point 5) ---
SOURCES["email"].append(_source_gravatar)
SOURCES["name"].extend([_source_wikipedia, _source_github])


# =============================================================
# Sources OSINT domain — Lot Vert
# =============================================================

async def _resolve(domain: str, rdtype: str) -> list[str]:
    """Résout un type d'enregistrement DNS. N'acquiert PAS le rate limiter
    (l'appelant gère le throttling pour regrouper un bundle de requêtes DNS).
    Retourne une liste de valeurs textuelles, [] en cas d'absence/erreur.
    """
    loop = asyncio.get_event_loop()
    try:
        resolver = dns.resolver.Resolver()
        answers = await loop.run_in_executor(
            None, lambda: resolver.resolve(domain, rdtype)
        )
        return [r.to_text() for r in answers]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return []
    except Exception as e:
        logger.debug(f"[dns] {rdtype} {domain} : {e}")
        return []


async def _source_crtsh(domain: str) -> OsintResult | None:
    """Sous-domaines via les logs de Certificate Transparency (crt.sh).

    crt.sh est fréquemment surchargé (502/503) : on effectue jusqu'à 3 tentatives
    avec backoff exponentiel avant d'abandonner silencieusement.
    """
    await _rate_limiter.acquire()
    logger.info(f"[crtsh] Recherche sous-domaines pour {domain}")

    certs = None
    last_exc = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=_timeout, headers=_UA) as client:
                resp = await client.get(
                    "https://crt.sh/", params={"q": f"%.{domain}", "output": "json"}
                )
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"crt.sh {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            certs = resp.json()
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                wait = 1.5 * (attempt + 1)  # 1.5s puis 3s
                logger.debug(f"[crtsh] tentative {attempt + 1} échouée ({e}), retry dans {wait}s")
                await asyncio.sleep(wait)

    if certs is None:
        logger.warning(f"[crtsh] Échec pour {domain} après 3 tentatives : {last_exc}")
        return None

    try:
        if not certs:
            return None

        subdomains: set[str] = set()
        for cert in certs:
            for name in (cert.get("name_value") or "").split("\n"):
                name = name.strip().lstrip("*.").lower()
                if name and name.endswith(domain):
                    subdomains.add(name)

        if not subdomains:
            return None

        ordered = sorted(subdomains)
        logger.info(f"[crtsh] {len(ordered)} sous-domaine(s) pour {domain}")
        return _normalize(
            "crt.sh", domain, "domain",
            data={"count": len(ordered), "subdomains": ordered},
            extracted={"domains": ordered},
        )
    except Exception as e:
        logger.warning(f"[crtsh] Échec pour {domain} : {e}")
        return None


async def _source_dns_records(domain: str) -> OsintResult | None:
    """Empreinte DNS : A / AAAA / NS / TXT / CNAME + SPF et DMARC (dérivés des TXT)."""
    await _rate_limiter.acquire()
    logger.info(f"[dns] Empreinte DNS pour {domain}")

    try:
        a = [v.strip() for v in await _resolve(domain, "A")]
        aaaa = [v.strip() for v in await _resolve(domain, "AAAA")]
        ns = [v.rstrip(".") for v in await _resolve(domain, "NS")]
        cname = [v.rstrip(".") for v in await _resolve(domain, "CNAME")]
        txt = [v.strip('"') for v in await _resolve(domain, "TXT")]
        dmarc_txt = [v.strip('"') for v in await _resolve(f"_dmarc.{domain}", "TXT")]

        spf = next((t for t in txt if t.lower().startswith("v=spf1")), None)
        dmarc = next((t for t in dmarc_txt if t.lower().startswith("v=dmarc1")), None)

        data = {
            "A": a, "AAAA": aaaa, "NS": ns, "CNAME": cname,
            "TXT": txt, "SPF": spf, "DMARC": dmarc,
        }
        # Aucun enregistrement du tout → rien d'exploitable
        if not any([a, aaaa, ns, cname, txt]):
            logger.debug(f"[dns] Aucun enregistrement pour {domain}")
            return None

        logger.info(f"[dns] {domain} → {len(a)} A, {len(ns)} NS, SPF={'oui' if spf else 'non'}")
        return _normalize(
            "dns", domain, "domain",
            data=data,
            extracted={"ips": a + aaaa, "domains": ns + cname},
        )
    except Exception as e:
        logger.warning(f"[dns] Échec pour {domain} : {e}")
        return None


async def _source_rdap(domain: str) -> OsintResult | None:
    """Enregistrement RDAP (successeur JSON de WHOIS) via l'agrégateur rdap.org."""
    await _rate_limiter.acquire()
    logger.info(f"[rdap] Lookup pour {domain}")

    try:
        async with httpx.AsyncClient(timeout=_timeout, headers=_UA, follow_redirects=True) as client:
            resp = await client.get(f"https://rdap.org/domain/{domain}")
        if resp.status_code == 404:
            logger.debug(f"[rdap] Domaine introuvable : {domain}")
            return None
        resp.raise_for_status()
        obj = resp.json()

        nameservers = [
            ns.get("ldhName", "").rstrip(".").lower()
            for ns in obj.get("nameservers", [])
            if ns.get("ldhName")
        ]

        # Emails éventuels dans les vcardArray des entités
        emails: list[str] = []
        for entity in obj.get("entities", []):
            vcard = entity.get("vcardArray")
            if isinstance(vcard, list) and len(vcard) == 2:
                for field in vcard[1]:
                    if isinstance(field, list) and field and field[0] == "email":
                        emails.append(field[3])

        logger.info(f"[rdap] {domain} → {len(nameservers)} NS, {len(emails)} email(s)")
        return _normalize(
            "rdap", domain, "domain",
            data={
                "ldhName": obj.get("ldhName"),
                "status": obj.get("status"),
                "events": obj.get("events"),
                "nameservers": nameservers,
            },
            extracted={"domains": nameservers, "emails": emails},
        )
    except Exception as e:
        logger.warning(f"[rdap] Échec pour {domain} : {e}")
        return None


async def _source_shodan_internetdb(domain: str) -> OsintResult | None:
    """Shodan InternetDB (gratuit, sans clé) : résout les IP du domaine puis
    interroge internetdb.shodan.io pour chaque IP (ports, CPE, vulns, hostnames)."""
    await _rate_limiter.acquire()
    per_ip: dict[str, dict] = {}
    hostnames: set[str] = set()

    try:
        ips = [v.strip() for v in await _resolve(domain, "A")]
        if not ips:
            logger.debug(f"[shodan] Aucune IP A pour {domain}")
            return None

        logger.info(f"[shodan] {len(ips)} IP pour {domain} → InternetDB")
        for ip in ips:
            await _rate_limiter.acquire()  # chaque appel InternetDB = appel externe
            async with httpx.AsyncClient(timeout=_timeout, headers=_UA) as client:
                resp = await client.get(f"https://internetdb.shodan.io/{ip}")
            if resp.status_code == 404:
                continue  # IP sans donnée référencée
            resp.raise_for_status()
            info = resp.json()
            per_ip[ip] = {
                "ports": info.get("ports", []),
                "cpes": info.get("cpes", []),
                "vulns": info.get("vulns", []),
                "hostnames": info.get("hostnames", []),
                "tags": info.get("tags", []),
            }
            for h in info.get("hostnames", []):
                hostnames.add(h.lower())

        if not per_ip:
            return None

        logger.info(f"[shodan] {domain} → données pour {len(per_ip)} IP")
        return _normalize(
            "shodan_internetdb", domain, "domain",
            data=per_ip,
            extracted={"ips": ips, "domains": sorted(hostnames)},
        )
    except Exception as e:
        logger.warning(f"[shodan] Échec pour {domain} : {e}")
        return None


SOURCES["domain"].extend(
    [_source_crtsh, _source_dns_records, _source_rdap, _source_shodan_internetdb]
)


# =============================================================
# Sources OSINT — Lot Freemium (clé API requise ; dormantes tant que la clé
# n'est pas fournie : "if not key -> return None" silencieux, aucune erreur).
# =============================================================

async def _source_virustotal(domain: str) -> OsintResult | None:
    """Réputation et DNS passif d'un domaine via l'API VirusTotal v3 (clé requise)."""
    key = get_settings().VIRUSTOTAL_API_KEY
    if not key:
        logger.debug("[virustotal] Pas de clé API — source désactivée")
        return None

    await _rate_limiter.acquire()
    logger.info(f"[virustotal] Lookup domaine {domain}")
    try:
        headers = dict(_UA)
        headers["x-apikey"] = key
        async with httpx.AsyncClient(timeout=_timeout, headers=headers) as client:
            resp = await client.get(f"https://www.virustotal.com/api/v3/domains/{domain}")
        if resp.status_code == 404:
            logger.debug(f"[virustotal] Domaine inconnu : {domain}")
            return None
        resp.raise_for_status()
        attrs = resp.json().get("data", {}).get("attributes", {})

        ips: list[str] = []
        domains: list[str] = []
        for rec in attrs.get("last_dns_records", []):
            rtype, value = rec.get("type"), rec.get("value")
            if not value:
                continue
            if rtype == "A":
                ips.append(value)
            elif rtype in ("NS", "CNAME"):
                domains.append(value.rstrip("."))

        logger.info(f"[virustotal] {domain} → réputation {attrs.get('reputation')}")
        return _normalize(
            "virustotal", domain, "domain",
            data={
                "reputation": attrs.get("reputation"),
                "last_analysis_stats": attrs.get("last_analysis_stats"),
                "categories": attrs.get("categories"),
            },
            extracted={"ips": ips, "domains": domains},
        )
    except Exception as e:
        logger.warning(f"[virustotal] Échec pour {domain} : {e}")
        return None


async def _source_otx(domain: str) -> OsintResult | None:
    """DNS passif d'un domaine via AlienVault OTX (clé requise)."""
    key = get_settings().OTX_API_KEY
    if not key:
        logger.debug("[otx] Pas de clé API — source désactivée")
        return None

    await _rate_limiter.acquire()
    logger.info(f"[otx] Passive DNS pour {domain}")
    try:
        headers = dict(_UA)
        headers["X-OTX-API-KEY"] = key
        async with httpx.AsyncClient(timeout=_timeout, headers=headers) as client:
            resp = await client.get(
                f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns"
            )
        resp.raise_for_status()
        records = resp.json().get("passive_dns", [])
        if not records:
            return None

        hostnames = {
            r.get("hostname", "").rstrip(".").lower()
            for r in records if r.get("hostname")
        }
        ips = {r.get("address") for r in records if r.get("address")}

        logger.info(f"[otx] {domain} → {len(records)} enregistrement(s) passive DNS")
        return _normalize(
            "otx", domain, "domain",
            data={"count": len(records), "passive_dns": records[:50]},
            extracted={"domains": sorted(hostnames), "ips": sorted(ips)},
        )
    except Exception as e:
        logger.warning(f"[otx] Échec pour {domain} : {e}")
        return None


async def _source_hunter(email: str) -> OsintResult | None:
    """Vérification d'un email via Hunter.io (clé requise) : statut + sources publiques."""
    key = get_settings().HUNTER_API_KEY
    if not key:
        logger.debug("[hunter] Pas de clé API — source désactivée")
        return None

    await _rate_limiter.acquire()
    logger.info(f"[hunter] Vérification {email}")
    try:
        async with httpx.AsyncClient(timeout=_timeout, headers=_UA) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/email-verifier",
                params={"email": email, "api_key": key},
            )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        if not data:
            return None

        sources = data.get("sources", [])
        urls = [s.get("uri") for s in sources if s.get("uri")]
        domains = [s.get("domain") for s in sources if s.get("domain")]

        logger.info(f"[hunter] {email} → statut {data.get('status')} ({len(sources)} source(s))")
        return _normalize(
            "hunter", email, "email",
            data={
                "status": data.get("status"),
                "result": data.get("result"),
                "score": data.get("score"),
            },
            extracted={"urls": urls, "domains": domains},
        )
    except Exception as e:
        logger.warning(f"[hunter] Échec pour {email} : {e}")
        return None


# Enregistrement freemium (dormant tant que la clé n'est pas fournie)
SOURCES["domain"].extend([_source_virustotal, _source_otx])
SOURCES["email"].append(_source_hunter)


# =============================================================
# Source scraping (BeautifulSoup) — enrichissement par contenu de page
# =============================================================

def _ddg_urls(ddg: dict) -> list[str]:
    """Extrait les URLs exploitables d'une réponse DuckDuckGo Instant Answer."""
    urls: list[str] = []
    if ddg.get("AbstractURL"):
        urls.append(ddg["AbstractURL"])
    for topic in ddg.get("RelatedTopics", []):
        if not isinstance(topic, dict):
            continue
        if topic.get("FirstURL"):
            urls.append(topic["FirstURL"])
        for sub in topic.get("Topics", []):
            if isinstance(sub, dict) and sub.get("FirstURL"):
                urls.append(sub["FirstURL"])
    # Déduplication en conservant l'ordre
    seen: list[str] = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    return seen


async def _source_scraper(entity: str, entity_type: str) -> OsintResult | None:
    """Scrape le contenu des pages liées à l'entité (max 3), agrège les entités.

    - email  : pages DuckDuckGo mentionnant l'email
    - domain : page principale du domaine
    - name   : pages Wikipedia retournées par _source_wikipedia
    """
    logger.info(f"[scraper] Source scraping {entity_type} pour {entity}")
    try:
        if entity_type == "domain":
            urls = [f"https://{entity}"]
        elif entity_type == "email":
            ddg = await _duckduckgo_search(entity)
            urls = _ddg_urls(ddg)
        elif entity_type == "name":
            wiki = await _source_wikipedia(entity)
            urls = list(wiki["extracted"]["urls"]) if wiki else []
        else:
            return None

        if not urls:
            logger.debug(f"[scraper] Aucune URL à scraper pour {entity}")
            return None

        scraped = await scrape_batch(urls, max_pages=3)
        if not scraped:
            return None

        emails: set[str] = set()
        domains: set[str] = set()
        out_urls: set[str] = set()
        names: list[str] = []
        phones: set[str] = set()
        for page in scraped:
            emails.update(page.get("emails", []))
            domains.update(page.get("domains", []))
            out_urls.update(page.get("urls", []))
            names.extend(page.get("names", []))
            phones.update(page.get("phones", []))

        logger.info(
            f"[scraper] {entity} → {len(scraped)} page(s) scrapée(s), "
            f"{len(emails)} email(s), {len(domains)} domaine(s), {len(phones)} téléphone(s)"
        )
        return _normalize(
            "scraper", entity, entity_type,
            data={"pages": scraped, "phones": sorted(phones)},
            extracted={
                "emails": sorted(emails),
                "domains": sorted(domains),
                "urls": sorted(out_urls)[:30],
                "names": names[:20],
            },
        )
    except Exception as e:
        logger.warning(f"[scraper] Échec pour {entity} ({entity_type}) : {e}")
        return None


# Enregistrement du scraper sur les 3 types. Le registre appelle chaque source avec
# un seul argument (entity) ; _source_scraper prend (entity, entity_type) → on passe
# l'entity_type via une lambda (sinon TypeError : missing argument 'entity_type').
SOURCES["email"].append(lambda e: _source_scraper(e, "email"))
SOURCES["domain"].append(lambda e: _source_scraper(e, "domain"))
SOURCES["name"].append(lambda e: _source_scraper(e, "name"))
