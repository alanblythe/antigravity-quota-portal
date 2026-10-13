#!/usr/bin/env bash
set -euo pipefail

TF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../terraform" && pwd)"

TF_CMD="$(command -v tofu 2>/dev/null || command -v terraform 2>/dev/null || true)"
if [[ -z "${TF_CMD}" ]]; then
  echo "Error: Neither 'tofu' nor 'terraform' was found in PATH." >&2
  exit 1
fi

echo "==> Using ${TF_CMD} in ${TF_DIR}..."
cd "${TF_DIR}"
trap 'rm -f "${TF_DIR}/tfplan"' EXIT

ADC_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-${CLOUDSDK_CONFIG:-${HOME}/.config/gcloud}/application_default_credentials.json}"
if [[ ! -f "${ADC_FILE}" ]] && command -v gcloud >/dev/null 2>&1; then
  export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"
fi

"${TF_CMD}" init

echo "==> Planning deployment..."
"${TF_CMD}" plan -out=tfplan "$@"

if [[ "${AUTO_APPROVE:-false}" != "true" ]]; then
  read -r -p "Apply this plan? [y/N] " confirm
  if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

echo "==> Applying configuration..."
"${TF_CMD}" apply tfplan

echo "==> Deployment finished successfully!"
"${TF_CMD}" output
