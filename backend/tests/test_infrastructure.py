"""Check resource ownership, distributed limiter wiring, and ASGI body boundaries."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Iterator
from io import StringIO
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import Request
from limits.storage import RedisStorage
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.types import Message, Receive, Scope, Send

from app.config import Settings
from app.main import create_app
from app.observability import (
    BoundedBodyMiddleware,
    JsonFormatter,
    RequestContextMiddleware,
    SanitizedErrorMiddleware,
    configure_logging,
)
from app.services import ServiceError
from app.throttling import SlowAPIRateGate


@pytest.fixture
def settings() -> Settings:
    """Provide validated non-live credentials and short Redis socket deadlines."""
    return Settings(
        gemini_api_key=SecretStr("infrastructure-test-provider-key"),
        ingest_api_key=SecretStr("infrastructure-ingest-key-at-least-thirty-two"),
        gemini_model="gemini-test-model",
        production_origin="https://dispatch.example.com",
        cache_timeout_seconds=0.05,
    )


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    """Capture JSON on a stable stream and restore global logging after tests."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app")
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "level", logging.INFO)
    monkeypatch.setattr("app.main.configure_logging", Mock())
    return stream


def test_json_formatter_replaces_untrusted_logger_messages() -> None:
    """Do not serialize a raw URL or exception message from another logger."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="GET /private?api_key=%s failed: %s",
        args=("private-query-secret", "private-exception-secret"),
        exc_info=None,
    )

    formatted = json.loads(JsonFormatter().format(record))

    assert formatted["message"] == "external_log_record"
    assert "private-query-secret" not in json.dumps(formatted)
    assert "private-exception-secret" not in json.dumps(formatted)


def test_json_formatter_sanitizes_fixed_event_metadata() -> None:
    """Even a trusted event name cannot smuggle paths or exception text in extras."""
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request_completed",
        args=(),
        exc_info=None,
    )
    record.httpRequest = {
        "requestMethod": "GET /private-path?request-secret",
        "requestUrl": "/private-path?query-secret",
        "status": "500: exception-secret",
        "latency": "1.000000s exception-secret",
    }
    record.latency_ms = float("nan")
    record.error_type = "RuntimeError: exception-secret"

    formatted = json.loads(JsonFormatter().format(record))

    assert formatted["message"] == "request_completed"
    assert formatted["httpRequest"] == {
        "requestMethod": "UNKNOWN",
        "requestUrl": "matched",
        "status": 500,
        "latency": "0.000000s",
    }
    assert "latency_ms" not in formatted
    assert "error_type" not in formatted
    assert "private-path" not in json.dumps(formatted)
    assert "query-secret" not in json.dumps(formatted)
    assert "exception-secret" not in json.dumps(formatted)


@pytest.mark.parametrize("route", [object(), "x" * 257])
def test_json_formatter_replaces_non_string_and_oversized_routes(route: object) -> None:
    """Keep malformed route metadata out of structured completion events."""
    record = logging.LogRecord(
        name="app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_completed",
        args=(),
        exc_info=None,
    )
    record.httpRequest = {
        "requestMethod": "GET",
        "requestUrl": route,
        "status": 200,
        "latency": "0.000001s",
    }

    formatted = json.loads(JsonFormatter().format(record))

    assert formatted["httpRequest"]["requestUrl"] == "matched"


def test_configure_logging_disables_raw_uvicorn_access_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep Uvicorn access records from writing raw URLs alongside app events."""
    logger_names = (
        "",
        "app",
        "slowapi",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "google",
        "google.api_core",
        "google.generativeai",
        "grpc",
        "redis",
    )
    for name in logger_names:
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", list(logger.handlers))
        monkeypatch.setattr(logger, "level", logger.level)
        monkeypatch.setattr(logger, "propagate", logger.propagate)
        monkeypatch.setattr(logger, "disabled", logger.disabled)

    configure_logging("INFO")

    access_logger = logging.getLogger("uvicorn.access")
    assert access_logger.disabled
    assert access_logger.handlers == []
    assert not access_logger.propagate
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    """Create the minimal real HTTP scope needed for ASGI and limiter checks."""
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/ingest",
        "raw_path": b"/api/ingest",
        "query_string": b"",
        "headers": headers or [(b"content-type", b"application/json")],
        "client": ("192.0.2.10", 1234),
        "server": ("testserver", 443),
        "scheme": "https",
        "http_version": "1.1",
    }


def _lifespan_scope() -> Scope:
    """Return the smallest non-HTTP scope accepted by pure ASGI middleware."""
    return {"type": "lifespan"}


async def test_request_context_delegates_non_http_scopes() -> None:
    """Leave lifespan events untouched instead of adding HTTP state or headers."""
    output: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Complete the delegated lifespan event."""
        assert scope["type"] == "lifespan"
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})

    async def receive() -> Message:
        """Supply one lifespan startup event to the downstream application."""
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        """Collect the event emitted by the downstream application."""
        output.append(message)

    await RequestContextMiddleware(application)(_lifespan_scope(), receive, send)

    assert output == [{"type": "lifespan.startup.complete"}]


async def test_request_context_returns_sanitized_response_for_pre_response_failure(
    logs: StringIO,
) -> None:
    """Turn a downstream failure into one correlated, private HTTP response."""
    output: list[Message] = []
    scope = _scope([(b"x-request-id", b"12345678-1234-4234-8234-123456789012")])

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Raise before emitting headers so the middleware owns the response."""
        raise RuntimeError("private-request-detail")

    async def receive() -> Message:
        """Provide an unused request event for the response callable."""
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        """Collect the response generated by the middleware."""
        output.append(message)

    await RequestContextMiddleware(application)(scope, receive, send)

    assert output[0]["status"] == 500
    headers = dict(output[0]["headers"])
    assert headers[b"x-request-id"] == b"12345678-1234-4234-8234-123456789012"
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"content-security-policy"] == (
        b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        b"form-action 'self'"
    )
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert json.loads(output[1]["body"])["data"]["code"] == "internal_error"
    assert "private-request-detail" not in logs.getvalue()


async def test_request_context_reraises_after_response_started() -> None:
    """Never append a second response after a downstream transport failure."""
    output: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Emit headers, then simulate a transport failure in the body phase."""
        await send({"type": "http.response.start", "status": 204, "headers": []})
        raise RuntimeError("private-after-start")

    async def receive() -> Message:
        """Provide an unused request event."""
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        """Collect emitted response messages."""
        output.append(message)

    with pytest.raises(RuntimeError, match="private-after-start"):
        await RequestContextMiddleware(application)(_scope(), receive, send)

    assert len(output) == 1
    assert output[0]["status"] == 204


async def test_sanitized_errors_delegate_non_http_scopes() -> None:
    """Leave non-HTTP protocol events to the wrapped ASGI application."""
    output: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Complete the delegated lifespan event."""
        assert scope["type"] == "lifespan"
        await send({"type": "lifespan.startup.complete"})

    async def receive() -> Message:
        """Supply an unused lifespan event."""
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        """Collect the delegated event."""
        output.append(message)

    await SanitizedErrorMiddleware(application)(_lifespan_scope(), receive, send)

    assert output == [{"type": "lifespan.startup.complete"}]


async def test_sanitized_errors_reraise_after_response_started() -> None:
    """Do not replace a partially sent response with a second error envelope."""
    output: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Emit headers, then fail while sending the response body."""
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("private-sanitized-detail")

    async def receive() -> Message:
        """Provide an unused request event."""
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        """Collect emitted response messages."""
        output.append(message)

    with pytest.raises(RuntimeError, match="private-sanitized-detail"):
        await SanitizedErrorMiddleware(application)(_scope(), receive, send)

    assert len(output) == 1
    assert output[0]["status"] == 200


async def test_throttler_runs_synchronous_gate_off_event_loop(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep synchronous slowapi Redis I/O away from the event-loop thread."""
    gate = SlowAPIRateGate(settings, storage_uri="memory://")
    loop_thread = threading.get_ident()
    threads: list[int] = []

    def record_thread(request: Request) -> None:
        """Capture the worker executing the otherwise synchronous gate."""
        threads.append(threading.get_ident())

    monkeypatch.setattr(gate, "_gate", record_thread)
    await gate.check(Request(_scope()))
    assert len(threads) == 1
    assert threads[0] != loop_thread
    await gate.aclose()


async def test_throttling_fails_closed_on_storage_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never silently fall back to per-process limits during a Redis outage."""
    gate = SlowAPIRateGate(settings, storage_uri="memory://")

    def unavailable(request: Request) -> None:
        """Simulate a backend failure with private exception metadata."""
        raise RedisConnectionError("private-redis-password")

    monkeypatch.setattr(gate, "_gate", unavailable)
    with pytest.raises(ServiceError) as failure:
        await gate.check(Request(_scope()))
    assert failure.value.status_code == 503
    assert failure.value.code == "rate_limit_unavailable"
    assert "private-redis-password" not in str(failure.value)
    await gate.aclose()


async def test_production_limiter_uses_bounded_redis_connections(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inspect real Redis configuration without opening any TCP connections."""
    gate = SlowAPIRateGate(settings)
    storage = gate.limiter.limiter.storage
    assert isinstance(storage, RedisStorage)
    options = storage.storage.connection_pool.connection_kwargs
    assert options["socket_timeout"] == settings.cache_timeout_seconds
    assert options["socket_connect_timeout"] == settings.cache_timeout_seconds
    assert options["retry_on_timeout"] is False
    assert storage.storage.connection_pool.max_connections == 16
    close = Mock()
    monkeypatch.setattr(storage.storage, "close", close)
    await gate.aclose()
    close.assert_called_once_with()


@pytest.mark.parametrize("ready", [True, False])
async def test_lifespan_closes_owned_resources_on_success_and_failure(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    logs: StringIO,
    ready: bool,
) -> None:
    """Close every owned client on either a successful lifespan or startup failure."""
    redis: Redis[str] = Redis(decode_responses=True)
    redis_close = AsyncMock()
    monkeypatch.setattr(redis, "ping", AsyncMock(return_value=ready))
    monkeypatch.setattr(redis, "aclose", redis_close)
    monkeypatch.setattr("app.main.Redis.from_url", Mock(return_value=redis))
    dispatcher = Mock()
    dispatcher.aclose = AsyncMock()
    gate = Mock()
    gate.aclose = AsyncMock()
    monkeypatch.setattr("app.main.GeminiDispatcher", Mock(return_value=dispatcher))
    monkeypatch.setattr("app.main.SlowAPIRateGate", Mock(return_value=gate))
    application = create_app(settings)
    if ready:
        async with (
            application.router.lifespan_context(application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="https://testserver",
            ) as client,
        ):
            assert (await client.get("/health")).status_code == 200
    else:
        with pytest.raises(RuntimeError, match="Redis startup check failed"):
            async with application.router.lifespan_context(application):
                pytest.fail("Startup must fail when required Redis is unavailable.")
    redis_close.assert_awaited_once_with()
    dispatcher.aclose.assert_awaited_once_with()
    gate.aclose.assert_awaited_once_with()


async def test_cleanup_failure_does_not_skip_other_clients(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, logs: StringIO
) -> None:
    """Continue cleanup and log only the exception type, never secret-bearing text."""
    redis: Redis[str] = Redis(decode_responses=True)
    redis_close = AsyncMock()
    monkeypatch.setattr(redis, "ping", AsyncMock(return_value=True))
    monkeypatch.setattr(redis, "aclose", redis_close)
    monkeypatch.setattr("app.main.Redis.from_url", Mock(return_value=redis))
    dispatcher = Mock()
    dispatcher.aclose = AsyncMock()
    gate = Mock()
    gate.aclose = AsyncMock(side_effect=RuntimeError("private-cleanup-secret"))
    monkeypatch.setattr("app.main.GeminiDispatcher", Mock(return_value=dispatcher))
    monkeypatch.setattr("app.main.SlowAPIRateGate", Mock(return_value=gate))
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        assert application.state.runtime is not None
    redis_close.assert_awaited_once()
    dispatcher.aclose.assert_awaited_once()
    records = [json.loads(line) for line in logs.getvalue().splitlines()]
    assert {record["message"] for record in records} >= {
        "application_started",
        "application_stopping",
        "resource_close_failed",
    }
    assert "private-cleanup-secret" not in logs.getvalue()


@pytest.mark.parametrize("value", [b"-1", b"abc", b"+20", b"9" * 5000])
async def test_body_length_headers_are_validated_without_integer_overflow(
    value: bytes,
) -> None:
    """Reject malformed or excessive Content-Length before consuming a request."""
    called = False
    output: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Record downstream entry to prove invalid headers short-circuit parsing."""
        nonlocal called
        called = True

    async def receive() -> Message:
        """Fail if a bad declared body is unnecessarily read."""
        pytest.fail("Invalid Content-Length must not read the body.")

    async def send(message: Message) -> None:
        """Collect the pure-ASGI error response."""
        output.append(message)

    middleware = BoundedBodyMiddleware(application, 196608, 0.05)
    await middleware(
        _scope([(b"content-type", b"application/json"), (b"content-length", value)]),
        receive,
        send,
    )
    assert not called
    assert output[0]["status"] == (413 if len(value) > 9 else 400)


async def test_body_read_deadline_returns_408() -> None:
    """Abort stalled bodies before they occupy resources indefinitely."""
    output: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Fail if an incomplete request reaches the application."""
        pytest.fail("Incomplete bodies must not reach routing.")

    async def receive() -> Message:
        """Emulate a client that opens an upload and sends no bytes."""
        await asyncio.Event().wait()
        raise AssertionError("Unreachable after the body deadline.")

    async def send(message: Message) -> None:
        """Collect the error status and JSON response."""
        output.append(message)

    await BoundedBodyMiddleware(application, 196608, 0.01)(_scope(), receive, send)
    assert output[0]["status"] == 408
    assert json.loads(output[1]["body"])["data"]["code"] == "request_timeout"


async def test_body_disconnect_stops_without_invoking_downstream() -> None:
    """Stop reading when a client disconnects before the body is complete."""
    called = False
    output: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Record unexpected downstream entry."""
        nonlocal called
        called = True

    async def receive() -> Message:
        """Simulate a client disconnect before sending request bytes."""
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        """Collect any response, which should remain empty."""
        output.append(message)

    await BoundedBodyMiddleware(application, 196608, 0.05)(_scope(), receive, send)

    assert not called
    assert output == []


async def test_body_replay_preserves_disconnect_after_bounded_body() -> None:
    """Replay the body once, then preserve the transport's next event."""
    received: list[Message] = []
    source: Iterator[Message] = iter(
        [
            {"type": "http.request", "body": b"bounded-body", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        """Read both the replayed body and the following transport event."""
        received.append(await receive())
        received.append(await receive())

    async def receive() -> Message:
        """Return the finite source stream consumed by the middleware."""
        return next(source)

    async def send(message: Message) -> None:
        """Collect unexpected responses."""
        pytest.fail(f"Unexpected response: {message!r}")

    await BoundedBodyMiddleware(application, 196608, 0.05)(_scope(), receive, send)

    assert received == [
        {"type": "http.request", "body": b"bounded-body", "more_body": False},
        {"type": "http.disconnect"},
    ]
