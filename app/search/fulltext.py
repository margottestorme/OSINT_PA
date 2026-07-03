"""
Recherche full-text Elasticsearch (Module 11) — lecture multi-index avec highlight.

Interroge un ou plusieurs index (selon le `scope`), fusionne les résultats, trie par
score décroissant et pagine. Tolérant aux pannes : si ES est indisponible, renvoie un
résultat vide (`{total: 0, hits: []}`) sans jamais lever — l'endpoint reste 200
(dégradation propre).

Chaque index est interrogé séparément avec une requête adaptée à ses champs : cela
évite les conflits de champs entre index et permet la clause `nested` sur `extracted`
(propre à osint_search_results) sans casser les autres index.
"""
from loguru import logger

from app.search.elastic import (
    INDEX_EMAILS,
    INDEX_PERSONS,
    INDEX_SEARCH_RESULTS,
    INDEX_SITES,
    elastic,
)

# scope (paramètre `type` de l'API) → index ES interrogés
_SCOPE_INDICES = {
    "persons": [INDEX_PERSONS],
    "results": [INDEX_SEARCH_RESULTS],
    "emails": [INDEX_EMAILS],
    "sites": [INDEX_SITES],
    "all": [INDEX_PERSONS, INDEX_SEARCH_RESULTS, INDEX_EMAILS, INDEX_SITES],
}

# Champs texte analysés interrogés (et surlignés) par index.
_INDEX_FIELDS = {
    INDEX_PERSONS: ["first_name", "last_name", "notes"],
    INDEX_SEARCH_RESULTS: ["entity"],
    INDEX_EMAILS: ["email"],
    INDEX_SITES: ["url", "server"],
}

# Sous-champs nested `extracted` interrogés en complément pour osint_search_results.
_EXTRACTED_FIELDS = [
    "extracted.emails", "extracted.domains", "extracted.urls",
    "extracted.ips", "extracted.names",
]

# Options de highlight : encoder HTML (le contenu est échappé côté ES → les fragments
# sont sûrs à rendre en innerHTML côté frontend, seules les balises <mark> subsistent)
# et balises <mark> (surlignage jaune) plutôt que les <em> par défaut.
_HIGHLIGHT_OPTS = {
    "encoder": "html",
    "pre_tags": ["<mark>"],
    "post_tags": ["</mark>"],
}


def _build_query(index: str, q: str) -> dict:
    """Construit la clause `query` adaptée à l'index (nested pour search_results)."""
    base = {"multi_match": {"query": q, "fields": _INDEX_FIELDS[index], "lenient": True}}
    if index != INDEX_SEARCH_RESULTS:
        return base
    # search_results : on cherche AUSSI dans le bloc nested `extracted` (emails,
    # domaines, urls, ips, noms découverts) via une clause nested + inner_hits.
    return {
        "bool": {
            "should": [
                base,
                {
                    "nested": {
                        "path": "extracted",
                        "query": {"multi_match": {
                            "query": q, "fields": _EXTRACTED_FIELDS, "lenient": True,
                        }},
                        "inner_hits": {
                            "highlight": {**_HIGHLIGHT_OPTS,
                                          "fields": {f: {} for f in _EXTRACTED_FIELDS}}
                        },
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


def _merge_highlight(hit: dict) -> dict:
    """Fusionne les fragments highlight de premier niveau et ceux des inner_hits nested."""
    highlight = dict(hit.get("highlight", {}))
    for inner in hit.get("inner_hits", {}).values():
        for inner_hit in inner.get("hits", {}).get("hits", []):
            highlight.update(inner_hit.get("highlight", {}))
    return highlight


async def fulltext_search(q: str, scope: str, limit: int, offset: int) -> dict:
    """Recherche `q` sur les index du `scope`, fusionne/trie/pagine. Ne lève jamais.

    Renvoie `{"total": int, "hits": [{index, id, score, highlight, source}]}`.
    Sur chaque index on récupère `offset + limit` documents afin de paginer
    correctement après fusion (les scores ES ne sont pas comparables entre index,
    mais l'ordre reste cohérent pour un volume académique).
    """
    if elastic.client is None or not await elastic.is_available():
        logger.warning("[fulltext] Recherche ignorée : ES indisponible")
        return {"total": 0, "hits": []}

    indices = _SCOPE_INDICES.get(scope, _SCOPE_INDICES["all"])
    window = offset + limit

    total = 0
    merged: list[dict] = []
    for index in indices:
        try:
            resp = await elastic.client.search(
                index=index,
                query=_build_query(index, q),
                highlight={**_HIGHLIGHT_OPTS, "fields": {f: {} for f in _INDEX_FIELDS[index]}},
                from_=0,
                size=window,
            )
        except Exception as e:
            # Index absent / ES tombé en cours de route : on ignore cet index
            logger.warning(f"[fulltext] Échec recherche sur {index} : {e}")
            continue

        total += resp["hits"]["total"]["value"]
        for hit in resp["hits"]["hits"]:
            merged.append({
                "index": hit["_index"],
                "id": hit["_id"],
                "score": hit["_score"],
                "highlight": _merge_highlight(hit),
                "source": hit["_source"],
            })

    merged.sort(key=lambda h: h["score"] or 0.0, reverse=True)
    return {"total": total, "hits": merged[offset:offset + limit]}
