from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Person(Base):
    """Représente une personne identifiée lors d'une recherche OSINT."""
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    emails: Mapped[list["Email"]] = relationship(back_populates="person", cascade="all, delete-orphan")
    correlations: Mapped[list["Correlation"]] = relationship(back_populates="person", cascade="all, delete-orphan")


class Email(Base):
    """Adresse email associée à une personne."""
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    source: Mapped[str] = mapped_column(String(255))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    person: Mapped[Person | None] = relationship(back_populates="emails")
    correlations: Mapped[list["Correlation"]] = relationship(back_populates="email", cascade="all, delete-orphan")


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    person: Mapped[Person | None] = relationship(back_populates="correlations")
    email: Mapped[Email | None] = relationship(back_populates="correlations")
    site: Mapped[Site | None] = relationship(back_populates="correlations")


class SearchHistory(Base):
    """Historique des recherches effectuées sur la plateforme."""
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text)
    query_type: Mapped[str] = mapped_column(String(50))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
