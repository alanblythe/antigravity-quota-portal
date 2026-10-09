#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROJECT_ID="${1:-${PROJECT_ID:-agy-quota-portal}}"
REGION="${2:-${REGION:-us-east5}}"
TAG="${3:-latest}"

echo "==> Submitting Cloud Build deployment pipeline for project: ${PROJECT_ID} (region: ${REGION}, tag: ${TAG})..."

gcloud builds submit \
  --project="${PROJECT_ID}" \
  --config="${ROOT_DIR}/cloudbuild.yaml" \
  --substitutions="_REGION=${REGION},_TAG=${TAG}" \
  "${ROOT_DIR}"

echo "==> Cloud Build deployment pipeline completed successfully!"
