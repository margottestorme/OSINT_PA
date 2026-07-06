import time
from collections import defaultdict

from fastapi import HTTPException, status


class APIRateLimiter:
    """Rate limiter en mémoire : max N requêtes par fenêtre glissante par clé (user ou IP)."""

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = 60,
        purge_interval: int = 300,
        detail: str = "Trop de requêtes — réessayez dans quelques instants",
    ):
        self._max_requests = max_requests
        self._window = window_seconds
        self._purge_interval = purge_interval
        self._detail = detail
        self._last_purge = time.monotonic()
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _purge_expired(self, now: float) -> None:
        """Supprime les utilisateurs dont tous les timestamps sont expirés (évite la fuite mémoire)."""
        if now - self._last_purge < self._purge_interval:
            return
        stale = [
            key for key, ts in self._requests.items()
            if not ts or now - ts[-1] >= self._window
        ]
        for key in stale:
            del self._requests[key]
        self._last_purge = now

    def check(self, user_id: str) -> None:
        now = time.monotonic()
        self._purge_expired(now)
        timestamps = self._requests[user_id]

        # Purger les entrées hors fenêtre
        self._requests[user_id] = [t for t in timestamps if now - t < self._window]

        if len(self._requests[user_id]) >= self._max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=self._detail,
            )

        self._requests[user_id].append(now)


search_rate_limiter = APIRateLimiter(max_requests=10, window_seconds=60)

# Limiter dédié au login (S3) : 5 tentatives/minute par IP, indépendant du global.
login_rate_limiter = APIRateLimiter(
    max_requests=5,
    window_seconds=60,
    detail="Trop de tentatives, réessayez dans une minute",
)

# Analyse de frames webcam (refonte client-side) : 2 requêtes/seconde par utilisateur.
stream_rate_limiter = APIRateLimiter(
    max_requests=2,
    window_seconds=1,
    detail="Trop de frames — l'analyse est limitée à 2 par seconde",
)

# Énumération de pseudos (Module 15) : 1 recherche / 30s par utilisateur (coûteux : 157 requêtes).
username_rate_limiter = APIRateLimiter(
    max_requests=1,
    window_seconds=30,
    detail="Une seule énumération de pseudo toutes les 30 secondes",
)
