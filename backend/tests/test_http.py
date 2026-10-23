"""Verify authenticated HTTP behavior with real routing and offline typed adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import StringIO
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.requests import Request
from starlette.types import Scope

from app.config import Settings
from app.main import (
    Runtime,
    _http_error_handler,
    _service_error_handler,
    create_app,
)
from app.models import (
    APIResponse,
    ErrorDetails,
    HealthState,
    StructuredDispatch,
)
from app.observability import JsonFormatter
from app.services import InMemoryDispatchStore, ServiceError
from app.throttling import SlowAPIRateGate

_KEY = "http-test-ingest-key-at-least-thirty-two-bytes"
_TEXT = "Station 7 reports a pump failure; send maintenance."
_DISPATCH = StructuredDispatch(
    title="Pump failure",
    severity_level="high",
    category="maintenance",
    key_entities=["Station 7"],
    confidence_score=0.95,
    action_items=["Send maintenance"],
    reasoning_summary="Pump failure at Station 7 warrants maintenance.",
    domain_tags=["infrastructure", "maintenance"],
)


def _handler_request() -> Request:
    """Build a request scope with correlation state for direct handler checks."""
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [],
        "client": ("192.0.2.10", 12345),
        "server": ("testserver", 443),
        "scheme": "https",
        "http_version": "1.1",
        "state": {"request_id": "handler-request-id"},
    }
    return Request(scope)


@dataclass
class _MemoryCache:
    """Provide an observable cache with the same digest contract as production."""

    entries: dict[str, StructuredDispatch] = field(default_factory=dict)
    reads: int = 0
    writes: int = 0

    def key_for(self, text: str) -> str:
        """Return a deterministic SHA-256 key without retaining source text."""
        return "dispatch:v2:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> StructuredDispatch | None:
        """Record a lookup and return any previously cached dispatch."""
        self.reads += 1
        return self.entries.get(key)

    async def put(self, key: str, dispatch: StructuredDispatch) -> None:
        """Record and retain a new dispatch."""
        self.writes += 1
        self.entries[key] = dispatch

    async def ping(self) -> bool:
        """Declare this in-process fake ready without a network operation."""
        return True


@dataclass
class _Dispatcher:
    """Simulate successful, failing, and slow asynchronous generation."""

    calls: list[str] = field(default_factory=list)
    failure: Exception | None = None
    delay: float = 0.0
    closed: bool = False

    async def dispatch(self, text: str) -> StructuredDispatch:
        """Record exact input and yield so concurrent misses can overlap."""
        self.calls.append(text)
        await asyncio.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return _DISPATCH.model_copy(deep=True)

    async def aclose(self) -> None:
        """Make lifecycle ownership observable to tests."""
        self.closed = True


@dataclass
class _Harness:
    """Collect a running real ASGI application and its controllable adapters."""

    app: FastAPI
    client: httpx.AsyncClient
    cache: _MemoryCache
    dispatcher: _Dispatcher
    store: InMemoryDispatchStore
    settings: Settings
    logs: StringIO


@pytest.fixture
def settings() -> Settings:
    """Provide complete credentials and a concrete production origin for tests."""
    return Settings(
        gemini_api_key=SecretStr("http-test-gemini-key"),
        ingest_api_key=SecretStr(_KEY),
        gemini_model="gemini-test-model",
        production_origin="https://dispatch.example.com",
        upstream_timeout_seconds=0.2,
        cache_timeout_seconds=0.01,
        cloud_project_id="dispatch-project",
    )


async def test_service_error_handler_falls_back_for_unexpected_exceptions() -> None:
    """Keep a misrouted exception private even when the service handler sees it."""
    response = await _service_error_handler(
        _handler_request(), RuntimeError("private-service-detail")
    )

    assert response.status_code == 500
    error = APIResponse[ErrorDetails].model_validate_json(bytes(response.body))
    assert error.data.code == "internal_error"
    assert error.data.message == "An internal server error occurred."
    assert error.data.request_id == "handler-request-id"
    assert b"private-service-detail" not in response.body


async def test_http_error_handler_falls_back_for_unexpected_exceptions() -> None:
    """Keep a misrouted exception private even when the HTTP handler sees it."""
    response = await _http_error_handler(
        _handler_request(), RuntimeError("private-http-detail")
    )

    assert response.status_code == 500
    error = APIResponse[ErrorDetails].model_validate_json(bytes(response.body))
    assert error.data.code == "internal_error"
    assert error.data.message == "An internal server error occurred."
    assert b"private-http-detail" not in response.body


@pytest.fixture
async def harness(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_Harness]:
    """Run the full lifespan and real slowapi limiter using an offline backend."""
    logs = StringIO()
    handler = logging.StreamHandler(logs)
    handler.setFormatter(JsonFormatter())
    for name in ("app", "slowapi"):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", [handler])
        monkeypatch.setattr(logger, "propagate", False)
        monkeypatch.setattr(logger, "level", logging.INFO)
    # Keep a stable stream across pytest's separate setup/call/teardown captures.
    monkeypatch.setattr("app.main.configure_logging", Mock())
    cache = _MemoryCache()
    dispatcher = _Dispatcher()
    store = InMemoryDispatchStore(10)
    gate = SlowAPIRateGate(settings, storage_uri="memory://")
    app = create_app(settings, Runtime(cache, dispatcher, store, gate))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
            base_url="https://testserver",
            headers={"X-API-Key": _KEY},
        ) as client,
    ):
        yield _Harness(app, client, cache, dispatcher, store, settings, logs)
    await gate.aclose()
    assert not dispatcher.closed, "Injected resources remain caller-owned."


async def test_health_is_live_without_upstream_calls(harness: _Harness) -> None:
    """Report a typed healthy process without querying the cache or provider."""
    response = await harness.client.get("/health")
    assert response.status_code == 200
    result = APIResponse[HealthState].model_validate_json(response.content)
    assert result.status == "success"
    assert result.data.state == "running"
    assert result.data.uptime_seconds >= 0.0
    assert result.latency_ms >= 0.0
    assert not result.cached
    assert harness.cache.reads == 0
    assert harness.dispatcher.calls == []
    assert UUID(response.headers["x-request-id"])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["server-timing"].startswith("app;dur=")


async def test_miss_persists_then_hit_returns_immediately(harness: _Harness) -> None:
    """Persist and cache a miss, then avoid generation and writes on a hit."""
    text = f"  {_TEXT}\n"
    first = await harness.client.post("/api/ingest", json={"text": text})
    assert first.status_code == 200
    result = APIResponse[StructuredDispatch].model_validate_json(first.content)
    assert result.data == _DISPATCH
    assert not result.cached
    assert harness.dispatcher.calls == [text]
    assert await harness.store.get(harness.cache.key_for(text)) == _DISPATCH
    before = harness.cache.reads
    second = await harness.client.post("/api/ingest", json={"text": text})
    assert second.status_code == 200
    assert APIResponse[StructuredDispatch].model_validate_json(second.content).cached
    assert harness.cache.reads == before + 1
    assert harness.cache.writes == 1
    assert harness.dispatcher.calls == [text]


@pytest.mark.parametrize(
    "text",
    ["a" * 5, "a" * 15000, "\U0001f680" * 15000],
    ids=["minimum", "maximum", "unicode-maximum"],
)
async def test_ingest_accepts_exact_character_bounds(
    harness: _Harness, text: str
) -> None:
    """Accept min/max character lengths, including JSON-escaped non-BMP Unicode."""
    response = await harness.client.post("/api/ingest", json={"text": text})
    assert response.status_code == 200
    assert harness.dispatcher.calls == [text]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": "tiny"},
        {"text": " " * 5},
        {"text": "a" * 15001},
        {"text": 12345},
        {"text": True},
        {"text": ["one", "two"]},
        {"text": "valid text", "unexpected": "secret-validation-content"},
    ],
)
async def test_validation_is_strict_and_sanitized(
    harness: _Harness, payload: dict[str, object]
) -> None:
    """Reject coercion, extras, blanks, and length errors without echoing input."""
    response = await harness.client.post("/api/ingest", json=payload)
    assert response.status_code == 422
    error = APIResponse[ErrorDetails].model_validate_json(response.content)
    assert error.data.code == "validation_error"
    assert error.data.request_id == response.headers["x-request-id"]
    assert "secret-validation-content" not in response.text
    assert "input" not in response.text
    assert "Traceback" not in response.text
    assert harness.dispatcher.calls == []


@pytest.mark.parametrize("key", [None, "incorrect", "\u00e9" * 40, "x" * 513])
async def test_ingest_authentication_rejects_bad_keys(
    harness: _Harness, key: str | None
) -> None:
    """Authenticate missing, incorrect, oversized, and non-ASCII headers safely."""
    harness.client.headers.pop("X-API-Key")
    headers = [(b"x-api-key", key.encode("latin-1"))] if key is not None else []
    response = await harness.client.post(
        "/api/ingest", json={"text": _TEXT}, headers=headers
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "ApiKey"
    assert harness.dispatcher.calls == []


async def test_duplicate_api_keys_are_rejected(harness: _Harness) -> None:
    """Avoid ambiguous authentication when a proxy preserves duplicate headers."""
    response = await harness.client.post(
        "/api/ingest",
        json={"text": _TEXT},
        headers=[("X-API-Key", _KEY), ("X-API-Key", _KEY)],
    )
    assert response.status_code == 401


async def test_sixth_ingest_is_throttled_even_if_cached(harness: _Harness) -> None:
    """Count cached calls and ignore spoofed forwarded-for headers as quota keys."""
    for index in range(5):
        response = await harness.client.post(
            "/api/ingest",
            json={"text": _TEXT},
            headers={"X-Forwarded-For": f"192.0.2.{index + 1}"},
        )
        assert response.status_code == 200
    response = await harness.client.post("/api/ingest", json={"text": _TEXT})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    error = APIResponse[ErrorDetails].model_validate_json(response.content)
    assert error.data.code == "rate_limit_exceeded"
    assert len(harness.dispatcher.calls) == 1
    assert (await harness.client.get("/health")).status_code == 200


async def test_concurrent_identical_misses_are_coalesced(harness: _Harness) -> None:
    """Perform only one generation for concurrent same-input local cache misses."""
    harness.dispatcher.delay = 0.01
    results = await asyncio.gather(
        *(harness.client.post("/api/ingest", json={"text": _TEXT}) for _ in range(5))
    )
    assert all(response.status_code == 200 for response in results)
    cached = [
        APIResponse[StructuredDispatch].model_validate_json(response.content).cached
        for response in results
    ]
    assert cached.count(False) == 1
    assert cached.count(True) == 4
    assert harness.dispatcher.calls == [_TEXT]


@pytest.mark.parametrize(
    "origin",
    ["https://dispatch.example.com", "http://localhost:3000", "http://127.0.0.1:3000"],
)
async def test_cors_allows_only_configured_origins(
    harness: _Harness, origin: str
) -> None:
    """Allow concrete origins and headers without wildcard or cookie credentials."""
    preflight = await harness.client.options(
        "/api/ingest",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-API-Key,Content-Type,X-Request-ID",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in preflight.headers
    assert "x-request-id" in preflight.headers
    response = await harness.client.get("/health", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


async def test_cors_does_not_authorize_untrusted_origins(harness: _Harness) -> None:
    """Disallow cross-origin reads; CORS is not used as authentication."""
    response = await harness.client.options(
        "/api/ingest",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert harness.dispatcher.calls == []


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_provider_failures_use_standard_envelopes(
    harness: _Harness, status: int
) -> None:
    """Return classified upstream failures without writing partial cache/store data."""
    harness.dispatcher.failure = ServiceError(
        "upstream_failure", "The dispatch provider request failed.", status
    )
    response = await harness.client.post("/api/ingest", json={"text": _TEXT})
    assert response.status_code == status
    result = APIResponse[ErrorDetails].model_validate_json(response.content)
    assert result.status == "error"
    assert not result.cached
    assert result.data.code == "upstream_failure"
    assert harness.cache.writes == 0
    assert await harness.store.get(harness.cache.key_for(_TEXT)) is None


async def test_unexpected_error_is_private_in_logs_and_cors_response(
    harness: _Harness,
) -> None:
    """Contain a raw exception even with ASGI exception propagation enabled."""
    secret = "private-upstream-api-key-and-input"
    harness.dispatcher.failure = RuntimeError(secret)
    response = await harness.client.post(
        "/api/ingest",
        json={"text": _TEXT},
        headers={"Origin": harness.settings.production_origin},
    )
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == (
        harness.settings.production_origin
    )
    assert secret not in response.text
    assert "Traceback" not in response.text
    logs = harness.logs.getvalue()
    assert secret not in logs
    assert _TEXT not in logs
    assert _KEY not in logs
    assert all(isinstance(json.loads(line), dict) for line in logs.splitlines())


async def test_request_ids_are_validated_and_correlate_logs(
    harness: _Harness,
) -> None:
    """Use only valid UUIDs and correlate start/completion JSON log records."""
    request_id = str(uuid4())
    response = await harness.client.get("/health", headers={"X-Request-ID": request_id})
    assert response.headers["x-request-id"] == request_id
    records = [json.loads(line) for line in harness.logs.getvalue().splitlines()]
    requests = [record for record in records if record.get("request_id") == request_id]
    assert [record["message"] for record in requests] == [
        "request_started",
        "request_completed",
    ]
    assert requests[-1]["httpRequest"]["status"] == 200
    malicious_id = "untrusted-log-content"
    other = await harness.client.get("/health", headers={"X-Request-ID": malicious_id})
    assert UUID(other.headers["x-request-id"])
    assert other.headers["x-request-id"] != malicious_id
    assert malicious_id not in harness.logs.getvalue()
    malformed_id = "z" * 36
    invalid = await harness.client.get(
        "/health", headers={"X-Request-ID": malformed_id}
    )
    assert UUID(invalid.headers["x-request-id"])
    assert invalid.headers["x-request-id"] != malformed_id
    assert malformed_id not in harness.logs.getvalue()


async def test_malformed_json_is_not_echoed(harness: _Harness) -> None:
    """Sanitize parser errors without returning the invalid request body."""
    response = await harness.client.post(
        "/api/ingest",
        content=b'{"text":"private-malformed-json",',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "private-malformed-json" not in response.text


@pytest.mark.parametrize(
    "headers", [{"Content-Type": "text/plain"}, {"Content-Encoding": "gzip"}]
)
async def test_ingest_rejects_non_json_and_compressed_bodies(
    harness: _Harness, headers: dict[str, str]
) -> None:
    """Reject unsupported representations before expensive parsing or generation."""
    response = await harness.client.post(
        "/api/ingest", json={"text": _TEXT}, headers=headers
    )
    assert response.status_code == 415
    assert harness.dispatcher.calls == []


async def test_declared_and_chunked_oversized_bodies_are_rejected(
    harness: _Harness,
) -> None:
    """Apply byte limits even if a client omits the Content-Length header."""
    oversized = b"x" * (harness.settings.max_request_bytes + 1)
    response = await harness.client.post(
        "/api/ingest", content=oversized, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413

    async def chunks() -> AsyncIterator[bytes]:
        """Yield an HTTP upload as multiple chunks without a declared length."""
        yield oversized[:100]
        yield oversized[100:]

    response = await harness.client.post(
        "/api/ingest", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert harness.dispatcher.calls == []


async def test_missing_and_wrong_method_routes_are_standardized(
    harness: _Harness,
) -> None:
    """Keep 404/405 consistent and disable interactive production documentation."""
    for path in ("/missing", "/docs", "/redoc", "/openapi.json"):
        response = await harness.client.get(path)
        assert response.status_code == 404
        error = APIResponse[ErrorDetails].model_validate_json(response.content)
        assert error.status == "error"
    response = await harness.client.get("/api/ingest")
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


async def test_deadline_bounds_single_flight_wait_and_generation(
    harness: _Harness,
) -> None:
    """Prevent queued extraction work from holding requests indefinitely."""
    harness.dispatcher.delay = 1.0
    response = await harness.client.post("/api/ingest", json={"text": _TEXT})
    assert response.status_code == 504
    error = APIResponse[ErrorDetails].model_validate_json(response.content)
    assert error.data.code == "upstream_timeout"


async def test_fully_escaped_non_bmp_input_fits_transport_bound(
    harness: _Harness,
) -> None:
    """Support all 15000 characters even when encoded as surrogate-pair escapes."""
    text = "\U0001f680" * 15000
    body = json.dumps({"text": text}, ensure_ascii=True).encode("ascii")
    response = await harness.client.post(
        "/api/ingest", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert harness.dispatcher.calls == [text]


async def test_internal_response_validation_is_sanitized(harness: _Harness) -> None:
    """Apply the global response validation handler without exposing output data."""

    async def invalid_health() -> dict[str, str]:
        """Return intentionally wrong application output to exercise the boundary."""
        return {"private-field": "private-response-validation-data"}

    harness.app.add_api_route(
        "/invalid-response", invalid_health, response_model=HealthState
    )
    response = await harness.client.get("/invalid-response")
    assert response.status_code == 500
    assert "private-response-validation-data" not in response.text
    assert "private-response-validation-data" not in harness.logs.getvalue()


async def test_cloud_trace_is_validated_and_reset_between_requests(
    harness: _Harness,
) -> None:
    """Correlate valid Cloud Run trace IDs without leaking context to later calls."""
    trace = "a" * 32
    first = await harness.client.get(
        "/health", headers={"X-Cloud-Trace-Context": f"{trace}/1;o=1"}
    )
    second = await harness.client.get(
        "/health", headers={"X-Cloud-Trace-Context": "invalid-untrusted-trace"}
    )
    records = [json.loads(line) for line in harness.logs.getvalue().splitlines()]
    first_logs = [
        record
        for record in records
        if record.get("request_id") == first.headers["x-request-id"]
    ]
    second_logs = [
        record
        for record in records
        if record.get("request_id") == second.headers["x-request-id"]
    ]
    assert all(
        record["logging.googleapis.com/trace"]
        == f"projects/dispatch-project/traces/{trace}"
        for record in first_logs
    )
    assert all("logging.googleapis.com/trace" not in record for record in second_logs)
    assert "invalid-untrusted-trace" not in harness.logs.getvalue()


async def test_logs_do_not_record_raw_paths_or_query_secrets(harness: _Harness) -> None:
    """Log route templates, never unrecognized URL paths or query strings."""
    response = await harness.client.get(
        "/private-path-sensitive-value?api_key=private-query-sensitive-value"
    )
    assert response.status_code == 404
    assert "private-path-sensitive-value" not in harness.logs.getvalue()
    assert "private-query-sensitive-value" not in harness.logs.getvalue()


async def test_analyse_happy_path_returns_dispatch(harness: _Harness) -> None:
    """POST /api/analyse with valid context returns a typed StructuredDispatch."""
    payload = {
        "text": _TEXT,
        "domain": "operations",
        "urgency_hint": "urgent",
        "language_hint": "en",
    }
    response = await harness.client.post("/api/analyse", json=payload)
    assert response.status_code == 200
    result = APIResponse[StructuredDispatch].model_validate_json(response.content)
    assert result.status == "success"
    assert result.data == _DISPATCH
    assert not result.cached
    assert result.latency_ms >= 0.0
    # Dispatcher receives the context-prefixed prompt, not bare text.
    assert len(harness.dispatcher.calls) == 1
    assert harness.dispatcher.calls[0].startswith(
        "[domain:operations][urgency:urgent][lang:en]\n"
    )


async def test_analyse_defaults_produce_context_prefixed_prompt(
    harness: _Harness,
) -> None:
    """Defaults (general, routine, None lang) build a correct prompt prefix."""
    response = await harness.client.post("/api/analyse", json={"text": _TEXT})
    assert response.status_code == 200
    assert len(harness.dispatcher.calls) == 1
    prompt = harness.dispatcher.calls[0]
    assert prompt.startswith("[domain:general][urgency:routine][lang:auto]\n")
    assert _TEXT in prompt


async def test_analyse_hit_skips_generation(harness: _Harness) -> None:
    """A cached analyse result bypasses Gemini and reports cached=True."""
    payload = {"text": _TEXT, "domain": "security"}
    first = await harness.client.post("/api/analyse", json=payload)
    assert first.status_code == 200
    assert not APIResponse[StructuredDispatch].model_validate_json(first.content).cached

    second = await harness.client.post("/api/analyse", json=payload)
    assert second.status_code == 200
    assert APIResponse[StructuredDispatch].model_validate_json(second.content).cached
    assert harness.dispatcher.calls.count(harness.dispatcher.calls[0]) == 1


async def test_analyse_cache_keys_are_domain_isolated(harness: _Harness) -> None:
    """Different domains produce different cache entries and separate Gemini calls."""
    await harness.client.post(
        "/api/analyse", json={"text": _TEXT, "domain": "security"}
    )
    await harness.client.post("/api/analyse", json={"text": _TEXT, "domain": "finance"})
    # Both are misses — two separate prompts sent to Gemini.
    assert len(harness.dispatcher.calls) == 2
    assert harness.dispatcher.calls[0] != harness.dispatcher.calls[1]


async def test_analyse_rejects_invalid_domain(harness: _Harness) -> None:
    """Unknown domain values are rejected as validation errors."""
    response = await harness.client.post(
        "/api/analyse", json={"text": _TEXT, "domain": "logistics"}
    )
    assert response.status_code == 422
    error = APIResponse[ErrorDetails].model_validate_json(response.content)
    assert error.data.code == "validation_error"
    assert harness.dispatcher.calls == []


async def test_analyse_requires_authentication(harness: _Harness) -> None:
    """The /api/analyse endpoint enforces the same key auth as /api/ingest."""
    harness.client.headers.pop("X-API-Key")
    response = await harness.client.post("/api/analyse", json={"text": _TEXT})
    assert response.status_code == 401
    assert harness.dispatcher.calls == []


async def test_analyse_body_middleware_enforces_json(harness: _Harness) -> None:
    """BoundedBodyMiddleware rejects non-JSON content-types for /api/analyse."""
    response = await harness.client.post(
        "/api/analyse",
        content=b"raw bytes",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415


async def test_analyse_validates_language_hint_pattern(harness: _Harness) -> None:
    """Invalid language_hint values are rejected with 422 without calling Gemini."""
    for lang in ("EN", "x", "1234", "en-gb", "toolong123"):
        response = await harness.client.post(
            "/api/analyse", json={"text": _TEXT, "language_hint": lang}
        )
        assert response.status_code == 422, f"Expected 422 for lang={lang!r}"
    assert harness.dispatcher.calls == []


async def test_analyse_route_is_logged_with_template_not_raw_path(
    harness: _Harness,
) -> None:
    """The observability layer logs the route template, not raw URL data."""
    await harness.client.post("/api/analyse", json={"text": _TEXT})
    logs = harness.logs.getvalue()
    assert "/api/analyse" in logs
    assert _TEXT not in logs


async def test_analyse_concurrent_identical_misses_are_coalesced(
    harness: _Harness,
) -> None:
    """Perform only one generation for concurrent local misses on analyse."""
    harness.dispatcher.delay = 0.01
    payload = {"text": _TEXT, "domain": "operations"}
    results = await asyncio.gather(
        *(harness.client.post("/api/analyse", json=payload) for _ in range(5))
    )
    assert all(response.status_code == 200 for response in results)
    cached = [
        APIResponse[StructuredDispatch].model_validate_json(response.content).cached
        for response in results
    ]
    assert cached.count(False) == 1
    assert cached.count(True) == 4


async def test_analyse_deadline_bounds_generation(harness: _Harness) -> None:
    """Prevent queued analyse extraction work from holding requests indefinitely."""
    harness.dispatcher.delay = 1.0
    response = await harness.client.post("/api/analyse", json={"text": _TEXT})
    assert response.status_code == 504
    error = APIResponse[ErrorDetails].model_validate_json(response.content)
    assert error.data.code == "upstream_timeout"
