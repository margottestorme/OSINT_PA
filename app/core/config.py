from pydantic_settings import BaseSettings
from functools import lru_cache

# Secrets JWT connus comme non sécurisés (défaut du code + valeur dev du .env).
# Si l'un d'eux est utilisé, le garde-fou de démarrage refuse de tourner en production.
INSECURE_JWT_SECRETS = {
    "change-me-in-production",
    "dev-secret-key-change-in-production",
}


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
    # Sécurité (S7) : défaut False — pas d'exposition par défaut.
    # Le développement active DEBUG=true via le .env.
    DEBUG: bool = False
    # A5 : echo SQL découplé de DEBUG (défaut False) — évite d'inonder les logs.
    SQL_ECHO: bool = False
    LOG_LEVEL: str = "DEBUG"

    API_PREFIX: str = "/api"

    ALLOWED_ORIGINS: str = "http://localhost:8000"
    EXTERNAL_REQUEST_TIMEOUT: int = 10
    GLOBAL_RATE_LIMIT: int = 100

    # --- Clés API sources OSINT (toutes optionnelles, vide = source désactivée) ---
    GITHUB_TOKEN: str = ""          # Lot Vert : optionnel, monte le quota GitHub
    VIRUSTOTAL_API_KEY: str = ""    # Freemium : réputation/DNS passif de domaine
    OTX_API_KEY: str = ""           # Freemium : AlienVault OTX (passive DNS)
    HUNTER_API_KEY: str = ""        # Freemium : vérification d'email

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_security(settings: Settings) -> None:
    """Garde-fou de démarrage : vérifie que le secret JWT n'est pas une valeur par défaut.

    - DEBUG=true  : avertissement seulement (développement autorisé).
    - DEBUG=false : log critique + refus de démarrer (RuntimeError).
    """
    from loguru import logger

    if settings.JWT_SECRET_KEY in INSECURE_JWT_SECRETS:
        message = (
            "JWT_SECRET_KEY utilise une valeur par défaut non sécurisée. "
            "Définissez une clé secrète forte dans .env avant tout déploiement."
        )
        if settings.DEBUG:
            logger.warning(f"[sécurité] {message} (toléré en mode DEBUG)")
        else:
            logger.critical(f"[sécurité] {message} Démarrage refusé en production.")
            raise RuntimeError(message)
