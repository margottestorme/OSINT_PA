import time
from collections import defaultdict

from fastapi import HTTPException, status


class APIRateLimiter:
    """Rate limiter en mémoire : max N requêtes par fenêtre glissante par utilisateur."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str) -> None:
        now = time.monotonic()
        timestamps = self._requests[user_id]

        # Purger les entrées hors fenêtre
        self._requests[user_id] = [t for t in timestamps if now - t < self._window]

        if len(self._requests[user_id]) >= self._max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Trop de requêtes — réessayez dans quelques instants",
            )

        self._requests[user_id].append(now)


search_rate_limiter = APIRateLimiter(max_requests=10, window_seconds=60)
