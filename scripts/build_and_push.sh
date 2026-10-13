#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  source "${ROOT_DIR}/.env"
fi

# Configuration
PROJECT_ID="${1:-${PROJECT_ID:-}}"
REGION="${2:-${REGION:-us-east5}}"
REPO_NAME="antigravity-portal"
IMAGE_TAG="${3:-${IMAGE_TAG:-latest}}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "your-portal-project" || "${PROJECT_ID}" == "your-portal-host-project" ]]; then
  echo "Error: A valid PROJECT_ID must be provided as arg 1 or via environment variable." >&2
  echo "Usage: ./scripts/build_and_push.sh <PROJECT_ID> [REGION] [IMAGE_TAG]" >&2
  exit 1
fi

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/app:${IMAGE_TAG}"

echo "==> Configuring Podman authentication for Artifact Registry..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet || true
gcloud auth print-access-token | podman login -u oauth2accesstoken --password-stdin "${REGION}-docker.pkg.dev"

echo "==> Building container image using Podman..."
podman build -t "${IMAGE_URI}" -f "${ROOT_DIR}/Containerfile" "${ROOT_DIR}"

echo "==> Pushing image to Artifact Registry (${IMAGE_URI})..."
podman push "${IMAGE_URI}"

echo "==> Build and push complete!"
