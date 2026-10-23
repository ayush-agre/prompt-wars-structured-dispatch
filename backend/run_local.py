"""Local development runner for AegisDispatch AI (PromptWars Vault).

Allows running the full application locally with zero external setup:
- Serves the frontend at http://localhost:8000
- Auto-detects Redis (uses in-memory fallback if Redis is not running)
- Auto-detects Gemini key (uses autonomous local engine if no key is configured)
- Acts as the same-origin authenticated proxy for the frontend UI
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sys
from pathlib import Path

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

from fastapi.responses import FileResponse  # noqa: E402
from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import Runtime, create_app  # noqa: E402
from app.models import StructuredDispatch  # noqa: E402
from app.services import (  # noqa: E402
    DispatchCache,
    Dispatcher,
    GeminiDispatcher,
    InMemoryDispatchStore,
)
from app.throttling import SlowAPIRateGate  # noqa: E402

# Default local development ingest key
DEV_INGEST_KEY = "local-dev-key-at-least-thirty-two-chars"


class _LocalMemoryCache:
    """Thread-safe in-memory cache replicating the SHA-256 v2 digest contract."""

    def __init__(self, model_name: str) -> None:
        self._model = model_name
        self._entries: dict[str, StructuredDispatch] = {}
        self._lock = asyncio.Lock()

    def key_for(self, text: str) -> str:
        canonical = json.dumps(
            {"model": self._model, "schema_version": 2, "text": text},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return "dispatch:v2:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> StructuredDispatch | None:
        async with self._lock:
            return self._entries.get(key)

    async def put(self, key: str, dispatch: StructuredDispatch) -> None:
        async with self._lock:
            self._entries[key] = dispatch

    async def ping(self) -> bool:
        return True


class _MockLocalDispatcher:
    """Autonomous offline triage dispatcher when Gemini key is not configured."""

    async def dispatch(self, text: str) -> StructuredDispatch:
        domain = "operations"
        urgency = "critical"
        raw_text = text
        if text.startswith("[domain:"):
            parts = text.split("\n", 1)
            raw_text = parts[1] if len(parts) > 1 else text
            if "domain:" in parts[0]:
                domain = parts[0].split("domain:")[1].split("]")[0]
            if "urgency:" in parts[0]:
                urgency = parts[0].split("urgency:")[1].split("]")[0]

        words = [w for w in raw_text.split() if len(w) > 2]
        title_snippet = " ".join(words[:5]).capitalize() if words else "Incident Alert"
        title = f"{title_snippet} Incident Analysis"[:200]

        severity = (
            "critical"
            if urgency == "critical"
            else ("high" if urgency == "urgent" else "medium")
        )

        return StructuredDispatch(
            title=title,
            severity_level=severity,
            category=f"{domain.capitalize()} Triage & Operations",
            key_entities=["Sector Dispatch", "Primary Operations", "Node 1"],
            confidence_score=0.965,
            action_items=[
                f"Isolate affected {domain} zone and initiate emergency protocol.",
                f"Dispatch specialized {domain} emergency response team.",
                "Verify redundant failover status and log SHA-256 audit entry.",
            ],
            reasoning_summary=(
                f"Incident triaged under {domain.upper()} with {urgency.upper()}.\n"
                "Autonomous heuristics evaluated potential cascading hazards and\n"
                "established immediate perimeter containment priorities."
            ),
            domain_tags=[domain, "autonomous-triage", urgency],
        )

    async def aclose(self) -> None:
        pass


class LocalProxyAuthMiddleware:
    """Local development pure ASGI proxy middleware.

    Injects the server's X-API-Key for browser calls originating from localhost,
    mirroring the production same-origin proxy pattern.
    """

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        """Initialize with inner app and API key."""
        self.app = app
        self.api_key_header = (b"x-api-key", api_key.encode("latin-1"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inject API key into HTTP request scope if missing."""
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            headers = list(scope.get("headers", []))
            has_key = any(name.lower() == b"x-api-key" for name, _ in headers)
            if not has_key:
                headers.append(self.api_key_header)
                scope["headers"] = headers
        await self.app(scope, receive, send)


def run() -> None:
    """Start the local AegisDispatch development server."""
    os.environ.setdefault("DISPATCH_INGEST_API_KEY", DEV_INGEST_KEY)
    os.environ.setdefault("DISPATCH_GEMINI_MODEL", "gemini-2.5-flash")
    os.environ.setdefault("DISPATCH_PRODUCTION_ORIGIN", "https://localhost")
    os.environ.setdefault("DISPATCH_REDIS_URL", "redis://localhost:6379/0")

    gemini_key = os.environ.get("DISPATCH_GEMINI_API_KEY", "").strip()
    is_real_gemini = bool(gemini_key and not gemini_key.startswith("your-gemini"))

    if not is_real_gemini:
        os.environ["DISPATCH_GEMINI_API_KEY"] = "local-offline-mock-key-not-for-live"

    settings = Settings()

    if is_real_gemini:
        dispatcher: Dispatcher = GeminiDispatcher(settings)
        engine_mode = "Google Gemini 2.0 Flash (Live)"
    else:
        dispatcher = _MockLocalDispatcher()
        engine_mode = "Autonomous Offline Mock (Set DISPATCH_GEMINI_API_KEY for live)"

    redis_available = False
    try:
        sock = socket.create_connection(("127.0.0.1", 6379), timeout=0.2)
        sock.close()
        redis_available = True
    except OSError:
        redis_available = False

    if redis_available:
        from redis.asyncio import Redis

        redis_client = Redis.from_url(
            settings.redis_url.get_secret_value(), decode_responses=True
        )
        cache = DispatchCache(redis_client, settings)
        cache_mode = "Redis Cache (Connected at 127.0.0.1:6379)"
    else:
        cache = _LocalMemoryCache(settings.gemini_model)
        cache_mode = "In-Memory SHA-256 v2 Cache (Redis offline fallback)"

    rate_gate = SlowAPIRateGate(settings, storage_uri="memory://")
    store = InMemoryDispatchStore(100)

    runtime = Runtime(
        cache=cache,
        dispatcher=dispatcher,
        store=store,
        rate_gate=rate_gate,
    )

    app = create_app(settings=settings, runtime=runtime)

    app.add_middleware(
        LocalProxyAuthMiddleware,
        api_key=settings.ingest_api_key.get_secret_value(),
    )

    index_html = FRONTEND_DIR / "index.html"

    @app.get("/")
    async def serve_index() -> FileResponse:
        return FileResponse(index_html, media_type="text/html")

    print("\n" + "=" * 65)
    print("   AEGISDISPATCH AI - LOCAL DEVELOPMENT RUNNER")
    print("=" * 65)
    print("  * UI Dashboard:      http://localhost:8000")
    print("  * API Health:        http://localhost:8000/health")
    print("  * Triage Endpoint:   http://localhost:8000/api/analyse")
    print(f"  * AI Engine:         {engine_mode}")
    print(f"  * Cache Backend:     {cache_mode}")
    print("=" * 65 + "\n")

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    run()
