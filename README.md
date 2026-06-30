# OSINT Platform

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql)
![Tests](https://img.shields.io/badge/tests-47%20passed-brightgreen)
![License](https://img.shields.io/badge/licence-usage%20acad%C3%A9mique-orange)

Plateforme OSINT (Open Source Intelligence) académique développée dans le cadre du **projet annuel ESGI** en cybersécurité (5e année). Elle permet la recherche, l'extraction et la corrélation d'entités à partir de sources publiques et légales.

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

- **Recherche OSINT** par email, domaine ou nom de personne
- **Extraction d'entités** (NLP) : emails, domaines, noms via spaCy
- **WHOIS lookup** automatisé sur les domaines
- **Résolution DNS MX** pour validation des domaines email
- **Moteur de corrélation** multi-sources avec scoring
- **API REST sécurisée** avec authentification JWT (15 min)
- **Rate limiting** : 10 requêtes/minute par utilisateur sur la recherche
- **Historique** complet des recherches effectuées
- **Logging structuré** de chaque requête (méthode, endpoint, user, status)

## Prérequis

- Python 3.11+
- Docker et Docker Compose (pour le déploiement complet)
- PostgreSQL 15 (ou SQLite pour le développement local)

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

### Recherche OSINT

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
│   │   ├── middleware.py       # Logging des requêtes HTTP
│   │   ├── rate_limit.py       # Rate limiter par utilisateur
│   │   └── routes.py           # Endpoints FastAPI
│   ├── core/
│   │   ├── config.py           # Configuration centralisée (pydantic-settings)
│   │   ├── logging.py          # Configuration loguru
│   │   └── security.py         # JWT + bcrypt
│   ├── db/
│   │   ├── models.py           # Modèles SQLAlchemy (5 tables)
│   │   └── session.py          # Engine async + session factory
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
│   ├── test_api.py             # Tests des endpoints (29 tests)
│   ├── test_engine.py          # Tests du moteur OSINT (16 tests)
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

# Tests spécifiques
python -m pytest tests/test_api.py -v       # 29 tests API
python -m pytest tests/test_engine.py -v    # 16 tests moteur OSINT
```

**47 tests** couvrent :
- Authentification JWT (token valide, expiré, invalide, identifiants erronés)
- Validation des inputs (injection SQL bloquée par enum Pydantic)
- Rate limiting (429 après 10 requêtes/minute)
- Pagination (paramètres valides et invalides)
- Moteur OSINT (toutes les fonctions avec mocks des appels externes)
- Rate limiter OSINT (délai minimum entre requêtes)

## Stack technique

| Composant | Technologie | Rôle |
|-----------|-------------|------|
| Backend | FastAPI + uvicorn | Framework web async |
| Base de données | PostgreSQL 15 | Stockage persistant |
| ORM | SQLAlchemy 2.0 (async) | Mapping objet-relationnel |
| Migrations | Alembic | Versioning du schéma DB |
| NLP | spaCy (fr_core_news_sm) | Extraction d'entités |
| HTTP client | httpx (async) | Requêtes vers APIs externes |
| Auth | python-jose + passlib | JWT + hachage bcrypt |
| Logging | loguru | Logs structurés |
| Cache | Redis 7 | Tâches async (futur) |
| Conteneurs | Docker Compose | Déploiement |

## Mentions légales et avertissements

### Usage académique uniquement

Ce projet est développé dans un **cadre strictement académique** (projet annuel ESGI, 5e année cybersécurité). Il n'est pas destiné à un usage commercial ou opérationnel.

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

Le module actuel (v0.1.0) ne traite que des données textuelles issues de sources publiques. Les modules futurs intégreront un traitement de données biométriques (reconnaissance faciale sur images statiques uploadées manuellement) dans le strict respect du RGPD et de la réglementation sur les données biométriques (article 9 RGPD). Ce traitement sera limité à un environnement académique isolé.

### Responsabilité

Les auteurs de ce projet déclinent toute responsabilité en cas d'utilisation abusive ou illégale de cet outil. L'utilisateur est seul responsable de l'usage qu'il fait de la plateforme et doit s'assurer de respecter la législation en vigueur dans sa juridiction.

---

**Projet annuel ESGI — Cybersécurité — 2024-2025**
