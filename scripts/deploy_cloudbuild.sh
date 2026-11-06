#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
fi

PROJECT_ID="${1:-${PROJECT_ID:-}}"
REGION="${2:-${REGION:-us-east5}}"
IMAGE_TAG="${3:-${IMAGE_TAG:-latest}}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "your-portal-project" || "${PROJECT_ID}" == "your-portal-host-project" ]]; then
  echo "Error: A valid PROJECT_ID must be provided as arg 1 or via environment variable." >&2
  echo "Usage: ./scripts/deploy_cloudbuild.sh <PROJECT_ID> [REGION] [IMAGE_TAG]" >&2
  exit 1
fi

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/antigravity-portal/app:${IMAGE_TAG}"

echo "==> Submitting Cloud Build for project: ${PROJECT_ID} (region: ${REGION}, tag: ${IMAGE_TAG})..."

if [[ -f "${ROOT_DIR}/cloudbuild.yaml" ]]; then
  gcloud builds submit \
    --project="${PROJECT_ID}" \
    --config="${ROOT_DIR}/cloudbuild.yaml" \
    --substitutions="_REGION=${REGION},_TAG=${IMAGE_TAG}" \
    "${ROOT_DIR}"
else
  gcloud builds submit \
    --project="${PROJECT_ID}" \
    --tag="${IMAGE_URI}" \
    "${ROOT_DIR}"
fi

echo "==> Cloud Build completed successfully!"
