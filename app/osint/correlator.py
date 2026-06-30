from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.db.models import Correlation, Email, Person, Site
from app.osint.engine import correlate, extract_entities

MAX_DEPTH = 3


async def enrich_person(person_id: int, db: AsyncSession) -> dict:
    """Enrichit une personne en pivotant sur ses emails via le moteur OSINT.

    Parcourt les emails associés, lance correlate() pour chacun, extrait
    les nouvelles entités (emails, sites) et stocke les corrélations en DB.
    Limité à MAX_DEPTH niveaux de profondeur pour éviter les boucles.
    """
    logger.info(f"[enrich] Démarrage pour person_id={person_id}")

    person = await db.get(Person, person_id)
    if not person:
        logger.warning(f"[enrich] Person {person_id} introuvable")
        return {"person_id": person_id, "new_emails": 0, "new_sites": 0, "new_correlations": 0}

    # Entités déjà traitées — pour éviter les boucles et la déduplication
    processed_emails: set[str] = set()
    processed_domains: set[str] = set()

    counters = {"new_emails": 0, "new_sites": 0, "new_correlations": 0}

    # Charger les emails déjà connus pour cette personne
    result = await db.execute(
        select(Email).where(Email.person_id == person_id)
    )
    known_emails = result.scalars().all()
    email_queue = [e.email for e in known_emails]

    for depth in range(MAX_DEPTH):
        if not email_queue:
            logger.debug(f"[enrich] Profondeur {depth} — plus d'emails à traiter")
            break

        logger.info(f"[enrich] Profondeur {depth}/{MAX_DEPTH} — {len(email_queue)} email(s) à traiter")
        next_queue: list[str] = []

        for email_addr in email_queue:
            if email_addr in processed_emails:
                continue
            processed_emails.add(email_addr)

            # Lancer le pivot OSINT sur cet email
            try:
                osint_results = await correlate(email_addr, "email")
            except Exception as e:
                logger.error(f"[enrich] Erreur correlate({email_addr}) : {e}")
                continue

            # Email pivoté résolu une seule fois (m6) — constant pour tous ses résultats
            current_email = await _find_email(db, email_addr)

            # Traiter chaque résultat OSINT
            for item in osint_results:
                source = item.get("source", "unknown")
                data = item.get("data", {})

                # Site à lier à la corrélation de ce résultat (None si aucun)
                item_site_id: int | None = None
                # Une corrélation n'est créée que si une nouvelle entité est découverte (m7)
                discovered_new = False

                # Extraire les entités textuelles des résultats
                text_content = _extract_text(data)
                if text_content:
                    try:
                        entities = await extract_entities(text_content)
                    except Exception as e:
                        logger.error(f"[enrich] Erreur extract_entities : {e}")
                        entities = {"emails": [], "domains": [], "names": []}

                    # Stocker les nouveaux emails découverts
                    for new_email in entities.get("emails", []):
                        if new_email not in processed_emails:
                            email_obj = await _get_or_create_email(
                                db, new_email, source, person_id
                            )
                            if email_obj:
                                counters["new_emails"] += 1
                                discovered_new = True
                                next_queue.append(new_email)

                    # Stocker les nouveaux domaines comme sites
                    for domain in entities.get("domains", []):
                        url = f"https://{domain.lower()}"
                        site_obj, created = await _get_or_create_site(db, url, source)
                        if created:
                            counters["new_sites"] += 1
                            discovered_new = True
                        if domain not in processed_domains:
                            processed_domains.add(domain)
                        item_site_id = site_obj.id

                # Stocker les sites provenant du WHOIS ou DNS (prioritaire)
                if source == "whois":
                    domain_name = data.get("domain_name")
                    if domain_name and isinstance(domain_name, str):
                        url = f"https://{domain_name.lower()}"
                        site_obj, created = await _get_or_create_site(db, url, "whois")
                        if created:
                            counters["new_sites"] += 1
                            discovered_new = True
                        if domain_name.lower() not in processed_domains:
                            processed_domains.add(domain_name.lower())
                        item_site_id = site_obj.id

                # Corrélation créée uniquement si une nouvelle entité a été découverte (m7)
                if discovered_new:
                    corr = Correlation(
                        person_id=person_id,
                        email_id=current_email.id if current_email else None,
                        site_id=item_site_id,
                        score=0.5,
                    )
                    db.add(corr)
                    counters["new_correlations"] += 1

        email_queue = next_queue

    await db.commit()
    logger.info(
        f"[enrich] Terminé pour person_id={person_id} — "
        f"{counters['new_emails']} email(s), "
        f"{counters['new_sites']} site(s), "
        f"{counters['new_correlations']} corrélation(s)"
    )
    return {"person_id": person_id, **counters}


def _extract_text(data: dict) -> str:
    """Extrait le contenu textuel exploitable d'un résultat OSINT."""
    parts: list[str] = []

    # DuckDuckGo : topics
    for topic in data.get("RelatedTopics", []):
        if isinstance(topic, dict) and "Text" in topic:
            parts.append(topic["Text"])

    # WHOIS : emails et org
    if data.get("emails"):
        emails = data["emails"]
        if isinstance(emails, list):
            parts.extend(emails)
        elif isinstance(emails, str):
            parts.append(emails)
    if data.get("org"):
        parts.append(str(data["org"]))

    # MX records
    for mx in data.get("mx_records", []):
        parts.append(str(mx))

    return " ".join(parts)


async def _find_email(db: AsyncSession, email_addr: str) -> Email | None:
    result = await db.execute(select(Email).where(Email.email == email_addr))
    return result.scalar_one_or_none()


async def _get_or_create_email(
    db: AsyncSession, email_addr: str, source: str, person_id: int
) -> Email | None:
    """Crée un email s'il n'existe pas déjà en DB. Retourne l'Email créé ou None."""
    existing = await _find_email(db, email_addr)
    if existing:
        return None

    email_obj = Email(email=email_addr, source=source, person_id=person_id)
    db.add(email_obj)
    await db.flush()
    logger.debug(f"[enrich] Nouvel email stocké : {email_addr} (source={source})")
    return email_obj


async def _get_or_create_site(
    db: AsyncSession, url: str, source: str
) -> tuple[Site, bool]:
    """Récupère ou crée un site en DB.

    Retourne (site, created) : `created=True` si le site vient d'être créé,
    `False` s'il existait déjà. Le site est toujours retourné pour permettre
    de lier une corrélation, qu'il soit nouveau ou non.
    """
    result = await db.execute(select(Site).where(Site.url == url))
    existing = result.scalar_one_or_none()
    if existing:
        return existing, False

    site_obj = Site(url=url, server=source)
    db.add(site_obj)
    await db.flush()
    logger.debug(f"[enrich] Nouveau site stocké : {url} (source={source})")
    return site_obj, True
