"""
Reset des données de démo — OSINT Platform.

Vide toutes les tables applicatives (TRUNCATE ... CASCADE) et recrée
l'utilisateur admin. NE touche PAS au schéma (pas d'alembic).

Usage : python scripts/reset_demo.py
Requiert PostgreSQL (TRUNCATE ... RESTART IDENTITY CASCADE).
"""
import asyncio
import os
import sys

# Rendre le package "app" importable quel que soit le cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from app.db.session import engine, async_session  # noqa: E402
from app.db.models import User  # noqa: E402
from app.core.security import hash_password  # noqa: E402

GREEN, RED, YELLOW, BOLD, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[1m", "\033[0m"

# Ordre sans importance : CASCADE gère les FK. alembic_version est volontairement exclue.
TABLES = [
    "audit_logs", "correlations", "search_results", "search_history",
    "emails", "sites", "persons", "users",
]


async def reset() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        )
    print(f"{GREEN}✅ Toutes les tables vidées (identités réinitialisées){RESET}")

    async with async_session() as db:
        db.add(User(
            username="admin",
            hashed_password=hash_password("osint2024"),
            role="admin",
            is_active=True,
        ))
        await db.commit()
    print(f"{GREEN}✅ Utilisateur admin recréé (admin / osint2024){RESET}")


def main() -> None:
    print(f"{YELLOW}{BOLD}⚠️  Ceci va SUPPRIMER toutes les données applicatives "
          f"(personnes, emails, sites, corrélations, recherches, audit, utilisateurs).{RESET}")
    print(f"{YELLOW}Le schéma (tables/migrations) n'est PAS modifié.{RESET}")
    answer = input("Confirmer le reset ? [y/N] ").strip().lower()
    if answer != "y":
        print("Annulé — aucune donnée supprimée.")
        sys.exit(0)

    try:
        asyncio.run(reset())
        print(f"{GREEN}{BOLD}Reset terminé. Base propre, prête pour le seed.{RESET}")
    except Exception as e:
        print(f"{RED}Échec du reset : {e}{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
