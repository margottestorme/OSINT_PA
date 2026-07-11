"""
Indexation d'un dossier local de sujets CONSENTANTS — reconnaissance faciale (démo).

Généralise la logique de scripts/seed_lfw_dataset.py à un dossier d'images fourni par
l'utilisateur (pas de téléchargement, pas de web ouvert). Chaque personne est ajoutée avec
`source="consented_demo"` (voir app/db/models.py), une TROISIÈME origine distincte de :

  - "investigation" : vraie personne sujette d'une enquête (traitement OSINT complet) ;
  - "lfw_dataset"   : célébrité du jeu académique figé (scripts/seed_lfw_dataset.py).

Différence clé : `consented_demo` désigne de vraies personnes (toi, tes camarades) qui ont
donné leur consentement explicite pour la démonstration. Elles doivent donc rester isolées
des deux autres environnements :
  - jamais d'enrichissement OSINT automatique (face_routes.py ne l'active que pour
    source=="investigation") — un sujet de démo n'est pas une cible d'enquête ;
  - badge visuel distinct en frontend (« ✓ Sujet consentant »).

Consentement (RGPD art. 9, données biométriques) : chaque personne indexée reçoit une trace
de consentement horodatée dans le champ `notes` (chiffré au repos, cf. Module 12). Fournir
`--consent-ref` pour rattacher une référence au formulaire papier/signé conservé hors ligne.

Aucune image n'est persistée : seul l'embedding 128D est stocké (comme partout ailleurs sur
la plateforme, cf. CLAUDE.md section RGPD). Idempotent (anti-doublon par image_hash).

Structure attendue du dossier :

    photos_demo/
        Alice_Martin/
            img1.jpg
            img2.jpg
        Bob_Durand/
            portrait.png

Le nom du sous-dossier donne l'identité (dernier mot = nom de famille), comme pour LFW.

Usage :
  python scripts/index_local_faces.py --dir ./photos_demo
  python scripts/index_local_faces.py --dir ./photos_demo --images-per-person 3
  python scripts/index_local_faces.py --dir ./photos_demo --consent-ref "formulaires-demo-2026"
"""
import argparse
import asyncio
import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from app.db.models import Person  # noqa: E402
from app.db.session import async_session  # noqa: E402
from app.facial.engine import extract_embedding  # noqa: E402
from app.facial.exceptions import FacialError  # noqa: E402

GREEN, CYAN, YELLOW, RED, BOLD, DIM, RESET = (
    "\033[92m", "\033[96m", "\033[93m", "\033[91m", "\033[1m", "\033[2m", "\033[0m"
)

DEMO_SOURCE = "consented_demo"
_IMAGE_EXTS = (".jpg", ".jpeg", ".png")


@dataclass
class IndexStats:
    """Compteurs retournés par index_directory (facilite l'assertion en test)."""
    added: int = 0            # embeddings ajoutés
    persons: int = 0          # personnes distinctes ayant au moins un embedding ajouté
    skipped_dup: int = 0      # images déjà en base (même hash)
    skipped_noface: int = 0   # images sans visage détectable


def _iter_person_images(root_dir: str):
    """Yields (nom_dossier, [chemins d'images]) — tri alphabétique pour reproductibilité."""
    for name in sorted(os.listdir(root_dir)):
        person_dir = os.path.join(root_dir, name)
        if not os.path.isdir(person_dir):
            continue
        images = sorted(
            os.path.join(person_dir, f)
            for f in os.listdir(person_dir)
            if f.lower().endswith(_IMAGE_EXTS)
        )
        if images:
            yield name, images


def _parse_name(folder_name: str) -> tuple[str, str]:
    """"Alice_Martin" -> ("Alice", "Martin") : le dernier mot est pris comme nom de famille."""
    parts = folder_name.replace("_", " ").split()
    if len(parts) == 1:
        return parts[0], "?"
    return " ".join(parts[:-1]), parts[-1]


def _consent_note(consent_ref: str | None) -> str:
    """Trace de consentement horodatée, stockée chiffrée dans Person.notes."""
    note = f"Consentement démo enregistré le {date.today().isoformat()} (source={DEMO_SOURCE})."
    if consent_ref:
        note += f" Référence formulaire : {consent_ref}."
    return note


async def index_directory(
    root_dir: str,
    *,
    consent_ref: str | None = None,
    images_per_person: int = 3,
    session_factory=async_session,
    progress=None,
) -> IndexStats:
    """Indexe les visages consentants d'un dossier local. get-or-create par image_hash.

    Args:
        root_dir: dossier racine (un sous-dossier `Prenom_Nom` par personne).
        consent_ref: référence optionnelle vers le formulaire de consentement signé.
        images_per_person: nombre max d'images traitées par personne (matching plus robuste).
        session_factory: fabrique de session AsyncSession (injectable pour les tests).
        progress: callback optionnel progress(i, total) pour l'affichage CLI.

    Returns:
        IndexStats (added, persons, skipped_dup, skipped_noface).
    """
    stats = IndexStats()
    candidates = list(_iter_person_images(root_dir))
    note = _consent_note(consent_ref)

    async with session_factory() as db:
        for i, (folder, images) in enumerate(candidates):
            first, last = _parse_name(folder)
            person_had_row = False
            for image_path in images[:images_per_person]:
                with open(image_path, "rb") as f:
                    image_bytes = f.read()
                image_hash = hashlib.sha256(image_bytes).hexdigest()

                existing = (await db.execute(
                    select(Person.id).where(Person.image_hash == image_hash)
                )).scalar_one_or_none()
                if existing:
                    stats.skipped_dup += 1
                    continue

                try:
                    embedding = extract_embedding(image_bytes)
                except FacialError:
                    stats.skipped_noface += 1
                    continue
                finally:
                    del image_bytes  # jamais conservé au-delà de ce traitement en mémoire

                db.add(Person(
                    first_name=first, last_name=last,
                    confidence_score=1.0, embedding=embedding,
                    image_hash=image_hash, source=DEMO_SOURCE,
                    notes=note,
                ))
                stats.added += 1
                person_had_row = True
            if person_had_row:
                stats.persons += 1
            if progress:
                progress(i + 1, len(candidates))
        await db.commit()

    return stats


def _draw_bar(current: int, total: int, width: int = 30, prefix: str = "") -> None:
    """Barre de progression réécrite en place (retour chariot)."""
    frac = min(current / total, 1.0) if total else 1.0
    bar = "█" * int(width * frac) + "░" * (width - int(width * frac))
    sys.stdout.write(f"\r  {prefix}[{CYAN}{bar}{RESET}] {int(frac * 100):3d}%")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Indexe un dossier local de sujets consentants (reconnaissance faciale, démo)."
    )
    parser.add_argument("--dir", required=True,
                        help="Dossier racine : un sous-dossier `Prenom_Nom` par personne.")
    parser.add_argument("--images-per-person", type=int, default=3,
                        help="Images max traitées par personne (défaut 3, matching plus robuste).")
    parser.add_argument("--consent-ref", type=str, default=None,
                        help="Référence du formulaire de consentement signé (stockée chiffrée).")
    args = parser.parse_args()

    if not os.path.isdir(args.dir):
        print(f"{RED}Dossier introuvable : {args.dir}{RESET}")
        sys.exit(1)

    print(f"{BOLD}{CYAN}Indexation de sujets consentants — reconnaissance faciale (démo){RESET}")
    print(f"{DIM}Vraies personnes ayant donné leur consentement explicite (RGPD art. 9). "
          f"source={DEMO_SOURCE} : isolé des enquêtes réelles et du jeu LFW, aucun "
          f"enrichissement OSINT automatique. Aucune image conservée (embedding 128D "
          f"seulement). Consentement horodaté dans les notes chiffrées.{RESET}\n")

    stats = asyncio.run(index_directory(
        args.dir,
        consent_ref=args.consent_ref,
        images_per_person=args.images_per_person,
        progress=lambda c, t: _draw_bar(c, t, prefix="Traitement "),
    ))

    if stats.added == 0 and stats.skipped_dup == 0 and stats.skipped_noface == 0:
        print(f"{YELLOW}Aucune image trouvée. Vérifie la structure : "
              f"{args.dir}/Prenom_Nom/photo.jpg{RESET}")
        return

    print(
        f"\n{GREEN}{BOLD}Terminé{RESET} — {stats.persons} personne(s) / {stats.added} "
        f"embedding(s) ajouté(s) (source={DEMO_SOURCE}), {stats.skipped_dup} doublon(s) "
        f"ignoré(s), {stats.skipped_noface} image(s) sans visage détectable ignorée(s)."
    )


if __name__ == "__main__":
    main()
