from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

security_scheme = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Hash bcrypt précalculé pour un mot de passe factice (S9, anti-timing).
# Permet d'exécuter un verify de coût équivalent même si l'utilisateur n'existe pas,
# afin que le temps de réponse ne révèle pas l'existence d'un compte.
_DUMMY_HASH = pwd_context.hash("dummy-password-for-timing-mitigation")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_minutes: int = 15) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    to_encode = {**data, "exp": expire}
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme)) -> dict:
    # HTTPBearer(auto_error=False) renvoie None (au lieu de lever un 403 lui-même) quand
    # aucun header Authorization n'est fourni — on distingue explicitement ce cas pour
    # renvoyer 401 (absence d'authentification), sémantiquement correct au sens HTTP,
    # plutôt que le 403 (accès refusé) que FastAPI renverrait par défaut ici.
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise",
        )
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


def require_role(*allowed_roles: str):
    """Construit une dépendance qui exige que le token porte l'un des rôles autorisés (S6)."""

    def checker(token_data: dict = Depends(verify_token)) -> dict:
        if token_data.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès réservé : privilèges insuffisants",
            )
        return token_data

    return checker


async def authenticate_user(username: str, password: str, db: AsyncSession):
    from app.db.models import User

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if not user:
        # Anti-timing (S9) : on exécute quand même un verify sur un hash factice
        # pour que le temps de réponse soit identique à celui d'un utilisateur existant.
        verify_password(password, _DUMMY_HASH)
        return None

    if not user.is_active:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    user.last_login = datetime.now(timezone.utc)
    await db.flush()

    return user
