# syntax=docker/dockerfile:1
# Build from the PromptWars_Vault repository root. The root context is required
# because the dependency metadata and the application live under backend/:
#   docker build -f docker/Dockerfile -t promptwars-vault-api .

FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && python -m pip install --no-cache-dir --no-compile uv==0.12.23 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# uv.lock and pyproject.toml are the single dependency source of truth.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY backend/app ./app
RUN uv sync --locked --no-dev


FROM python:3.11-slim-bookworm AS runner

ENV PATH="/opt/venv/bin:$PATH" \
    HOME="/home/appuser" \
    PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 appuser \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --shell /usr/sbin/nologin \
        appuser

WORKDIR /app

COPY --from=builder /build/.venv /opt/venv
# Only the importable backend package is copied into the runtime image. Tests,
# source metadata, dotenv files, and browser assets never enter this stage.
COPY --from=builder --chown=appuser:appuser /build/app ./app

USER appuser:appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import http.client, os; c = http.client.HTTPConnection('127.0.0.1', int(os.environ.get('PORT') or '8080'), timeout=2); c.request('GET', '/health'); r = c.getresponse(); status = r.status; r.read(); c.close(); raise SystemExit(0 if status == 200 else 1)"]

# create_app is the factory in backend/app/main.py; Settings reads DISPATCH_*
# variables in backend/app/config.py. Access logging is disabled because raw
# Uvicorn URLs can contain query secrets; the app emits fixed safe events.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port \"${PORT:-8080}\" --workers 1 --timeout-graceful-shutdown 45 --no-access-log --no-proxy-headers --no-server-header"]
