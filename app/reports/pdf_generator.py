"""Génération de rapports PDF forensiques pour les enquêtes (Module 14).

Le PDF est intégralement construit **en mémoire** (`io.BytesIO`) : aucune écriture disque,
conforme à la même exigence que le module facial. Un **hash SHA256** est calculé sur la
sérialisation canonique du contenu (métadonnées + entités + chaîne d'audit) *avant* la
signature, puis imprimé sur la page d'intégrité — il rend le rapport tamper-evident : toute
altération des données présentées invaliderait l'empreinte.

Découpage :
- `collect_report_data(db, id)` : accès base, agrège les entités et l'audit → `ReportData`
- `compute_content_hash(data)` : empreinte SHA256 du contenu (déterministe pour un contenu donné)
- `build_investigation_pdf(data)` : génération PDF pure (aucun accès base, aucun disque)
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Correlation,
    Email,
    Investigation,
    InvestigationEntity,
    Person,
    SearchHistory,
    Site,
)

# --- Charte graphique (sobre, professionnelle) ---
NAVY = colors.HexColor("#1a1f2e")
NAVY_LIGHT = colors.HexColor("#2b3350")
ACCENT = colors.HexColor("#3b82f6")
GREY = colors.HexColor("#64748b")
LIGHT_GREY = colors.HexColor("#f1f5f9")
RED = colors.HexColor("#b91c1c")


def _iso(dt: datetime | None) -> str:
    """Formate un datetime en ISO 8601 UTC ; chaîne vide si None."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# --- Structures de données (découplées de l'ORM et du PDF) ---

@dataclass
class ReportData:
    """Données agrégées d'une enquête, prêtes à être rendues en PDF."""
    report_uuid: str
    generated_at: datetime
    classification: str

    investigation_id: int
    title: str
    description: str | None
    status: str
    analyst: str | None
    created_at: datetime | None
    updated_at: datetime | None

    persons: list[dict] = field(default_factory=list)
    emails: list[dict] = field(default_factory=list)
    sites: list[dict] = field(default_factory=list)
    correlations: list[dict] = field(default_factory=list)
    searches: list[dict] = field(default_factory=list)
    username_searches: list[dict] = field(default_factory=list)

    period_start: datetime | None = None
    period_end: datetime | None = None

    audit_chain: list[dict] = field(default_factory=list)
    content_sha256: str = ""

    @property
    def entity_total(self) -> int:
        return (len(self.persons) + len(self.emails) + len(self.sites)
                + len(self.correlations) + len(self.searches)
                + len(self.username_searches))


async def collect_report_data(db: AsyncSession, investigation_id: int) -> ReportData:
    """Agrège toutes les données d'une enquête (entités rattachées + chaîne d'audit).

    Renvoie `None` si l'enquête n'existe pas (l'appelant lève alors un 404).
    """
    inv = await db.get(Investigation, investigation_id)
    if inv is None:
        return None

    # Résout le username de l'analyste responsable
    from app.db.models import User
    analyst_name = None
    if inv.analyst_id is not None:
        analyst_name = await db.scalar(select(User.username).where(User.id == inv.analyst_id))

    links = list((await db.execute(
        select(InvestigationEntity)
        .where(InvestigationEntity.investigation_id == investigation_id)
        .order_by(InvestigationEntity.added_at, InvestigationEntity.id)
    )).scalars().all())

    ids_by_type: dict[str, list[int]] = {}
    notes_by_key: dict[tuple[str, int], str | None] = {}
    for link in links:
        ids_by_type.setdefault(link.entity_type, []).append(link.entity_id)
        notes_by_key.setdefault((link.entity_type, link.entity_id), link.notes)

    async def _fetch(model, ids):
        if not ids:
            return []
        return list((await db.execute(select(model).where(model.id.in_(ids)))).scalars().all())

    from app.db.models import UsernameSearch
    persons = await _fetch(Person, ids_by_type.get("person", []))
    emails = await _fetch(Email, ids_by_type.get("email", []))
    sites = await _fetch(Site, ids_by_type.get("site", []))
    correlations = await _fetch(Correlation, ids_by_type.get("correlation", []))
    searches = await _fetch(SearchHistory, ids_by_type.get("search", []))
    username_searches = await _fetch(UsernameSearch, ids_by_type.get("username_search", []))

    persons_d = [{
        "id": p.id, "name": f"{p.first_name} {p.last_name}".strip(),
        "confidence_score": p.confidence_score, "created_at": p.created_at,
        "notes": notes_by_key.get(("person", p.id)),
    } for p in persons]
    emails_d = [{
        "id": e.id, "email": e.email, "source": e.source,
        "person_id": e.person_id, "created_at": e.created_at,
        "notes": notes_by_key.get(("email", e.id)),
    } for e in emails]
    sites_d = [{
        "id": s.id, "url": s.url, "server": s.server, "discovered_at": s.discovered_at,
        "notes": notes_by_key.get(("site", s.id)),
    } for s in sites]
    correlations_d = [{
        "id": c.id, "person_id": c.person_id, "email_id": c.email_id,
        "site_id": c.site_id, "score": c.score,
        "ips": (c.data or {}).get("ips") if isinstance(c.data, dict) else None,
        "created_at": c.created_at,
        "notes": notes_by_key.get(("correlation", c.id)),
    } for c in correlations]
    searches_d = [{
        "id": h.id, "query": h.query, "query_type": h.query_type,
        "result_count": h.result_count, "created_at": h.created_at,
        "notes": notes_by_key.get(("search", h.id)),
    } for h in searches]
    username_searches_d = [{
        "id": u.id, "username": u.username, "found_count": u.found_count,
        "total_platforms": u.total_platforms, "status": u.status, "created_at": u.created_at,
        "notes": notes_by_key.get(("username_search", u.id)),
    } for u in username_searches]

    # Période couverte : min/max des dates de découverte réelles des entités
    all_dates = (
        [p["created_at"] for p in persons_d]
        + [e["created_at"] for e in emails_d]
        + [s["discovered_at"] for s in sites_d]
        + [c["created_at"] for c in correlations_d]
        + [h["created_at"] for h in searches_d]
        + [u["created_at"] for u in username_searches_d]
    )
    all_dates = [d for d in all_dates if d is not None]
    period_start = min(all_dates) if all_dates else None
    period_end = max(all_dates) if all_dates else None

    # Chaîne d'audit propre à cette enquête (actions loggées)
    audit_rows = list((await db.execute(
        select(AuditLog)
        .where(AuditLog.action.like("investigation%"))
        .where(AuditLog.target.like(f"{investigation_id}:%"))
        .order_by(AuditLog.created_at, AuditLog.id)
    )).scalars().all())
    audit_chain = [{
        "action": a.action, "target": a.target,
        "user_id": a.user_id, "ip_address": a.ip_address,
        "created_at": a.created_at,
    } for a in audit_rows]

    data = ReportData(
        report_uuid=str(uuid.uuid4()),
        generated_at=datetime.now(timezone.utc),
        classification="CONFIDENTIEL",
        investigation_id=inv.id,
        title=inv.title,
        description=inv.description,
        status=inv.status,
        analyst=analyst_name,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
        persons=persons_d,
        emails=emails_d,
        sites=sites_d,
        correlations=correlations_d,
        searches=searches_d,
        username_searches=username_searches_d,
        period_start=period_start,
        period_end=period_end,
        audit_chain=audit_chain,
    )
    data.content_sha256 = compute_content_hash(data)
    return data


def compute_content_hash(data: ReportData) -> str:
    """SHA256 hex de la sérialisation canonique du contenu du rapport.

    Couvre les métadonnées, toutes les entités et la chaîne d'audit — mais **pas**
    le hash lui-même. Déterministe pour un contenu donné (tri des clés, dates ISO),
    ce qui permet à un vérificateur de recalculer l'empreinte et de détecter toute
    altération des données présentées.
    """
    payload = {
        "report_uuid": data.report_uuid,
        "generated_at": _iso(data.generated_at),
        "investigation": {
            "id": data.investigation_id, "title": data.title,
            "description": data.description, "status": data.status,
            "analyst": data.analyst,
            "created_at": _iso(data.created_at), "updated_at": _iso(data.updated_at),
        },
        "persons": data.persons,
        "emails": data.emails,
        "sites": data.sites,
        "correlations": data.correlations,
        "searches": data.searches,
        "username_searches": data.username_searches,
        "audit_chain": [{**a, "created_at": _iso(a["created_at"])} for a in data.audit_chain],
    }
    canonical = json.dumps(payload, sort_keys=True, default=_iso, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Canvas avec en-tête / pied de page et numérotation sur chaque page ---

def _make_canvas(data: ReportData):
    """Fabrique une classe Canvas capturant les métadonnées pour l'en-tête/pied.

    `pageCompression=0` garde les flux de texte lisibles → l'empreinte SHA256 imprimée
    reste retrouvable dans les octets du PDF (utile pour la vérification/les tests).
    """
    report_ref = data.report_uuid[:8].upper()
    generated = _iso(data.generated_at)
    classification = data.classification

    class ForensicCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            kwargs["pageCompression"] = 0
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._draw_furniture(total)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def _draw_furniture(self, total):
            w, h = A4
            # En-tête : bandeau navy fin
            self.setFillColor(NAVY)
            self.rect(0, h - 12 * mm, w, 12 * mm, stroke=0, fill=1)
            self.setFillColor(colors.white)
            self.setFont("Helvetica-Bold", 8)
            self.drawString(15 * mm, h - 8 * mm, "OSINT PLATFORM — RAPPORT D'ENQUÊTE")
            self.setFillColor(colors.HexColor("#fca5a5"))
            self.setFont("Helvetica-Bold", 8)
            self.drawRightString(w - 15 * mm, h - 8 * mm, classification)
            # Pied de page : référence, pagination, horodatage
            self.setStrokeColor(GREY)
            self.setLineWidth(0.3)
            self.line(15 * mm, 12 * mm, w - 15 * mm, 12 * mm)
            self.setFillColor(GREY)
            self.setFont("Helvetica", 7)
            self.drawString(15 * mm, 8 * mm, f"Réf. rapport : {report_ref}")
            self.drawCentredString(w / 2, 8 * mm, f"Page {self._pageNumber} / {total}")
            self.drawRightString(w - 15 * mm, 8 * mm, generated)

    return ForensicCanvas


# --- Styles ---

def _styles():
    ss = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                                 fontSize=24, textColor=NAVY, spaceAfter=6, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("st", parent=ss["Normal"], fontName="Helvetica",
                                    fontSize=12, textColor=GREY, alignment=TA_CENTER, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                              fontSize=14, textColor=NAVY, spaceBefore=14, spaceAfter=8),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=9.5, textColor=colors.black, leading=13),
        "cell": ParagraphStyle("c", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=8, leading=10),
        "cell_head": ParagraphStyle("ch", parent=ss["Normal"], fontName="Helvetica-Bold",
                                    fontSize=8, textColor=colors.white, leading=10),
        "mono": ParagraphStyle("m", parent=ss["Normal"], fontName="Courier",
                               fontSize=8, leading=11, textColor=NAVY),
        "label": ParagraphStyle("l", parent=ss["Normal"], fontName="Helvetica-Bold",
                                fontSize=9.5, textColor=NAVY, leading=13),
    }
    return styles


def _p(text, style):
    """Paragraphe robuste (échappe le HTML, gère None)."""
    from xml.sax.saxutils import escape
    return Paragraph(escape(str(text)) if text is not None else "—", style)


def _data_table(headers, rows, col_widths, st):
    """Construit une Table stylée (en-tête navy, lignes alternées)."""
    head = [_p(h, st["cell_head"]) for h in headers]
    body = [[_p(c, st["cell"]) for c in row] for row in rows]
    table = Table([head] + body, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(body) + 1):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GREY))
    table.setStyle(TableStyle(style))
    return table


# --- Pages ---

def _cover(data, st):
    story = []
    story.append(Spacer(1, 3 * cm))
    # Emblème simple : loupe vectorielle
    from reportlab.graphics.shapes import Circle, Drawing, Line
    d = Drawing(60, 60)
    d.add(Circle(24, 36, 16, strokeColor=NAVY, strokeWidth=3, fillColor=colors.white))
    d.add(Line(36, 24, 48, 12, strokeColor=NAVY, strokeWidth=3))
    d.hAlign = "CENTER"
    story.append(d)
    story.append(Spacer(1, 6 * mm))
    story.append(_p("OSINT PLATFORM", st["subtitle"]))
    story.append(Spacer(1, 8 * mm))
    story.append(_p(data.title, st["title"]))
    story.append(Spacer(1, 4 * mm))
    story.append(_p("Rapport d'enquête forensique", st["subtitle"]))
    story.append(Spacer(1, 1.5 * cm))

    meta = [
        ["Analyste responsable", data.analyst or "—"],
        ["Date de génération (UTC)", _iso(data.generated_at)],
        ["Numéro de rapport", data.report_uuid],
        ["Identifiant d'enquête", str(data.investigation_id)],
        ["Statut de l'enquête", data.status.upper()],
    ]
    t = Table([[_p(k, st["label"]), _p(v, st["body"])] for k, v in meta],
              colWidths=[6 * cm, 9.5 * cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(t)
    story.append(Spacer(1, 1.5 * cm))

    # Bandeau de classification
    band = Table([[_p(f"CLASSIFICATION : {data.classification}", ParagraphStyle(
        "cl", fontName="Helvetica-Bold", fontSize=12, textColor=colors.white,
        alignment=TA_CENTER))]], colWidths=[15.5 * cm])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RED),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(band)
    return story


def _executive_summary(data, st):
    story = [_p("Résumé exécutif", st["h2"])]
    story.append(_p("Description de l'enquête", st["label"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_p(data.description or "Aucune description fournie.", st["body"]))
    story.append(Spacer(1, 6 * mm))

    story.append(_p("Statistiques", st["label"]))
    story.append(Spacer(1, 2 * mm))
    stats_rows = [
        ["Personnes", str(len(data.persons))],
        ["Emails", str(len(data.emails))],
        ["Sites", str(len(data.sites))],
        ["Corrélations", str(len(data.correlations))],
        ["Recherches OSINT", str(len(data.searches))],
        ["Recherches de pseudos", str(len(data.username_searches))],
        ["Total d'entités", str(data.entity_total)],
    ]
    story.append(_data_table(["Type d'entité", "Nombre"], stats_rows,
                             [8 * cm, 4 * cm], st))
    story.append(Spacer(1, 6 * mm))

    story.append(_p("Période couverte", st["label"]))
    story.append(Spacer(1, 2 * mm))
    if data.period_start:
        period = f"Du {_iso(data.period_start)} au {_iso(data.period_end)}"
    else:
        period = "Aucune entité datée rattachée à l'enquête."
    story.append(_p(period, st["body"]))
    story.append(Spacer(1, 4 * mm))
    story.append(_p("Statut de l'enquête", st["label"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_p(data.status.upper(), st["body"]))
    return story


def _entities_sections(data, st):
    story = [_p("Entités rattachées", st["h2"])]

    def _section(title, headers, rows, widths):
        block = [_p(title, st["label"]), Spacer(1, 2 * mm)]
        if rows:
            block.append(_data_table(headers, rows, widths, st))
        else:
            block.append(_p("Aucune entité de ce type.", st["body"]))
        block.append(Spacer(1, 6 * mm))
        return block

    story += _section(
        f"Personnes ({len(data.persons)})",
        ["ID", "Nom", "Score de confiance", "Découverte (UTC)"],
        [[p["id"], p["name"], f"{p['confidence_score']:.2f}", _iso(p["created_at"])]
         for p in data.persons],
        [1.2 * cm, 6 * cm, 3 * cm, 5.3 * cm],
    )
    story += _section(
        f"Emails ({len(data.emails)})",
        ["ID", "Email", "Source", "Découverte (UTC)"],
        [[e["id"], e["email"], e["source"], _iso(e["created_at"])] for e in data.emails],
        [1.2 * cm, 6 * cm, 3 * cm, 5.3 * cm],
    )
    story += _section(
        f"Sites ({len(data.sites)})",
        ["ID", "URL", "Serveur", "Découverte (UTC)"],
        [[s["id"], s["url"], s["server"] or "—", _iso(s["discovered_at"])] for s in data.sites],
        [1.2 * cm, 7 * cm, 3 * cm, 4.3 * cm],
    )
    story += _section(
        f"Corrélations ({len(data.correlations)})",
        ["ID", "Personne", "Email", "Site", "Score", "IPs"],
        [[c["id"], c["person_id"] or "—", c["email_id"] or "—", c["site_id"] or "—",
          f"{c['score']:.2f}", ", ".join(c["ips"]) if c.get("ips") else "—"]
         for c in data.correlations],
        [1.2 * cm, 2.3 * cm, 2 * cm, 1.8 * cm, 1.7 * cm, 6.5 * cm],
    )
    story += _section(
        f"Recherches OSINT ({len(data.searches)})",
        ["ID", "Requête", "Type", "Résultats", "Date (UTC)"],
        [[h["id"], h["query"], h["query_type"], h["result_count"], _iso(h["created_at"])]
         for h in data.searches],
        [1.2 * cm, 5.5 * cm, 2.3 * cm, 2 * cm, 4.5 * cm],
    )
    story += _section(
        f"Recherches de pseudos ({len(data.username_searches)})",
        ["ID", "Pseudo", "Trouvés", "Plateformes", "Date (UTC)"],
        [[u["id"], u["username"], u["found_count"], u["total_platforms"], _iso(u["created_at"])]
         for u in data.username_searches],
        [1.2 * cm, 5.5 * cm, 2.3 * cm, 2.5 * cm, 4 * cm],
    )
    return story


def _integrity_page(data, st):
    story = [_p("Intégrité forensique", st["h2"])]
    story.append(_p(
        "Cette page atteste de l'intégrité du présent rapport. L'empreinte SHA256 "
        "ci-dessous est calculée sur la sérialisation canonique de l'ensemble du contenu "
        "(métadonnées, entités et journal d'audit). Toute altération des données "
        "présentées invaliderait cette empreinte.", st["body"]))
    story.append(Spacer(1, 5 * mm))

    story.append(_p("Empreinte SHA256 du contenu", st["label"]))
    story.append(Spacer(1, 2 * mm))
    # Hash sur deux lignes pour la lisibilité
    h = data.content_sha256
    story.append(_p(h[:32], st["mono"]))
    story.append(_p(h[32:], st["mono"]))
    story.append(Spacer(1, 5 * mm))

    meta_rows = [
        ["Numéro de rapport (UUID)", data.report_uuid],
        ["Horodatage de génération", _iso(data.generated_at)],
        ["Algorithme d'empreinte", "SHA-256"],
    ]
    story.append(_data_table(["Élément", "Valeur"], meta_rows, [6 * cm, 9.5 * cm], st))
    story.append(Spacer(1, 6 * mm))

    story.append(_p("Chaîne d'audit", st["label"]))
    story.append(Spacer(1, 2 * mm))
    if data.audit_chain:
        rows = [[_iso(a["created_at"]), a["action"], a["target"] or "—",
                 str(a["user_id"]) if a["user_id"] is not None else "—"]
                for a in data.audit_chain]
        story.append(_data_table(["Horodatage (UTC)", "Action", "Cible", "Utilisateur"],
                                 rows, [5 * cm, 4.5 * cm, 4 * cm, 2 * cm], st))
    else:
        story.append(_p("Aucune action auditée pour cette enquête.", st["body"]))
    story.append(Spacer(1, 8 * mm))

    story.append(_p("Rapport généré par OSINT Platform", ParagraphStyle(
        "sig", fontName="Helvetica-Oblique", fontSize=9, textColor=GREY, alignment=TA_LEFT)))
    return story


def build_investigation_pdf(data: ReportData) -> bytes:
    """Génère le PDF forensique complet en mémoire et renvoie ses octets.

    Aucune écriture disque : tout transite par un `BytesIO`.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm,
        title=f"Rapport d'enquête #{data.investigation_id}",
        author="OSINT Platform",
    )
    st = _styles()
    story = []
    story += _cover(data, st)
    story.append(PageBreak())
    story += _executive_summary(data, st)
    story.append(PageBreak())
    story += _entities_sections(data, st)
    story.append(PageBreak())
    story += _integrity_page(data, st)

    doc.build(story, canvasmaker=_make_canvas(data))
    return buf.getvalue()
