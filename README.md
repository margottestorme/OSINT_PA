# OSINT Platform

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql)
![Tests](https://img.shields.io/badge/tests-77%20passed-brightgreen)
![License](https://img.shields.io/badge/licence-usage%20acad%C3%A9mique-orange)

Plateforme OSINT (Open Source Intelligence) académique développée dans le cadre du **projet annuel ESGI** en cybersécurité (4e année). Elle permet la recherche, l'extraction et la corrélation d'entités à partir de sources publiques et légales, ainsi que la reconnaissance faciale sur images statiques.

---

## Architecture technique

```
┌─────────────────────────────────────────────────────────┐
│                      Client HTTP                        │
│                  (curl / Swagger UI)                     │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS
                       ▼
┌──────────────────────────────────────────────────────────┐
│                    FastAPI (uvicorn)                      │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────────┐  │
│  │  Auth    │  │  Routes   │  │  Middleware           │  │
│  │  JWT     │  │  /api/*   │  │  Logging + RateLimit  │  │
│  └──────────┘  └─────┬─────┘  └──────────────────────┘  │
│                      │                                   │
│  ┌───────────────────▼───────────────────────────────┐  │
│  │              Moteur OSINT (async)                  │  │
│  │  ┌────────────┐ ┌──────────┐ ┌─────────────────┐ │  │
│  │  │ DuckDuckGo │ │  WHOIS   │ │ DNS MX resolver │ │  │
│  │  │  API       │ │  lookup  │ │                 │ │  │
│  │  └────────────┘ └──────────┘ └─────────────────┘ │  │
│  │  ┌──────────────────────────────────────────────┐ │  │
│  │  │        spaCy NLP (fr_core_news_sm)           │ │  │
│  │  │     extraction noms / emails / domaines      │ │  │
│  │  └──────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │          Module Facial (face_recognition)         │  │
│  │  ┌─────────────┐ ┌────────────┐ ┌─────────────┐ │  │
│  │  │  Détection  │ │ Embedding  │ │  Matching    │ │  │
│  │  │  de visages │ │ 128D (dlib)│ │  euclidien   │ │  │
│  │  └─────────────┘ └────────────┘ └─────────────┘ │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │ SQLAlchemy (async)
                       ▼
         ┌─────────────────────────┐
         │   PostgreSQL 15         │
         │   (persons, emails,     │
         │    sites, correlations, │
         │    search_history)      │
         └─────────────────────────┘
```

## Fonctionnalites

### Module 1 — OSINT textuel
- **Recherche OSINT** par email, domaine ou nom de personne
- **Extraction d'entités** (NLP) : emails, domaines, noms via spaCy
- **WHOIS lookup** automatisé sur les domaines
- **Résolution DNS MX** pour validation des domaines email
- **Moteur de corrélation** multi-sources avec scoring
- **API REST sécurisée** avec authentification JWT (15 min)
- **Rate limiting** : 10 requêtes/minute par utilisateur sur la recherche
- **Historique** complet des recherches effectuées
- **Logging structuré** de chaque requête (méthode, endpoint, user, status)

### Module 2 — Reconnaissance faciale
- **Détection de visages** sur images statiques (JPG/PNG, max 5 Mo)
- **Extraction d'embeddings** faciaux (vecteur 128 dimensions via dlib)
- **Matching par distance euclidienne** avec seuil configurable (défaut : 0.6)
- **Anti-doublon** par hash SHA256 de l'image
- **Aucune image persistée** — traitement en mémoire uniquement
- **Création automatique** de nouvelles personnes si pas de match

## Prérequis

- Python 3.11+
- Docker et Docker Compose (pour le déploiement complet)
- PostgreSQL 15 (ou SQLite pour le développement local)
- CMake + g++ (pour la compilation de dlib)

## Installation

### Avec Docker (recommandé)

```bash
git clone https://github.com/margottestorme/OSINT_PA.git
cd OSINT_PA/osint-platform

# Configurer l'environnement
cp .env.example .env
# Éditer .env avec vos valeurs (JWT_SECRET_KEY notamment)

# Lancer les services
docker compose up -d

# Vérifier le statut
curl http://localhost:8000/health
```

### En local (développement)

```bash
git clone https://github.com/margottestorme/OSINT_PA.git
cd OSINT_PA/osint-platform

# Environnement virtuel
python3 -m venv venv
source venv/bin/activate

# Dépendances
pip install -r requirements.txt
python -m spacy download fr_core_news_sm

# Configuration
cp .env.example .env

# Migrations de base de données
alembic upgrade head

# Lancement
uvicorn app.main:app --reload
```

## Configuration (.env)

| Variable | Description | Valeur par défaut |
|----------|-------------|-------------------|
| `DATABASE_URL` | URL PostgreSQL (async) | `postgresql+asyncpg://...` |
| `DATABASE_URL_SYNC` | URL PostgreSQL (sync, Alembic) | `postgresql://...` |
| `REDIS_URL` | URL Redis | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | Clé secrète pour les tokens JWT | **A changer impérativement** |
| `JWT_ALGORITHM` | Algorithme JWT | `HS256` |
| `JWT_EXPIRATION_MINUTES` | Durée de vie des tokens | `60` |
| `APP_NAME` | Nom de l'application | `OSINT-Platform` |
| `DEBUG` | Mode debug | `true` |
| `LOG_LEVEL` | Niveau de log | `DEBUG` |

## Endpoints API

### Authentification

```bash
# Obtenir un token JWT (utilisateur démo : admin / osint2024)
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "osint2024"}'

# Réponse :
# {"access_token": "eyJhbGci...", "token_type": "bearer"}
```

### Module 1 — Recherche OSINT

```bash
# Lancer une recherche par email
curl -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"query": "exemple@domaine.com", "query_type": "email"}'

# Recherche par domaine
curl -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"query": "example.com", "query_type": "domain"}'

# Recherche par nom
curl -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"query": "Jean Dupont", "query_type": "name"}'
```

### Module 2 — Reconnaissance faciale

```bash
# Upload d'une image pour détection + matching
curl -X POST http://localhost:8000/api/faces/upload \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@photo.jpg"

# Réponse (nouvelle personne) :
# {"person_id": 1, "is_new": true, "confidence_score": 1.0, "faces_count": 1}

# Réponse (match existant) :
# {"person_id": 1, "is_new": false, "confidence_score": 0.72, "faces_count": 1}

# Réponse (doublon hash) :
# {"person_id": 1, "is_new": false, "confidence_score": 1.0, "faces_count": 1}

# Liste des personnes avec embedding (sans exposer l'embedding brut)
curl http://localhost:8000/api/faces/persons?limit=20&offset=0 \
  -H "Authorization: Bearer <TOKEN>"

# Réponse :
# [{"id": 1, "first_name": "Inconnu", "last_name": "#a1b2c3d4",
#   "confidence_score": 1.0, "has_embedding": true, "created_at": "..."}]
```

### Consultation des données

```bash
# Liste des corrélations trouvées (paginée)
curl http://localhost:8000/api/correlations?limit=20&offset=0 \
  -H "Authorization: Bearer <TOKEN>"

# Liste des personnes identifiées
curl http://localhost:8000/api/persons?limit=20&offset=0 \
  -H "Authorization: Bearer <TOKEN>"

# Historique des recherches
curl http://localhost:8000/api/history?limit=20&offset=0 \
  -H "Authorization: Bearer <TOKEN>"
```

### Healthcheck

```bash
# Statut de l'application (pas d'authentification requise)
curl http://localhost:8000/health
# {"status": "ok", "version": "0.1.0"}
```

### Documentation interactive

- **Swagger UI** : [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc** : [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Structure du projet

```
osint-platform/
├── app/
│   ├── api/
│   │   ├── face_routes.py      # Endpoints reconnaissance faciale
│   │   ├── middleware.py       # Logging des requêtes HTTP
│   │   ├── rate_limit.py       # Rate limiter par utilisateur
│   │   └── routes.py           # Endpoints OSINT + auth + health
│   ├── core/
│   │   ├── config.py           # Configuration centralisée (pydantic-settings)
│   │   ├── logging.py          # Configuration loguru
│   │   └── security.py         # JWT + bcrypt
│   ├── db/
│   │   ├── models.py           # Modèles SQLAlchemy (5 tables + embedding)
│   │   └── session.py          # Engine async + session factory
│   ├── facial/
│   │   ├── engine.py           # Détection, embedding, matching facial
│   │   └── exceptions.py       # Exceptions métier (NoFace, MultipleFaces...)
│   ├── osint/
│   │   ├── engine.py           # Moteur OSINT (DuckDuckGo, WHOIS, DNS, spaCy)
│   │   └── rate_limiter.py     # Rate limiter pour requêtes externes (2s min)
│   ├── schemas/
│   │   └── schemas.py          # Schemas Pydantic (validation I/O)
│   └── main.py                 # Point d'entrée FastAPI
├── alembic/
│   ├── versions/               # Fichiers de migration
│   └── env.py                  # Configuration Alembic
├── tests/
│   ├── conftest.py             # Fixtures pytest (DB en mémoire, auth)
│   ├── test_api.py             # Tests des endpoints OSINT (29 tests)
│   ├── test_engine.py          # Tests du moteur OSINT (16 tests)
│   ├── test_face_api.py        # Tests des endpoints faciaux (13 tests)
│   ├── test_facial.py          # Tests du moteur facial (17 tests)
│   └── test_rate_limiter.py    # Tests du rate limiter (2 tests)
├── .env.example                # Template des variables d'environnement
├── .gitignore
├── alembic.ini                 # Configuration Alembic
├── docker-compose.yml          # PostgreSQL + Redis + App
├── Dockerfile
├── requirements.txt
└── README.md
```

## Tests

```bash
# Lancer tous les tests
python -m pytest tests/ -v

# Avec couverture de code
python -m pytest tests/ -v --cov=app

# Tests par module
python -m pytest tests/test_api.py -v           # 29 tests API OSINT
python -m pytest tests/test_engine.py -v        # 16 tests moteur OSINT
python -m pytest tests/test_face_api.py -v      # 13 tests API faciale
python -m pytest tests/test_facial.py -v        # 17 tests moteur facial
python -m pytest tests/test_rate_limiter.py -v  # 2 tests rate limiter
```

**77 tests** couvrent :
- Authentification JWT (token valide, expiré, invalide, identifiants erronés)
- Validation des inputs (injection SQL bloquée par enum Pydantic)
- Rate limiting (429 après 10 requêtes/minute)
- Pagination (paramètres valides et invalides)
- Moteur OSINT (toutes les fonctions avec mocks des appels externes)
- Rate limiter OSINT (délai minimum entre requêtes)
- Upload facial (format valide/invalide, taille, doublon hash, match/pas de match)
- Moteur facial (détection, embedding, comparaison euclidienne)

## Stack technique

| Composant | Technologie | Rôle |
|-----------|-------------|------|
| Backend | FastAPI + uvicorn | Framework web async |
| Base de données | PostgreSQL 15 | Stockage persistant |
| ORM | SQLAlchemy 2.0 (async) | Mapping objet-relationnel |
| Migrations | Alembic | Versioning du schéma DB |
| NLP | spaCy (fr_core_news_sm) | Extraction d'entités |
| Reconnaissance faciale | face_recognition + dlib | Détection et embedding 128D |
| Traitement image | Pillow | Validation format (JPEG/PNG) |
| HTTP client | httpx (async) | Requêtes vers APIs externes |
| Auth | python-jose + passlib | JWT + hachage bcrypt |
| Logging | loguru | Logs structurés |
| Cache | Redis 7 | Tâches async (futur) |
| Conteneurs | Docker Compose | Déploiement |

## Mentions légales et avertissements

### Usage académique uniquement

Ce projet est développé dans un **cadre strictement académique** (projet annuel ESGI, 4e année cybersécurité). Il n'est pas destiné à un usage commercial ou opérationnel.

### Cadre juridique

- **RGPD (Règlement Général sur la Protection des Données)** : toute donnée personnelle collectée lors des recherches OSINT est soumise au RGPD. Les données ne sont pas partagées avec des tiers et sont stockées uniquement dans la base de données locale du projet.
- **Loi Informatique et Libertés** (loi n°78-17 du 6 janvier 1978) : le traitement de données à caractère personnel est effectué dans le respect de cette loi.
- **Article 226-18 du Code pénal** : la collecte de données personnelles par un moyen frauduleux, déloyal ou illicite est punie de 5 ans d'emprisonnement et de 300 000 euros d'amende.

### Sources de données

Le moteur OSINT utilise **exclusivement des sources publiques et légales** :
- **DuckDuckGo Instant Answer API** : API publique, pas de scraping
- **WHOIS** : données d'enregistrement de domaines (publiques)
- **DNS MX** : enregistrements DNS publics

Aucun scraping agressif n'est effectué. Un **rate limiting de 2 secondes minimum** est appliqué entre chaque requête externe pour respecter les conditions d'utilisation des services interrogés.

### Données biométriques

Le module de reconnaissance faciale (v0.2.0) traite des données biométriques dans le strict respect du RGPD et de la réglementation sur les données biométriques (article 9 RGPD) :
- **Images uploadées manuellement uniquement** — pas de flux webcam, pas de capture automatique
- **Aucune image persistée sur le disque** — traitement en mémoire, suppression immédiate après extraction de l'embedding
- **Seul l'embedding (vecteur numérique 128D) est stocké** en base de données, jamais l'image source
- **Anti-doublon par hash SHA256** — une même image ne peut pas être traitée deux fois
- **Environnement académique isolé** — aucune connexion à des bases de données biométriques externes

### Responsabilité

Les auteurs de ce projet déclinent toute responsabilité en cas d'utilisation abusive ou illégale de cet outil. L'utilisateur est seul responsable de l'usage qu'il fait de la plateforme et doit s'assurer de respecter la législation en vigueur dans sa juridiction.

---

## Auteurs

| Nom | Rôle |
|-----|------|
| **STORME Margotte** | Développement |
| **SIVATHASAN Rakulan** | Développement |
| **AZOUZ Idir** | Développement |

**Projet annuel ESGI — Cybersécurité 4e année — 2025-2026**
