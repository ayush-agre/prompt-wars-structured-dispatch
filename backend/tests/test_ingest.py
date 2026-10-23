"""Exercise real ingest behavior with the standalone offline conftest fixtures.

Run from the project root: pytest --cov=app --cov-report=term-missing
No other test modules, Redis server, Gemini API key, or network are required.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from google.api_core import exceptions as google_errors
from google.generativeai.types.generation_types import AsyncGenerateContentResponse
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.main import Runtime
from app.models import APIResponse, ErrorDetails, StructuredDispatch

pytestmark = pytest.mark.asyncio
_INGEST = "/api/ingest"
_PRIVATE_ERROR = "offline-private-provider-detail-not-for-clients"


def _assert_error(
    response: httpx.Response, *, status_code: int, code: str
) -> APIResponse[ErrorDetails]:
    """Require a sanitized JSON error contract rather than arbitrary error text."""
    assert response.status_code == status_code
    assert response.headers["content-type"].split(";", maxsplit=1)[0] == (
        "application/json"
    )
    result = APIResponse[ErrorDetails].model_validate_json(
        response.content, strict=True
    )
    assert result.status == "error"
    assert result.cached is False
    assert result.latency_ms >= 0.0
    assert result.data.code == code
    assert result.data.message
    assert result.data.request_id == response.headers["x-request-id"]
    assert str(UUID(result.data.request_id)) == result.data.request_id
    assert "traceback" not in response.text.lower()
    assert _PRIVATE_ERROR not in response.text
    return result


async def test_successful_ingestion_on_cache_miss(
    client: httpx.AsyncClient,
    app: FastAPI,
    ingest_settings: Settings,
    request_payload: dict[str, str],
    structured_dispatch: StructuredDispatch,
    gemini_mock: AsyncMock,
    redis_mock: Mock,
    redis_data: dict[str, str],
    redis_expirations: dict[str, float],
    clock: Mock,
) -> None:
    """Generate, strictly validate, persist, and cache an initially absent input."""
    runtime = cast(Runtime, app.state.runtime)
    key = runtime.cache.key_for(request_payload["text"])
    assert redis_data == {}

    response = await client.post(_INGEST, json=request_payload)

    assert response.status_code == 200
    result = APIResponse[StructuredDispatch].model_validate_json(
        response.content, strict=True
    )
    assert result.status == "success"
    assert result.data == structured_dispatch
    assert result.cached is False
    assert result.latency_ms >= 0.0
    gemini_mock.assert_awaited_once_with(
        request_payload["text"],
        request_options={
            "timeout": ingest_settings.upstream_timeout_seconds,
            "retry": None,
        },
    )
    redis_mock.get.assert_awaited_with(key)
    # Production uses SET with EX, not SETEX; assert the actual boundary call.
    redis_mock.set.assert_awaited_once_with(
        key, structured_dispatch.model_dump_json(), ex=3600
    )
    assert StructuredDispatch.model_validate_json(redis_data[key]) == (
        structured_dispatch
    )
    assert redis_expirations[key] == clock() + 3600
    assert await runtime.store.get(key) == structured_dispatch


async def test_successful_ingestion_on_cache_hit(
    client: httpx.AsyncClient,
    app: FastAPI,
    request_payload: dict[str, str],
    structured_dispatch: StructuredDispatch,
    gemini_mock: AsyncMock,
    redis_mock: Mock,
) -> None:
    """Preseed Redis directly: a hit must never call Gemini, even for warm-up."""
    runtime = cast(Runtime, app.state.runtime)
    key = runtime.cache.key_for(request_payload["text"])
    await redis_mock.setex(key, 3600, structured_dispatch.model_dump_json())
    redis_mock.setex.reset_mock()  # Seeding is not an application cache write.

    response = await client.post(_INGEST, json=request_payload)

    assert response.status_code == 200
    result = APIResponse[StructuredDispatch].model_validate_json(
        response.content, strict=True
    )
    assert result.status == "success"
    assert result.data == structured_dispatch
    assert result.cached is True
    gemini_mock.assert_not_called()
    gemini_mock.assert_not_awaited()
    redis_mock.get.assert_awaited_once_with(key)
    redis_mock.set.assert_not_called()
    redis_mock.setex.assert_not_called()
    assert await runtime.store.get(key) is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-text"),
        pytest.param({"text": ""}, id="empty-text"),
        pytest.param({"text": "a"}, id="one-character"),
        pytest.param({"text": "abcd"}, id="below-minimum"),
        pytest.param({"text": " " * 5}, id="whitespace-only"),
        pytest.param({"text": "a" * 15001}, id="above-maximum"),
        pytest.param({"text": None}, id="null"),
        pytest.param({"text": 12345}, id="integer"),
        pytest.param({"text": True}, id="boolean"),
        pytest.param({"text": ["alarm"]}, id="array"),
        pytest.param(
            {"text": "valid alarm", "unexpected": _PRIVATE_ERROR}, id="unknown-field"
        ),
    ],
)
async def test_input_validation_failure(
    client: httpx.AsyncClient,
    payload: dict[str, object],
    gemini_mock: AsyncMock,
    redis_mock: Mock,
) -> None:
    """Return 422 without cache/generation work for invalid raw request data."""
    response = await client.post(_INGEST, json=payload)

    _assert_error(response, status_code=422, code="validation_error")
    gemini_mock.assert_not_called()
    redis_mock.get.assert_not_called()
    redis_mock.set.assert_not_called()
    redis_mock.setex.assert_not_called()


@pytest.mark.parametrize(
    ("error_factory", "status_code", "code"),
    [
        pytest.param(google_errors.PermissionDenied, 502, "upstream_failure", id="api"),
        pytest.param(
            google_errors.InvalidArgument, 502, "upstream_failure", id="bad-api-request"
        ),
        pytest.param(RuntimeError, 500, "internal_error", id="unexpected"),
        pytest.param(
            google_errors.ServiceUnavailable, 503, "upstream_unavailable", id="outage"
        ),
        pytest.param(
            google_errors.ResourceExhausted, 503, "upstream_unavailable", id="quota"
        ),
        pytest.param(
            google_errors.DeadlineExceeded, 504, "upstream_timeout", id="sdk-timeout"
        ),
        pytest.param(TimeoutError, 504, "upstream_timeout", id="timeout"),
    ],
)
async def test_upstream_llm_exception_handling(
    client: httpx.AsyncClient,
    app: FastAPI,
    request_payload: dict[str, str],
    error_factory: Callable[[str], Exception],
    status_code: int,
    code: str,
    gemini_mock: AsyncMock,
    redis_mock: Mock,
    redis_data: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Map real SDK exception types to clean JSON; never cache a failed result."""
    gemini_mock.side_effect = error_factory(_PRIVATE_ERROR)

    response = await client.post(_INGEST, json=request_payload)

    _assert_error(response, status_code=status_code, code=code)
    assert _PRIVATE_ERROR not in caplog.text
    gemini_mock.assert_awaited_once()
    redis_mock.set.assert_not_called()
    redis_mock.setex.assert_not_called()
    assert redis_data == {}
    runtime = cast(Runtime, app.state.runtime)
    key = runtime.cache.key_for(request_payload["text"])
    assert await runtime.store.get(key) is None


async def test_rate_limit_trigger_verification(
    client: httpx.AsyncClient,
    request_payload: dict[str, str],
    gemini_mock: AsyncMock,
    redis_mock: Mock,
) -> None:
    """Allow exactly five requests, count cache hits, and reject the sixth."""
    for index in range(5):
        response = await client.post(
            _INGEST,
            json=request_payload,
            headers={"X-Forwarded-For": f"192.0.2.{index + 20}"},
        )
        assert response.status_code == 200
        result = APIResponse[StructuredDispatch].model_validate_json(response.content)
        assert result.cached is (index > 0)
    reads_before_limit = redis_mock.get.await_count

    response = await client.post(_INGEST, json=request_payload)

    _assert_error(response, status_code=429, code="rate_limit_exceeded")
    assert response.headers["retry-after"] == "60"
    assert redis_mock.get.await_count == reads_before_limit
    gemini_mock.assert_awaited_once()
    redis_mock.set.assert_awaited_once()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a" * 5, id="minimum-length"),
        pytest.param("a" * 15000, id="maximum-length"),
        pytest.param("\U0001f680" * 15000, id="unicode-maximum"),
        pytest.param("  Station 7 pump failure.\n", id="whitespace-preserved"),
    ],
)
async def test_valid_text_boundaries_preserve_input(
    client: httpx.AsyncClient, text: str, gemini_mock: AsyncMock
) -> None:
    """Accept inclusive character limits without trimming or normalizing input."""
    response = await client.post(_INGEST, json={"text": text})

    assert response.status_code == 200
    result = APIResponse[StructuredDispatch].model_validate_json(response.content)
    assert result.cached is False
    gemini_mock.assert_awaited_once()
    assert gemini_mock.await_args is not None
    assert gemini_mock.await_args.args[0] == text


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="empty-body"),
        pytest.param(b'{"text":', id="malformed-json"),
        pytest.param(b"null", id="null-body"),
        pytest.param(b"[]", id="array-body"),
    ],
)
async def test_invalid_json_body_is_rejected_before_generation(
    client: httpx.AsyncClient, body: bytes, gemini_mock: AsyncMock, redis_mock: Mock
) -> None:
    """Keep parser and top-level shape failures in the same clean 422 envelope."""
    response = await client.post(
        _INGEST, content=body, headers={"Content-Type": "application/json"}
    )

    _assert_error(response, status_code=422, code="validation_error")
    gemini_mock.assert_not_called()
    redis_mock.get.assert_not_called()
    redis_mock.set.assert_not_called()


@pytest.mark.parametrize(
    "output",
    [
        pytest.param("", id="empty-output"),
        pytest.param(_PRIVATE_ERROR, id="non-json-output"),
        pytest.param("{}", id="missing-schema-fields"),
        pytest.param("[]", id="non-object-output"),
    ],
)
async def test_invalid_gemini_output_is_not_persisted_or_cached(
    client: httpx.AsyncClient,
    app: FastAPI,
    request_payload: dict[str, str],
    output: str,
    gemini_response_factory: Callable[[str], AsyncGenerateContentResponse],
    gemini_mock: AsyncMock,
    redis_mock: Mock,
    redis_data: dict[str, str],
) -> None:
    """Validate SDK text as StructuredDispatch instead of trusting JSON mode."""
    gemini_mock.return_value = gemini_response_factory(output)

    response = await client.post(_INGEST, json=request_payload)

    _assert_error(response, status_code=502, code="invalid_upstream_response")
    gemini_mock.assert_awaited_once()
    redis_mock.set.assert_not_called()
    redis_mock.setex.assert_not_called()
    assert redis_data == {}
    runtime = cast(Runtime, app.state.runtime)
    key = runtime.cache.key_for(request_payload["text"])
    assert await runtime.store.get(key) is None


@pytest.mark.parametrize(
    "cached_value",
    [
        pytest.param(_PRIVATE_ERROR, id="corrupt-json"),
        pytest.param("{}", id="invalid-schema"),
    ],
)
async def test_corrupt_cache_is_replaced_with_valid_generated_data(
    client: httpx.AsyncClient,
    app: FastAPI,
    request_payload: dict[str, str],
    structured_dispatch: StructuredDispatch,
    cached_value: str,
    redis_data: dict[str, str],
    redis_mock: Mock,
    gemini_mock: AsyncMock,
) -> None:
    """Treat untrusted cached bytes as a miss, never as a successful dispatch."""
    runtime = cast(Runtime, app.state.runtime)
    key = runtime.cache.key_for(request_payload["text"])
    redis_data[key] = cached_value

    response = await client.post(_INGEST, json=request_payload)

    assert response.status_code == 200
    result = APIResponse[StructuredDispatch].model_validate_json(response.content)
    assert result.cached is False
    assert result.data == structured_dispatch
    assert _PRIVATE_ERROR not in response.text
    gemini_mock.assert_awaited_once()
    redis_mock.set.assert_awaited_once_with(
        key, structured_dispatch.model_dump_json(), ex=3600
    )
    cached_dispatch = StructuredDispatch.model_validate_json(redis_data[key])
    assert cached_dispatch == structured_dispatch


@pytest.mark.parametrize("operation", ["get", "set"], ids=["read", "write"])
async def test_cache_client_failure_does_not_discard_success(
    client: httpx.AsyncClient,
    app: FastAPI,
    request_payload: dict[str, str],
    structured_dispatch: StructuredDispatch,
    operation: str,
    redis_mock: Mock,
    gemini_mock: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cache errors are best-effort; the separate memory rate backend stays live."""
    getattr(redis_mock, operation).side_effect = RedisConnectionError(_PRIVATE_ERROR)

    response = await client.post(_INGEST, json=request_payload)

    assert response.status_code == 200
    result = APIResponse[StructuredDispatch].model_validate_json(response.content)
    assert result.data == structured_dispatch
    assert result.cached is False
    assert _PRIVATE_ERROR not in response.text
    assert _PRIVATE_ERROR not in caplog.text
    gemini_mock.assert_awaited_once()
    redis_mock.set.assert_awaited_once()
    runtime = cast(Runtime, app.state.runtime)
    assert await runtime.store.get(runtime.cache.key_for(request_payload["text"])) == (
        structured_dispatch
    )


async def test_cache_expires_at_exact_one_hour_without_sleeping(
    client: httpx.AsyncClient,
    request_payload: dict[str, str],
    gemini_mock: AsyncMock,
    redis_mock: Mock,
    clock: Mock,
) -> None:
    """Reuse before TTL expiry, but regenerate at 3600 seconds without a live clock."""
    first = await client.post(_INGEST, json=request_payload)
    assert first.status_code == 200
    first_result = APIResponse[StructuredDispatch].model_validate_json(first.content)
    assert first_result.cached is False

    clock.return_value += 3599
    hit = await client.post(_INGEST, json=request_payload)
    assert hit.status_code == 200
    hit_result = APIResponse[StructuredDispatch].model_validate_json(hit.content)
    assert hit_result.cached is True
    gemini_mock.assert_awaited_once()
    redis_mock.set.assert_awaited_once()

    clock.return_value += 1
    expired = await client.post(_INGEST, json=request_payload)
    assert expired.status_code == 200
    expired_result = APIResponse[StructuredDispatch].model_validate_json(
        expired.content
    )
    assert expired_result.cached is False
    assert gemini_mock.await_count == 2
    assert redis_mock.set.await_count == 2


async def test_concurrent_cache_misses_generate_only_once(
    client: httpx.AsyncClient,
    request_payload: dict[str, str],
    gemini_response: AsyncGenerateContentResponse,
    gemini_mock: AsyncMock,
    redis_mock: Mock,
) -> None:
    """Force overlapping SDK work to verify the actual per-input coalescing lock."""

    async def yield_response(
        *args: object, **kwargs: object
    ) -> AsyncGenerateContentResponse:
        """Yield scheduling while the first request holds its coalescing lock."""
        await asyncio.sleep(0)
        return gemini_response

    gemini_mock.side_effect = yield_response
    responses = await asyncio.gather(
        *(client.post(_INGEST, json=request_payload) for _ in range(5))
    )

    assert all(response.status_code == 200 for response in responses)
    results = [
        APIResponse[StructuredDispatch].model_validate_json(response.content)
        for response in responses
    ]
    assert [result.cached for result in results].count(False) == 1
    assert [result.cached for result in results].count(True) == 4
    gemini_mock.assert_awaited_once()
    redis_mock.set.assert_awaited_once()


async def test_rate_quota_recovers_after_moving_window(
    client: httpx.AsyncClient, request_payload: dict[str, str], clock: Mock
) -> None:
    """Verify expiration of real quota state without sleeping for a minute."""
    for _ in range(5):
        assert (await client.post(_INGEST, json=request_payload)).status_code == 200
    blocked = await client.post(_INGEST, json=request_payload)
    _assert_error(blocked, status_code=429, code="rate_limit_exceeded")

    clock.return_value += 61
    recovered = await client.post(_INGEST, json=request_payload)

    assert recovered.status_code == 200
    assert APIResponse[StructuredDispatch].model_validate_json(recovered.content).cached


@pytest.mark.parametrize(
    "api_key",
    [None, "not-the-ingest-key", " offline-ingest-key-at-least-thirty-two "],
    ids=["missing", "bad", "whitespace-padded"],
)
async def test_authentication_rejects_requests_without_upstream_work(
    client: httpx.AsyncClient,
    request_payload: dict[str, str],
    api_key: str | None,
    gemini_mock: AsyncMock,
    redis_mock: Mock,
) -> None:
    """Avoid testing a happy path that accidentally bypasses real authentication."""
    client.headers.pop("X-API-Key")
    headers = {} if api_key is None else {"X-API-Key": api_key}

    response = await client.post(_INGEST, json=request_payload, headers=headers)

    _assert_error(response, status_code=401, code="unauthorized")
    assert response.headers["www-authenticate"] == "ApiKey"
    gemini_mock.assert_not_called()
    redis_mock.get.assert_not_called()
    redis_mock.set.assert_not_called()
