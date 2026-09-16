#!/usr/bin/env bash
set -euo pipefail

# Configuration
PROJECT_ID="${1:-${PROJECT_ID:-}}"
REGION="${2:-${REGION:-us-central1}}"
REPO_NAME="antigravity-portal"
IMAGE_TAG="${3:-latest}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Error: PROJECT_ID must be provided as arg 1 or via environment variable."
  echo "Usage: ./scripts/build_and_push.sh <PROJECT_ID> [REGION] [IMAGE_TAG]"
  exit 1
fi

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/app:${IMAGE_TAG}"

echo "==> Configuring Podman authentication for Artifact Registry..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet || true
gcloud auth print-access-token | podman login -u oauth2accesstoken --password-stdin "${REGION}-docker.pkg.dev"

echo "==> Building container image using Podman..."
podman build -t "${IMAGE_URI}" -f Containerfile .

echo "==> Pushing image to Artifact Registry (${IMAGE_URI})..."
podman push "${IMAGE_URI}"

echo "==> Build and push complete!"
