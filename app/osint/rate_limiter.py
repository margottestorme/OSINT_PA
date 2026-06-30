import asyncio
import time

from loguru import logger


class RateLimiter:
    """Limite le débit des requêtes externes avec un intervalle minimum entre chaque appel."""

    def __init__(self, min_interval: float = 2.0):
        self._min_interval = min_interval
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                logger.debug(f"Rate limiter : attente de {wait:.2f}s avant la prochaine requête")
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
