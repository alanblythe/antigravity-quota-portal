#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Running Trivy security scan on ${ROOT_DIR}..."

if command -v trivy &>/dev/null; then
  trivy fs --config "${ROOT_DIR}/trivy.yaml" "${ROOT_DIR}"
elif command -v podman &>/dev/null; then
  podman run --rm -v "${ROOT_DIR}:/workspace:z" -w /workspace aquasec/trivy:latest fs --config /workspace/trivy.yaml /workspace
elif command -v docker &>/dev/null; then
  docker run --rm -v "${ROOT_DIR}:/workspace" -w /workspace aquasec/trivy:latest fs --config /workspace/trivy.yaml /workspace
else
  echo "Error: trivy, podman, or docker is required to run security scans."
  exit 1
fi

echo "==> Trivy security scan passed with 0 High/Critical findings!"
