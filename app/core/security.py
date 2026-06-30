from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext

from app.core.config import get_settings

security_scheme = HTTPBearer(auto_error=True)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Utilisateur de démonstration — en production, stocker en DB
DEMO_USERS = {
    "admin": pwd_context.hash("osint2024"),
}


def create_access_token(data: dict, expires_minutes: int = 15) -> str:
    """Génère un token JWT avec une durée de vie de 15 minutes par défaut."""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    to_encode = {**data, "exp": expire}
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)) -> dict:
    """Vérifie et décode un token JWT depuis l'en-tête Authorization."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if "sub" not in payload:
            raise JWTError("Champ 'sub' manquant")
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
        )


def authenticate_user(username: str, password: str) -> str | None:
    """Vérifie les identifiants et retourne le username si valide."""
    hashed = DEMO_USERS.get(username)
    if hashed and pwd_context.verify(password, hashed):
        return username
    return None
