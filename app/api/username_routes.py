"""Routes d'énumération de pseudos (Module 15).

Deux entrées :
- `GET /api/username/check`  : **stream SSE** temps réel (grille live) — ne persiste rien.
- `GET /api/username/search/{username}` : lance l'énumération en tâche de fond et **persiste**
  les résultats en base, renvoie immédiatement un `search_id`.

Le username est validé (3–30 caractères, `[A-Za-z0-9._-]`) et l'énumération est limitée à
1 recherche / 30 s par utilisateur (112 plateformes actives × jusqu'à 20 variantes générées
par `generate_username_variants` — voir `app/osint/username_checker.py`).
"""
import asyncio
import json
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from sqlalchemy import select

from app.core.security import verify_token
from app.core.audit import create_audit_log, get_client_ip, resolve_user_id
from app.db.session import get_db
from app.db.models import Site, UsernameResult, UsernameSearch
from app.schemas.schemas import UsernameSaveRequest, UsernameSearchStartResponse
from app.api.rate_limit import username_rate_limiter
from app.osint.username_checker import (
    PLATFORM_COUNT,
    _build_dynamic_platforms,
    check_all_platforms,
    normalize_username,
    platform_categories,
)

username_router = APIRouter(prefix="/api/username", tags=["username"])


@username_router.get("/platforms")
async def username_platforms(token_data: dict = Depends(verify_token)) -> dict:
    """Métadonnées du registre : total de plateformes + catégories (pour l'UI).

    Inclut les plateformes à API officielle (YouTube, Twitch) si leur clé est configurée
    (cf. `_build_dynamic_platforms` — désactivées silencieusement sinon).
    """
    dynamic = _build_dynamic_platforms()
    categories = set(platform_categories()) | {p["category"] for p in dynamic}
    return {"total": PLATFORM_COUNT + len(dynamic), "categories": sorted(categories)}

# 3 à 30 caractères : lettres, chiffres, point, underscore, tiret.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")


def _validate_username(username: str) -> str:
    """Normalise (strip) et valide le format du username, sinon lève 422."""
    username = normalize_username(username)
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="Username invalide : 3 à 30 caractères, lettres/chiffres/._- uniquement",
        )
    return username


def _sse(payload: dict) -> str:
    """Sérialise un événement au format Server-Sent Events."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# --------------------------------------------------------------------------- #
# Intégration OSINT : URLs trouvées -> Sites, enrichissement GitHub (Module 15 §4) #
# --------------------------------------------------------------------------- #

async def _persist_found_sites(session, results: list[dict]) -> int:
    """Crée un Site pour chaque URL trouvée (dédup sur l'URL, déjà unique en base)."""
    urls = [r["url"] for r in results if r.get("found") and r.get("url")]
    if not urls:
        return 0
    existing = set((await session.execute(
        select(Site.url).where(Site.url.in_(urls))
    )).scalars().all())
    added = 0
    seen = set(existing)
    for url in urls:
        if url in seen:
            continue
        session.add(Site(url=url, server="username-enum"))
        seen.add(url)
        added += 1
    return added


def _github_found(results: list[dict]) -> bool:
    return any(r.get("found") and r.get("platform") == "GitHub" for r in results)


async def _enrich_github(username: str) -> None:
    """Enrichissement de fond : GitHub trouvé -> _source_github, URLs de profils -> Sites."""
    from app.db.session import async_session
    from app.osint.engine import _source_github
    try:
        result = await _source_github(username)
        if not result:
            return
        urls = (result.get("extracted") or {}).get("urls") or []
        if not urls:
            return
        async with async_session() as session:
            existing = set((await session.execute(
                select(Site.url).where(Site.url.in_(urls))
            )).scalars().all())
            for url in urls:
                if url not in existing:
                    session.add(Site(url=url, server="github-enrich"))
                    existing.add(url)
            await session.commit()
        logger.info(f"[username] Enrichissement GitHub effectué pour '{username}' ({len(urls)} URL)")
    except Exception as exc:
        logger.error(f"[username] Échec enrichissement GitHub '{username}' : {exc}")


# --------------------------------------------------------------------------- #
# Stream SSE temps réel (non persisté)                                        #
# --------------------------------------------------------------------------- #

@username_router.get("/check")
async def username_check_stream(
    request: Request,
    username: str = Query(..., description="Pseudo à énumérer (3–30 caractères)"),
    token_data: dict = Depends(verify_token),
):
    """Énumère un pseudo et **stream** les résultats en SSE au fil de l'eau.

    Chaque plateforme produit un événement `data: {...}` ; un événement final
    `data: {"type":"complete", ...}` clôt le flux. Si le navigateur se déconnecte,
    le générateur s'arrête et `check_all_platforms` annule les requêtes en vol.
    """
    username = _validate_username(username)
    user = token_data.get("sub", "unknown")
    username_rate_limiter.check(user)

    # Audit dans une session courte (ne pas garder une connexion DB ouverte le temps du stream)
    from app.db.session import async_session
    async with async_session() as session:
        user_id = await resolve_user_id(session, user)
        await create_audit_log(
            session, "username_search_stream", target=username,
            user_id=user_id, ip_address=get_client_ip(request),
        )
        await session.commit()

    async def event_stream():
        found = not_found = errors = 0
        completed = True
        try:
            async for result in check_all_platforms(username):
                # Arrêt propre si le client s'est déconnecté
                if await request.is_disconnected():
                    logger.info(f"[username] Client déconnecté — arrêt du stream pour '{username}'")
                    completed = False
                    break
                if result["error"]:
                    errors += 1
                elif result["found"]:
                    found += 1
                else:
                    not_found += 1
                yield _sse(result)

            if completed:
                yield _sse({
                    "type": "complete",
                    "total": PLATFORM_COUNT,
                    "found": found,
                    "not_found": not_found,
                    "errors": errors,
                })
        except asyncio.CancelledError:
            # StreamingResponse annule le générateur quand la connexion tombe :
            # on relaie l'annulation pour que check_all_platforms libère ses tâches.
            logger.info(f"[username] Stream annulé pour '{username}'")
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # désactive le buffering nginx pour le SSE
        },
    )


# --------------------------------------------------------------------------- #
# Recherche persistée (tâche de fond)                                         #
# --------------------------------------------------------------------------- #

async def _run_username_search(search_id: int, username: str) -> None:
    """Tâche de fond : énumère, persiste chaque résultat, crée les Sites trouvés, clôt."""
    from app.db.session import async_session
    try:
        collected: list[dict] = []
        found = 0
        async with async_session() as session:
            async for result in check_all_platforms(username):
                collected.append(result)
                session.add(UsernameResult(
                    search_id=search_id,
                    platform=result["platform"],
                    url=result["url"],
                    category=result["category"],
                    found=result["found"],
                    response_time_ms=result["response_time_ms"],
                    error=result["error"],
                    matched_variant=result.get("variant"),
                ))
                if result["found"]:
                    found += 1
            # Intégration OSINT : URLs trouvées -> Sites (§4.1)
            await _persist_found_sites(session, collected)
            search = await session.get(UsernameSearch, search_id)
            if search is not None:
                search.found_count = found
                search.status = "completed"
            await session.commit()
        # GitHub trouvé -> enrichissement (§4.2)
        if _github_found(collected):
            await _enrich_github(username)
        logger.info(f"[username] Recherche {search_id} terminée pour '{username}' — {found} trouvé(s)")
    except Exception as exc:
        logger.error(f"[username] Erreur pendant la recherche {search_id} '{username}' : {exc}")


@username_router.get("/search/{username}", response_model=UsernameSearchStartResponse)
async def username_search_persist(
    username: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Lance une énumération **persistée** et renvoie immédiatement le `search_id`.

    Les résultats sont écrits en base par une tâche de fond ; consultables ensuite.
    """
    username = _validate_username(username)
    user = token_data.get("sub", "unknown")
    username_rate_limiter.check(user)

    user_id = await resolve_user_id(db, user)
    search = UsernameSearch(
        username=username,
        user_id=user_id,
        total_platforms=PLATFORM_COUNT,
        status="running",
    )
    db.add(search)
    await db.flush()
    await create_audit_log(
        db, "username_search", target=username,
        user_id=user_id, ip_address=get_client_ip(request),
    )
    search_id = search.id

    background_tasks.add_task(_run_username_search, search_id, username)

    return UsernameSearchStartResponse(
        search_id=search_id,
        username=username,
        total_platforms=PLATFORM_COUNT,
        status="running",
    )


@username_router.post("/save", response_model=UsernameSearchStartResponse)
async def username_save(
    body: UsernameSaveRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Persiste des résultats **déjà collectés** côté client (issus du stream SSE).

    Évite de ré-énumérer toutes les plateformes : crée le `UsernameSearch` (déjà terminé),
    ses résultats, les `Site` pour les URLs trouvées, et déclenche l'enrichissement GitHub.
    Renvoie le `search_id`, rattachable ensuite à une enquête (entity_type `username_search`).
    """
    username = _validate_username(body.username)
    results = [r.model_dump() for r in body.results]
    found = sum(1 for r in results if r["found"])

    user_id = await resolve_user_id(db, token_data.get("sub", "unknown"))
    search = UsernameSearch(
        username=username,
        user_id=user_id,
        total_platforms=len(results) or PLATFORM_COUNT,
        found_count=found,
        status="completed",
    )
    db.add(search)
    await db.flush()

    for r in results:
        db.add(UsernameResult(
            search_id=search.id,
            platform=r["platform"], url=r["url"], category=r["category"],
            found=r["found"], response_time_ms=r["response_time_ms"], error=r["error"],
            matched_variant=r.get("variant"),
        ))
    await _persist_found_sites(db, results)

    await create_audit_log(
        db, "username_search_saved", target=f"{username}:{found}found",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    search_id = search.id

    if _github_found(results):
        background_tasks.add_task(_enrich_github, username)

    return UsernameSearchStartResponse(
        search_id=search_id,
        username=username,
        total_platforms=search.total_platforms,
        status="completed",
    )
