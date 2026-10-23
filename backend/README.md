# PromptWars Vault backend

This directory contains the authenticated, asynchronous FastAPI service used by
the intake dashboard. `pyproject.toml` and `uv.lock` are the single dependency
source of truth.

## Local setup

Run the following from this directory:

```bash
uv sync --locked --python 3.11
```

The application requires these settings at runtime:

| Variable | Meaning |
| --- | --- |
| `DISPATCH_GEMINI_API_KEY` | Gemini credential, supplied through a secret manager |
| `DISPATCH_INGEST_API_KEY` | 32–512 printable-ASCII-byte API key for `/api/ingest` |
| `DISPATCH_GEMINI_MODEL` | Available JSON-schema-capable Gemini model ID |
| `DISPATCH_PRODUCTION_ORIGIN` | Exact HTTPS browser origin, without a path |
| `DISPATCH_REDIS_URL` | Redis or Redis TLS URL ending in a database index |

Redis is required for production startup because both caching and distributed
throttling fail closed. The application does not load dotenv files.

## API contract

`POST /api/ingest` accepts an uncompressed JSON body containing exactly one
field:

```json
{"text":"Station 7 reports a pump failure; send maintenance."}
```

The caller must send exactly one `X-API-Key` header. Successful responses use
`APIResponse[StructuredDispatch]`; all application errors use the same wrapper
with sanitized `ErrorDetails`. The frontend component sends this request only
through a same-origin server-side proxy that injects the API key; the key is
never placed in browser JavaScript.

## Verification

From this directory, run the complete offline gate:

```bash
uv lock --check
uv run --locked --python 3.11 ruff check app tests
uv run --locked --python 3.11 ruff format --check app tests
uv run --locked --python 3.11 mypy app tests
uv run --locked --python 3.11 pytest -q --cov=app --cov-branch --cov-fail-under=100
uv run --locked --python 3.11 bandit -r app -c pyproject.toml
uv run --locked --python 3.11 pip-audit --local --progress-spinner off
uv build
```

These checks block network access in tests and use injected Gemini/Redis
boundaries. They do not replace live Redis/Gemini smoke tests, a Supabase RLS
test, a frontend build/accessibility audit, or a container smoke test.
