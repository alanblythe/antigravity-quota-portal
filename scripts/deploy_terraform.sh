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

"${TF_CMD}" init

echo "==> Planning deployment..."
"${TF_CMD}" plan -out=tfplan

echo "==> Applying configuration..."
"${TF_CMD}" apply tfplan

echo "==> Deployment finished successfully!"
"${TF_CMD}" output
