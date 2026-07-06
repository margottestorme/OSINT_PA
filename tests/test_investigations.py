"""Tests du Module 14 — gestion d'enquêtes + export PDF forensique.

Couvre le CRUD des enquêtes, le rattachement/retrait d'entités, la chronologie
forensique, l'export PDF et le RBAC (analyst vs admin, sans JWT, token expiré).
"""
from datetime import datetime, timezone

import pytest

from app.db.models import Correlation, Email, Person, SearchHistory, Site


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

async def _create_investigation(client, headers, title="Enquête test", description="desc"):
    resp = await client.post(
        "/api/investigations",
        json={"title": title, "description": description},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_person(first="Jean", last="Dupont", score=0.9, created_at=None):
    from tests.conftest import session_test
    async with session_test() as db:
        p = Person(first_name=first, last_name=last, confidence_score=score)
        if created_at:
            p.created_at = created_at
        db.add(p)
        await db.commit()
        return p.id


async def _seed_email(email="jean@example.com", source="test", person_id=None, created_at=None):
    from tests.conftest import session_test
    async with session_test() as db:
        e = Email(email=email, source=source, person_id=person_id)
        if created_at:
            e.created_at = created_at
        db.add(e)
        await db.commit()
        return e.id


async def _seed_site(url="https://example.com", server="nginx", discovered_at=None):
    from tests.conftest import session_test
    async with session_test() as db:
        s = Site(url=url, server=server)
        if discovered_at:
            s.discovered_at = discovered_at
        db.add(s)
        await db.commit()
        return s.id


async def _seed_search(query="jean@example.com", query_type="email", count=3):
    from tests.conftest import session_test
    async with session_test() as db:
        h = SearchHistory(query=query, query_type=query_type, result_count=count)
        db.add(h)
        await db.commit()
        return h.id


# --------------------------------------------------------------------------- #
# CRUD investigations                                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_create_investigation_admin(client, auth_headers):
    data = await _create_investigation(client, auth_headers, title="Affaire A")
    assert data["title"] == "Affaire A"
    assert data["status"] == "open"
    assert "created_at" in data and "updated_at" in data


@pytest.mark.asyncio
async def test_create_investigation_analyst(client, analyst_headers):
    data = await _create_investigation(client, analyst_headers, title="Affaire Analyst")
    assert data["title"] == "Affaire Analyst"
    assert data["status"] == "open"


@pytest.mark.asyncio
async def test_create_investigation_requires_title(client, auth_headers):
    resp = await client.post("/api/investigations", json={"description": "sans titre"}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_investigations_with_status_filter(client, auth_headers):
    a = await _create_investigation(client, auth_headers, title="Ouverte")
    b = await _create_investigation(client, auth_headers, title="À fermer")
    # Ferme la seconde
    await client.patch(f"/api/investigations/{b['id']}", json={"status": "closed"}, headers=auth_headers)

    # Sans filtre : les deux
    resp = await client.get("/api/investigations", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # Filtre open
    resp = await client.get("/api/investigations?status=open", headers=auth_headers)
    ids = [i["id"] for i in resp.json()]
    assert a["id"] in ids and b["id"] not in ids

    # Filtre closed
    resp = await client.get("/api/investigations?status=closed", headers=auth_headers)
    ids = [i["id"] for i in resp.json()]
    assert b["id"] in ids and a["id"] not in ids


@pytest.mark.asyncio
async def test_list_investigations_entity_count(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    pid = await _seed_person()
    await client.post(f"/api/investigations/{inv['id']}/entities",
                      json={"entity_type": "person", "entity_id": pid}, headers=auth_headers)

    resp = await client.get("/api/investigations", headers=auth_headers)
    item = next(i for i in resp.json() if i["id"] == inv["id"])
    assert item["entity_count"] == 1


@pytest.mark.asyncio
async def test_get_investigation_detail_with_entities(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    pid = await _seed_person()
    eid = await _seed_email()
    await client.post(f"/api/investigations/{inv['id']}/entities",
                      json={"entity_type": "person", "entity_id": pid, "notes": "suspect"}, headers=auth_headers)
    await client.post(f"/api/investigations/{inv['id']}/entities",
                      json={"entity_type": "email", "entity_id": eid}, headers=auth_headers)

    resp = await client.get(f"/api/investigations/{inv['id']}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entities"]) == 2
    types = {e["entity_type"] for e in data["entities"]}
    assert types == {"person", "email"}
    person_entity = next(e for e in data["entities"] if e["entity_type"] == "person")
    assert person_entity["notes"] == "suspect"


@pytest.mark.asyncio
async def test_get_investigation_not_found(client, auth_headers):
    resp = await client.get("/api/investigations/9999", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_investigation(client, auth_headers):
    inv = await _create_investigation(client, auth_headers, title="Ancien", description="ancienne desc")
    resp = await client.patch(
        f"/api/investigations/{inv['id']}",
        json={"title": "Nouveau", "description": "nouvelle desc", "status": "closed"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Nouveau"
    assert data["description"] == "nouvelle desc"
    assert data["status"] == "closed"


@pytest.mark.asyncio
async def test_patch_investigation_partial(client, auth_headers):
    inv = await _create_investigation(client, auth_headers, title="Titre", description="desc")
    # Ne change que le statut : titre/description conservés
    resp = await client.patch(f"/api/investigations/{inv['id']}", json={"status": "archived"}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "archived"
    assert data["title"] == "Titre"


@pytest.mark.asyncio
async def test_delete_investigation_admin_soft_deletes(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    resp = await client.delete(f"/api/investigations/{inv['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    # Toujours consultable (soft delete), statut archived
    detail = await client.get(f"/api/investigations/{inv['id']}", headers=auth_headers)
    assert detail.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_delete_investigation_analyst_forbidden(client, auth_headers, analyst_headers):
    inv = await _create_investigation(client, auth_headers)
    resp = await client.delete(f"/api/investigations/{inv['id']}", headers=analyst_headers)
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Entités rattachées                                                          #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_add_existing_entity(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    pid = await _seed_person()
    resp = await client.post(
        f"/api/investigations/{inv['id']}/entities",
        json={"entity_type": "person", "entity_id": pid, "notes": "pertinent"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["entity_type"] == "person"
    assert data["entity_id"] == pid
    assert data["notes"] == "pertinent"


@pytest.mark.asyncio
async def test_add_nonexistent_entity_404(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    resp = await client.post(
        f"/api/investigations/{inv['id']}/entities",
        json={"entity_type": "person", "entity_id": 99999},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_entity_to_nonexistent_investigation_404(client, auth_headers):
    pid = await _seed_person()
    resp = await client.post(
        "/api/investigations/9999/entities",
        json={"entity_type": "person", "entity_id": pid},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_remove_entity(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    pid = await _seed_person()
    add = await client.post(f"/api/investigations/{inv['id']}/entities",
                            json={"entity_type": "person", "entity_id": pid}, headers=auth_headers)
    link_id = add.json()["id"]

    resp = await client.delete(f"/api/investigations/{inv['id']}/entities/{link_id}", headers=auth_headers)
    assert resp.status_code == 200

    # L'entité n'est plus rattachée
    detail = await client.get(f"/api/investigations/{inv['id']}", headers=auth_headers)
    assert detail.json()["entities"] == []


@pytest.mark.asyncio
async def test_remove_nonexistent_entity_404(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    resp = await client.delete(f"/api/investigations/{inv['id']}/entities/99999", headers=auth_headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Timeline forensique                                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_timeline_sorted_and_multi_type(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)

    # Dates de découverte distinctes et volontairement dé-ordonnées à l'insertion
    d_site = datetime(2020, 1, 1, tzinfo=timezone.utc)
    d_email = datetime(2021, 1, 1, tzinfo=timezone.utc)
    d_person = datetime(2022, 1, 1, tzinfo=timezone.utc)

    pid = await _seed_person(created_at=d_person)
    eid = await _seed_email(created_at=d_email)
    sid = await _seed_site(discovered_at=d_site)

    # Rattache dans un ordre différent de l'ordre chronologique
    for et, eid_ in (("person", pid), ("email", eid), ("site", sid)):
        await client.post(f"/api/investigations/{inv['id']}/entities",
                          json={"entity_type": et, "entity_id": eid_}, headers=auth_headers)

    resp = await client.get(f"/api/investigations/{inv['id']}/timeline", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3

    # Types différents tous présents
    types = {it["entity_type"] for it in data["items"]}
    assert types == {"person", "email", "site"}

    # Trié chronologiquement par date de découverte croissante : site → email → person
    order = [it["entity_type"] for it in data["items"]]
    assert order == ["site", "email", "person"]

    # Les libellés résolus sont exposés (nom/email/url, pas juste l'id)
    labels = {it["entity_type"]: it["label"] for it in data["items"]}
    assert labels["person"] == "Jean Dupont"
    assert labels["email"] == "jean@example.com"
    assert labels["site"] == "https://example.com"


@pytest.mark.asyncio
async def test_timeline_empty(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    resp = await client.get(f"/api/investigations/{inv['id']}/timeline", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# --------------------------------------------------------------------------- #
# Export PDF forensique                                                       #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_export_pdf(client, auth_headers):
    inv = await _create_investigation(client, auth_headers, title="Rapport test")

    # Une entité de chaque type pour couvrir toutes les sections du PDF
    pid = await _seed_person()
    eid = await _seed_email()
    sid = await _seed_site()
    hid = await _seed_search()
    from tests.conftest import session_test
    async with session_test() as db:
        corr = Correlation(person_id=pid, email_id=eid, site_id=sid, score=0.9, data={"ips": ["1.2.3.4"]})
        db.add(corr)
        await db.commit()
        cid = corr.id

    for et, eid_ in (("person", pid), ("email", eid), ("site", sid), ("search", hid), ("correlation", cid)):
        await client.post(f"/api/investigations/{inv['id']}/entities",
                          json={"entity_type": et, "entity_id": eid_}, headers=auth_headers)

    resp = await client.get(f"/api/investigations/{inv['id']}/export/pdf", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    body = resp.content
    assert body[:5] == b"%PDF-"

    sha = resp.headers.get("x-content-sha256")
    assert sha is not None
    assert len(sha) == 64
    int(sha, 16)  # doit être une chaîne hexadécimale valide

    # Le hash imprimé doit être retrouvable dans les octets du PDF (pageCompression=0)
    assert sha[:32].encode() in body and sha[32:].encode() in body


@pytest.mark.asyncio
async def test_export_pdf_empty_investigation(client, auth_headers):
    inv = await _create_investigation(client, auth_headers)
    resp = await client.get(f"/api/investigations/{inv['id']}/export/pdf", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_export_pdf_not_found_404(client, auth_headers):
    resp = await client.get("/api/investigations/9999/export/pdf", headers=auth_headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Permissions / RBAC                                                          #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_no_jwt_unauthorized(client):
    resp = await client.get("/api/investigations")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_analyst_can_create_and_modify(client, analyst_headers):
    inv = await _create_investigation(client, analyst_headers, title="Par analyst")
    # Modification
    resp = await client.patch(f"/api/investigations/{inv['id']}",
                              json={"title": "Modifié par analyst"}, headers=analyst_headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Modifié par analyst"
    # Ajout d'entité
    pid = await _seed_person()
    add = await client.post(f"/api/investigations/{inv['id']}/entities",
                            json={"entity_type": "person", "entity_id": pid}, headers=analyst_headers)
    assert add.status_code == 201


@pytest.mark.asyncio
async def test_analyst_cannot_delete(client, auth_headers, analyst_headers):
    inv = await _create_investigation(client, auth_headers)
    resp = await client.delete(f"/api/investigations/{inv['id']}", headers=analyst_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_expired_token_unauthorized(client, expired_headers):
    resp = await client.get("/api/investigations", headers=expired_headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_expired_token_unauthorized(client, expired_headers):
    resp = await client.post("/api/investigations", json={"title": "x"}, headers=expired_headers)
    assert resp.status_code == 401
