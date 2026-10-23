# Intake dashboard integration

`src/components/IntakeDashboard.tsx` is intentionally transport-safe: it never
embeds or sends `X-API-Key` from browser JavaScript.

The host frontend must provide a same-origin `POST /api/ingest` proxy (or pass a
different same-origin `endpoint`) that authenticates the browser session and
adds the server-held `X-API-Key` when forwarding to the FastAPI service.

The request body is exactly:

```json
{"text":"..."}
```

The component accepts textual TXT, CSV, and JSON files and sends their contents
through the same text contract. PDF files are deliberately rejected because the
backend has no PDF parser. The backend enforces 5–15,000 Unicode characters and
the component mirrors those limits before submission.

## Modular skinning

`IntakeDashboard.tsx` exposes a readonly `IntakeDashboardSkin` adapter. The
engine-support and engine-core regions own validation, keyboard behavior, refs,
focus restoration, live regions, accessibility tokens, abort/timeout handling,
and the same-origin JSON transport. The visual-skin region owns layout, branding,
dropzones, metric cards, and structured result panels. A replacement skin must
consume `IntakeDashboardSkinProps` without adding endpoint, controller, raw-file,
or API-key state.

This repository does not contain the host frontend's package manifest or test
runner. Before deployment, the host application must run its TypeScript/build,
axe/accessibility, keyboard, screen-reader, zoom, and forced-colors checks.

## Offline contract check

The repository includes a dependency-free request/response fixture and source
contract check. From this directory, run:

```bash
python verify_contract.py
```

This check does not make a network request or replace the host application's
TypeScript/build and accessibility checks.
