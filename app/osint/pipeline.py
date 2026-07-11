"""
Pipeline d'enrichissement OSINT complet après identification faciale.

Contexte : `enrich_person()` (correlator.py) ne pivote que sur les emails DÉJÀ présents en
base. Pour une personne fraîchement identifiée sans email connu, la corrélation s'arrête
aussitôt. Ce pipeline amorce l'enrichissement à partir du NOM :

    nom → emails → domaines → HIBP → pseudos

en réutilisant les briques existantes (`correlate`, `enrich_person`, `check_all_platforms`) —
aucune source nouvelle, uniquement les sources publiques déjà déclarées dans `SOURCES`.

GARDE-FOU CENTRAL (voir CLAUDE.md, section reconnaissance faciale). Ce pipeline ne s'exécute
QUE pour les vraies personnes sujettes d'une enquête (`Person.source == "investigation"`). Il
REFUSE explicitement de profiler :
  - un sujet de démonstration consentant (`consented_demo`) — son consentement porte sur la
    reconnaissance faciale, PAS sur un profilage OSINT web-wide (HIBP, énumération de comptes,
    cartographie de domaines) ;
  - une entrée du jeu académique LFW (`lfw_dataset`) — personnalité non ciblée, jeu figé ;
  - une personne encore anonyme (`first_name == "Inconnu"`) — on ne saurait pas qui chercher.

Ce garde-fou est **doublé** côté appelants (face_routes) : défense en profondeur, jamais un
point de contrôle unique. Ne pas le retirer « pour simplifier » — profiler automatiquement la
vraie personne derrière un visage reconnu est précisément le comportement type Clearview AI
refusé par le projet. Ce pipeline ne doit jamais être déclenché automatiquement par le flux
webcam (`/api/stream/analyze`) : uniquement par une action analyste délibérée.

Tolérance aux erreurs : chaque étape est isolée dans un try/except — une étape qui échoue
n'empêche pas les suivantes, et le pipeline renvoie toujours un résumé (jamais d'exception
remontée à l'appelant, cf. convention « dégradation propre »).
"""
import time
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Email, EnrichmentJob, Person, SearchHistory, SearchResult, Site, hash_email,
)
from app.osint.correlator import enrich_person
from app.osint.engine import correlate
from app.osint.username_checker import check_all_platforms, normalize_username

# Seule origine autorisée à déclencher ce pipeline (voir docstring). Toute autre est refusée.
ENRICHABLE_SOURCE = "investigation"


async def create_pending_job(db: AsyncSession, person_id: int, triggered_by: str) -> int:
    """Crée un job `pending` (appelé côté requête, avant la tâche de fond) et renvoie son id.

    Le job est ainsi immédiatement visible par le polling frontend, sans fenêtre 404 entre le
    retour de la requête et le démarrage effectif du pipeline en arrière-plan.
    """
    job = EnrichmentJob(person_id=person_id, status="pending", triggered_by=triggered_by)
    db.add(job)
    await db.flush()
    return job.id


async def full_enrich_after_face_match(
    person_id: int, db: AsyncSession, job_id: int | None = None
) -> dict:
    """Pipeline complet après identification faciale d'un sujet d'enquête.

    Ne lève jamais : renvoie toujours un dict de résumé (`skipped` renseigné si non exécuté).
    Si `job_id` est fourni, le job EnrichmentJob correspondant est mis à jour tout au long
    (running → completed/failed) — et TOUJOURS résolu vers un état terminal, pour que le
    polling frontend ne tourne jamais indéfiniment.
    """
    t0 = time.perf_counter()
    summary: dict = {
        "person_id": person_id,
        "name_searched": None,
        "emails_found": [],
        "domains_searched": [],
        "sites_found": 0,
        "correlations_created": 0,
        "username_platforms_found": 0,
        "duration_seconds": 0.0,
        "skipped": None,
    }

    person = await db.get(Person, person_id)
    if person is None:
        logger.warning(f"[pipeline] person_id={person_id} introuvable")
        summary["skipped"] = "personne introuvable"
        await _job_failed(db, job_id, "personne introuvable")
        return summary

    # --- GARDE-FOU : uniquement les vraies personnes d'enquête, identifiées ---
    if person.source != ENRICHABLE_SOURCE:
        logger.info(
            f"[pipeline] Refusé — person_id={person_id} source={person.source} : "
            f"enrichissement réservé à source={ENRICHABLE_SOURCE} (un sujet consentant ou "
            f"une entrée LFW ne doit jamais être profilé automatiquement)."
        )
        summary["skipped"] = f"source={person.source} non enrichissable"
        await _job_failed(db, job_id, summary["skipped"])
        return summary
    if person.first_name == "Inconnu":
        logger.info(f"[pipeline] Refusé — person_id={person_id} non identifiée (Inconnu)")
        summary["skipped"] = "personne non identifiée"
        await _job_failed(db, job_id, summary["skipped"])
        return summary

    full_name = f"{person.first_name} {person.last_name}".strip()
    summary["name_searched"] = full_name
    logger.info(f"[pipeline] Démarrage — person_id={person_id}, nom='{full_name}'")
    await _job_running(db, job_id)

    try:
        # --- ÉTAPE 2 : recherche OSINT par nom, historisée (SearchHistory + SearchResult) ---
        name_results: list[dict] = []
        try:
            name_results = await correlate(full_name, "name")
            await _persist_search(db, full_name, "name", name_results)
            await db.commit()
            logger.info(f"[pipeline] Recherche nom '{full_name}' — {len(name_results)} résultat(s)")
        except Exception as e:
            logger.error(f"[pipeline] Étape 'recherche par nom' échouée : {e}")
            await db.rollback()

        # --- ÉTAPE 3 : les emails découverts deviennent des graines de pivot (table emails) ---
        emails_found = _emails_from_results(name_results)
        summary["emails_found"] = sorted(emails_found)
        for email_addr in emails_found:
            try:
                await _seed_email(db, email_addr, person_id)
            except Exception as e:
                logger.error(f"[pipeline] Email graine '{email_addr}' non persisté : {e}")
        try:
            await db.commit()
        except Exception as e:
            logger.error(f"[pipeline] Commit des emails graines échoué : {e}")
            await db.rollback()

        # --- ÉTAPES 4-6 : pivot email → domaine → HIBP → corrélations ---
        # Délégué à enrich_person() : respecte déjà MAX_DEPTH=3, la déduplication par sets, le
        # rate limit externe (2s) et inclut HIBP (présent dans SOURCES["email"]). On ne réécrit
        # pas cette logique — on l'amorce simplement avec les emails trouvés par nom ci-dessus.
        try:
            counts = await enrich_person(person_id, db)
            summary["correlations_created"] = counts.get("new_correlations", 0)
            summary["sites_found"] += counts.get("new_sites", 0)
        except Exception as e:
            logger.error(f"[pipeline] enrich_person échoué : {e}")
        summary["domains_searched"] = sorted(
            {e.split("@", 1)[1].lower() for e in emails_found if "@" in e}
        )

        # --- ÉTAPE 7 : énumération de pseudo « prenom.nom » (Module 15), tolérante ---
        slug = normalize_username(f"{person.first_name}.{person.last_name}")
        try:
            found_urls: list[str] = []
            async for evt in check_all_platforms(slug):
                if evt.get("found") and evt.get("url"):
                    found_urls.append(evt["url"])
            summary["username_platforms_found"] = len(found_urls)
            summary["sites_found"] += await _persist_username_sites(db, found_urls)
            await db.commit()
            logger.info(f"[pipeline] Énumération pseudo '{slug}' — {len(found_urls)} compte(s)")
        except Exception as e:
            logger.error(f"[pipeline] Étape 'énumération pseudo' échouée : {e}")
            await db.rollback()

        summary["duration_seconds"] = round(time.perf_counter() - t0, 2)
        await _job_completed(db, job_id, summary)
    except Exception as e:
        # Filet de sécurité : une erreur inattendue hors des try/except par étape ne doit
        # jamais laisser le job coincé en "running" (polling infini côté UI).
        logger.error(f"[pipeline] Erreur inattendue person_id={person_id} : {e}")
        summary["duration_seconds"] = round(time.perf_counter() - t0, 2)
        await _job_failed(db, job_id, str(e))
        return summary

    logger.info(
        f"[pipeline] Terminé — person_id={person_id} : "
        f"{len(summary['emails_found'])} email(s), {summary['sites_found']} site(s), "
        f"{summary['correlations_created']} corrélation(s), "
        f"{summary['username_platforms_found']} plateforme(s), {summary['duration_seconds']}s"
    )
    return summary


# --------------------------------------------------------------------------------------------
# Suivi du job (EnrichmentJob) — tolérant : un échec de mise à jour du job ne casse pas le
# pipeline. Chaque helper est un no-op si job_id est None (pipeline invoqué sans suivi).
# --------------------------------------------------------------------------------------------

async def _job_running(db: AsyncSession, job_id: int | None) -> None:
    if job_id is None:
        return
    try:
        job = await db.get(EnrichmentJob, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception as e:
        logger.error(f"[pipeline] MAJ job {job_id} -> running échouée : {e}")
        await db.rollback()


async def _job_completed(db: AsyncSession, job_id: int | None, summary: dict) -> None:
    if job_id is None:
        return
    try:
        job = await db.get(EnrichmentJob, job_id)
        if job:
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.summary = {
                "emails_found": len(summary["emails_found"]),
                "sites_found": summary["sites_found"],
                "correlations_created": summary["correlations_created"],
                "username_platforms_found": summary["username_platforms_found"],
            }
            await db.commit()
    except Exception as e:
        logger.error(f"[pipeline] MAJ job {job_id} -> completed échouée : {e}")
        await db.rollback()


async def _job_failed(db: AsyncSession, job_id: int | None, message: str) -> None:
    if job_id is None:
        return
    try:
        job = await db.get(EnrichmentJob, job_id)
        if job:
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = (message or "")[:2000]
            await db.commit()
    except Exception as e:
        logger.error(f"[pipeline] MAJ job {job_id} -> failed échouée : {e}")
        await db.rollback()


# --------------------------------------------------------------------------------------------
# Helpers internes
# --------------------------------------------------------------------------------------------

async def _persist_search(
    db: AsyncSession, query: str, query_type: str, results: list[dict]
) -> None:
    """Historise une recherche OSINT (SearchHistory + un SearchResult par OsintResult).

    Mappe 1:1 comme routes._run_search — flush pour obtenir l'id d'historique, pas de commit
    (l'appelant décide du moment du commit).
    """
    entry = SearchHistory(query=query, query_type=query_type, result_count=len(results))
    db.add(entry)
    await db.flush()
    for r in results:
        db.add(SearchResult(
            search_id=entry.id,
            source=r.get("source", "unknown"),
            entity=r.get("entity", query),
            entity_type=r.get("type", query_type),
            data=r.get("data"),
            extracted=r.get("extracted"),
        ))


def _emails_from_results(results: list[dict]) -> set[str]:
    """Agrège les emails du champ normalisé `extracted` de chaque OsintResult."""
    emails: set[str] = set()
    for r in results:
        emails.update((r.get("extracted") or {}).get("emails", []))
    return emails


async def _seed_email(db: AsyncSession, email_addr: str, person_id: int) -> bool:
    """Rattache un email à la personne s'il n'existe pas déjà (dédup par hash SHA256).

    La recherche passe par email_hash (la colonne email est chiffrée / non déterministe),
    jamais par une comparaison sur le texte chiffré. Retourne True si créé.
    """
    exists = (await db.execute(
        select(Email).where(Email.email_hash == hash_email(email_addr))
    )).scalar_one_or_none()
    if exists:
        return False
    db.add(Email(email=email_addr, source="pipeline-name", person_id=person_id))
    return True


async def _persist_username_sites(db: AsyncSession, urls: list[str]) -> int:
    """Crée un Site par URL de profil trouvée (dédup sur l'URL, déjà unique en base)."""
    urls = [u for u in urls if u]
    if not urls:
        return 0
    existing = set((await db.execute(
        select(Site.url).where(Site.url.in_(urls))
    )).scalars().all())
    added = 0
    seen = set(existing)
    for url in urls:
        if url in seen:
            continue
        db.add(Site(url=url, server="username-enum"))
        seen.add(url)
        added += 1
    return added
