# PromptWars Vault architecture

## Data path

1. The browser sends exactly `{"text": string}` to a same-origin proxy. It never carries the ingest API key.
2. `backend/app/observability.py` bounds the body, establishes a UUID request context, sanitizes errors, and applies security headers.
3. `backend/app/main.py` performs constant-time API-key authentication, then applies the five-per-minute limiter.
4. `backend/app/services.py` computes a SHA-256 cache key over canonical text, Gemini model, and schema version. Striped asyncio locks coalesce identical misses.
5. `GeminiDispatcher` uses the pinned `google-generativeai==0.8.6` async generation surface, JSON response schema, bounded semaphore, and cancellation-safe timeout.
6. The provider response is checked for blocking, completion, text-only parts, size, and strict Pydantic validity before it is stored.
7. Redis is best-effort for cache reads/writes but required at production startup; the bounded in-memory store retains validated results.

## Security boundaries

- Secrets are `SecretStr` settings and are consumed only at client construction or constant-time authentication comparison.
- Error envelopes contain stable codes, fixed messages, and request IDs only. Logs use a closed event vocabulary and never serialize bodies, exception text, or arbitrary extras.
- `database/schema.sql` enables and forces RLS, grants table/RPC access only to `authenticated`, and repeats the owner predicate inside the invoker RPC.
- `docker/Dockerfile` runs as UID/GID 10001. `docker/deploy.sh` requires Cloud Run/IAM authentication, internal/load-balancer ingress, Secret Manager bindings, bounded resources, and health probes.

## Frontend skinning boundary

`frontend/src/components/IntakeDashboard.tsx` is split into three explicit source
regions: immutable engine support, mutable visual skin, and immutable engine core.
The engine owns runtime validation, request construction, same-origin fetch, abort
and timeout state, keyboard handling, refs, focus restoration, live-region output,
and accessibility tokens. The skin owns branding, layout, dropzones, metric cards,
and structured-result presentation. `IntakeDashboardSkinProps` exposes readonly
state and actions only; it never receives an endpoint, controller, raw `File`, or
secret. The default skin can therefore be replaced without changing the transport
or accessibility contract.

## Source of truth

Domain fields are defined once per boundary: `backend/app/models.py` is canonical for API validation, `backend/app/services.py` contains the provider schema mirror, and `frontend/src/components/IntakeDashboard.tsx` contains the browser transport/runtime validation mirror. `frontend/contract_fixture.json` and `backend/tests/test_schema_contract.py` verify those contracts offline. The setup manifest points to these exact paths; it does not describe alternate modules.
