"""
Réindexation PostgreSQL → Elasticsearch — OSINT Platform (Module 11).

Relit toutes les tables applicatives et (ré)indexe chaque ligne dans Elasticsearch,
avec une barre de progression par type et un résumé final coloré.

Idempotent : l'`_id` Elasticsearch est la clé primaire PostgreSQL → chaque exécution
fait un *upsert* (aucun doublon, mise à jour en place). On peut donc relancer le script
autant de fois que nécessaire.

Si Elasticsearch est indisponible, affiche un message clair et sort en code 1.

Usage : python scripts/reindex.py
"""
import asyncio
import os
import sys

# Rendre le package "app" importable quel que soit le cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import async_session  # noqa: E402
from app.db.models import Email, Person, SearchResult, Site  # noqa: E402
from app.search.elastic import ALL_INDICES, elastic  # noqa: E402
from app.search.indexer import (  # noqa: E402
    index_email, index_person, index_search_result, index_site,
)

GREEN, RED, YELLOW, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)

# (libellé, modèle SQLAlchemy, fonction d'indexation, clé du compteur)
TYPES = [
    ("Personnes",  Person,       index_person,        "persons"),
    ("Recherches", SearchResult, index_search_result, "search_results"),
    ("Emails",     Email,        index_email,         "emails"),
    ("Sites",      Site,         index_site,          "sites"),
]


def _draw_bar(label: str, current: int, total: int, width: int = 30) -> None:
    """Barre de progression réécrite en place (retour chariot)."""
    frac = current / total if total else 1.0
    bar = "█" * int(width * frac) + "░" * (width - int(width * frac))
    sys.stdout.write(f"\r  {label:<11} [{CYAN}{bar}{RESET}] {int(frac * 100):3d}% ({current}/{total})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


async def _reindex_type(session, label, model, index_fn) -> int:
    """Réindexe toutes les lignes d'un type ; renvoie le nombre de documents indexés."""
    rows = (await session.execute(select(model))).scalars().all()
    total = len(rows)
    if total == 0:
        print(f"  {label:<11} {DIM}— aucune donnée{RESET}")
        return 0
    indexed = 0
    for i, row in enumerate(rows, 1):
        if await index_fn(row):
            indexed += 1
        _draw_bar(label, i, total)
    return indexed


async def _run() -> dict | None:
    """Réindexe tous les types. Renvoie les compteurs, ou None si ES est indisponible."""
    await elastic.connect()
    try:
        if not await elastic.is_available():
            return None
        # Garantit des index au mapping strict AVANT toute écriture (sinon ES créerait
        # des index à mapping dynamique sur la première indexation).
        await elastic.create_indices()
        counts: dict[str, int] = {}
        async with async_session() as session:
            for label, model, index_fn, key in TYPES:
                counts[key] = await _reindex_type(session, label, model, index_fn)
        return counts
    finally:
        await elastic.disconnect()


def _print_summary(counts: dict) -> None:
    total = sum(counts.values())
    print(f"\n{BOLD}Résumé{RESET}")
    for label, _model, _fn, key in TYPES:
        print(f"  {GREEN}●{RESET} {label:<11}: {BOLD}{counts[key]}{RESET}")
    print(f"  {DIM}{'─' * 28}{RESET}")
    print(f"  {BOLD}Total indexé : {GREEN}{total}{RESET}{BOLD} document(s){RESET}")


def main() -> None:
    print(f"{BOLD}{CYAN}Réindexation PostgreSQL → Elasticsearch{RESET}")
    print(f"{DIM}Index cibles : {', '.join(ALL_INDICES)}{RESET}\n")

    try:
        counts = asyncio.run(_run())
    except Exception as e:
        print(f"{RED}{BOLD}Échec de la réindexation : {e}{RESET}")
        sys.exit(1)

    if counts is None:
        url = get_settings().ELASTICSEARCH_URL
        print(f"{RED}{BOLD}❌ Elasticsearch indisponible{RESET} ({url}).")
        print(f"{YELLOW}Démarrez-le (ex : docker compose up -d elasticsearch), "
              f"vérifiez ELASTICSEARCH_URL, puis relancez.{RESET}")
        sys.exit(1)

    _print_summary(counts)
    print(f"\n{GREEN}{BOLD}✅ Réindexation terminée (upsert idempotent).{RESET}")


if __name__ == "__main__":
    main()
