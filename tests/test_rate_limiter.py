import asyncio
import time

import pytest

from app.osint.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_enforces_min_interval():
    """Vérifie que le rate limiter impose bien un délai minimum entre les appels."""
    limiter = RateLimiter(min_interval=0.3)

    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.3, f"Le rate limiter n'a pas attendu assez : {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_rate_limiter_first_call_immediate():
    """Vérifie que le premier appel passe immédiatement."""
    limiter = RateLimiter(min_interval=5.0)

    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.1, f"Le premier appel ne devrait pas attendre : {elapsed:.2f}s"
