# Export manifest

| Artifact | Purpose | Canonical source |
| --- | --- | --- |
| `backend/app/` | Production FastAPI package | `backend/pyproject.toml` |
| `backend/tests/` | Hermetic Python verification | `backend/pyproject.toml` |
| `backend/dist/structured_dispatch_api-1.0.0-py3-none-any.whl` | Built wheel; local caches and virtual environments excluded | `uv build` |
| `backend/dist/structured_dispatch_api-1.0.0.tar.gz` | Built sdist; required to remain below 150 KiB | `uv build` |
| `database/schema.sql` | Supabase RLS and vector RPC migration | Database deployment |
| `frontend/src/components/IntakeDashboard.tsx` | Accessible browser intake component | Host frontend import |
| `frontend/verify_contract.py` | Offline frontend contract gate | Python standard library |
| `docker/Dockerfile` | Non-root Cloud Run image | Repository-root Docker build |
| `docker/deploy.sh` | Authenticated Cloud Run deployment | Google Cloud CLI |
| `ARCHITECTURE.md` | Human-readable architecture contract | Repository root |
| `architecture/antigravity.setup.json` | Machine-readable path/command contract | Repository root |
| `FINAL_SOURCE_TEMPLATES.md` | Complete uncompressed verified templates for the requested final handoff | Generated from canonical files |

Generated caches, virtual environments, bytecode, coverage data, and local secrets are not export artifacts.
