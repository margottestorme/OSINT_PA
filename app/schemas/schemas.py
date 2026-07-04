from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --- Auth ---
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Person ---
class PersonBase(BaseModel):
    first_name: str
    last_name: str

class PersonCreate(PersonBase):
    pass

class PersonUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)

class PersonResponse(PersonBase):
    id: int
    confidence_score: float
    notes: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Email ---
class EmailBase(BaseModel):
    email: str
    source: str

class EmailResponse(EmailBase):
    id: int
    person_id: int | None
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Site ---
class SiteResponse(BaseModel):
    id: int
    url: str
    server: str | None
    discovered_at: datetime
    model_config = {"from_attributes": True}


# --- Correlation ---
class CorrelationResponse(BaseModel):
    id: int
    person_id: int | None
    email_id: int | None
    site_id: int | None
    score: float
    data: dict | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Recherche ---
class QueryType(str, Enum):
    email = "email"
    domain = "domain"
    name = "name"

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    query_type: QueryType = QueryType.email

class SearchResponse(BaseModel):
    message: str
    query: str
    query_type: str


# --- Historique ---
class SearchHistoryResponse(BaseModel):
    id: int
    query: str
    query_type: str
    result_count: int
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Résultat de recherche ---
class SearchResultResponse(BaseModel):
    id: int
    search_id: int
    source: str
    entity: str
    entity_type: str
    data: dict | None = None
    extracted: dict | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Recherche full-text (Elasticsearch, Module 11) ---
class FulltextScope(str, Enum):
    """Périmètre de la recherche full-text (paramètre `type` de l'endpoint)."""
    persons = "persons"
    results = "results"
    emails = "emails"
    sites = "sites"
    all = "all"

class FulltextHit(BaseModel):
    index: str                                    # index ES d'origine (ex : osint_persons)
    id: str                                       # _id du document (= PK PostgreSQL)
    score: float | None = None                    # score de pertinence ES
    highlight: dict[str, list[str]] = Field(default_factory=dict)  # fragments surlignés
    source: dict[str, Any]                        # document indexé (_source)

class FulltextResponse(BaseModel):
    total: int
    hits: list[FulltextHit]


# --- Health ---
class HealthResponse(BaseModel):
    status: str
    version: str


# --- Statistiques (dashboard KPI) ---
class StatsResponse(BaseModel):
    total_searches: int
    total_persons: int
    total_correlations: int
    last_activity: datetime | None = None


# --- Facial ---
class FaceUploadResponse(BaseModel):
    person_id: int
    is_new: bool
    confidence_score: float
    faces_count: int

class FacePersonResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    confidence_score: float
    has_embedding: bool
    notes: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Stream webcam (analyse de frames côté client) ---
class StreamFrameRequest(BaseModel):
    frame: str = Field(..., description="Image JPEG encodée en base64 (data URL acceptée)")

class StreamFaceResult(BaseModel):
    location: dict[str, int]              # {top, right, bottom, left}
    person_id: int | None = None
    name: str | None = None
    confidence: float | None = None
    is_new: bool

class StreamAnalyzeResponse(BaseModel):
    faces: list[StreamFaceResult]
    faces_count: int
    processing_ms: float


# --- Report ---
class PersonReportResponse(BaseModel):
    person: PersonResponse
    emails: list[EmailResponse]
    sites: list[SiteResponse]
    correlations: list[CorrelationResponse]


# --- Pagination ---
class PaginationParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
