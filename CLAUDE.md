# CLAUDE.md

Ce fichier guide Claude Code (et tout contributeur) dans ce dépôt.

## Vue d'ensemble du projet

**OSINT Platform** — projet annuel ESGI (cybersécurité, 4e année, 2025-2026), développé par
STORME Margotte, SIVATHASAN Rakulan et AZOUZ Idir.

Le [cahier des charges](Cahier_des_charges_OSINT_Reconnaissance_Faciale.pdf) initial demande une
plateforme **interne** capable de : capturer un flux webcam → détecter/reconnaître des visages →
comparer à une base d'embeddings → lancer des recherches OSINT sur les entités trouvées →
corréler itérativement (email → site → organisation → domaine → nouveaux emails) → centraliser
le tout dans une base sécurisée, avec interface d'administration.

**Le projet livré réinterprète volontairement ce CDC dans un cadre académique** (voir
[README.md](README.md), section « Mentions légales ») : au lieu d'une infrastructure interne
isolée « sans flux public », c'est une plateforme qui n'utilise **que des sources publiques et
légales** (DuckDuckGo, WHOIS, DNS, crt.sh, RDAP, HIBP, scraping respectueux du robots.txt...) et
peut tourner sur n'importe quelle machine. Ce choix est assumé et documenté dans le code — voir
la section « Écarts CDC ↔ implémentation » plus bas avant de proposer un changement qui romprait
cette cohérence.

Le CDC est correctement respecté sur le fond : reconnaissance faciale (embeddings 128D, distance
euclidienne, seuil configurable), corrélation multi-niveaux, PostgreSQL + Elasticsearch, JWT +
RBAC, chiffrement au repos, RGPD/article 9. 13 « modules » fonctionnels ont été livrés
successivement (voir le détail dans le [README.md](README.md), qui documente chacun en
profondeur — c'est la meilleure source pour comprendre le *pourquoi* de chaque décision).

## Stack technique et dépendances clés

| Domaine | Techno | Notes |
|---|---|---|
| Backend | FastAPI 0.111 + uvicorn (async) | point d'entrée [app/main.py](app/main.py) |
| ORM / DB | SQLAlchemy 2.0 (async, `Mapped[...]`) + PostgreSQL 15 | SQLite (aiosqlite) en tests |
| Migrations | Alembic (`alembic/versions/`, 13 révisions) | jamais de `create_all` en prod |
| Auth | python-jose (JWT HS256, 15-60 min) + passlib/bcrypt | rôles `admin` / `analyst` |
| NLP | spaCy `fr_core_news_sm` | extraction emails/domaines/noms |
| Reconnaissance faciale | face_recognition + dlib + Pillow | embedding 128D, aucune image persistée |
| Webcam temps réel | OpenCV (côté serveur, informatif) + `getUserMedia` (côté navigateur, réel) | voir écart CDC plus bas |
| Scraping | BeautifulSoup4 + lxml | robots.txt respecté, blacklist réseaux sociaux |
| Recherche full-text | Elasticsearch 8.11 (async) | **optionnelle**, dégradation propre partout |
| Cache | Redis 7 | présent, pas encore utilisé pour des tâches async |
| Chiffrement | pgcrypto (PostgreSQL) via `EncryptedText` | [app/db/crypto.py](app/db/crypto.py) |
| HTTP externe | httpx (async) | remplace `requests` du CDC (contrainte async) |
| Frontend | HTML/CSS/JS vanilla, vis.js pour les graphes | aucun framework JS, servi par FastAPI/Jinja2 |
| Tests | pytest + pytest-asyncio + pytest-cov | 393 tests (+8 pgcrypto sur PostgreSQL) |
| Conteneurs | Docker Compose (app + db + redis + elasticsearch) | |

**Absent de `requirements.txt` malgré le CDC** : `scikit-learn` (le matching facial n'utilise que
la distance euclidienne native de `face_recognition`, pas de cosine similarity ni de ML), et
`numpy` n'est déclaré nulle part explicitement (dépendance transitive de dlib/opencv).

## Structure du projet

```
app/
├── main.py                # FastAPI app : middlewares, routes frontend, lifespan (init ES)
├── cli.py                 # CLI de gestion des utilisateurs (create-user)
├── api/
│   ├── routes.py          # Auth, recherche OSINT, persons/report, historique, fulltext
│   ├── face_routes.py     # Upload image → détection/matching facial
│   ├── stream_routes.py   # POST /api/stream/analyze (webcam côté client)
│   ├── middleware.py      # SecurityHeaders, RequestLogging, GlobalRateLimit
│   └── rate_limit.py      # Rate limiter par utilisateur (recherche OSINT)
├── core/
│   ├── config.py          # Settings (pydantic-settings) + validate_security() (garde-fou JWT prod)
│   ├── security.py        # JWT + bcrypt + auth DB + require_role()
│   ├── audit.py           # create_audit_log() + résolution IP/user_id
│   └── logging.py         # Config loguru
├── db/
│   ├── models.py          # 7 tables : users, persons, emails, sites, correlations,
│   │                        search_history, search_results, audit_logs
│   ├── crypto.py           # EncryptedText (TypeDecorator dialect-aware pg/sqlite)
│   └── session.py          # Engine async + session factory
├── facial/
│   ├── engine.py           # Détection, embedding 128D, matching euclidien
│   ├── stream.py            # locate_and_encode (multi-visages, une passe)
│   └── exceptions.py        # NoFace, MultipleFaces, InvalidImage...
├── osint/
│   ├── engine.py            # Moteur OSINT : registre déclaratif SOURCES (10 sources)
│   ├── correlator.py        # Enrichissement itératif facial→OSINT (3 niveaux, dédup par sets)
│   ├── scraper.py           # fetch_page/scrape_entities/scrape_batch (robots.txt, blacklist)
│   └── rate_limiter.py      # Rate limiter externe (2s min entre requêtes)
├── search/
│   ├── elastic.py           # Client ES + 4 index à mapping `dynamic: strict`
│   ├── indexer.py           # index_*() + bulk_reindex (upsert idempotent, BackgroundTask)
│   └── fulltext.py          # Recherche multi-index + highlight
└── schemas/schemas.py        # Schémas Pydantic (validation I/O, enums query_type)

alembic/versions/             # 8 migrations, dont f1a2b3c4d5e6 (chiffrement + backfill hash)
frontend/                     # 7 pages HTML vanilla + static/{css,js}
  ├── index.html (login) · dashboard.html · results.html · search.html
  └── graph.html (vis.js) · report.html · stream.html
tests/                        # 20 fichiers, 393 tests (miroir de la structure app/)
scripts/
  ├── reindex.py              # Réindexation PostgreSQL → Elasticsearch (barre de progression)
  ├── seed_demo.py             # Jeu de données fictif idempotent pour démo
  └── reset_demo.py            # Reset des données de démo
docker-compose.yml             # app + db + redis + elasticsearch (dev)
docker-compose.prod.yml        # variante production
.env.example / .env.production.example
AUDIT.md                       # Rapport d'audit sécurité/qualité (Modules 1-5, score 6.4→8.8/10)
```

## Conventions de code à respecter

- **Français** pour tous les commentaires, docstrings, messages de log et d'erreur API.
  Identifiants (variables, fonctions, classes) en anglais.
- **Commentaires uniquement sur le "pourquoi"**, jamais sur le "quoi" : ex. `hash_email()` dans
  [app/db/models.py](app/db/models.py) explique *pourquoi* un hash SHA256 est nécessaire
  (chiffrement pgcrypto non déterministe), pas ce que fait le hachage.
- **Typage moderne SQLAlchemy 2.0** : `Mapped[T]` / `mapped_column()` partout, jamais l'ancien
  style déclaratif.
- **Registre déclaratif plutôt que if/elif** : les sources OSINT s'ajoutent au dict `SOURCES`
  dans [app/osint/engine.py](app/osint/engine.py) — ne jamais ajouter de branche conditionnelle
  pour une nouvelle source, l'enregistrer dans le registre (avec une `lambda` si la fonction a des
  paramètres supplémentaires, cf. correction Module 8 sur `_source_scraper`).
- **Enveloppe `OsintResult` normalisée** : toute nouvelle source doit retourner
  `{source, entity, type, data, extracted: {emails, domains, urls, ips, names}}` — c'est ce
  format que l'enrichissement (correlator.py) et l'indexation (Elasticsearch) consomment.
- **Dégradation propre systématique** : toute dépendance externe (source OSINT, Elasticsearch,
  scraping) doit échouer silencieusement (`None` + log `debug`/`warning`), **jamais** remonter une
  exception qui casse une route. Voir `elastic.py`, `engine.py`.
- **Rate limiting partout où on sort du process** : 2s min entre requêtes externes OSINT
  (`osint/rate_limiter.py`), 10 req/min recherche utilisateur, 2 req/s stream webcam, 100 req/min
  global par IP, 5 tentatives/min sur `/login`.
- **Chiffrement transparent pour l'ORM** : les colonnes sensibles utilisent `EncryptedText`
  ([app/db/crypto.py](app/db/crypto.py)) — on lit/écrit du texte clair en Python, jamais de
  `pgp_sym_encrypt`/`decrypt` manuel dans les routes. Ne jamais logger ou exposer
  `DB_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `hashed_password`, ni l'`embedding` facial brut.
  La recherche/dédup sur colonnes chiffrées passe **toujours** par le hash SHA256 associé
  (`email_hash`), jamais par une requête `WHERE email = ...` sur le texte chiffré.
- **RBAC explicite** : `require_role("admin")` sur les endpoints sensibles ; ne pas se fier au
  rôle porté dans le JWT sans revalidation via `require_role`.
- **Aucune donnée biométrique en clair côté ES** : les mappings Elasticsearch sont `dynamic:
  strict` — n'indexer jamais l'embedding ni le hash d'image, uniquement des métadonnées publiques.
- **Frontend vanilla, pas de build step** : pas de bundler/framework JS à introduire. JWT gardé
  **en mémoire** côté client (jamais `localStorage`). Toute donnée externe affichée doit passer
  par `escapeHtml` (protection XSS).
- **Tests miroir de `app/`** : un fichier `tests/test_<module>.py` par module ; les appels
  externes (réseau, tâches de fond) sont mockés dans `conftest.py`, jamais réellement exécutés en
  CI.

## Commandes utiles

```bash
# Setup local
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m spacy download fr_core_news_sm
cp .env.example .env
alembic upgrade head

# Lancer l'app
uvicorn app.main:app --reload                       # http://localhost:8000
docker compose up -d                                # stack complète (app+db+redis+es)
docker compose up -d elasticsearch                  # ES seul (optionnel)

# Tests
python -m pytest tests/ -v                          # suite complète (SQLite)
python -m pytest tests/ -v --cov=app                 # avec couverture
python -m pytest tests/test_<module>.py -v           # un module ciblé
TEST_DATABASE_URL=postgresql+asyncpg://osint_user:<pwd>@localhost:5432/osint_test \
  python -m pytest tests/test_crypto.py -v           # 8 tests pgcrypto (PostgreSQL requis)

# CLI utilisateurs
python -m app.cli create-user --username admin --role admin

# Scripts
python scripts/reindex.py                            # réindexation PostgreSQL → Elasticsearch
python scripts/seed_demo.py                           # jeu de données de démo (idempotent)
python scripts/reset_demo.py                           # reset des données de démo
python scripts/audit_username_platforms.py            # audit fiabilité registre pseudos (Module 15)
python scripts/seed_lfw_dataset.py --limit 50          # jeu de données académique LFW (facial)
python scripts/index_local_faces.py --dir ./photos_demo # indexe des sujets consentants (démo facial)

# Migrations
alembic revision -m "message" --autogenerate
alembic upgrade head
alembic downgrade -1
```

Pas de linter/formatter configuré (pas de `ruff`/`black`/`pyflakes` dans `requirements.txt` ni de
config dédiée) — l'AUDIT.md recommande d'en ajouter en CI mais ce n'est pas encore fait.

## Points d'attention / contraintes spécifiques (CDC)

### Écarts CDC ↔ implémentation à connaître avant de modifier le code

| # | CDC demande | Implémentation réelle | À faire si on touche ce point |
|---|---|---|---|
| 1 | Infrastructure interne isolée, « aucun flux public » | Sources 100 % publiques/légales, déployable n'importe où (cadre académique assumé, cf. README « Mentions légales ») | Ne pas « corriger » vers du scraping agressif ou des sources privées sans en discuter — c'est un choix délibéré, pas un oubli |
| 2 | Capture webcam serveur (OpenCV) | Capture **côté navigateur** (`getUserMedia`), le serveur ne fait qu'analyser les frames reçues via `POST /api/stream/analyze` | OpenCV reste présent côté serveur pour `/api/stream/info` (informatif) — ne pas réintroduire une capture serveur sans raison |
| 3 | Recherche Google/Bing | DuckDuckGo Instant Answer API + 9 autres sources publiques sans clé (Gravatar, crt.sh, RDAP, Shodan InternetDB, Wikipedia, GitHub...) + 3 sources freemium à clé optionnelle | Ajouter une source suit le pattern `SOURCES` registry, jamais de scraping direct de Google/Bing (ToS) |
| 4 | Chaîne organisation intermédiaire (email→site→**organisation**→domaine) | Pas de table `organisations` — uniquement persons/emails/sites/correlations | Si demandé explicitement : nouvelle table + migration Alembic, pas de champ JSON improvisé |
| 5 | `scikit-learn`, cosine similarity | Distance euclidienne uniquement (dlib via `face_recognition`) | N'ajouter `scikit-learn` que si une vraie fonctionnalité ML l'exige — pas de dépendance inutilisée |
| 6 | Perf : détection <200ms, comparaison <100ms, corrélation <1s | Mesuré (`processing_ms` dans la réponse stream) mais **aucun test de perf formalisé** | Ajouter des tests de perf si le CDC est audité sur ce point |
| 7 | Sauvegardes chiffrées quotidiennes (GPG) | `.env.production.example` référence `BACKUP_GPG_RECIPIENT` et un `scripts/backup.sh` **qui n'existe pas** | Roadmap — voir plus bas |
| 8 | Serveur 32 Go RAM / GPU CUDA / Ubuntu 22.04 | Non contraint dans le code (Docker Compose générique, pas de config CUDA) | Non pertinent pour le code applicatif, à traiter au niveau infra de déploiement réel |

### Contraintes légales/sécurité à ne jamais régresser

- **RGPD article 9 (données biométriques)** : aucune image persistée sur disque, seul
  l'embedding 128D est stocké, suppression immédiate des bytes après traitement (upload et
  stream). Ne jamais ajouter de sauvegarde d'image, même temporaire sur disque.
- **Anti-doublon par hash SHA256** (`image_hash` sur `persons`) — préserver l'index unique
  partiel (`sqlite_where`/`postgresql_where`) lors de toute migration touchant cette colonne.
- **k-anonymity HIBP** : l'email complet ne doit **jamais** être envoyé à l'API HIBP — seuls les
  5 premiers caractères du SHA1 partent en clair (`_source_hibp`). Ne pas « simplifier » cet appel.
- **Aucun mot de passe hardcodé** — auth exclusivement via la table `users` (bcrypt), CLI
  `app/cli.py` pour la création de comptes.
- **Garde-fou de démarrage** (`validate_security()` dans [app/core/config.py](app/core/config.py))
  refuse de démarrer en production (`DEBUG=false`) avec un `JWT_SECRET_KEY` par défaut — ne pas
  contourner ce comportement, l'étendre plutôt (ex. à `DB_ENCRYPTION_KEY`) si on durcit encore.
- **Ordre des middlewares** dans [app/main.py](app/main.py) est significatif (`SecurityHeaders` le
  plus externe pour couvrir même les 429) — ne pas réordonner sans relire le commentaire associé.

### Reconnaissance faciale : refus délibéré de la recherche inversée sur bases publiques

Une demande de faire matcher tout visage envoyé contre des « bases de données publiques et
open-source avec recherche inversée » (2026-07-07) a été **refusée** : ça décrit précisément le
fonctionnement de Clearview AI (scraping continu du web pour identifier n'importe quel inconnu à
partir d'une photo), jugé illégal et sanctionné par la CNIL (20M€) et plusieurs autorités
européennes — indépendamment du cadre académique du projet, puisque ça viserait de vraies
personnes non consentantes. Ne pas réintroduire cette fonctionnalité sans en rediscuter
explicitement.

À la place : `Person.source` (`investigation` par défaut / `lfw_dataset`) distingue les vraies
personnes ajoutées via la plateforme des entrées issues du jeu de données académique **Labeled
Faces in the Wild** (Huang et al., UMass Amherst TR 07-49, 2007 — benchmark standard utilisé
jusque dans `sklearn.datasets.fetch_lfw_people`), seedées via
[scripts/seed_lfw_dataset.py](scripts/seed_lfw_dataset.py). Différence clé avec un reverse-search
public : les photos LFW sont un jeu figé collecté une fois par les auteurs originaux (pas un
scraping continu de cibles choisies), et servent uniquement à démontrer un matching contre une
base élargie. Toute réponse API/UI impliquant une entrée `lfw_dataset` doit rester visuellement
distincte d'un vrai résultat d'enquête (badge ambre `.lfw-badge` en frontend, colonne « Origine »
dans le rapport PDF forensique, pas d'enrichissement OSINT automatique déclenché) — ne jamais
fusionner les deux dans l'UI ou supprimer cette distinction pour « simplifier ».

**Troisième origine — `consented_demo` (2026-07-10)** : `Person.source` prend une 3e valeur pour
les vraies personnes ayant donné leur **consentement explicite** en vue d'une démonstration en
direct (l'équipe filmée devant la webcam pour le jury). Elle se distingue de `investigation`
(sujet d'enquête réel, traitement OSINT complet) ET de `lfw_dataset` (célébrités du jeu figé,
jamais filmées en direct). Indexées via
[scripts/index_local_faces.py](scripts/index_local_faces.py) `--dir <dossier>` (dossiers locaux
fournis par l'utilisateur, **aucun** web ouvert, aucune image persistée — embedding 128D
seulement). Garde-fous à ne pas régresser : (1) trace de consentement horodatée dans
`Person.notes` (chiffré au repos) ; (2) badge violet `.consent-badge` (« ✓ Sujet consentant »)
distinct du vert « enquête »/ambre « LFW »/rouge « inconnu », en overlay webcam comme dans les
listes ; (3) **jamais d'enrichissement OSINT automatique** — `face_routes.py` ne le déclenche que
pour `source == "investigation"`, un sujet de démo n'est pas une cible d'enquête. Ne pas réutiliser
`investigation` pour ces sujets (mélangerait démo et enquêtes réelles et réactiverait
l'enrichissement auto).

### Pipeline d'enrichissement OSINT post-identification faciale (2026-07-11)

[app/osint/pipeline.py](app/osint/pipeline.py) — `full_enrich_after_face_match(person_id, db,
job_id)` enchaîne, à partir du NOM d'un sujet identifié : recherche par nom → emails extraits →
domaines → HIBP → énumération de pseudos (`prenom.nom`). Comble le trou de `enrich_person()`
(correlator.py), qui ne pivote que sur des emails déjà en base — inutile pour une personne
fraîchement reconnue. Réutilise l'existant (`correlate`, `enrich_person`, `check_all_platforms`),
aucune source nouvelle.

**Garde-fou à ne jamais régresser** : le pipeline ne s'exécute QUE pour `source ==
"investigation"` (refuse `consented_demo`/`lfw_dataset`) et une personne nommée (pas « Inconnu »).
Ce contrôle est **doublé** aux points d'appel : `POST /api/persons/{id}/enrich` et le déclencheur
d'upload dans `face_routes.py` re-vérifient l'origine avant de lancer la tâche. Décision
explicite (2026-07-11, confirmée) : **jamais d'auto-déclenchement depuis le flux webcam**
(`/api/stream/analyze`) — le flux affiche un bouton « Enrichir » cliquable, pas un profilage
automatique déclenché par la simple présence d'un visage devant la caméra (motif Clearview
refusé). Ne pas ajouter d'auto-fire sur le stream sans en rediscuter.

**Suivi (table `enrichment_jobs`, migration e5f6a7b8c9d0)** : chaque enrichissement crée un job
(`pending` créé côté requête → `running` → `completed`/`failed`), lu par
`GET /api/persons/{id}/enrichment_status` (dernier job) pour le polling frontend (dashboard,
stream, section « Dernier enrichissement » du rapport). Le pipeline garantit qu'un job atteint
**toujours** un état terminal (même sur skip/exception) — sinon le polling UI tournerait à
l'infini. `triggered_by` ∈ {`face_upload`, `manual`, `stream`}.

## Ce qu'il reste à faire / roadmap

D'après le CDC, l'AUDIT.md et les écarts identifiés ci-dessus :

1. **Sauvegardes chiffrées quotidiennes** (CDC §8) : `scripts/backup.sh` référencé en config mais
   absent — à écrire (dump PostgreSQL + chiffrement GPG via `BACKUP_GPG_RECIPIENT`, cron).
2. **Tests de performance formalisés** (CDC §9) : valider `< 200ms` détection, `< 100ms`
   comparaison, `< 1s` corrélation initiale via une suite dédiée plutôt que la seule mesure
   `processing_ms` exposée en réponse.
3. **CI** (recommandation AUDIT.md) : `pip-audit` + linter (`ruff`/`pyflakes`) pour prévenir la
   réintroduction de CVEs et de code mort — toujours pas fait. Un premier `.github/workflows`
   existe désormais (`audit-username-platforms.yml`, hebdomadaire), mais uniquement pour
   l'audit de fiabilité du registre de pseudos (Module 15) — pas encore de suite de tests ni
   de linter/pip-audit exécutés en CI.
4. **Révocation de session (S5, AUDIT.md)** : le JWT n'est pas revalidé contre l'état DB entre son
   émission et son expiration (≤15-60 min) — envisager une dénylist ou un cache court si le besoin
   se confirme.
5. **Points mineurs reportés** (AUDIT.md §4) : `faces_count=1` codé en dur sur le chemin doublon
   (m8), clé `"hash"` redondante dans `process_image` (m9), pattern `background_tasks=None`
   fragile (m11) — non bloquants, nettoyage opportuniste.
6. **Captures d'écran** du frontend mentionnées comme « à ajouter » dans le README (Module 4).
7. **Rôle `viewer` en lecture seule** évoqué par l'AUDIT.md comme extension possible du RBAC
   (`admin`/`analyst` uniquement aujourd'hui).

Le CDC est par ailleurs considéré comme **100 % couvert sur le fond** (voir README, section
« ✅ CDC 100 % implémenté ») — les points ci-dessus sont des affinages, pas des manques
fonctionnels majeurs.
