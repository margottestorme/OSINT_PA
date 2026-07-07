"""
Audit de fiabilité du registre de plateformes — énumération de pseudos (Module 15).

Deux classes de faux positifs ont été découvertes le 2026-07-07 dans le registre
`PLATFORMS` (app/osint/username_checker.py) :
1. Un pseudo aléatoire "propre" garanti inexistant déclaré "trouvé" — sites qui renvoient
   un 200 générique (SPA rendue côté client, ou mur anti-bot) quel que soit le compte.
2. Plus insidieux : un pseudo bidon AVEC séparateur (point/underscore/tiret) déclaré
   "trouvé" — la plateforme n'accepte pas ce caractère comme pseudo valide et bascule sur
   une page générique au lieu d'un vrai 404 (ex : Telegram, Twitter/X, Trakt, ENS).

Ce script reproduit les deux tests sur chaque plateforme **active** (`PLATFORMS`, y compris
les sources à API officielle YouTube/Twitch si leurs clés sont configurées) avec un pseudo
aléatoire FRAIS à chaque exécution (jamais mémorisé par un site), et signale toute
plateforme qui répond "trouvé" à tort.

Ne modifie jamais le registre automatiquement — affiche un rapport à traiter manuellement :
trouver un marqueur `not_found_text`/`found_text` fiable, ou désactiver la plateforme
(`"reliable": False`, voir la section correspondante dans username_checker.py).

Sort en code 1 si au moins un faux positif est détecté (utilisable en tâche planifiée pour
détecter la dérive du registre au fil du temps — un site peut changer son HTML et rendre un
marqueur existant obsolète, comme observé sur Telegram et eBay), 0 sinon.

Usage : python scripts/audit_username_platforms.py
"""
import asyncio
import os
import random
import string
import sys

# Rendre le package "app" importable quel que soit le cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from app.osint.username_checker import (  # noqa: E402
    PLATFORMS, _build_dynamic_platforms, check_username,
)

GREEN, RED, YELLOW, CYAN, BOLD, DIM, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[1m", "\033[2m", "\033[0m"
)

MAX_CONCURRENT = 20


def _random_token(length: int = 10) -> str:
    """Chaîne aléatoire (lettres/chiffres) garantie inexistante nulle part."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _garbage_variants() -> list[str]:
    """Un pseudo bidon neutre + un par séparateur (point/underscore/tiret).

    Reproduit les deux classes de faux positifs décrites en en-tête : le pseudo neutre
    attrape les SPA/murs anti-bot toujours "200" ; les 3 variantes séparées attrapent les
    plateformes qui n'acceptent pas ce caractère et retombent sur une page générique.
    """
    token = _random_token()
    prefix, suffix = token[:4], token[4:]
    return [token, f"{prefix}.{suffix}", f"{prefix}_{suffix}", f"{prefix}-{suffix}"]


def _draw_bar(current: int, total: int, width: int = 30) -> None:
    """Barre de progression réécrite en place (retour chariot)."""
    frac = current / total if total else 1.0
    bar = "█" * int(width * frac) + "░" * (width - int(width * frac))
    sys.stdout.write(f"\r  Audit [{CYAN}{bar}{RESET}] {int(frac * 100):3d}% ({current}/{total})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


async def _audit_platform(
    platform: dict, garbage: list[str], session: httpx.AsyncClient, semaphore: asyncio.Semaphore,
) -> list[str]:
    """Teste une plateforme sur tous les pseudos bidons ; renvoie ceux déclarés (à tort) trouvés."""
    check_fn = platform.get("checker", check_username)

    async def _bounded(g: str) -> tuple[str, dict]:
        async with semaphore:
            return g, await check_fn(g, platform, session)

    results = await asyncio.gather(*(_bounded(g) for g in garbage))
    return [g for g, r in results if r["found"]]


async def _run() -> dict[str, list[str]]:
    """Audite toutes les plateformes actives ; renvoie {nom: [pseudos bidons déclarés trouvés]}."""
    platforms = PLATFORMS + _build_dynamic_platforms()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    issues: dict[str, list[str]] = {}
    async with httpx.AsyncClient(follow_redirects=True) as session:
        for i, platform in enumerate(platforms, 1):
            triggers = await _audit_platform(platform, _garbage_variants(), session, semaphore)
            if triggers:
                issues[platform["name"]] = triggers
            _draw_bar(i, len(platforms))
    return issues


def main() -> None:
    total = len(PLATFORMS) + len(_build_dynamic_platforms())
    print(f"{BOLD}{CYAN}Audit de fiabilité — registre de plateformes (Module 15){RESET}")
    print(f"{DIM}{total} plateformes actives testées avec des pseudos bidons garantis "
          f"inexistants (neutre + point/underscore/tiret).{RESET}\n")

    issues = asyncio.run(_run())

    print()
    if not issues:
        print(f"{GREEN}{BOLD}✅ Aucun faux positif détecté sur les {total} plateformes actives.{RESET}")
        sys.exit(0)

    print(f"{RED}{BOLD}⚠ {len(issues)} plateforme(s) avec faux positif détecté :{RESET}\n")
    for name, triggers in sorted(issues.items()):
        print(f"  {RED}●{RESET} {name:<25} déclenché par : {', '.join(triggers)}")
    print(
        f"\n{YELLOW}Action recommandée : trouver un marqueur not_found_text/found_text fiable "
        f"(voir la section « Registre déclaratif » dans app/osint/username_checker.py), "
        f"ou désactiver la plateforme (\"reliable\": False).{RESET}"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
