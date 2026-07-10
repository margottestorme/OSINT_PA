"""
Seed du jeu de données académique LFW (Labeled Faces in the Wild) — reconnaissance faciale.

Ajoute des personnes labellisées `source="lfw_dataset"` (voir app/db/models.py) avec leur
embedding facial, pour démontrer un matching contre une base élargie SANS jamais scraper ou
identifier de vraie personne non consentante en temps réel :

- LFW est un jeu de données de RECHERCHE reconnu (Huang, Ramesh, Berg, Learned-Miller —
  University of Massachusetts Amherst, Technical Report 07-49, 2007), utilisé comme benchmark
  standard dans des milliers de publications académiques et jusque dans scikit-learn
  (`sklearn.datasets.fetch_lfw_people`) — ce n'est PAS un outil de reconnaissance faciale en
  direct sur des inconnus, contrairement à un service type Clearview AI/PimEyes qui scrape le
  web en continu pour identifier n'importe qui à partir d'une photo (jugé illégal par la CNIL
  et plusieurs autorités européennes). Ici, les images sont figées, collectées une fois pour
  toutes par les auteurs originaux (photos de presse de personnalités déjà publiques), pas
  aspirées en direct depuis les réseaux sociaux de vraies personnes ciblées.
- Citation requise en cas de publication/rapport : voir référence ci-dessus.
- Idempotent (anti-doublon par image_hash, comme pour les personnes d'enquête réelles).
- Aucune image n'est persistée au-delà de l'archive temporaire de traitement (supprimée en
  fin de script) — seul l'embedding 128D est stocké en base, cohérent avec le reste de la
  plateforme (voir CLAUDE.md, section RGPD).

Usage :
  python scripts/seed_lfw_dataset.py                                  # 50 personnes, 1 image
  python scripts/seed_lfw_dataset.py --limit 200 --images-per-person 2
  python scripts/seed_lfw_dataset.py --tarball /chemin/lfw-deepfunneled.tgz  # déjà téléchargé
"""
import argparse
import asyncio
import hashlib
import os
import random
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.db.session import async_session  # noqa: E402
from app.db.models import Person  # noqa: E402
from app.facial.engine import extract_embedding  # noqa: E402
from app.facial.exceptions import FacialError  # noqa: E402

GREEN, CYAN, YELLOW, RED, BOLD, DIM, RESET = (
    "\033[92m", "\033[96m", "\033[93m", "\033[91m", "\033[1m", "\033[2m", "\033[0m"
)

# URL canonique (parfois injoignable selon l'environnement réseau — le serveur UMass est
# ancien et instable) + mirror archive.org vérifié fonctionnel le 2026-07-07 en secours.
PRIMARY_URL = "https://vis-www.cs.umass.edu/lfw/lfw-deepfunneled.tgz"
FALLBACK_URL = "https://web.archive.org/web/2023/https://vis-www.cs.umass.edu/lfw/lfw-deepfunneled.tgz"


def _draw_bar(current: int, total: int, width: int = 30, prefix: str = "") -> None:
    """Barre de progression réécrite en place (retour chariot)."""
    frac = current / total if total else 1.0
    frac = min(frac, 1.0)
    bar = "█" * int(width * frac) + "░" * (width - int(width * frac))
    sys.stdout.write(f"\r  {prefix}[{CYAN}{bar}{RESET}] {int(frac * 100):3d}%")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


def _download(dest_path: str, urls: list[str]) -> bool:
    """Tente chaque URL dans l'ordre ; renvoie True dès qu'une réussit."""
    for url in urls:
        try:
            with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=256 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            _draw_bar(downloaded, total, prefix="Téléchargement ")
            return True
        except Exception as e:
            print(f"{YELLOW}Échec depuis {url} : {e}{RESET}")
    return False


def _iter_person_images(root_dir: str):
    """Yields (nom_dossier, [chemins d'images]) — tri alphabétique pour reproductibilité."""
    for name in sorted(os.listdir(root_dir)):
        person_dir = os.path.join(root_dir, name)
        if not os.path.isdir(person_dir):
            continue
        images = sorted(
            os.path.join(person_dir, f)
            for f in os.listdir(person_dir)
            if f.lower().endswith((".jpg", ".jpeg"))
        )
        if images:
            yield name, images


def _parse_name(folder_name: str) -> tuple[str, str]:
    """"George_W_Bush" -> ("George W", "Bush") : le dernier mot est pris comme nom de famille."""
    parts = folder_name.replace("_", " ").split()
    if len(parts) == 1:
        return parts[0], "?"
    return " ".join(parts[:-1]), parts[-1]


def _matches_requested_name(folder: str, requested: set[str]) -> bool:
    return folder.replace("_", " ").strip().lower() in requested


async def _seed(
    root_dir: str, limit: int, images_per_person: int,
    names: list[str] | None = None, top_known: bool = False,
) -> tuple[int, int, int]:
    """Ajoute jusqu'à `limit` personnes (get-or-create par image_hash).

    Trois modes de sélection, mutuellement exclusifs :
    - `names` : uniquement les personnes listées (ex : "George W Bush", "Michael Jackson").
    - `top_known=True` : les `limit` personnes avec le PLUS de photos dans LFW. LFW étant
      construit à partir de photos de presse (Yahoo News, ~2002-2007), le nombre de photos
      d'une personne reflète directement sa notoriété média de l'époque (George W. Bush,
      alors président, culmine à 530 photos) — un proxy objectif et vérifiable pour « les
      plus connus », contrairement à une liste de noms devinée à la main (risque de citer
      quelqu'un d'absent du dataset, comme Barack Obama — trop peu présent en 2007).
    - ni l'un ni l'autre : sélection aléatoire (seed fixe, reproductible).

    Renvoie (ajoutées, doublons_ignorés, sans_visage_ignorées)."""
    added = skipped_dup = skipped_noface = 0
    candidates = list(_iter_person_images(root_dir))

    if names:
        requested = {n.replace("_", " ").strip().lower() for n in names}
        candidates = [c for c in candidates if _matches_requested_name(c[0], requested)]
        found_names = {c[0].replace("_", " ").lower() for c in candidates}
        for n in requested - found_names:
            print(f"{YELLOW}Introuvable dans LFW : {n!r} (nom de dossier exact requis, "
                  f"ex: \"George W Bush\"){RESET}")
        limit = len(candidates)  # seed toutes les personnes demandées trouvées, pas de troncature
    elif top_known:
        # Tri par nombre de photos décroissant : les personnes les plus médiatisées d'abord.
        candidates.sort(key=lambda c: len(c[1]), reverse=True)
    else:
        # Mélange déterministe (seed fixe) : évite le biais des noms les plus fréquents en tête
        # d'alphabet (LFW n'est pas trié aléatoirement à la base).
        random.Random(42).shuffle(candidates)

    # `limit` compte des PERSONNES (dossiers), jamais des lignes/images — avec
    # images_per_person > 1, compter les lignes ferait sortir de la boucle bien avant d'avoir
    # traité toutes les personnes demandées (bug constaté : Michael Jackson jamais atteint
    # après les 3 images de George W Bush, alors que la limite comptait 2 personnes demandées).
    people_added = 0
    async with async_session() as db:
        for i, (folder, images) in enumerate(candidates):
            if people_added >= limit:
                break
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
                    skipped_dup += 1
                    continue

                try:
                    embedding = extract_embedding(image_bytes)
                except FacialError:
                    skipped_noface += 1
                    continue
                finally:
                    del image_bytes  # jamais conservé au-delà de ce traitement en mémoire

                db.add(Person(
                    first_name=first, last_name=last,
                    confidence_score=1.0, embedding=embedding,
                    image_hash=image_hash, source="lfw_dataset",
                ))
                added += 1
                person_had_row = True
            if person_had_row:
                people_added += 1
            _draw_bar(i + 1, len(candidates), prefix="Traitement      ")
        await db.commit()
    return added, skipped_dup, skipped_noface


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed du jeu de données académique LFW")
    parser.add_argument("--limit", type=int, default=50,
                         help="Nombre de personnes à ajouter (défaut 50)")
    parser.add_argument("--images-per-person", type=int, default=1,
                         help="Images par personne, pour un matching plus robuste (défaut 1)")
    parser.add_argument("--tarball", type=str, default=None,
                         help="Archive lfw-deepfunneled.tgz déjà téléchargée (évite le téléchargement)")
    parser.add_argument("--names", type=str, nargs="+", default=None,
                         help='Ne seeder que ces personnes précises (nom exact du dossier LFW, '
                              'espaces ou underscores acceptés) — ex: --names "George W Bush" '
                              '"Michael Jackson". Ignore --limit.')
    parser.add_argument("--top-known", action="store_true",
                         help="Sélectionne les `--limit` personnes avec le plus de photos dans "
                              "LFW (proxy objectif de notoriété média ~2002-2007) au lieu d'une "
                              "sélection aléatoire. Ignoré si --names est fourni.")
    args = parser.parse_args()

    if args.names and args.top_known:
        print(f"{RED}--names et --top-known sont mutuellement exclusifs.{RESET}")
        sys.exit(1)

    print(f"{BOLD}{CYAN}Seed LFW (Labeled Faces in the Wild) — reconnaissance faciale{RESET}")
    print(f"{DIM}Jeu de données académique public (personnalités déjà médiatisées, photos de "
          f"presse figées) — utilisé uniquement pour démontrer un matching contre une base "
          f"élargie, jamais pour identifier un inconnu en direct. Citation requise en cas de "
          f"publication : Huang et al., UMass Amherst TR 07-49, 2007.{RESET}\n")

    with tempfile.TemporaryDirectory(prefix="lfw_seed_") as tmp:
        tarball_path = args.tarball
        if not tarball_path:
            tarball_path = os.path.join(tmp, "lfw-deepfunneled.tgz")
            if not _download(tarball_path, [PRIMARY_URL, FALLBACK_URL]):
                print(f"\n{RED}Échec du téléchargement depuis toutes les sources.{RESET}")
                print(f"{YELLOW}Téléchargez manuellement lfw-deepfunneled.tgz "
                      f"(ex: {FALLBACK_URL}) et relancez avec --tarball <chemin>.{RESET}")
                sys.exit(1)

        print("Extraction...")
        extract_dir = os.path.join(tmp, "extracted")
        with tarfile.open(tarball_path) as tar:
            tar.extractall(extract_dir, filter="data")  # filter="data" : contre le path traversal

        root = next(
            os.path.join(extract_dir, d) for d in os.listdir(extract_dir)
            if os.path.isdir(os.path.join(extract_dir, d))
        )

        added, skipped_dup, skipped_noface = asyncio.run(
            _seed(root, args.limit, args.images_per_person, names=args.names, top_known=args.top_known)
        )
        # `tmp` (tarball + images extraites) est supprimé ici, en sortie de bloc `with`.

    print(
        f"\n{GREEN}{BOLD}Terminé{RESET} — {added} personne(s) ajoutée(s) (source=lfw_dataset), "
        f"{skipped_dup} doublon(s) ignoré(s), {skipped_noface} image(s) sans visage détectable "
        f"ignorée(s)."
    )


if __name__ == "__main__":
    main()
