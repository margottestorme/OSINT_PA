from datetime import datetime, timezone
import hashlib

from sqlalchemy import Boolean, String, Float, Integer, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.session import Base
from app.db.crypto import EncryptedText


def hash_email(value: str) -> str:
    """SHA256 hex de l'email normalisé (minuscules, sans espaces).

    Sert d'index de recherche/déduplication sur l'email *en clair* — indispensable car
    le chiffrement pgcrypto est non déterministe (le même email produit un chiffré
    différent à chaque écriture, donc inutilisable pour une contrainte d'unicité).
    """
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="analyst")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Person(Base):
    """Représente une personne identifiée lors d'une recherche OSINT."""
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)  # chiffré au repos (Module 12)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_persons_image_hash", "image_hash", unique=True,
              sqlite_where=text("image_hash IS NOT NULL"),
              postgresql_where=text("image_hash IS NOT NULL")),
    )

    emails: Mapped[list["Email"]] = relationship(back_populates="person", cascade="all, delete-orphan")
    correlations: Mapped[list["Correlation"]] = relationship(back_populates="person", cascade="all, delete-orphan")


class Email(Base):
    """Adresse email associée à une personne.

    L'email est **chiffré au repos** (pgcrypto sur PostgreSQL — Module 12). La recherche
    et la déduplication passent par `email_hash` (SHA256 de l'email en clair), car la
    colonne chiffrée n'est pas déterministe.
    """
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(EncryptedText)  # chiffré au repos
    email_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA256 du clair
    source: Mapped[str] = mapped_column(String(255))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    person: Mapped[Person | None] = relationship(back_populates="emails")
    correlations: Mapped[list["Correlation"]] = relationship(back_populates="email", cascade="all, delete-orphan")

    @validates("email")
    def _sync_email_hash(self, key, value):
        """Recalcule email_hash à chaque écriture de l'email (SHA256 du clair)."""
        if value is not None:
            self.email_hash = hash_email(value)
        return value


class Site(Base):
    """Site web découvert pendant une recherche."""
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    server: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    correlations: Mapped[list["Correlation"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Correlation(Base):
    """Lien de corrélation entre une personne, un email et un site."""
    __tablename__ = "correlations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id"), nullable=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    # Métadonnées additionnelles issues des sources (ex : IPs, nom de la source)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    person: Mapped[Person | None] = relationship(back_populates="correlations")
    email: Mapped[Email | None] = relationship(back_populates="correlations")
    site: Mapped[Site | None] = relationship(back_populates="correlations")


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text)
    query_type: Mapped[str] = mapped_column(String(50))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    results: Mapped[list["SearchResult"]] = relationship(
        back_populates="search", cascade="all, delete-orphan"
    )


class SearchResult(Base):
    """Résultat brut d'une recherche manuelle (Approche B) : une ligne par OsintResult.

    Découplé du modèle person-centrique — mappe 1:1 le format OsintResult du moteur.
    """
    __tablename__ = "search_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    search_id: Mapped[int] = mapped_column(
        ForeignKey("search_history.id"), index=True
    )
    source: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extracted: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    search: Mapped["SearchHistory"] = relationship(back_populates="results")


class Investigation(Base):
    """Dossier d'enquête OSINT regroupant des entités traçables (Module 14).

    Transforme la plateforme d'un ensemble d'outils en plateforme d'investigation :
    chaque enquête agrège personnes, emails, sites, corrélations et recherches, et
    peut être exportée en rapport PDF forensique opposable.
    """
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / closed / archived
    analyst_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    entities: Mapped[list["InvestigationEntity"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )


class InvestigationEntity(Base):
    """Rattachement d'une entité (personne/email/site/corrélation/recherche) à une enquête.

    Table de liaison polymorphe : `entity_type` + `entity_id` référencent une ligne d'une
    des tables métier, sans FK dure (les entités peuvent vivre hors d'une enquête).
    """
    __tablename__ = "investigation_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(20))  # person / email / site / correlation / search
    entity_id: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    investigation: Mapped["Investigation"] = relationship(back_populates="entities")

    __table_args__ = (
        Index("ix_investigation_entities_type_id", "entity_type", "entity_id"),
    )


class UsernameSearch(Base):
    """Recherche d'énumération de pseudo multi-plateformes (Module 15)."""
    __tablename__ = "username_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(30), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    total_platforms: Mapped[int] = mapped_column(Integer, default=0)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running / completed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    results: Mapped[list["UsernameResult"]] = relationship(
        back_populates="search", cascade="all, delete-orphan"
    )


class UsernameResult(Base):
    """Résultat d'une vérification sur une plateforme (Module 15)."""
    __tablename__ = "username_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("username_searches.id"), index=True)
    platform: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(30))
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    response_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Variante testée sur cette plateforme (le pseudo saisi ou l'une de ses variantes générées
    # — séparateurs/ordre/casse/suffixe) ; NULL si le résultat vient d'un ancien run sans
    # génération de variantes. Toujours l'original si aucune variante n'a matché.
    matched_variant: Mapped[str | None] = mapped_column(String(30), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    search: Mapped["UsernameSearch"] = relationship(back_populates="results")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
