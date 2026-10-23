"""Distributed slowapi throttling without blocking the ASGI event loop."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, cast

import anyio
from limits.errors import StorageError
from limits.storage import RedisStorage
from redis.exceptions import RedisError
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from .config import Settings
from .services import ServiceError

_LOGGER = logging.getLogger(__name__)


class RateGate(Protocol):
    """Describe an asynchronously checked and closeable ingest rate limit."""

    async def check(self, request: Request) -> None:
        """Consume one request, raising if throttled or storage is unavailable."""
        ...

    async def aclose(self) -> None:
        """Release any rate-limiter backend connections."""
        ...


def _consume_rate_limit(request: Request) -> None:
    """Give slowapi a synchronous request gate to decorate and run in a worker."""


class SlowAPIRateGate:
    """Enforce five requests per moving minute per trusted transport client IP."""

    def __init__(self, settings: Settings, storage_uri: str | None = None) -> None:
        """Use Redis in production; permit an explicit memory backend in tests."""
        uri = storage_uri or settings.redis_url.get_secret_value()
        options: dict[str, object] = {}
        if uri.startswith(("redis://", "rediss://")):
            options = {
                "socket_timeout": settings.cache_timeout_seconds,
                "socket_connect_timeout": settings.cache_timeout_seconds,
                "retry_on_timeout": False,
                "max_connections": 16,
            }
        self.limiter = Limiter(
            key_func=get_remote_address,
            storage_uri=uri,
            # slowapi 0.1.9 incorrectly annotates all Redis option values as str.
            storage_options=cast(dict[str, str], options),
            strategy="moving-window",
            key_prefix="dispatch:v1:ingest",
            headers_enabled=False,
            swallow_errors=False,
            in_memory_fallback_enabled=False,
        )
        # slowapi's untyped decorator preserves this callable's inspected signature.
        self._gate = cast(
            Callable[[Request], None],
            self.limiter.limit("5/minute")(_consume_rate_limit),
        )
        self._threads = anyio.CapacityLimiter(8)

    async def check(self, request: Request) -> None:
        """Offload slowapi's synchronous Redis operations to bounded worker threads."""
        try:
            await anyio.to_thread.run_sync(self._gate, request, limiter=self._threads)
        except (RedisError, OSError, StorageError):
            _LOGGER.warning("rate_limit_storage_unavailable")
            raise ServiceError(
                "rate_limit_unavailable", "Request throttling is unavailable.", 503
            ) from None

    async def aclose(self) -> None:
        """Close the owned synchronous Redis connection pool off the event loop."""
        storage = self.limiter.limiter.storage
        if isinstance(storage, RedisStorage):
            await anyio.to_thread.run_sync(storage.storage.close, limiter=self._threads)
