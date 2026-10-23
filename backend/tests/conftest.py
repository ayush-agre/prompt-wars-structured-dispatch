"""Standalone offline ingest fixtures; no live credentials or services are used.

The production application is exercised through its real lifespan and ASGI stack.
Only Gemini's SDK/network boundary and Redis's client boundary are replaced. The
real slowapi moving-window limiter uses an explicitly test-only memory backend.
"""

from __future__ import annotations

import os
import socket
import warnings
from collections.abc import AsyncIterator, Callable
from importlib import import_module
from types import SimpleNamespace
from typing import NoReturn, cast
from unittest.mock import AsyncMock, Mock

# The retained google-generativeai SDK emits this known lifecycle warning while
# importing the test boundary. Keep the explicit project-wide filter in
# pyproject.toml effective even when a caller adds `-W error` on the command line.
warnings.filterwarnings("ignore", category=FutureWarning)

import httpx  # noqa: E402
import limits.storage.memory as memory_storage  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from google.ai import generativelanguage_v1beta as glm  # noqa: E402
from google.generativeai import client as genai_client  # noqa: E402
from google.generativeai.generative_models import GenerativeModel  # noqa: E402
from google.generativeai.types.generation_types import (  # noqa: E402
    AsyncGenerateContentResponse,
)
from limits.storage import MemoryStorage  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import StructuredDispatch  # noqa: E402
from app.throttling import SlowAPIRateGate  # noqa: E402


def _deny_network(*args: object, **kwargs: object) -> NoReturn:
    """Fail immediately, without logging potentially secret-bearing arguments."""
    pytest.fail(
        "Network access is forbidden: use the Gemini/Redis mocks or ASGITransport.",
        pytrace=False,
    )


@pytest_asyncio.fixture(autouse=True)
async def forbid_network(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Block DNS, outbound sockets, HTTP transports, and native gRPC channels.

    Install guards after pytest-asyncio creates its event loop: on Windows the
    loop may need an internal socket pair. Remove guards before loop teardown.
    pytest.fail raises outside Exception, so application error handlers cannot
    silently turn a forbidden network attempt into a passing HTTP response.
    """
    with monkeypatch.context() as patch:
        for name in (
            "create_connection",
            "getaddrinfo",
            "gethostbyname",
            "gethostbyname_ex",
            "gethostbyaddr",
        ):
            patch.setattr(socket, name, _deny_network)
        for name in ("connect", "connect_ex", "sendto", "sendmsg"):
            if hasattr(socket.socket, name):
                patch.setattr(socket.socket, name, _deny_network)
        # C-backed gRPC can bypass Python's socket methods entirely.
        for module in (import_module("grpc"), import_module("grpc.aio")):
            patch.setattr(module, "secure_channel", _deny_network)
            patch.setattr(module, "insecure_channel", _deny_network)
        patch.setattr(httpx.HTTPTransport, "handle_request", _deny_network)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _deny_network)
        yield


@pytest.fixture
def ingest_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Ignore developer/CI credentials and supply only synthetic configuration."""
    for name in tuple(os.environ):
        if name.upper().startswith("DISPATCH_") or name.upper() in {
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
        }:
            monkeypatch.delenv(name, raising=False)
    return Settings(
        gemini_api_key=SecretStr("offline-gemini-key-not-a-real-credential"),
        ingest_api_key=SecretStr("offline-ingest-key-at-least-thirty-two-bytes"),
        gemini_model="gemini-offline-test-model",
        production_origin="https://dispatch.example.test",
        redis_url=SecretStr("redis://redis.invalid:6379/0"),
        upstream_timeout_seconds=5.0,
        cache_timeout_seconds=1.0,
        max_concurrent_generations=8,
        store_max_entries=20,
        max_request_bytes=196608,
        body_timeout_seconds=5.0,
        log_level="WARNING",
        cloud_project_id=None,
    )


@pytest.fixture
def request_payload() -> dict[str, str]:
    """Return fresh valid input for every test; preserve its exact raw text."""
    return {"text": "Station 7 reports a pump failure; send maintenance."}


@pytest.fixture
def structured_dispatch() -> StructuredDispatch:
    """Validate all required structured fields before using this mock response."""
    return StructuredDispatch(
        title="Pump failure",
        severity_level="high",
        category="maintenance",
        key_entities=["Station 7"],
        confidence_score=0.95,
        action_items=["Send maintenance"],
        reasoning_summary="Pump failure at Station 7 requires immediate maintenance.",
        domain_tags=["infrastructure", "maintenance"],
    )


@pytest.fixture
def clock() -> Mock:
    """Provide controllable seconds for Redis expiry and the real rate limiter."""
    return Mock(name="offline_clock", return_value=1_700_000_000.0)


@pytest.fixture
def redis_data() -> dict[str, str]:
    """Keep isolated Redis string values in memory for each individual test."""
    return {}


@pytest.fixture
def redis_expirations() -> dict[str, float]:
    """Keep absolute expiration times without sleeping or using wall-clock time."""
    return {}


@pytest.fixture
def redis_mock(
    redis_data: dict[str, str], redis_expirations: dict[str, float], clock: Mock
) -> Mock:
    """Fake redis.asyncio with observable get, set, setex, ping, and aclose.

    SET with EX and SETEX share the same storage semantics, but retain separate
    mock histories: the suite must not claim production called SETEX when it
    actually called SET(..., ex=3600). Unsupported client methods fail closed.
    """

    async def get(key: str) -> str | None:
        """Read a string, treating a key at or beyond its expiry as absent."""
        expiry = redis_expirations.get(key)
        if expiry is not None and expiry <= cast(float, clock()):
            redis_data.pop(key, None)
            redis_expirations.pop(key, None)
        return redis_data.get(key)

    def write(key: str, value: str, seconds: int) -> bool:
        """Implement only the positive-TTL string writes used by this service."""
        if seconds <= 0:
            raise ValueError("The fake Redis requires a positive expiry.")
        redis_data[key] = value
        redis_expirations[key] = cast(float, clock()) + seconds
        return True

    async def set_value(key: str, value: str, *, ex: int) -> bool:
        """Implement the application's actual SET key value EX seconds call."""
        return write(key, value, ex)

    async def setex(key: str, seconds: int, value: str) -> bool:
        """Support the equivalent SETEX API for direct fixture seeding."""
        return write(key, value, seconds)

    client = Mock(spec_set=Redis, name="offline_redis")
    client.get = AsyncMock(name="redis.get", side_effect=get)
    client.set = AsyncMock(name="redis.set", side_effect=set_value)
    client.setex = AsyncMock(name="redis.setex", side_effect=setex)
    client.ping = AsyncMock(name="redis.ping", return_value=True)
    client.aclose = AsyncMock(name="redis.aclose")
    client.execute_command = AsyncMock(side_effect=_deny_network)
    return client


@pytest.fixture
def gemini_response_factory() -> Callable[[str], AsyncGenerateContentResponse]:
    """Build genuine SDK response objects without constructing a network client."""

    def make_response(text: str) -> AsyncGenerateContentResponse:
        """Include a single unblocked, completed, text-only model candidate."""
        raw = glm.GenerateContentResponse(
            candidates=[
                glm.Candidate(
                    index=0,
                    finish_reason=glm.Candidate.FinishReason.STOP,
                    content=glm.Content(role="model", parts=[glm.Part(text=text)]),
                )
            ]
        )
        return cast(
            AsyncGenerateContentResponse,
            AsyncGenerateContentResponse.from_response(raw),
        )

    return make_response


@pytest.fixture
def gemini_response(
    gemini_response_factory: Callable[[str], AsyncGenerateContentResponse],
    structured_dispatch: StructuredDispatch,
) -> AsyncGenerateContentResponse:
    """Return schema-valid JSON wrapped in the SDK's real response container."""
    return gemini_response_factory(structured_dispatch.model_dump_json())


@pytest.fixture
def gemini_sdk_client(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Prevent even constructing a gRPC channel or consulting real credentials."""
    transport = Mock(spec_set=["close"])
    transport.close = AsyncMock(name="gemini.transport.close")
    client = Mock(spec_set=["transport", "generate_content"])
    client.transport = transport
    client.generate_content = AsyncMock(side_effect=_deny_network)
    monkeypatch.setattr(
        vars(genai_client)["glm"],
        "GenerativeServiceAsyncClient",
        Mock(return_value=client),
    )
    return client


@pytest.fixture
def gemini_mock(
    monkeypatch: pytest.MonkeyPatch,
    gemini_sdk_client: Mock,
    gemini_response: AsyncGenerateContentResponse,
) -> AsyncMock:
    """Mock the public async SDK call, not the application's dispatch adapter."""
    generate = AsyncMock(
        name="GenerativeModel.generate_content_async", return_value=gemini_response
    )
    monkeypatch.setattr(GenerativeModel, "generate_content_async", generate)
    monkeypatch.setattr(
        GenerativeModel, "generate_content", Mock(side_effect=_deny_network)
    )
    return generate


@pytest_asyncio.fixture
async def rate_gate(
    ingest_settings: Settings, clock: Mock, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SlowAPIRateGate]:
    """Keep real throttling and deterministic time, but never connect to Redis."""
    # Replace only this backend's time reference, not process-wide time.time.
    monkeypatch.setattr(memory_storage, "time", SimpleNamespace(time=clock))
    gate = SlowAPIRateGate(ingest_settings, storage_uri="memory://")
    storage = gate.limiter.limiter.storage
    assert isinstance(storage, MemoryStorage)
    try:
        yield gate
    finally:
        await gate.aclose()
        # limits' memory storage owns a short-lived cleanup thread. Do not leak
        # that thread or quota state across tests, including failed tests.
        storage.timer.cancel()
        storage.timer.join(timeout=1.0)
        storage.reset()


@pytest_asyncio.fixture
async def app(
    ingest_settings: Settings,
    redis_mock: Mock,
    gemini_mock: AsyncMock,
    gemini_sdk_client: Mock,
    rate_gate: SlowAPIRateGate,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """Run production startup/shutdown with fresh fake clients for every test."""
    monkeypatch.setattr("app.main.Redis.from_url", Mock(return_value=redis_mock))
    monkeypatch.setattr("app.main.SlowAPIRateGate", Mock(return_value=rate_gate))
    # Leave pytest's logging capture intact; do not retain its temporary streams.
    monkeypatch.setattr("app.main.configure_logging", Mock())
    application = create_app(ingest_settings)
    async with application.router.lifespan_context(application):
        redis_mock.ping.assert_awaited_once_with()
        yield application
    redis_mock.aclose.assert_awaited_once_with()
    gemini_sdk_client.transport.close.assert_awaited_once_with()
    gemini_sdk_client.generate_content.assert_not_called()


@pytest_asyncio.fixture
async def client(
    app: FastAPI, ingest_settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """Send requests in-process; ASGITransport does not manage lifespan itself."""
    transport = httpx.ASGITransport(
        app=app, client=("192.0.2.10", 12345), raise_app_exceptions=True
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-API-Key": ingest_settings.ingest_api_key.get_secret_value()},
        trust_env=False,
        follow_redirects=False,
    ) as test_client:
        yield test_client
