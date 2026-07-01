from datetime import datetime
from enum import Enum

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

class PersonResponse(PersonBase):
    id: int
    confidence_score: float
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


# --- Health ---
class HealthResponse(BaseModel):
    status: str
    version: str


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
    created_at: datetime
    model_config = {"from_attributes": True}


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
