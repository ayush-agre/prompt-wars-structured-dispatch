# Verification receipt

This file is a release checklist, not a guarantee of a universal security score.

## Offline gate

Run from `backend/` with Python 3.11:

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

The test suite must report zero failures and must retain the network-blocking
fixtures in `tests/conftest.py`. It tests strict request/output validation,
sanitized errors and logs, cache isolation, async cancellation, rate limits,
resource cleanup, and the pgvector/RLS migration contract.

## Packaging artifact contract

`uv build` must produce an sdist smaller than 150 KiB and must exclude `.venv`,
`venv`, `dist`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.coverage`,
and `htmlcov` content from both the sdist and wheel while retaining the `app/`
package in the wheel.

## Required deployment gates

- Run `docker build -f docker/Dockerfile .` from the PromptWars_Vault root.
- Start the image with real secret-manager values and verify `GET /health`.
- Verify a real Redis connection, cache hit on the second identical request, and
  HTTP 429 on the sixth authenticated request in the moving window.
- Run `database/schema.sql` in a disposable Supabase project with pgvector
  already installed, then test authenticated owner isolation and anonymous,
  cross-owner, and service-role access.
- Put `frontend/src/components/IntakeDashboard.tsx` behind a same-origin proxy
  that injects `X-API-Key`; never expose that key to the browser.
- Run the frontend's TypeScript/build, axe, keyboard, screen-reader, zoom, and
  forced-colors checks once the host frontend supplies its package/tooling.

Live provider, Redis, Supabase, browser, and container checks are not replaced
by the offline Python suite.
