# PromptWars Vault

PromptWars Vault is a tenant-safe structured-dispatch intake slice. It accepts bounded, unstructured text, validates it at the HTTP boundary, asks Gemini for schema-constrained JSON, validates the provider result with Pydantic, and returns reviewable severity, entities, confidence, and actions.

## Canonical layout

- `backend/app/` — FastAPI application, immutable models, Gemini adapter, Redis cache, throttling, and sanitized observability.
- `backend/tests/` — hermetic ASGI, cache, provider, limiter, model, and SQL-contract tests.
- `database/schema.sql` — Supabase owner-scoped `pgvector` migration and invoker RPC.
- `frontend/src/components/IntakeDashboard.tsx` — transport-safe accessible intake UI with immutable engine and replaceable visual skin boundaries.
- `frontend/verify_contract.py` — dependency-free offline frontend contract and accessibility gate.
- `docker/` — non-root runtime image and Cloud Run deployment command.
- `ARCHITECTURE.md` — system boundaries and source-of-truth rules.
- `EXPORT_MANIFEST.md` — deliverable inventory.
- `architecture/antigravity.setup.json` — machine-readable path and command manifest.

## Verification

Backend commands run from `backend/`:

```bash
uv lock --check
uv run --locked --python 3.11 ruff check app tests
uv run --locked --python 3.11 ruff format --check app tests
uv run --locked --python 3.11 mypy app tests
uv run --locked --python 3.11 pytest -q --cov=app --cov-branch --cov-fail-under=100
uv run --locked --python 3.11 bandit -r app -c pyproject.toml
uv run --locked --python 3.11 pip-audit --local --progress-spinner off
uv build
# The resulting structured_dispatch_api-1.0.0.tar.gz must be <150 KiB.
```

Frontend command from `frontend/`:

```bash
python verify_contract.py
```

Live Gemini, Redis, Supabase, browser, and Cloud Run smoke tests still require the deployment environment and are not substituted by the offline gates.
