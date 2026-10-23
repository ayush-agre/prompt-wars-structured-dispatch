"""JSON request correlation, safe error envelopes, and bounded ASGI request reads."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import cast
from uuid import UUID, uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .models import APIResponse, ErrorDetails

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="")
_TRACE: ContextVar[str] = ContextVar("cloud_trace", default="")
_LOGGER = logging.getLogger(__name__)
_CLOUD_TRACE = re.compile(r"([0-9a-f]{32})/([0-9]{1,20})(?:;o=([01]))?\Z")
_SAFE_MESSAGES = frozenset(
    {
        "application_started",
        "application_stopping",
        "dispatch_cache_corrupt",
        "dispatch_cache_ping_failed",
        "dispatch_cache_read_failed",
        "dispatch_cache_write_failed",
        "rate_limit_storage_unavailable",
        "request_completed",
        "request_failed",
        "request_started",
        "resource_close_failed",
        "response_validation_failed",
        "service_request_failed",
        "unhandled_error",
    }
)
_SAFE_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_SAFE_METHOD = re.compile(r"[A-Z]{1,16}\Z")
_SAFE_LOGGER = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_SAFE_ERROR_CLASS = re.compile(r"[A-Z][A-Za-z0-9]*(?:Error|Exception)\Z")
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_DURATION = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,6})?s\Z")
_KNOWN_ROUTES = frozenset({"/health", "/api/ingest", "/api/analyse", "unmatched"})


def _safe_route_template(value: object) -> str:
    """Keep only router templates, never a client-supplied URL path."""
    if value is None:
        return "unmatched"
    if not isinstance(value, str) or len(value) > 256:
        return "matched"
    if value in _KNOWN_ROUTES:
        return value
    # Keep this app-specific allowlist closed: even a path that looks like a
    # Starlette template may have come from an untrusted logging extra.
    return "matched"


def _safe_http_request(value: object) -> dict[str, object]:
    """Build a fixed HTTP event without copying arbitrary logging extras."""
    fields = value if isinstance(value, Mapping) else {}
    method = fields.get("requestMethod")
    safe_method = (
        method
        if isinstance(method, str) and _SAFE_METHOD.fullmatch(method)
        else "UNKNOWN"
    )
    status = fields.get("status")
    safe_status = (
        status
        if isinstance(status, int)
        and not isinstance(status, bool)
        and 100 <= status <= 599
        else 500
    )
    duration = fields.get("latency")
    safe_duration = (
        duration
        if isinstance(duration, str) and _SAFE_DURATION.fullmatch(duration)
        else "0.000000s"
    )
    return {
        "requestMethod": safe_method,
        "requestUrl": _safe_route_template(fields.get("requestUrl")),
        "status": safe_status,
        "latency": safe_duration,
    }


def _safe_number(value: object) -> float | None:
    """Return finite numeric metadata only, excluding arbitrary object values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return round(number, 3) if math.isfinite(number) else None


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line without formatting raw exception payloads."""

    def format(self, record: logging.LogRecord) -> str:
        """Format safe application metadata in Google Cloud Logging's shape."""
        message = (
            record.msg
            if isinstance(record.msg, str)
            and not record.args
            and record.msg in _SAFE_MESSAGES
            else "external_log_record"
        )
        level = record.levelname if record.levelname in _SAFE_LEVELS else "INFO"
        logger_name = (
            record.name if _SAFE_LOGGER.fullmatch(record.name) else "external_logger"
        )
        payload: dict[str, object] = {
            "severity": level,
            "message": message,
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "logger": logger_name,
        }
        if request_id := _REQUEST_ID.get():
            payload["request_id"] = request_id
        if trace := _TRACE.get():
            payload["logging.googleapis.com/trace"] = trace
        if message in _SAFE_MESSAGES:
            if "httpRequest" in record.__dict__:
                payload["httpRequest"] = _safe_http_request(
                    record.__dict__["httpRequest"]
                )
            if (latency := _safe_number(record.__dict__.get("latency_ms"))) is not None:
                payload["latency_ms"] = latency
            error_type = record.__dict__.get("error_type")
            safe_error_type = isinstance(error_type, str) and (
                _SAFE_ERROR_CLASS.fullmatch(error_type)
                or (
                    message == "service_request_failed"
                    and _SAFE_ERROR_CODE.fullmatch(error_type)
                )
            )
            if safe_error_type:
                payload["error_type"] = error_type
        # Deliberately exclude exc_info, stack_info, arbitrary extras, and bodies.
        return json.dumps(payload, ensure_ascii=True, allow_nan=False)


def configure_logging(level: str) -> None:
    """Configure application, limiter, and server logs on Cloud Run stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in (
        "app",
        "slowapi",
        "uvicorn",
        "uvicorn.error",
        "google",
        "google.api_core",
        "google.generativeai",
        "grpc",
        "redis",
    ):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(level)
        logger.propagate = False

    # Uvicorn's access message contains the raw URL, including query strings.
    # The API already emits a sanitized request-completion record, so do not
    # retain a second handler that could write untrusted paths or parameters.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.disabled = True
    access_logger.propagate = False


def latency_ms(scope: Scope) -> float:
    """Measure elapsed handler time against a monotonic, request-local clock."""
    started = cast(float, scope.get("state", {}).get("started_at", perf_counter()))
    return round(max(0.0, (perf_counter() - started) * 1000.0), 3)


def error_response(
    scope: Scope,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the common error wrapper without echoing validation or SDK input."""
    state = scope.get("state", {})
    request_id = cast(str, state.get("request_id") or str(uuid4()))
    envelope = APIResponse[ErrorDetails](
        status="error",
        data=ErrorDetails(code=code, message=message, request_id=request_id),
        latency_ms=latency_ms(scope),
        cached=False,
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


def _request_id(headers: Headers) -> str:
    """Accept a UUID correlation header only, otherwise generate a fresh UUID."""
    value = headers.get("x-request-id", "")
    try:
        return str(UUID(value)) if len(value) == 36 else str(uuid4())
    except ValueError:
        return str(uuid4())


def _cloud_trace(headers: Headers, project_id: str | None) -> str:
    """Validate the Cloud Run trace identifier before placing it in JSON logs."""
    value = headers.get("x-cloud-trace-context", "")
    match = _CLOUD_TRACE.fullmatch(value) if len(value) <= 64 else None
    if project_id and match and int(match[2]) < 2**64:
        return f"projects/{project_id}/traces/{match[1]}"
    return ""


class RequestContextMiddleware:
    """Correlate requests and sanitize failures using pure ASGI, not body copies."""

    def __init__(self, app: ASGIApp, project_id: str | None = None) -> None:
        """Retain the downstream application and optional Cloud project."""
        self._app = app
        self._project_id = project_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Attach timing, correlation, and security headers to all HTTP results."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = _request_id(headers)
        state = scope.setdefault("state", {})
        state.update(request_id=request_id, started_at=perf_counter())
        request_token = _REQUEST_ID.set(request_id)
        trace_token = _TRACE.set(_cloud_trace(headers, self._project_id))
        status_code = 500
        response_started = False

        async def send_with_headers(message: Message) -> None:
            """Record the status and append safe request-local response headers."""
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["X-Frame-Options"] = "DENY"
                response_headers["Cache-Control"] = "no-store"
                response_headers["Content-Security-Policy"] = (
                    "default-src 'none'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'"
                )
                response_headers["Referrer-Policy"] = "no-referrer"
                response_headers["Server-Timing"] = f"app;dur={latency_ms(scope):.3f}"
            await send(message)

        _LOGGER.info("request_started")
        try:
            await self._app(scope, receive, send_with_headers)
        except Exception as exc:
            _LOGGER.error("request_failed", extra={"error_type": type(exc).__name__})
            if response_started:
                # Never attempt a second response after headers have been sent.
                raise
            response = error_response(
                scope, 500, "internal_error", "An internal server error occurred."
            )
            await response(scope, receive, send_with_headers)
        finally:
            route = scope.get("route")
            path = _safe_route_template(getattr(route, "path", None))
            elapsed = latency_ms(scope)
            _LOGGER.info(
                "request_completed",
                extra={
                    "latency_ms": elapsed,
                    "httpRequest": {
                        "requestMethod": scope["method"],
                        "requestUrl": path,
                        "status": status_code,
                        "latency": f"{elapsed / 1000.0:.6f}s",
                    },
                },
            )
            _TRACE.reset(trace_token)
            _REQUEST_ID.reset(request_token)


class BoundedBodyMiddleware:
    """Bound JSON bodies before FastAPI buffers or parses untrusted input."""

    def __init__(self, app: ASGIApp, max_bytes: int, timeout_seconds: float) -> None:
        """Retain body byte and read-time limits for the ingest endpoint."""
        self._app = app
        self._max_bytes = max_bytes
        self._timeout = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Read at most the configured bytes, including chunked HTTP uploads."""
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in {"/api/ingest", "/api/analyse"}
        ):
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if (
            media_type != "application/json"
            or headers.get("content-encoding", "identity").lower() != "identity"
        ):
            response = error_response(
                scope, 415, "unsupported_media_type", "Uncompressed JSON is required."
            )
            await response(scope, receive, send)
            return
        lengths = headers.getlist("content-length")
        if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0])):
            response = error_response(
                scope, 400, "invalid_content_length", "Content-Length is invalid."
            )
            await response(scope, receive, send)
            return
        # Bound the digits before int() to avoid an unbounded integer conversion.
        if lengths and (len(lengths[0]) > 9 or int(lengths[0]) > self._max_bytes):
            response = error_response(
                scope, 413, "request_too_large", "The request body is too large."
            )
            await response(scope, receive, send)
            return
        body = bytearray()
        too_large = False
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self._max_bytes:
                        too_large = True
                        break
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            response = error_response(
                scope, 408, "request_timeout", "The request body timed out."
            )
            await response(scope, receive, send)
            return
        if too_large:
            response = error_response(
                scope, 413, "request_too_large", "The request body is too large."
            )
            await response(scope, receive, send)
            return
        delivered = False

        async def replay_body() -> Message:
            """Deliver the bounded body once, then preserve disconnect semantics."""
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self._app(scope, replay_body, send)


class SanitizedErrorMiddleware:
    """Contain unexpected errors inside CORS so even error responses retain CORS."""

    def __init__(self, app: ASGIApp) -> None:
        """Retain the downstream ASGI application."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle pre-response failures without exposing exception text or traces."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        response_started = False

        async def track_start(message: Message) -> None:
            """Track headers to avoid a second HTTP response on transport failures."""
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, receive, track_start)
        except Exception as exc:
            if response_started:
                raise
            _LOGGER.error("request_failed", extra={"error_type": type(exc).__name__})
            response = error_response(
                scope, 500, "internal_error", "An internal server error occurred."
            )
            await response(scope, receive, track_start)
