from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Configuration centralisée chargée depuis les variables d'environnement."""

    DATABASE_URL: str = "postgresql+asyncpg://osint_user:osint_pass@localhost:5432/osint_db"
    DATABASE_URL_SYNC: str = "postgresql://osint_user:osint_pass@localhost:5432/osint_db"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60

    APP_NAME: str = "OSINT-Platform"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"

    API_PREFIX: str = "/api"

    ALLOWED_ORIGINS: str = "http://localhost:8000"
    EXTERNAL_REQUEST_TIMEOUT: int = 10
    GLOBAL_RATE_LIMIT: int = 100

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
