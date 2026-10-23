"""Compose the production FastAPI application and explicitly owned async resources."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Annotated, Protocol, cast

from fastapi import Depends, FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from redis.asyncio import Redis
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException

from .config import Settings
from .models import (
    AnalyseRequest,
    APIResponse,
    HealthState,
    IngestRequest,
    StructuredDispatch,
)
from .observability import (
    BoundedBodyMiddleware,
    RequestContextMiddleware,
    SanitizedErrorMiddleware,
    configure_logging,
    error_response,
    latency_ms,
)
from .services import (
    Cache,
    DispatchCache,
    Dispatcher,
    DispatchStore,
    GeminiDispatcher,
    InMemoryDispatchStore,
    ServiceError,
)
from .throttling import RateGate, SlowAPIRateGate

_LOGGER = logging.getLogger(__name__)
_INGEST_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


class _AsyncCloseable(Protocol):
    """Describe asynchronous resources owned by the application lifespan."""

    async def aclose(self) -> None:
        """Release the resource's connections."""
        ...


@dataclass(frozen=True, slots=True)
class Runtime:
    """Group typed adapters, allowing offline application tests without SDK calls."""

    cache: Cache
    dispatcher: Dispatcher
    store: DispatchStore
    rate_gate: RateGate


async def _safe_close(resource: _AsyncCloseable) -> None:
    """Attempt every resource shutdown without logging sensitive exception text."""
    try:
        await resource.aclose()
    except Exception as exc:
        _LOGGER.error("resource_close_failed", extra={"error_type": type(exc).__name__})


def _runtime(request: Request) -> Runtime:
    """Read the runtime initialized by the ASGI lifespan, not a global singleton."""
    return cast(Runtime, request.app.state.runtime)


async def _authorize_and_limit(
    request: Request,
    api_key: Annotated[str | None, Security(_INGEST_HEADER)],
) -> None:
    """Authenticate with constant-time comparison before consuming rate quota."""
    settings = cast(Settings, request.app.state.settings)
    valid = (
        api_key is not None
        and len(api_key) <= 512
        and len(request.headers.getlist("x-api-key")) == 1
        and hmac.compare_digest(
            api_key.encode("utf-8"),
            settings.ingest_api_key.get_secret_value().encode("ascii"),
        )
    )
    if not valid:
        raise ServiceError("unauthorized", "A valid ingest API key is required.", 401)
    await _runtime(request).rate_gate.check(request)


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    """Do not echo FastAPI's validation input, context, or malformed JSON body."""
    return error_response(
        request.scope, 422, "validation_error", "The request failed schema validation."
    )


async def _response_validation_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Keep internal response schema errors private."""
    _LOGGER.error("response_validation_failed")
    return error_response(
        request.scope, 500, "internal_error", "An internal server error occurred."
    )


async def _service_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Expose only service-owned constant messages, never original upstream errors."""
    if not isinstance(exc, ServiceError):
        return await _unexpected_error_handler(request, exc)
    headers: dict[str, str] = {}
    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "ApiKey"
    if exc.status_code == 503:
        headers["Retry-After"] = "60"
    _LOGGER.warning("service_request_failed", extra={"error_type": exc.code})
    return error_response(
        request.scope, exc.status_code, exc.code, exc.message, headers=headers
    )


async def _rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a sanitized 429 and a conservative moving-window retry interval."""
    return error_response(
        request.scope,
        429,
        "rate_limit_exceeded",
        "A maximum of five ingest requests per minute is allowed.",
        headers={"Retry-After": "60"},
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Standardize HTTP errors without trusting an exception's detail string."""
    if not isinstance(exc, HTTPException):
        return await _unexpected_error_handler(request, exc)
    messages = {
        400: "The request is invalid.",
        404: "The requested resource was not found.",
        405: "The request method is not allowed.",
    }
    headers = (
        {"Allow": exc.headers["Allow"]}
        if exc.headers and "Allow" in exc.headers
        else None
    )
    return error_response(
        request.scope,
        exc.status_code,
        "http_error",
        messages.get(exc.status_code, "The request could not be completed."),
        headers=headers,
    )


async def _unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Provide a final global fallback in addition to the ASGI error boundary."""
    _LOGGER.error("unhandled_error", extra={"error_type": type(exc).__name__})
    return error_response(
        request.scope, 500, "internal_error", "An internal server error occurred."
    )


def create_app(
    settings: Settings | None = None, runtime: Runtime | None = None
) -> FastAPI:
    """Create an ASGI app; the lifespan owns production clients, not injected fakes."""
    configuration = settings if settings is not None else Settings()
    configure_logging(configuration.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Own event-loop-bound clients through startup, requests, and shutdown."""
        application.state.started_at = perf_counter()
        application.state.ingest_locks = tuple(asyncio.Lock() for _ in range(64))
        async with AsyncExitStack() as resources:
            if runtime is not None:
                application.state.runtime = runtime
            else:
                redis: Redis[str] = Redis.from_url(
                    configuration.redis_url.get_secret_value(),
                    decode_responses=True,
                    socket_connect_timeout=configuration.cache_timeout_seconds,
                    socket_timeout=configuration.cache_timeout_seconds,
                    retry_on_timeout=False,
                    health_check_interval=30,
                    max_connections=32,
                )
                # types-redis 4.x predates Redis 5's public async aclose method.
                resources.push_async_callback(_safe_close, cast(_AsyncCloseable, redis))
                dispatcher = GeminiDispatcher(configuration)
                resources.push_async_callback(_safe_close, dispatcher)
                gate = SlowAPIRateGate(configuration)
                resources.push_async_callback(_safe_close, gate)
                cache = DispatchCache(redis, configuration)
                if not await cache.ping():
                    raise RuntimeError("Redis startup check failed.")
                application.state.runtime = Runtime(
                    cache=cache,
                    dispatcher=dispatcher,
                    store=InMemoryDispatchStore(configuration.store_max_entries),
                    rate_gate=gate,
                )
            _LOGGER.info("application_started")
            try:
                yield
            finally:
                _LOGGER.info("application_stopping")

    application = FastAPI(
        title="Structured Dispatch API",
        version="1.0.0",
        lifespan=lifespan,
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    application.state.settings = configuration
    application.add_exception_handler(RequestValidationError, _validation_handler)
    application.add_exception_handler(
        ResponseValidationError, _response_validation_handler
    )
    application.add_exception_handler(ServiceError, _service_error_handler)
    application.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    application.add_exception_handler(HTTPException, _http_error_handler)
    application.add_exception_handler(Exception, _unexpected_error_handler)

    # Starlette adds each new middleware outside the previous one.
    application.add_middleware(
        BoundedBodyMiddleware,
        max_bytes=configuration.max_request_bytes,
        timeout_seconds=configuration.body_timeout_seconds,
    )
    application.add_middleware(SanitizedErrorMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Server-Timing", "Retry-After"],
        max_age=600,
    )
    application.add_middleware(
        RequestContextMiddleware, project_id=configuration.cloud_project_id
    )

    @application.get("/health", response_model=APIResponse[HealthState])
    async def health(request: Request) -> APIResponse[HealthState]:
        """Report process liveness without making a paid or blocking upstream call."""
        started = cast(float, request.app.state.started_at)
        return APIResponse[HealthState](
            status="success",
            data=HealthState(
                state="running", uptime_seconds=max(0.0, perf_counter() - started)
            ),
            latency_ms=latency_ms(request.scope),
            cached=False,
        )

    @application.post(
        "/api/ingest",
        response_model=APIResponse[StructuredDispatch],
        dependencies=[Depends(_authorize_and_limit)],
    )
    async def ingest(
        payload: IngestRequest, request: Request
    ) -> APIResponse[StructuredDispatch]:
        """Return a cached dispatch or validate, persist, and cache a new extraction."""
        adapters = _runtime(request)
        key = adapters.cache.key_for(payload.text)
        result = await adapters.cache.get(key)
        cached = result is not None
        if result is None:
            locks = cast(tuple[asyncio.Lock, ...], request.app.state.ingest_locks)
            # Bounded striped locks avoid an attacker-controlled dictionary of locks.
            lock = locks[int(key[-8:], 16) % len(locks)]
            try:
                async with asyncio.timeout(
                    configuration.upstream_timeout_seconds
                    + 3 * configuration.cache_timeout_seconds
                ):
                    async with lock:
                        result = await adapters.cache.get(key)
                        cached = result is not None
                        if result is None:
                            result = await adapters.dispatcher.dispatch(payload.text)
                            await adapters.store.save(key, result)
                            await adapters.cache.put(key, result)
            except TimeoutError:
                raise ServiceError(
                    "upstream_timeout", "The dispatch provider timed out.", 504
                ) from None
        return APIResponse[StructuredDispatch](
            status="success",
            data=result,
            latency_ms=latency_ms(request.scope),
            cached=cached,
        )

    @application.post(
        "/api/analyse",
        response_model=APIResponse[StructuredDispatch],
        dependencies=[Depends(_authorize_and_limit)],
    )
    async def analyse(
        payload: AnalyseRequest, request: Request
    ) -> APIResponse[StructuredDispatch]:
        """Return a cached or freshly generated contextualised dispatch analysis.

        The prompt is prefixed with domain, urgency, and language context so
        Gemini can specialize its extraction.  Each unique combination of
        text + domain + urgency_hint + language_hint gets its own cache entry.
        """
        adapters = _runtime(request)
        lang_tag = payload.language_hint or "auto"
        prompt = (
            f"[domain:{payload.domain}]"
            f"[urgency:{payload.urgency_hint}]"
            f"[lang:{lang_tag}]\n"
            f"{payload.text}"
        )
        key = adapters.cache.key_for(prompt)
        result = await adapters.cache.get(key)
        cached = result is not None
        if result is None:
            locks = cast(tuple[asyncio.Lock, ...], request.app.state.ingest_locks)
            lock = locks[int(key[-8:], 16) % len(locks)]
            try:
                async with asyncio.timeout(
                    configuration.upstream_timeout_seconds
                    + 3 * configuration.cache_timeout_seconds
                ):
                    async with lock:
                        result = await adapters.cache.get(key)
                        cached = result is not None
                        if result is None:
                            result = await adapters.dispatcher.dispatch(prompt)
                            await adapters.store.save(key, result)
                            await adapters.cache.put(key, result)
            except TimeoutError:
                raise ServiceError(
                    "upstream_timeout", "The dispatch provider timed out.", 504
                ) from None
        return APIResponse[StructuredDispatch](
            status="success",
            data=result,
            latency_ms=latency_ms(request.scope),
            cached=cached,
        )

    return application
