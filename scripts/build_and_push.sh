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

CONTAINER_CLI="$(command -v podman 2>/dev/null || command -v docker 2>/dev/null || true)"
if [[ -z "${CONTAINER_CLI}" ]]; then
  echo "Error: Neither 'podman' nor 'docker' was found in PATH." >&2
  exit 1
fi

echo "==> Configuring ${CONTAINER_CLI} authentication for Artifact Registry..."
AUTH_ARGS=()
if [[ "$(basename "${CONTAINER_CLI}")" == "podman" ]]; then
  AUTHFILE="/tmp/podman-auth-$$.json"
  trap 'rm -f "${AUTHFILE}"' EXIT
  AUTH_ARGS=(--authfile="${AUTHFILE}")
else
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet || true
fi
ACCESS_TOKEN="$(gcloud auth application-default print-access-token 2>/dev/null || CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE=false gcloud auth print-access-token)"
printf '%s' "${ACCESS_TOKEN}" | "${CONTAINER_CLI}" login "${AUTH_ARGS[@]}" -u oauth2accesstoken --password-stdin "${REGION}-docker.pkg.dev"

echo "==> Building linux/amd64 container image using ${CONTAINER_CLI}..."
"${CONTAINER_CLI}" build --platform linux/amd64 -t "${IMAGE_URI}" -f "${ROOT_DIR}/Containerfile" "${ROOT_DIR}"

echo "==> Pushing image to Artifact Registry (${IMAGE_URI})..."
"${CONTAINER_CLI}" push "${AUTH_ARGS[@]}" "${IMAGE_URI}"

echo "==> Build and push complete!"
