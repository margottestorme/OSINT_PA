# 🎓 Guide de démonstration — Soutenance ESGI

> **Plateforme OSINT** — recherche multi-sources, reconnaissance faciale, corrélation, enquêtes & rapport PDF forensique.
> Document à garder ouvert (2ᵉ écran / téléphone) pendant la soutenance.

---

## 🔑 Accès rapide

| Élément | Valeur |
|---------|--------|
| URL | http://localhost:8000/login |
| Identifiants | **admin** / **osint2024** |
| Lancement | `./scripts/start_demo.sh` |
| Reset + données démo | `echo y \| python scripts/reset_demo.py && python scripts/seed_demo.py` |
| Cible fil rouge | Startup fictive **demo-osint.fr** et ses 3 membres |

**Personnes déjà dans la base** (issues du seed) :
- **Camille Laurent** — fondatrice, confiance 0,92 (dossier le plus riche → à utiliser pour le rapport)
- **Julien Mercier** — CTO/dev, confiance 0,85
- **Sofia Nakamura** — consultante externe, confiance 0,78

---

## 🎬 Scénario de démonstration (15 minutes, narratif)

> Convention : **[DIRE]** = ce que vous prononcez ; **[FAIRE]** = ce que vous cliquez. Parlez lentement, laissez les animations se terminer avant de cliquer.

### ⏱️ 0:00 – 2:00 — Introduction & connexion

**[DIRE]** « Bonjour. Nous vous présentons une plateforme OSINT — *Open Source Intelligence* — capable d'agréger des informations publiques sur une cible, de les corréler automatiquement, d'y ajouter de la reconnaissance faciale, et de produire un rapport d'enquête forensique opposable. Tout est conçu *privacy by design* et conforme au RGPD, un point sur lequel nous reviendrons. »

**[FAIRE]** Afficher la page `/login`. Montrer le design.

**[DIRE]** « L'authentification repose sur des jetons JWT signés, des mots de passe hachés en bcrypt, et une protection anti-force-brute. Je vais volontairement me tromper de mot de passe. »

**[FAIRE]** Saisir `admin` / un mauvais mot de passe (≥ 6 caractères, ex. `mauvais`). → message clair **« Identifiants invalides »**.

**[DIRE]** « L'erreur est explicite et ne révèle pas si c'est l'utilisateur ou le mot de passe qui est faux — bonne pratique de sécurité. Après cinq tentatives par minute, l'IP est bloquée. Je me connecte correctement. »

**[FAIRE]** `admin` / `osint2024` → **Se connecter**. Arrivée sur le dashboard.

---

### ⏱️ 2:00 – 4:00 — Dashboard & indicateurs

**[DIRE]** « Voici le poste de travail de l'analyste. En haut, les indicateurs clés : nombre de recherches, de personnes identifiées, de corrélations établies. »

**[FAIRE]** Pointer les KPI. **[FAIRE]** Descendre sur la liste des personnes et sur l'historique des recherches.

**[DIRE]** « Chaque personne dispose d'un dossier consolidé, chaque recherche est historisée et rejouable. Remarquez les boutons "+" : ils permettent de rattacher n'importe quelle entité à une enquête — j'y reviens. Toute la navigation — dashboard, graphe, enquêtes, énumération de pseudos, webcam — est accessible depuis la barre latérale. »

---

### ⏱️ 4:00 – 6:00 — Recherche OSINT en direct

**[DIRE]** « Lançons une recherche réelle. Je saisis un domaine et la plateforme interroge en parallèle une quinzaine de sources publiques : WHOIS, RDAP, DNS, certificats crt.sh, Hunter, Shodan, VirusTotal, GitHub… »

**[FAIRE]** Dans la section recherche : type **domaine**, saisir la cible live pré-testée (voir *Données à préparer*). Cliquer **Rechercher**.

**[DIRE]** « La recherche s'exécute en tâche de fond, en asynchrone — l'interface reste réactive et interroge le serveur jusqu'à la fin. »

**[FAIRE]** Attendre le **toast « Recherche … terminée »**. Cliquer **Voir résultats** sur la ligne d'historique.

**[DIRE]** « Chaque source produit une carte avec les entités extraites — emails, sous-domaines, IP — sous forme de badges. Ces badges sont cliquables : un clic relance une recherche sur l'entité. En dessous, le payload brut JSON reste disponible pour la traçabilité, et tout est exportable. »

**[FAIRE]** Déplier un accordéon **JSON**, cliquer un **badge** (retour dashboard avec recherche relancée), puis **Exporter JSON**.

---

### ⏱️ 6:00 – 8:00 — Reconnaissance faciale

**[DIRE]** « L'OSINT ne se limite pas au texte. Nous intégrons de la reconnaissance faciale. Point important : le traitement de la webcam est **100 % côté navigateur** — l'image ne quitte jamais le poste, seul un vecteur mathématique anonyme est comparé. C'est du *privacy by design*, essentiel pour des données biométriques relevant de l'article 9 du RGPD. »

**[FAIRE]** Aller sur **/stream**. Démarrer la webcam (autoriser la caméra). Montrer l'overlay de détection en temps réel et le compteur.

> ⚠️ **Pas de webcam ?** La page affiche un message propre. Repli : sur le dashboard, **glisser-déposer une photo de visage** → « Visage détecté / nouvelle personne créée ».

**[DIRE]** « Un visage reconnu peut être relié à un dossier OSINT existant : c'est la corrélation faciale → renseignement, que nous voyons maintenant sur un dossier déjà constitué. »

---

### ⏱️ 8:00 – 10:00 — Corrélations & dossier consolidé

**[DIRE]** « Voici le dossier de Camille Laurent, fondatrice présumée de notre cible. »

**[FAIRE]** Ouvrir le **rapport** de Camille Laurent (bouton depuis la liste des personnes).

**[DIRE]** « En haut, l'identité et un score de confiance. Le mini-graphe relie la personne à ses emails et à ses sites. Les quatre onglets détaillent emails, sites, corrélations et chronologie. Chaque corrélation porte un score et l'IP partagée qui a permis le recoupement — par exemple ici, deux services hébergés sur la même IP. »

**[FAIRE]** Parcourir les 4 onglets. Montrer une corrélation avec son score et son IP.

**[DIRE]** « Détail qui compte : les emails et les notes sont **chiffrés au repos** en base. Ce que vous voyez est déchiffré à la volée pour l'analyste autorisé. »

---

### ⏱️ 10:00 – 12:30 — Énumération de pseudos *(le moment fort)*

**[DIRE]** « Passons à la fonctionnalité la plus spectaculaire : à partir d'un simple pseudo, la plateforme vérifie sa présence sur **157 plateformes** — réseaux sociaux, forums, dépôts de code, crypto… — le tout en temps réel. »

**[FAIRE]** Aller sur **/username**. Saisir le pseudo pré-testé (voir *Données à préparer*, ex. `torvalds`). Lancer.

**[DIRE]** « Chaque case s'illumine au fur et à mesure des vérifications HTTP réelles, en parallèle. Le vert signale un profil trouvé. »

**[FAIRE]** Laisser la grille se remplir (~15-30 s). Utiliser les **filtres par catégorie**. Pointer quelques profils trouvés.

**[DIRE]** « En quelques secondes, on obtient l'empreinte numérique d'un pseudo sur tout le web. Les profils trouvés peuvent alimenter une enquête et sont exportables. »

**[FAIRE]** Cliquer **Exporter JSON**.

---

### ⏱️ 12:30 – 14:00 — Graphe de corrélations & enquête

**[DIRE]** « Toutes ces données prennent leur sens dans le graphe global : personnes, emails, sites et leurs liens. »

**[FAIRE]** Aller sur **/graph**. Montrer les nœuds, jouer avec les **filtres** (personnes / emails / sites). Cliquer un nœud personne → bascule sur son rapport.

**[DIRE]** « Et tout ce travail se capitalise dans une enquête. Voici l'enquête déjà constituée sur demo-osint.fr, qui regroupe les trois personnes et les six recherches. »

**[FAIRE]** Aller sur **/investigations** → ouvrir **« Enquête démo — demo-osint.fr »**. Montrer l'onglet **Entités** et l'onglet **Timeline**.

---

### ⏱️ 14:00 – 15:00 — Export PDF forensique

**[DIRE]** « Le livrable final : un rapport PDF forensique. Il intègre l'identité des entités, la chronologie horodatée en UTC, la chaîne d'audit complète — la *chain of custody* — et une empreinte SHA-256 garantissant l'intégrité. C'est un document opposable. »

**[FAIRE]** Depuis le détail de l'enquête : **Exporter PDF** → ouvrir le PDF téléchargé, faire défiler (entités, timeline, section intégrité SHA-256).

**[DIRE]** « De la collecte multi-sources à un rapport juridiquement exploitable, en respectant le RGPD à chaque étape. Merci, nous sommes à votre disposition pour vos questions. »

---

## 📦 Données à préparer avant la démo (≈ 30 min avant)

1. **Base pristine** — repartir d'un jeu de données propre et riche :
   ```bash
   echo y | python scripts/reset_demo.py && python scripts/seed_demo.py
   ```
   Vous obtenez : 3 personnes, 6 emails, 6 sites, 5 corrélations, 6 recherches (21 résultats), **1 enquête pré-remplie** et 12 logs d'audit.

2. **Choisir le pseudo à énumérer** — testez 2-3 handles pour retenir le plus riche visuellement. `torvalds` donne de bons résultats (GitHub, Codeberg, Hashnode…). Lancez-le **une fois à blanc** pour vérifier le réseau *(rappel : 1 énumération / 30 s par utilisateur)*.

3. **Choisir la recherche OSINT “live”** — préférez un domaine **fiable et rapide** (ex. le domaine de l'école, ou `github.com`). Lancez-le une fois avant pour confirmer que les clés API et le réseau répondent. En repli, l'historique seedé (`demo-osint.fr`) offre déjà des résultats riches.

4. **Reconnaissance faciale** — préparez **1 à 2 photos de visage nettes** (JPG/PNG) et **testez la webcam** dans le navigateur (autorisation caméra accordée).
   > ⚠️ Les embeddings du seed sont générés aléatoirement : une photo réelle **ne correspondra pas** à Camille/Julien/Sofia. Ne promettez pas un “match” avec une personne seedée — montrez la **détection** et la **création d'une nouvelle personne**, puis basculez sur un dossier existant pour les corrélations.

5. **Recherche globale full-text (optionnel)** — nécessite Elasticsearch :
   ```bash
   docker compose up -d elasticsearch && python scripts/reindex.py
   ```
   Sans ES, la barre de recherche globale affiche proprement « service indisponible » (dégradation maîtrisée — c'est un argument, pas un bug).

6. **Échauffement** — connectez-vous, ouvrez chaque page une fois (amorce les caches, vérifie le rendu), puis déconnectez-vous pour repartir propre.

---

## ❓ 10 questions probables du jury + réponses

**1. Comment respectez-vous le RGPD alors que vous collectez des données personnelles ?**
Trois axes : (a) *minimisation* — uniquement des sources publiques, robots.txt respecté, réseaux sociaux exclus du scraping ; (b) *sécurité* — chiffrement au repos des données sensibles (emails, notes) et RBAC ; (c) *transparence* — chaîne d'audit horodatée de toutes les actions. Les données de démo sont 100 % fictives (emails `@demo-*.fr`, IP en plages de test RFC 5737). Aucune donnée n'est partagée avec des tiers, tout reste en base locale.

**2. Le chiffrement au repos : comment ça marche, et comment cherche-t-on dans du chiffré ?**
On utilise **pgcrypto** de PostgreSQL : les colonnes sensibles sont chiffrées via `pgp_sym_encrypt` avec une clé symétrique hors dépôt (variable d'environnement). Pour pouvoir chercher malgré le chiffrement, on maintient un **index aveugle** (*blind index*) : un hash SHA-256 de la valeur, non réversible, sert de clé de recherche et de contrainte d'unicité. On peut donc retrouver un email exact sans jamais le stocker en clair.

**3. Reconnaissance faciale et article 9 du RGPD (données biométriques) — comment vous positionnez-vous ?**
La biométrie est une donnée sensible (article 9). Notre choix d'architecture répond directement au risque : le flux webcam est traité **entièrement côté navigateur** (`getUserMedia` + canvas), **aucune image n'est envoyée au serveur** — seul un vecteur numérique de 128 dimensions, anonyme, est comparé. La page affiche un avertissement RGPD explicite. En contexte réel, l'usage supposerait une base légale (consentement ou mission d'intérêt public) ; nous l'avons conçu pour minimiser l'exposition par défaut.

**4. Vous interrogez Have I Been Pwned : n'envoyez-vous pas l'email en clair à un tiers ?**
Non, on applique le principe de **k-anonymity** : on calcule `SHA1(email)` et on n'envoie que les **5 premiers caractères** du hash à l'API `range/`. HIBP renvoie tous les hashes partageant ce préfixe, et la comparaison finale se fait **localement**. L'email complet, ni même son hash complet, ne quitte jamais la plateforme.

**5. Qu'est-ce qui rend votre rapport PDF « forensique » et opposable ?**
Trois garanties : la **chaîne d'audit** de l'enquête est reproduite dans le rapport (toutes les actions loggées, horodatées en **UTC ISO 8601**) → *chain of custody* de bout en bout ; une empreinte **SHA-256** scelle l'intégrité du contenu ; et le journal d'audit est **chaîné** (chaque entrée liée à la précédente), ce qui rend toute altération détectable. Le PDF est généré en mémoire avec reportlab, sans écriture disque intermédiaire.

**6. Comment sécurisez-vous l'API ?**
Défense en profondeur : authentification **JWT** (HS256, expiration courte 15 min), autorisation par rôle (**RBAC**, endpoints admin protégés), **triple rate-limiting** (5 tentatives de login/min par IP, 1 énumération de pseudo/30 s, plafond global de requêtes), **5 en-têtes de sécurité** sur chaque réponse (Content-Security-Policy, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, X-XSS-Protection), et **CORS** restreint aux origines autorisées.

**7. L'énumération de pseudos, n'est-ce pas un outil de *stalking* ?**
C'est un outil d'analyse à **double usage**, comme Sherlock ou Maigret dont il s'inspire. Il n'exploite que des informations **publiques** (l'existence ou non d'une page de profil), sans authentification ni contournement. Son usage légitime relève d'audits de sécurité, d'investigations autorisées ou de la vérification de sa propre empreinte numérique. Les garde-fous (rate-limiting, audit, RBAC) et la conformité RGPD encadrent l'usage.

**8. Pourquoi une architecture asynchrone, et est-ce que ça passe à l'échelle ?**
L'OSINT est massivement *I/O-bound* : on attend des dizaines de services externes. Une stack **async de bout en bout** (FastAPI + `asyncpg` + `httpx`) permet d'interroger toutes les sources — et les 157 plateformes de l'énumération — **en parallèle** sans bloquer. Résultat : une énumération complète en secondes au lieu de minutes. La persistance passe par SQLAlchemy 2.0 async et des migrations Alembic ; le déploiement est conteneurisé (Docker Compose : PostgreSQL, Redis, Elasticsearch).

**9. Que se passe-t-il si une source OSINT tombe ou vous bloque ?**
**Dégradation propre** systématique : chaque source est isolée, une erreur ou un timeout sur l'une n'interrompt pas les autres (timeout réseau configurable). Elasticsearch et Redis sont **optionnels** — sans eux, l'application démarre et fonctionne, la recherche full-text signalant simplement son indisponibilité. Cette résilience est explicitement couverte par les tests.

**10. Parlez-nous des tests et de la qualité.**
**293 tests** passants (+ 8 dédiés au chiffrement, exécutés sur PostgreSQL réel). Ils couvrent chaque module : recherche/corrélation OSINT, API et sécurité (login, rate-limit, RBAC, en-têtes), reconnaissance faciale, chiffrement pgcrypto, HIBP/LeakCheck (dont la garantie *k-anonymity*), Elasticsearch, enquêtes et export PDF, énumération de pseudos. La suite tourne en ~85 s. S'y ajoutent les migrations versionnées et une CI locale reproductible.

> 💡 **Bonus — « En quoi êtes-vous différents de Maltego / theHarvester / Sherlock ? »** : nous réunissons dans **une seule plateforme** la collecte multi-sources, la reconnaissance faciale, la corrélation automatique, la gestion d'enquêtes et le **rapport forensique opposable** — le tout avec chiffrement au repos et conformité RGPD *by design*. Ce sont généralement des outils séparés.

---

## 🌟 Points techniques à mettre en avant

- **Async de bout en bout** (FastAPI + asyncpg + httpx) → parallélisation réelle des sources et des 157 plateformes.
- **Chiffrement au repos avec index aveugle** → recherche exacte sur données chiffrées (pgcrypto + SHA-256).
- **k-anonymity HIBP** → l'email n'est jamais transmis, même haché en entier.
- **Reconnaissance faciale 100 % côté client** → aucune image biométrique sur le serveur (*privacy by design*, article 9 RGPD).
- **Rapport PDF forensique** → chain of custody, horodatage UTC, SHA-256, audit chaîné → **opposable**.
- **Résilience & dégradation propre** → ES/Redis optionnels, isolation des sources, timeouts.
- **Sécurité multi-niveaux** → JWT, RBAC, triple rate-limiting, 5 en-têtes, CORS restreint, bcrypt.
- **Ingénierie** → 293 tests, migrations Alembic, Docker Compose, corrélation automatique facial → OSINT.
- **Couverture fonctionnelle** → des modules 1 à 15, au-delà du cahier des charges (enquêtes + énumération de pseudos).

---

## ✅ Checklist pré-démo (étape par étape)

**Infrastructure**
- [ ] PostgreSQL démarré (`pg_isready` OK)
- [ ] *(optionnel)* Elasticsearch démarré + `python scripts/reindex.py` pour la recherche globale
- [ ] Base réinitialisée + seedée (données pristine + enquête pré-remplie)
- [ ] `./scripts/start_demo.sh` lancé, `http://localhost:8000/login` accessible
- [ ] `curl http://localhost:8000/health` → 200

**Fonctionnel**
- [ ] Connexion testée (`admin` / `osint2024`)
- [ ] Chaque page ouverte une fois (rendu OK, caches amorcés)
- [ ] Énumération de pseudo testée avec le handle retenu (hits + réseau OK)
- [ ] Recherche OSINT “live” testée sur la cible fiable choisie
- [ ] Photos de visage prêtes **et/ou** webcam autorisée et testée
- [ ] Export PDF de l'enquête vérifié (s'ouvre correctement)

**Confort de présentation**
- [ ] Connexion réseau stable vérifiée (les sources OSINT en dépendent)
- [ ] Navigateur en plein écran, zoom lisible de loin, onglets inutiles fermés
- [ ] Fenêtre large (≥ 1200 px) — le responsive est validé jusqu'à 900 px mais confort en grand
- [ ] Notifications système coupées, apps gourmandes fermées
- [ ] **Plan B** : captures d'écran / courte vidéo de secours si le réseau tombe
- [ ] Code poussé sur le remote (`git push origin main` à jour)

---

## 🛠️ Commandes utiles le jour J

```bash
# Lancement fiable (check PG → migrations → serveur avec reload → URL)
./scripts/start_demo.sh

# Réinitialiser + recharger les données de démo (base pristine)
echo y | python scripts/reset_demo.py && python scripts/seed_demo.py

# Recherche globale : démarrer Elasticsearch puis réindexer
docker compose up -d elasticsearch && python scripts/reindex.py

# Vérifier que le serveur répond
curl -s http://localhost:8000/health

# Récupérer un jeton (dépannage / test API à la main)
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"osint2024"}'

# Rejouer toute la suite de tests (rassurer le jury en direct si besoin)
python -m pytest -q

# Suivre les logs applicatifs pendant la démo
tail -f logs/*.log
```

> ⚠️ **Piège classique** : si vous modifiez du code juste avant la démo, relancez le serveur (`start_demo.sh` utilise `--reload`, mais un serveur lancé « à la main » sans `--reload` **ne prend pas** les changements — c'est exactement le problème repéré pendant l'audit, où d'anciennes routes renvoyaient des 404).
