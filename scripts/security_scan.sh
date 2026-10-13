#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Running Trivy security scan on ${ROOT_DIR}..."

TRIVY_CONFIG_ARGS=()
CONTAINER_CONFIG_ARGS=()
if [[ -f "${ROOT_DIR}/trivy.yaml" ]]; then
  TRIVY_CONFIG_ARGS=(--config "${ROOT_DIR}/trivy.yaml")
  CONTAINER_CONFIG_ARGS=(--config /workspace/trivy.yaml)
fi

TRIVY_SEVERITY_ARGS=(--severity HIGH,CRITICAL --exit-code 1)

if command -v trivy &>/dev/null; then
  trivy fs ${TRIVY_CONFIG_ARGS[@]+"${TRIVY_CONFIG_ARGS[@]}"} "${TRIVY_SEVERITY_ARGS[@]}" "${ROOT_DIR}"
elif command -v podman &>/dev/null; then
  podman run --rm -v "${ROOT_DIR}:/workspace:z" -w /workspace aquasec/trivy:latest fs ${CONTAINER_CONFIG_ARGS[@]+"${CONTAINER_CONFIG_ARGS[@]}"} "${TRIVY_SEVERITY_ARGS[@]}" /workspace
elif command -v docker &>/dev/null; then
  docker run --rm -v "${ROOT_DIR}:/workspace" -w /workspace aquasec/trivy:latest fs ${CONTAINER_CONFIG_ARGS[@]+"${CONTAINER_CONFIG_ARGS[@]}"} "${TRIVY_SEVERITY_ARGS[@]}" /workspace
else
  echo "Error: trivy, podman, or docker is required to run security scans." >&2
  exit 1
fi

echo "==> Trivy security scan passed with 0 High/Critical findings!"
