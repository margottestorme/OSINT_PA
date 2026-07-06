"""
Seed des données de démo — OSINT Platform (présentation ESGI).

Insère un jeu de données FICTIF mais cohérent (enquête OSINT sur la startup
fictive demo-osint.fr) : 3 personnes, 6 emails, 6 sites, 5 corrélations,
6 recherches + leurs SearchResults, et des logs d'audit.

- Idempotent : relançable sans doublon (get-or-create par clé unique).
- Données 100% fictives : emails @demo-*.fr, IPs en plages RFC 5737.
- Embeddings valides : np.random.seed(42), 128 floats dans [-1, 1].

Usage : python scripts/seed_demo.py   (après reset_demo.py pour une démo propre)
"""
import asyncio
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.db.session import async_session  # noqa: E402
from app.db.models import (  # noqa: E402
    AuditLog, Correlation, Email, Investigation, InvestigationEntity,
    Person, SearchHistory, SearchResult, Site, User,
)

GREEN, CYAN, YELLOW, BOLD, RESET = "\033[92m", "\033[96m", "\033[93m", "\033[1m", "\033[0m"

NOW = datetime.now(timezone.utc)


def ts(days_ago: float) -> datetime:
    """Timestamp échelonné (5 jours en arrière → maintenant)."""
    return NOW - timedelta(days=days_ago)


def ihash(n: int) -> str:
    """Hash d'image déterministe (clé d'idempotence des personnes)."""
    return hashlib.sha256(f"demo-person-{n}".encode()).hexdigest()


# --- Embeddings reproductibles (128 floats dans [-1, 1]) ---
np.random.seed(42)
EMBEDDINGS = [np.random.uniform(-1, 1, 128).round(6).tolist() for _ in range(3)]

# --- Personnes ---
PERSONS = [
    dict(first="Camille", last="Laurent", conf=0.92, days=5.0, hash=ihash(1),
         notes="Fondatrice presumee de demo-osint.fr. Recoupee via WHOIS + profil GitHub. Lien fort au domaine principal."),
    dict(first="Julien", last="Mercier", conf=0.85, days=4.6, hash=ihash(2),
         notes="Developpeur/CTO. Profil technique (sous-domaines dev/git). Lie a Camille via le domaine societe."),
    dict(first="Sofia", last="Nakamura", conf=0.78, days=4.2, hash=ihash(3),
         notes="Consultante externe. Lien plus faible : un seul recoupement via email dans le WHOIS de demo-consulting.fr."),
]

# --- Emails (person_idx = index dans PERSONS) ---
EMAILS = [
    dict(email="camille.laurent@demo-osint.fr", person=0, source="whois", days=5.0),
    dict(email="c.laurent@demo-mail.fr", person=0, source="hunter", days=3.1),
    dict(email="contact@demo-osint.fr", person=0, source="rdap", days=4.9),
    dict(email="julien.mercier@demo-osint.fr", person=1, source="crt.sh", days=4.6),
    dict(email="jmercier.dev@demo-mail.fr", person=1, source="hunter", days=2.4),
    dict(email="sofia.nakamura@demo-consulting.fr", person=2, source="whois", days=4.2),
]

# --- Sites ---
SITES = [
    dict(url="https://demo-osint.fr", server="nginx", days=5.0),
    dict(url="https://blog.demo-osint.fr", server="nginx", days=3.1),
    dict(url="https://dev.demo-osint.fr", server="Apache", days=4.6),
    dict(url="https://git.demo-osint.fr", server="gitlab", days=4.5),
    dict(url="https://demo-consulting.fr", server="cloudflare", days=4.2),
    dict(url="https://demo-mail.fr", server="postfix", days=2.4),
]

# --- Corrélations (person_idx, email, site_url, score, ips) ---
CORRELATIONS = [
    dict(person=0, email="camille.laurent@demo-osint.fr", site="https://demo-osint.fr", score=0.92, ips=["203.0.113.10"], days=5.0),
    dict(person=0, email="c.laurent@demo-mail.fr", site="https://blog.demo-osint.fr", score=0.80, ips=["203.0.113.42"], days=3.0),
    dict(person=1, email="julien.mercier@demo-osint.fr", site="https://dev.demo-osint.fr", score=0.88, ips=["198.51.100.7"], days=4.5),
    dict(person=1, email="jmercier.dev@demo-mail.fr", site="https://git.demo-osint.fr", score=0.75, ips=["198.51.100.7"], days=2.3),
    dict(person=2, email="sofia.nakamura@demo-consulting.fr", site="https://demo-consulting.fr", score=0.70, ips=["192.0.2.55"], days=4.1),
]

# --- Recherches + résultats (SearchResult) ---
SEARCHES = [
    dict(query="demo-osint.fr", type="domain", days=5.0, results=[
        dict(source="whois", entity="demo-osint.fr", type="domain",
             data={"registrar": "Demo Registrar SAS", "org": "Demo OSINT SAS", "country": "FR"},
             extracted={"emails": ["contact@demo-osint.fr"], "domains": ["ns1.demo-host.fr", "ns2.demo-host.fr"]}),
        dict(source="dns", entity="demo-osint.fr", type="domain",
             data={"A": ["203.0.113.10"], "NS": ["ns1.demo-host.fr", "ns2.demo-host.fr"], "SPF": "v=spf1 include:demo-mail.fr ~all", "DMARC": "v=DMARC1; p=quarantine"},
             extracted={"ips": ["203.0.113.10"], "domains": ["ns1.demo-host.fr", "ns2.demo-host.fr"]}),
        dict(source="rdap", entity="demo-osint.fr", type="domain",
             data={"ldhName": "demo-osint.fr", "status": ["active"]},
             extracted={"domains": ["ns1.demo-host.fr", "ns2.demo-host.fr"], "emails": ["abuse@demo-host.fr"]}),
        dict(source="crt.sh", entity="demo-osint.fr", type="domain",
             data={"count": 4, "subdomains": ["blog.demo-osint.fr", "demo-osint.fr", "dev.demo-osint.fr", "git.demo-osint.fr"]},
             extracted={"domains": ["blog.demo-osint.fr", "demo-osint.fr", "dev.demo-osint.fr", "git.demo-osint.fr"]}),
        dict(source="shodan_internetdb", entity="demo-osint.fr", type="domain",
             data={"203.0.113.10": {"ports": [80, 443], "vulns": [], "hostnames": ["demo-osint.fr", "www.demo-osint.fr"]}},
             extracted={"ips": ["203.0.113.10"], "domains": ["demo-osint.fr", "www.demo-osint.fr"]}),
        dict(source="virustotal", entity="demo-osint.fr", type="domain",
             data={"reputation": 0, "last_analysis_stats": {"malicious": 0, "harmless": 72}},
             extracted={"ips": ["203.0.113.10"], "domains": ["ns1.demo-host.fr"]}),
    ]),
    dict(query="camille.laurent@demo-osint.fr", type="email", days=4.8, results=[
        dict(source="dns_mx", entity="camille.laurent@demo-osint.fr", type="email",
             data={"domain": "demo-osint.fr", "mx_records": ["mail.demo-osint.fr"]},
             extracted={"domains": ["mail.demo-osint.fr"]}),
        dict(source="whois", entity="demo-osint.fr", type="email",
             data={"org": "Demo OSINT SAS"}, extracted={"emails": ["contact@demo-osint.fr"], "domains": ["ns1.demo-host.fr"]}),
        dict(source="gravatar", entity="camille.laurent@demo-osint.fr", type="email",
             data={"displayName": "Camille Laurent"},
             extracted={"names": ["Camille Laurent"], "urls": ["https://gravatar.com/demolaurent", "https://github.com/camille-laurent-demo"]}),
        dict(source="hunter", entity="camille.laurent@demo-osint.fr", type="email",
             data={"status": "valid", "result": "deliverable", "score": 88},
             extracted={"domains": ["demo-blog.fr"], "urls": ["https://demo-blog.fr/equipe"]}),
    ]),
    dict(query="Camille Laurent", type="name", days=4.7, results=[
        dict(source="duckduckgo", entity="Camille Laurent", type="name",
             data={"Type": "A", "RelatedTopics": [{"Text": "Camille Laurent — fondatrice de Demo OSINT SAS"}]}, extracted={}),
        dict(source="wikipedia", entity="Camille Laurent", type="name",
             data=[{"title": "Camille Laurent (informaticienne)"}],
             extracted={"names": ["Camille Laurent (informaticienne)"], "urls": ["https://fr.wikipedia.org/wiki/Camille_Laurent_(informaticienne)"]}),
        dict(source="github", entity="Camille Laurent", type="name",
             data=[{"login": "camille-laurent-demo", "html_url": "https://github.com/camille-laurent-demo"}],
             extracted={"names": ["camille-laurent-demo"], "urls": ["https://github.com/camille-laurent-demo"]}),
    ]),
    dict(query="demo-consulting.fr", type="domain", days=4.2, results=[
        dict(source="whois", entity="demo-consulting.fr", type="domain",
             data={"registrar": "Demo Registrar SAS", "org": "Demo Consulting"},
             extracted={"emails": ["sofia.nakamura@demo-consulting.fr"], "domains": ["ns1.demo-host.fr"]}),
        dict(source="dns", entity="demo-consulting.fr", type="domain",
             data={"A": ["192.0.2.55"], "SPF": "v=spf1 ~all"},
             extracted={"ips": ["192.0.2.55"], "domains": ["ns1.demo-host.fr"]}),
        dict(source="rdap", entity="demo-consulting.fr", type="domain",
             data={"ldhName": "demo-consulting.fr", "status": ["active"]},
             extracted={"domains": ["ns1.demo-host.fr"], "emails": ["sofia.nakamura@demo-consulting.fr"]}),
    ]),
    dict(query="julien.mercier@demo-osint.fr", type="email", days=3.4, results=[
        dict(source="dns_mx", entity="julien.mercier@demo-osint.fr", type="email",
             data={"mx_records": ["mail.demo-osint.fr"]}, extracted={"domains": ["mail.demo-osint.fr"]}),
        dict(source="whois", entity="demo-osint.fr", type="email",
             data={"org": "Demo OSINT SAS"}, extracted={"domains": ["ns1.demo-host.fr"]}),
        dict(source="hunter", entity="julien.mercier@demo-osint.fr", type="email",
             data={"status": "valid", "result": "deliverable", "score": 91},
             extracted={"domains": ["git.demo-osint.fr"], "urls": ["https://git.demo-osint.fr/jmercier"]}),
    ]),
    dict(query="Julien Mercier", type="name", days=2.4, results=[
        dict(source="duckduckgo", entity="Julien Mercier", type="name",
             data={"Type": "A", "RelatedTopics": [{"Text": "Julien Mercier — developpeur, Demo OSINT"}]}, extracted={}),
        dict(source="github", entity="Julien Mercier", type="name",
             data=[{"login": "jmercier-demo", "html_url": "https://github.com/jmercier-demo"}],
             extracted={"names": ["jmercier-demo"], "urls": ["https://github.com/jmercier-demo"]}),
    ]),
]

# --- Logs d'audit (action, target, days_ago) ---
AUDIT = (
    [dict(action="login_success", target="admin", days=5.1)]
    + [dict(action="face_upload", target=f"person_id={i + 1},is_new=true", days=p["days"]) for i, p in enumerate(PERSONS)]
    + [dict(action="search_launched", target=f"{s['type']}:{s['query']}", days=s["days"]) for s in SEARCHES]
    + [dict(action="report_viewed", target="1", days=1.2),
       dict(action="report_viewed", target="2", days=1.0)]
)


async def seed() -> dict:
    counts = dict(persons=0, emails=0, sites=0, correlations=0, searches=0, results=0,
                  investigations=0, inv_entities=0, audit=0)

    async with async_session() as db:
        admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
        admin_id = admin.id if admin else None

        # Personnes
        person_objs = []
        for i, p in enumerate(PERSONS):
            existing = (await db.execute(select(Person).where(Person.image_hash == p["hash"]))).scalar_one_or_none()
            if existing:
                person_objs.append(existing)
                continue
            obj = Person(first_name=p["first"], last_name=p["last"], confidence_score=p["conf"],
                         notes=p["notes"], embedding=EMBEDDINGS[i], image_hash=p["hash"], created_at=ts(p["days"]))
            db.add(obj)
            await db.flush()
            person_objs.append(obj)
            counts["persons"] += 1

        # Emails
        email_objs = {}
        for e in EMAILS:
            existing = (await db.execute(select(Email).where(Email.email == e["email"]))).scalar_one_or_none()
            if existing:
                email_objs[e["email"]] = existing
                continue
            obj = Email(email=e["email"], source=e["source"], person_id=person_objs[e["person"]].id, created_at=ts(e["days"]))
            db.add(obj)
            await db.flush()
            email_objs[e["email"]] = obj
            counts["emails"] += 1

        # Sites
        site_objs = {}
        for s in SITES:
            existing = (await db.execute(select(Site).where(Site.url == s["url"]))).scalar_one_or_none()
            if existing:
                site_objs[s["url"]] = existing
                continue
            obj = Site(url=s["url"], server=s["server"], discovered_at=ts(s["days"]))
            db.add(obj)
            await db.flush()
            site_objs[s["url"]] = obj
            counts["sites"] += 1

        # Corrélations
        for c in CORRELATIONS:
            pid = person_objs[c["person"]].id
            eid = email_objs[c["email"]].id
            sid = site_objs[c["site"]].id
            existing = (await db.execute(select(Correlation).where(
                Correlation.person_id == pid, Correlation.email_id == eid, Correlation.site_id == sid,
            ))).scalar_one_or_none()
            if existing:
                continue
            db.add(Correlation(person_id=pid, email_id=eid, site_id=sid, score=c["score"],
                               data={"source": "enrichment", "ips": c["ips"]}, created_at=ts(c["days"])))
            counts["correlations"] += 1

        # Recherches + résultats
        search_objs = []  # tracé (créées + existantes) pour rattachement à l'enquête démo
        for s in SEARCHES:
            existing = (await db.execute(select(SearchHistory).where(
                SearchHistory.query == s["query"], SearchHistory.query_type == s["type"],
            ))).scalar_one_or_none()
            if existing:
                search_objs.append(existing)
                continue
            hist = SearchHistory(query=s["query"], query_type=s["type"],
                                 result_count=len(s["results"]), created_at=ts(s["days"]))
            db.add(hist)
            await db.flush()
            search_objs.append(hist)
            counts["searches"] += 1
            for r in s["results"]:
                db.add(SearchResult(search_id=hist.id, source=r["source"], entity=r["entity"],
                                    entity_type=r["type"], data=r["data"], extracted=r["extracted"],
                                    created_at=ts(s["days"])))
                counts["results"] += 1

        # Enquête de démo (Module 14) — rattache les 3 personnes + les 6 recherches.
        # Idempotent : get-or-create par titre, puis get-or-create de chaque rattachement.
        INV_TITLE = "Enquête démo — demo-osint.fr"
        investigation = (await db.execute(select(Investigation).where(
            Investigation.title == INV_TITLE,
        ))).scalar_one_or_none()
        if investigation is None:
            investigation = Investigation(
                title=INV_TITLE,
                description="Investigation sur la startup fictive demo-osint.fr et ses membres",
                status="open",
                analyst_id=admin_id,
                created_at=ts(5.0),
            )
            db.add(investigation)
            await db.flush()
            counts["investigations"] += 1

        inv_links = (
            [("person", p.id, ts(4.9)) for p in person_objs]
            + [("search", h.id, ts(s["days"])) for h, s in zip(search_objs, SEARCHES)]
        )
        for entity_type, entity_id, added in inv_links:
            existing = (await db.execute(select(InvestigationEntity).where(
                InvestigationEntity.investigation_id == investigation.id,
                InvestigationEntity.entity_type == entity_type,
                InvestigationEntity.entity_id == entity_id,
            ))).scalar_one_or_none()
            if existing:
                continue
            db.add(InvestigationEntity(
                investigation_id=investigation.id, entity_type=entity_type,
                entity_id=entity_id, added_by=admin_id, added_at=added,
            ))
            counts["inv_entities"] += 1

        # Logs d'audit
        for a in AUDIT:
            existing = (await db.execute(select(AuditLog).where(
                AuditLog.action == a["action"], AuditLog.target == a["target"],
            ))).scalar_one_or_none()
            if existing:
                continue
            db.add(AuditLog(user_id=admin_id, action=a["action"], target=a["target"],
                            ip_address="127.0.0.1", created_at=ts(a["days"])))
            counts["audit"] += 1

        await db.commit()

    return counts


def main() -> None:
    print(f"{CYAN}{BOLD}Seed des données de démo OSINT (demo-osint.fr)...{RESET}")
    try:
        c = asyncio.run(seed())
    except Exception as e:
        print(f"\033[91mÉchec du seed : {e}{RESET}")
        sys.exit(1)

    def line(label, n):
        mark = f"{GREEN}✅{RESET}" if n else f"{YELLOW}•{RESET}"
        suffix = "" if n else f" {YELLOW}(déjà présent){RESET}"
        print(f"  {mark} {n} {label}{suffix}")

    print(f"\n{BOLD}Résumé :{RESET}")
    line("personnes créées", c["persons"])
    line("emails créés", c["emails"])
    line("sites créés", c["sites"])
    line("corrélations créées", c["correlations"])
    print(f"  {GREEN if c['searches'] else YELLOW}{'✅' if c['searches'] else '•'}{RESET} "
          f"{c['searches']} recherches + {c['results']} SearchResults créés")
    print(f"  {GREEN if c['investigations'] else YELLOW}{'✅' if c['investigations'] else '•'}{RESET} "
          f"{c['investigations']} enquête + {c['inv_entities']} entités rattachées")
    line("logs d'audit créés", c["audit"])
    print(f"\n{GREEN}{BOLD}Seed terminé.{RESET} Ouvrez le dashboard pour visualiser la démo.")


if __name__ == "__main__":
    main()
