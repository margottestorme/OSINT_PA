"""
CLI de gestion — OSINT Platform
Usage : python -m app.cli create-user --username admin --role admin
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import inspect, select

from app.core.security import hash_password
from app.db.session import async_session, engine
from app.db.models import User


async def create_user(username: str, password: str, role: str) -> None:
    # Le schéma doit avoir été créé via Alembic (m10) — on ne crée plus les tables ici,
    # pour éviter toute divergence avec les migrations.
    async with engine.connect() as conn:
        has_users_table = await conn.run_sync(lambda c: inspect(c).has_table("users"))
    if not has_users_table:
        print("Erreur : schéma non initialisé. Lancez d'abord : alembic upgrade head")
        sys.exit(1)

    async with async_session() as db:
        existing = await db.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none():
            print(f"Erreur : l'utilisateur '{username}' existe déjà.")
            sys.exit(1)

        user = User(
            username=username,
            hashed_password=hash_password(password),
            role=role,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        print(f"Utilisateur '{username}' créé avec le rôle '{role}'.")


def main():
    parser = argparse.ArgumentParser(description="OSINT Platform CLI")
    subparsers = parser.add_subparsers(dest="command")

    create_cmd = subparsers.add_parser("create-user", help="Créer un utilisateur")
    create_cmd.add_argument("--username", required=True, help="Nom d'utilisateur")
    create_cmd.add_argument("--role", choices=["admin", "analyst"], default="analyst",
                            help="Rôle (admin ou analyst)")
    create_cmd.add_argument("--password", help="Mot de passe (demandé interactivement si omis)")

    args = parser.parse_args()

    if args.command == "create-user":
        password = args.password
        if not password:
            password = getpass.getpass("Mot de passe : ")
            confirm = getpass.getpass("Confirmer : ")
            if password != confirm:
                print("Erreur : les mots de passe ne correspondent pas.")
                sys.exit(1)

        if len(password) < 6:
            print("Erreur : le mot de passe doit contenir au moins 6 caractères.")
            sys.exit(1)

        asyncio.run(create_user(args.username, password, args.role))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
