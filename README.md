# OSINT Platform

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql)
![Tests](https://img.shields.io/badge/tests-238%20passed-brightgreen)
![License](https://img.shields.io/badge/licence-usage%20acad%C3%A9mique-orange)

Plateforme OSINT (Open Source Intelligence) académique développée dans le cadre du **projet annuel ESGI** en cybersécurité (4e année). Elle permet la recherche, l'extraction et la corrélation d'entités à partir de sources publiques et légales, la reconnaissance faciale sur images statiques, et l'enrichissement automatique croisé entre les deux modules.

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

### Module 3 — Corrélation automatique facial → OSINT
- **Enrichissement automatique** : lorsqu'un visage est matché à une personne existante, un enrichissement OSINT est lancé en tâche de fond
- **Enrichissement itératif** sur 3 niveaux de profondeur maximum :
  1. Niveau 0 : emails déjà associés à la personne
  2. Niveau 1 : nouvelles entités découvertes via corrélation des emails
  3. Niveau 2 : entités découvertes à partir des résultats du niveau 1
- **Déduplication** : les emails et domaines déjà traités sont ignorés (sets `processed_emails` / `processed_domains`)
- **Rapport complet** par personne : endpoint `GET /api/persons/{id}/report` regroupant personne, emails, sites et corrélations
- **Rate limiting respecté** sur toutes les requêtes externes pendant l'enrichissement

### Module 4 — Dashboard frontend
- **Interface web** HTML/CSS/JS vanilla (aucun framework), servie directement par FastAPI
- **Page de login** (`/login`) : formulaire username/password, JWT stocké en mémoire (jamais localStorage)
- **Dashboard** (`/dashboard`) : 4 sections — upload image, recherche OSINT, liste des personnes, historique
- **Rapport personne** (`/report?id=X`) : détail complet (infos, emails, sites, corrélations)
- **Protection côté client** : redirection automatique vers `/login` si JWT absent ou expiré
- **Gestion des erreurs** : messages visibles dans l'interface pour chaque section

#### Pages disponibles

| Route | Description |
|-------|-------------|
| `GET /` | Redirige vers `/login` |
| `GET /login` | Page de connexion |
| `GET /dashboard` | Dashboard principal (JWT requis côté client) |
| `GET /report?id=X` | Rapport détaillé d'une personne |

#### Accès à l'interface

```bash
# En local
uvicorn app.main:app --reload
# → http://localhost:8000/

# Depuis la VM
uvicorn app.main:app --host 0.0.0.0 --port 8000
# → http://<IP_VM>:8000/

# Identifiants démo : admin / osint2024
```

*Captures d'écran : à ajouter.*

### Module 5 — Sécurité & Hardening
- **Gestion multi-utilisateurs** : table `users` en DB avec rôles `admin` / `analyst`, comptes activables/désactivables
- **Authentification en DB** : vérification bcrypt en base, plus aucun mot de passe hardcodé
- **CLI de gestion** : `python -m app.cli create-user --username admin --role admin`
- **Logs d'audit** : table `audit_logs` traçant 5 événements (login réussi/échoué, upload facial, recherche OSINT, consultation rapport)
- **En-têtes de sécurité HTTP** sur toutes les réponses : `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Content-Security-Policy`
- **Rate limiting global** : 100 requêtes/minute par IP (configurable via `.env`)
- **CORS restreint** : origines autorisées via `ALLOWED_ORIGINS` dans `.env`, méthodes GET/POST uniquement
- **Timeout externe** : 10s max sur les requêtes httpx (configurable)

#### Commandes CLI

```bash
# Créer un administrateur
python -m app.cli create-user --username admin --role admin

# Créer un analyste
python -m app.cli create-user --username analyst1 --role analyst

# Le mot de passe est demandé interactivement (ou --password pour scripts)
```

### Module 7 — Sources OSINT enrichies
Le moteur agrège désormais **10 sources** (en plus de DuckDuckGo / WHOIS / DNS MX historiques), toutes passant par le rate limiter (2s) et le timeout httpx, avec un User-Agent descriptif et une résilience totale (une source qui échoue renvoie `None`, jamais d'exception).

**Lot Vert — 7 sources actives sans aucune clé API :**

| Source | Type | Ce qu'elle retourne |
|--------|------|---------------------|
| **Gravatar** | email | Profil public (nom, avatar, liens sociaux) via hash MD5 |
| **crt.sh** | domain | Sous-domaines via les logs de Certificate Transparency |
| **DNS étendu** | domain | A / AAAA / NS / CNAME / TXT + SPF + DMARC |
| **RDAP** | domain | Enregistrement structuré (successeur JSON de WHOIS) : NS, emails |
| **Shodan InternetDB** | domain | Ports, CPE, vulnérabilités, hostnames (par IP résolue) |
| **Wikipedia** | name | Pages fr.wikipedia correspondant au nom |
| **GitHub** | name | Profils publics d'utilisateurs (token optionnel pour le quota) |

**Lot Freemium — 3 sources dormantes (activées dès qu'une clé est fournie) :**

| Source | Type | Clé (.env) | Où l'obtenir |
|--------|------|-----------|--------------|
| **VirusTotal** | domain | `VIRUSTOTAL_API_KEY` | https://www.virustotal.com/gui/my-apikey |
| **AlienVault OTX** | domain | `OTX_API_KEY` | https://otx.alienvault.com/api |
| **Hunter.io** | email | `HUNTER_API_KEY` | https://hunter.io/api-keys |

> Sans clé, ces sources renvoient `None` silencieusement (log `debug`) : aucune erreur, aucun appel réseau. Il suffit de renseigner la clé dans `.env` pour les activer, sans toucher au code.

#### Format `OsintResult` normalisé
Chaque source renvoie une enveloppe commune, ce qui permet à l'enrichissement de peupler automatiquement les tables `emails` / `sites` / `correlations` :

```json
{
  "source": "crt.sh",
  "entity": "example.com",
  "type": "domain",
  "data": { "...": "payload brut spécifique à la source" },
  "extracted": {
    "emails":  [], "domains": [], "urls": [], "ips": [], "names": []
  }
}
```

Le champ `extracted` alimente l'enrichissement : `emails` → table emails, `domains`/`urls` → table sites, `ips` → champ `data` de la corrélation. Les sources s'ajoutent via un **registre déclaratif** `SOURCES` (aucun `if/elif` à modifier).

### Module 8 — Refonte UI & Persistance des recherches
Refonte complète de l'interface (design professionnel inspiré des outils OSINT type Maltego/Shodan) et correction d'un manque majeur : les recherches manuelles ne persistaient aucun résultat.

**Persistance des recherches (table `SearchResult`)**
Auparavant `POST /api/search` ne stockait qu'un compteur. Désormais chaque `OsintResult` du moteur est persisté dans la table `search_results` (mapping 1:1 : `source`, `entity`, `entity_type`, `data`, `extracted`) et consultable via l'API.

**5 pages frontend** (HTML/CSS/JS vanilla, [vis.js](https://visjs.org) pour les graphes) :

| Page | Route | Contenu |
|------|-------|---------|
| Login | `/login` | Connexion épurée, thème sombre |
| Dashboard | `/dashboard` | Sidebar + 4 KPI + upload drag&drop + recherche (spinner/polling/toast) + tableaux personnes/historique |
| Résultats | `/results?id=X` | Résultats groupés par source, badges d'entités cliquables, accordéon JSON, export |
| Rapport | `/report?id=X` | Fiche personne éditable, onglets (emails/sites/corrélations/activité), mini-graphe |
| Graphe | `/graph` | Graphe global interactif (personnes ↔ emails ↔ domaines ↔ IPs), filtres, légende |

**Nouveaux endpoints API**

| Endpoint | Rôle |
|----------|------|
| `GET /api/stats` | KPI : recherches, personnes, corrélations, dernière activité |
| `GET /api/history/{id}` | Métadonnées d'une recherche |
| `GET /api/history/{id}/results` | Contenu détaillé d'une recherche (par source) |
| `GET /api/emails`, `GET /api/sites` | Listes (nœuds du graphe) |
| `PATCH /api/persons/{id}` | Édition nom/notes d'une personne |

**Design system** ([style.css](osint-platform/frontend/static/css/style.css)) : sidebar sombre `#1a1f2e` + contenu clair `#f8fafc`, accents bleu `#3b82f6` / vert `#10b981`, police **Inter**, icônes **Lucide**. Composants : cards, KPI, badges typés, onglets, modal, accordéon, toasts, dropzone, timeline, empty-states. CSP durcie avec allowlist des CDN utilisés.

### Module 9 — Stream Webcam temps réel (architecture côté client)
Détection et reconnaissance faciale en direct (le CDC exige un flux vidéo temps réel, en plus de l'upload statique). La webcam est capturée **dans le navigateur** (`getUserMedia`) : le serveur ne capture rien lui-même → la fonctionnalité marche depuis **n'importe quel appareil**, sans webcam serveur.

**Capture côté client + analyse serveur**
- Le navigateur ouvre la caméra via `navigator.mediaDevices.getUserMedia({video:{width:640,height:480}})` et l'affiche dans une balise `<video>`.
- Toutes les **500 ms**, un frame est capturé sur un `<canvas>` caché, encodé en **JPEG base64** (`toDataURL('image/jpeg', 0.7)`) et envoyé à `POST /api/stream/analyze`.
- Les visages détectés sont dessinés sur un `<canvas id="overlay">` **superposé** à la vidéo (position absolute) : rectangle **vert + nom · score** si personne connue, **rouge + « Inconnu »** sinon.
- Stats temps réel : FPS effectif, visages détectés, correspondances. Un frame est **skippé** si l'analyse précédente n'est pas terminée (évite l'accumulation quand le serveur est lent).

**Endpoint d'analyse** — `POST /api/stream/analyze` (JWT, **2 requêtes/seconde** par utilisateur)
```json
// Requête : {"frame": "<base64 JPEG>"}
// Réponse :
{
  "faces": [
    {"location": {"top":…, "right":…, "bottom":…, "left":…},
     "person_id": 1, "name": "Jean Dupont", "confidence": 0.82, "is_new": false}
  ],
  "faces_count": 1,
  "processing_ms": 48.3
}
```
Le serveur décode le frame, détecte tous les visages (`locate_and_encode` → localisation + embedding en une passe), les compare à la base (`compare_embedding`), puis **supprime immédiatement les bytes** (aucune écriture disque, aucune conservation).

**Endpoints**

| Endpoint | Rôle |
|----------|------|
| `POST /api/stream/analyze` | Analyse un frame (base64) → visages localisés + identifiés |
| `GET /api/stream/info` | Caractéristiques webcam serveur (informatif) |
| `GET /api/stream/status` | Statut simplifié : `{mode: "client-side", known_faces}` |

**Page** `/stream` : `<video>` + overlay canvas, contrôle Démarrer/Arrêter, compteurs live, dernière personne identifiée (→ rapport) ou bouton « Enregistrer » pour un nouveau visage, **avertissement RGPD** (article 9).

**Gestion des erreurs (côté navigateur)** : permission refusée, absence de caméra, caméra occupée → messages clairs ; en **HTTP hors localhost**, avertissement informatif non bloquant (`getUserMedia` fonctionne en HTTP sur `localhost`/IP privée dans Chrome, HTTPS requis en production).

**Contraintes respectées** : aucune frame écrite sur le disque, bytes supprimés immédiatement après analyse, JWT requis, rate limiting 2/s, aucune donnée biométrique exposée.

### Module 10 — Scraping BeautifulSoup
Enrichissement OSINT par **contenu de page web** : le moteur récupère et analyse le HTML des pages liées à une entité pour en extraire de nouvelles données. Scraping **léger et respectueux** (sources publiques uniquement, jamais de scraping agressif), branché sur le registre `SOURCES` comme les autres sources.

**`fetch_page` — récupération sécurisée d'une page**
- **robots.txt respecté** : le fichier de l'hôte est lu et interrogé (`RobotFileParser`) avant toute récupération ; absent, illisible ou injoignable → autorisé (permissif standard)
- **Blacklist réseaux sociaux** : `facebook.com`, `twitter.com`, `x.com`, `instagram.com`, `linkedin.com`, `tiktok.com` sont refusés d'emblée (ToS / robots stricts), **sans aucune requête réseau**
- **Garde-fous** : taille max **1 Mo**, timeout **10s** (httpx), User-Agent descriptif, résilience totale (toute erreur → `None`)

**`scrape_entities` — extraction d'entités depuis le HTML**
Nettoyage préalable (balises `<script>` / `<style>` / `<noscript>` retirées) via BeautifulSoup + lxml, puis extraction :
- **Emails** (regex, triés et dédupliqués)
- **Téléphones** au format international (préfixe `+` obligatoire pour limiter le bruit)
- **Domaines** externes (hôtes différents de la page courante, hors blacklist)
- **Noms** (`<title>`, `<h1>`, `<h2>`) et **description** (`<meta name="description">`)
- **URLs** absolues collectées (max 20)

**`scrape_batch` — traitement par lot**
- **Maximum 3 pages** par entité (`max_pages=3`)
- **Rate limiting 2s** entre chaque page (le même limiter que les autres requêtes externes)
- Les erreurs individuelles sont ignorées : une page qui échoue n'interrompt pas le lot

**Intégration dans les 3 types de recherche** — la source `_source_scraper` est enregistrée sur `email`, `domain` et `name` :

| Type | Pages scrapées |
|------|----------------|
| `email` | Pages retournées par DuckDuckGo mentionnant l'email |
| `domain` | Page principale du domaine (`https://<domaine>`) |
| `name` | Pages Wikipedia retournées par la source Wikipedia |

Les entités agrégées (emails, domaines, urls, noms, téléphones) alimentent l'enrichissement au même titre que les autres sources, via l'enveloppe `OsintResult` normalisée.

### Module 11 — Indexation & recherche full-text (Elasticsearch)
Couche d'indexation **optionnelle** au-dessus de PostgreSQL (qui reste la source de vérité) pour une **recherche full-text multi-critères rapide**. Elasticsearch 8.x en nœud unique. Si ES est indisponible, l'application fonctionne normalement : la **dégradation propre** est garantie de bout en bout (aucune écriture ES n'est bloquante, aucune route ne casse).

**4 index à mappings stricts** (`dynamic: strict` → tout champ non déclaré est rejeté à l'indexation) :

| Index | Champs indexés |
|-------|----------------|
| `osint_persons` | id, first_name, last_name, notes, confidence_score, created_at |
| `osint_search_results` | search_id, source, entity, entity_type, **extracted** (nested), created_at |
| `osint_emails` | email, source, person_id, created_at |
| `osint_sites` | url, server, created_at |

> **Zéro fuite biométrique** : l'index `osint_persons` **n'indexe jamais** l'embedding facial ni le hash d'image — uniquement des métadonnées publiques recherchables. Le mapping `strict` sert de garde-fou (un champ imprévu ferait échouer l'indexation).

**Indexation automatique et non bloquante** (`BackgroundTask`, jamais dans le chemin de réponse HTTP) :
- `POST /api/search` → indexe chaque `SearchResult` créé
- `POST /api/faces/upload` → indexe la personne créée (et la personne matchée mise à jour)
- `PATCH /api/persons/{id}` → réindexe la personne
- `_id` Elasticsearch = clé primaire PostgreSQL → **upsert idempotent** (réindexer ne duplique jamais)

**Recherche full-text** — `GET /api/search/fulltext` (JWT admin) :

| Paramètre | Rôle |
|-----------|------|
| `q` | termes recherchés (**400** si vide) |
| `type` | `persons` / `results` / `emails` / `sites` / `all` (défaut) |
| `limit` / `offset` | pagination |

Recherche multi-index fusionnée et triée par score si `type=all`, **highlight** des termes (`<mark>`, contenu échappé côté ES → rendu HTML sûr), clause **nested** sur `extracted` pour retrouver les emails/domaines/IPs découverts par scraping. Réponse : `{total, hits: [{index, id, score, highlight, source}]}`. Endpoint compagnon `GET /api/search/status` → `{available}` (l'UI distingue « 0 résultat » d'« ES indisponible »).

**Barre de recherche globale** dans la topbar de **toutes les pages** (dashboard, results, report, graph, stream) : `Entrée` ou clic → page `/search`. Résultats **groupés par type** (👤 Personnes / 📧 Emails / 🌐 Sites / 🔍 Recherches), score en **% de pertinence**, termes **surlignés en jaune**, clic → page correspondante, message clair si Elasticsearch est indisponible.

**Script de réindexation** — `python scripts/reindex.py` : réindexe toute la base PostgreSQL vers Elasticsearch avec une **barre de progression par type**, un **résumé coloré**, en **upsert idempotent** ; sortie en **code 1** avec message clair si ES est indisponible.

**Lancement d'Elasticsearch**
```bash
# Démarrer Elasticsearch seul (port 9200, lié à la loopback)
docker compose up -d elasticsearch

# Indexer les données déjà présentes en base
python scripts/reindex.py
```
L'URL est configurable via `ELASTICSEARCH_URL` (`.env`, défaut `http://localhost:9200`). Les index sont créés automatiquement au démarrage de l'application s'ils sont absents.

### Corrections récentes (suite aux tests manuels)
- **Graphe global vide (422)** : la borne `limit` max des endpoints de liste (`persons`, `correlations`, `emails`, `sites`, `history`) est passée à **500** — le graphe charge `limit=200` et recevait un `422` (persons était plafonné à 100), ce qui vidait le graphe.
- **Source scraper cassée** : `_source_scraper(entity, entity_type)` est désormais enregistrée dans le registre `SOURCES` via des **lambdas** passant l'`entity_type` (`lambda e: _source_scraper(e, "domain")`…), corrigeant `missing 1 required positional argument: 'entity_type'` à chaque recherche.

## Prérequis

- Python 3.11+
- Docker et Docker Compose (pour le déploiement complet)
- PostgreSQL 15 (ou SQLite pour le développement local)
- CMake + g++ (pour la compilation de dlib)
- Elasticsearch 8.x (**optionnel** — recherche full-text ; l'app fonctionne sans)

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

### Module 3 — Rapport et enrichissement

```bash
# Rapport complet d'une personne (emails, sites, corrélations)
curl http://localhost:8000/api/persons/1/report?limit=20&offset=0 \
  -H "Authorization: Bearer <TOKEN>"

# Réponse :
# {
#   "person": {"id": 1, "first_name": "Inconnu", "last_name": "#a1b2c3d4", ...},
#   "emails": [{"id": 1, "email": "contact@example.com", "source": "duckduckgo", ...}],
#   "sites": [{"id": 1, "url": "https://example.com", "server": "nginx", ...}],
#   "correlations": [{"id": 1, "person_id": 1, "email_id": 1, "site_id": 1, "score": 0.85, ...}]
# }
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
│   │   ├── middleware.py       # Logging, en-têtes sécurité, rate limit global
│   │   ├── rate_limit.py       # Rate limiter par utilisateur
│   │   └── routes.py           # Endpoints OSINT + auth + health
│   ├── core/
│   │   ├── audit.py            # Fonction create_audit_log + extraction IP
│   │   ├── config.py           # Configuration centralisée (pydantic-settings)
│   │   ├── logging.py          # Configuration loguru
│   │   └── security.py         # JWT + bcrypt + auth en DB
│   ├── db/
│   │   ├── models.py           # Modèles SQLAlchemy (7 tables : users, audit_logs...)
│   │   └── session.py          # Engine async + session factory
│   ├── facial/
│   │   ├── engine.py           # Détection, embedding, matching facial
│   │   └── exceptions.py       # Exceptions métier (NoFace, MultipleFaces...)
│   ├── osint/
│   │   ├── correlator.py       # Enrichissement itératif facial→OSINT (3 niveaux)
│   │   ├── engine.py           # Moteur OSINT (DuckDuckGo, WHOIS, DNS, spaCy)
│   │   ├── rate_limiter.py     # Rate limiter pour requêtes externes (2s min)
│   │   └── scraper.py          # Scraping léger BeautifulSoup (robots.txt, blacklist)
│   ├── search/
│   │   ├── elastic.py          # Client Elasticsearch + 4 index (mappings stricts)
│   │   ├── indexer.py          # Indexation PostgreSQL → ES (index_* + bulk_reindex)
│   │   └── fulltext.py         # Recherche full-text multi-index + highlight
│   ├── schemas/
│   │   └── schemas.py          # Schemas Pydantic (validation I/O)
│   ├── cli.py                  # CLI gestion utilisateurs
│   └── main.py                 # Point d'entrée FastAPI + CORS
├── alembic/
│   ├── versions/               # Fichiers de migration
│   └── env.py                  # Configuration Alembic
├── tests/
│   ├── conftest.py             # Fixtures pytest (DB en mémoire, auth)
│   ├── test_api.py             # Tests des endpoints OSINT (29 tests)
│   ├── test_engine.py          # Tests du moteur OSINT (16 tests)
│   ├── test_face_api.py        # Tests des endpoints faciaux (13 tests)
│   ├── test_correlator.py       # Tests du corrélateur (6 tests)
│   ├── test_facial.py          # Tests du moteur facial (17 tests)
│   ├── test_rate_limiter.py    # Tests du rate limiter (2 tests)
│   ├── test_report_api.py      # Tests de l'endpoint rapport (5 tests)
│   ├── test_frontend_routes.py # Tests des routes frontend (8 tests)
│   ├── test_security.py        # Tests sécurité (29 tests)
│   ├── test_sources.py         # Tests des 10 sources OSINT (30 tests)
│   ├── test_scraper.py         # Tests du scraping BeautifulSoup (28 tests)
│   ├── test_cli.py             # Tests du CLI (4 tests)
│   ├── test_search_results.py  # Tests persistance recherches (2 tests)
│   ├── test_elastic.py         # Tests Elasticsearch — Module 11 (26 tests)
│   └── test_stream.py          # Tests stream webcam (16 tests)
├── frontend/
│   ├── index.html              # Page de login
│   ├── dashboard.html          # Dashboard principal
│   ├── results.html            # Resultats d'une recherche
│   ├── search.html             # Recherche globale full-text (Module 11)
│   ├── graph.html              # Graphe global (vis.js)
│   ├── report.html             # Rapport par personne
│   ├── stream.html             # Stream webcam temps reel
│   └── static/
│       ├── css/style.css       # Styles globaux
│       └── js/
│           ├── api.js          # Client API (JWT en mémoire)
│           └── app.js          # Logique UI
├── .env.example                # Template des variables d'environnement
├── .gitignore
├── alembic.ini                 # Configuration Alembic
├── docker-compose.yml          # PostgreSQL + Redis + Elasticsearch + App
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
python -m pytest tests/test_correlator.py -v    # 6 tests corrélateur
python -m pytest tests/test_report_api.py -v    # 5 tests rapport personne
python -m pytest tests/test_frontend_routes.py -v # 6 tests routes frontend
python -m pytest tests/test_security.py -v        # 25 tests sécurité
python -m pytest tests/test_sources.py -v         # 30 tests sources OSINT
python -m pytest tests/test_scraper.py -v         # 28 tests scraping BeautifulSoup
python -m pytest tests/test_search_results.py -v  # 2 tests persistance recherches
python -m pytest tests/test_stream.py -v          # 16 tests stream webcam
python -m pytest tests/test_elastic.py -v         # 26 tests Elasticsearch (Module 11)

# Lancer la suite sur PostgreSQL au lieu de SQLite (compatibilité)
TEST_DATABASE_URL=postgresql+asyncpg://osint_user:<password>@localhost:5432/osint_test \
  python -m pytest tests/ -v
```

**234 tests** couvrent :
- Authentification JWT (token valide, expiré, invalide, identifiants erronés)
- Validation des inputs (injection SQL bloquée par enum Pydantic)
- Rate limiting (429 après 10 requêtes/minute)
- Pagination (paramètres valides et invalides)
- Moteur OSINT (toutes les fonctions avec mocks des appels externes)
- Rate limiter OSINT (délai minimum entre requêtes)
- Upload facial (format valide/invalide, taille, doublon hash, match/pas de match)
- Moteur facial (détection, embedding, comparaison euclidienne)
- Corrélateur itératif (enrichissement multi-niveaux, déduplication)
- Rapport par personne (auth, 404, données vides, données complètes, pagination)
- Routes frontend (redirect, pages HTML, fichiers statiques CSS/JS)
- Multi-utilisateurs (login DB, user inactif, rôle dans JWT, last_login)
- Logs d'audit (login succès/échec, upload, recherche, rapport)
- En-têtes de sécurité (5 headers sur health, login, dashboard)
- Rate limiting global (blocage par IP, IP différente non bloquée)
- RBAC (analyst 403 / admin 200 sur les endpoints sensibles)
- Sources OSINT (10 sources : réponse OK, cas vide, 404, résilience réseau, freemium dormant/actif)
- Scraping BeautifulSoup (blacklist réseaux sociaux, robots.txt, taille max, extraction d'entités, lot 3 pages, rate limiting)
- Persistance des recherches (`_run_search` écrit les résultats, endpoint 200/404)
- Routes frontend (login, dashboard, results, report, graph, stream)
- Stream webcam client-side (caméra serveur, `/info`, `/status`, `locate_and_encode` multi-visages, `POST /analyze` : match / nouveau visage / aucun visage / base64 corrompu 422 / sans JWT 401 / rate limit 429)
- Elasticsearch (client mocké, indexation sans fuite biométrique, `extracted` filtré, bulk_reindex, recherche full-text multi-index, `/api/search/fulltext` 200/400/401/403/422, `/api/search/status`, dégradation propre ES down)

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
| Indexation full-text | Elasticsearch 8.x | Recherche multi-critères rapide (optionnel) |
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
- **Scraping web léger** (Module 10) : uniquement des pages publiques, **robots.txt respecté**, réseaux sociaux exclus, taille et fréquence plafonnées

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
