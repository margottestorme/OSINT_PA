"""
Client Elasticsearch (Module 11) — couche d'indexation full-text OPTIONNELLE.

Elasticsearch vient EN COMPLÉMENT de PostgreSQL (qui reste la source de vérité) :
il ne sert qu'à la recherche full-text multi-critères rapide. L'application doit
fonctionner normalement si ES est indisponible — c'est le principe de *dégradation
propre* : toutes les méthodes échouent en silence (retour `False`/`None` + log
`warning`), jamais d'exception propagée vers les routes.

Confidentialité : AUCUNE donnée biométrique n'est indexée (pas d'embedding facial,
pas de hash d'image) — uniquement des métadonnées publiques recherchables. Les
mappings sont `strict` : tout champ non déclaré est rejeté à l'indexation, ce qui
constitue un garde-fou contre l'indexation accidentelle de données sensibles.
"""
from elasticsearch import AsyncElasticsearch
from loguru import logger

from app.core.config import get_settings

# --- Noms des index (préfixe osint_ pour l'isolation) ---
INDEX_PERSONS = "osint_persons"
INDEX_SEARCH_RESULTS = "osint_search_results"
INDEX_EMAILS = "osint_emails"
INDEX_SITES = "osint_sites"

ALL_INDICES = (INDEX_PERSONS, INDEX_SEARCH_RESULTS, INDEX_EMAILS, INDEX_SITES)

# Champ recherchable : text (analysé, full-text) + sous-champ keyword (exact/tri/agrégation).
_TEXT_KEYWORD = {
    "type": "text",
    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
}

# Mappings STRICTS ("dynamic": "strict") : keyword pour les identifiants et valeurs
# exactes, text+keyword pour les champs recherchables. Un champ non déclaré (ex : un
# embedding qui se glisserait dans un document) fait échouer l'indexation → sécurité.
INDEX_MAPPINGS: dict[str, dict] = {
    # osint_persons — jamais d'embedding ni d'image_hash (données biométriques exclues)
    INDEX_PERSONS: {
        "dynamic": "strict",
        "properties": {
            "id": {"type": "keyword"},
            "first_name": _TEXT_KEYWORD,
            "last_name": _TEXT_KEYWORD,
            "notes": {"type": "text"},
            "confidence_score": {"type": "float"},
            # "investigation" ou "lfw_dataset" (jeu de données académique public) — permet à
            # l'UI de recherche full-text de distinguer les deux, jamais confondues.
            "source": {"type": "keyword"},
            "created_at": {"type": "date"},
        },
    },
    # osint_search_results — calqué sur le format OsintResult (hors payload brut `data`)
    INDEX_SEARCH_RESULTS: {
        "dynamic": "strict",
        "properties": {
            "search_id": {"type": "keyword"},
            "source": {"type": "keyword"},
            "entity": _TEXT_KEYWORD,
            "entity_type": {"type": "keyword"},
            "extracted": {
                "type": "nested",
                "properties": {
                    "emails": {"type": "keyword"},
                    "domains": {"type": "keyword"},
                    "urls": {"type": "keyword"},
                    "ips": {"type": "keyword"},
                    "names": _TEXT_KEYWORD,
                },
            },
            "created_at": {"type": "date"},
        },
    },
    INDEX_EMAILS: {
        "dynamic": "strict",
        "properties": {
            "email": _TEXT_KEYWORD,
            "source": {"type": "keyword"},
            "person_id": {"type": "keyword"},
            "created_at": {"type": "date"},
        },
    },
    INDEX_SITES: {
        "dynamic": "strict",
        "properties": {
            "url": _TEXT_KEYWORD,
            "server": _TEXT_KEYWORD,
            "created_at": {"type": "date"},  # alimenté depuis Site.discovered_at (cf. indexer)
        },
    },
}


class ElasticClient:
    """Enveloppe minimale et tolérante aux pannes autour d'AsyncElasticsearch.

    Instance unique partagée (`elastic` ci-dessous), branchée sur le cycle de vie
    FastAPI : connect() au démarrage, disconnect() à l'arrêt.
    """

    def __init__(self) -> None:
        self._client: AsyncElasticsearch | None = None

    async def connect(self) -> None:
        """Initialise le client AsyncElasticsearch (paresseux : aucune I/O réseau ici).

        Idempotent : un second appel ne recrée pas le client.
        """
        if self._client is not None:
            return
        url = get_settings().ELASTICSEARCH_URL
        self._client = AsyncElasticsearch(
            hosts=[url],
            request_timeout=5,       # court : ne jamais bloquer une requête HTTP de l'app
            max_retries=1,
            retry_on_timeout=False,
        )
        logger.info(f"[elastic] Client initialisé (hosts={url})")

    async def disconnect(self) -> None:
        """Ferme proprement la connexion et libère le client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("[elastic] Client fermé")

    @property
    def client(self) -> AsyncElasticsearch | None:
        """Accès au client brut (None tant que connect() n'a pas été appelé)."""
        return self._client

    async def is_available(self) -> bool:
        """True si ES répond au ping. Ne lève jamais (dégradation propre)."""
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception as e:
            logger.warning(f"[elastic] ES indisponible (ping) : {e}")
            return False

    async def create_indices(self) -> None:
        """Crée les index manquants (idempotent), et complète le mapping de ceux déjà
        présents avec les champs manquants (ex : ajout de `source` sur `osint_persons` —
        Module 15bis). N'écrase jamais un index existant, ni un champ déjà déclaré :
        `put_mapping` sur un index `dynamic: strict` n'autorise que l'AJOUT de nouveaux
        champs, jamais la modification d'un champ existant (ce qui échouerait de toute
        façon côté ES) — sûr à rejouer à chaque démarrage.

        Sans ES disponible : ne fait rien (log warning) — l'app démarre quand même.
        """
        if not await self.is_available():
            logger.warning("[elastic] create_indices ignoré : ES indisponible")
            return
        for name, mappings in INDEX_MAPPINGS.items():
            try:
                if await self._client.indices.exists(index=name):
                    await self._client.indices.put_mapping(
                        index=name, properties=mappings["properties"]
                    )
                    logger.debug(f"[elastic] Index déjà présent (mapping vérifié) : {name}")
                    continue
                await self._client.indices.create(index=name, mappings=mappings)
                logger.info(f"[elastic] Index créé : {name}")
            except Exception as e:
                logger.warning(f"[elastic] Échec création/mise à jour index {name} : {e}")


# Instance unique partagée par l'application (comme les rate limiters du projet).
elastic = ElasticClient()
