#!/usr/bin/env bash
set -Eeuo pipefail

# Deploy an already-built and pushed image containing linux/amd64 support.
# Prefer an immutable image digest:
#   IMAGE="us-central1-docker.pkg.dev/PROJECT/REPOSITORY/promptwars-vault@sha256:DIGEST"
#
# The application reads DISPATCH_* settings from backend/app/config.py. Secret
# values are resolved by Cloud Run and are never passed to this script as
# arguments or baked into the image. Build from the repository root with
# `docker build -f docker/Dockerfile .`.

# Required deployment values.
: "${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project ID}"
: "${IMAGE:?Set IMAGE to your pushed container image, preferably by digest}"
: "${RUNTIME_SERVICE_ACCOUNT:?Set RUNTIME_SERVICE_ACCOUNT to its email address}"
: "${DISPATCH_GEMINI_MODEL:?Set DISPATCH_GEMINI_MODEL to an available JSON-capable Gemini model}"
: "${DISPATCH_PRODUCTION_ORIGIN:?Set DISPATCH_PRODUCTION_ORIGIN to the exact HTTPS browser origin}"

REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-promptwars-vault-api}"
LOG_LEVEL="${DISPATCH_LOG_LEVEL:-INFO}"

# Secret Manager resource names, not secret values.
GEMINI_SECRET_NAME="${GEMINI_SECRET_NAME:-gemini-api-key}"
INGEST_SECRET_NAME="${INGEST_SECRET_NAME:-dispatch-ingest-api-key}"
REDIS_SECRET_NAME="${REDIS_SECRET_NAME:-dispatch-redis-url}"

# Pin numeric versions so deployments and rollbacks remain predictable.
GEMINI_SECRET_VERSION="${GEMINI_SECRET_VERSION:-1}"
INGEST_SECRET_VERSION="${INGEST_SECRET_VERSION:-1}"
REDIS_SECRET_VERSION="${REDIS_SECRET_VERSION:-1}"

[[ "${GEMINI_SECRET_VERSION}" =~ ^[1-9][0-9]*$ ]]
[[ "${INGEST_SECRET_VERSION}" =~ ^[1-9][0-9]*$ ]]
[[ "${REDIS_SECRET_VERSION}" =~ ^[1-9][0-9]*$ ]]
[[ "${LOG_LEVEL}" =~ ^(DEBUG|INFO|WARNING|ERROR)$ ]]

# Require Cloud Run/IAM authentication at the service boundary. Put a
# same-origin authenticated proxy or an authenticated load balancer in front of
# the service; never expose DISPATCH_INGEST_API_KEY to client JavaScript.
gcloud run deploy "${SERVICE_NAME}" \
    --project "${PROJECT_ID}" \
    --image "${IMAGE}" \
    --platform managed \
    --region "${REGION}" \
    --ingress internal-and-cloud-load-balancing \
    --no-allow-unauthenticated \
    --service-account "${RUNTIME_SERVICE_ACCOUNT}" \
    --port 8080 \
    --cpu 1 \
    --memory "${MEMORY:-512Mi}" \
    --concurrency "${CONCURRENCY:-40}" \
    --min-instances "${MIN_INSTANCES:-0}" \
    --max-instances "${MAX_INSTANCES:-10}" \
    --timeout "${TIMEOUT:-180s}" \
    --cpu-boost \
    --set-env-vars "DISPATCH_GEMINI_MODEL=${DISPATCH_GEMINI_MODEL},DISPATCH_PRODUCTION_ORIGIN=${DISPATCH_PRODUCTION_ORIGIN},DISPATCH_LOG_LEVEL=${LOG_LEVEL},DISPATCH_CLOUD_PROJECT_ID=${PROJECT_ID}" \
    --set-secrets "DISPATCH_GEMINI_API_KEY=${GEMINI_SECRET_NAME}:${GEMINI_SECRET_VERSION},DISPATCH_INGEST_API_KEY=${INGEST_SECRET_NAME}:${INGEST_SECRET_VERSION},DISPATCH_REDIS_URL=${REDIS_SECRET_NAME}:${REDIS_SECRET_VERSION}" \
    --startup-probe "httpGet.path=/health,httpGet.port=8080,periodSeconds=5,timeoutSeconds=2,failureThreshold=24" \
    --liveness-probe "httpGet.path=/health,httpGet.port=8080,periodSeconds=30,timeoutSeconds=2,failureThreshold=3" \
    --quiet
