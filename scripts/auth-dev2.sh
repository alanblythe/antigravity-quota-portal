#!/usr/bin/env bash
set -euo pipefail
# Load local .env if present
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "${SCRIPT_DIR}/../.env" ] && source "${SCRIPT_DIR}/../.env"

DEV_USER="dev2"
DEV_EMAIL="${TEST_DEV2_EMAIL:-${DEV_USER}@${ORG_DOMAIN:-example.com}}"
TEST_USERS_BASE="${TEST_USERS_DIR:-${HOME}/.agy_test_users}"
TEST_HOME="${TEST_USERS_BASE}/${DEV_USER}"
GCLOUD_CONFIG="${TEST_HOME}/.config/gcloud"
PROJECT_ID="${WORKLOAD_PROJECT_ID:-${GCP_PROJECT_ID:-my-workload-project}}"

echo "============================================================"
echo "Authenticating Isolated Session for Developer 2"
echo " User:    ${DEV_EMAIL}"
echo " Home:    ${TEST_HOME}"
echo " Config:  ${GCLOUD_CONFIG}"
echo "============================================================"
echo ""

mkdir -p "${GCLOUD_CONFIG}" "${TEST_HOME}/.gemini"

echo "[1/2] Authenticating Google Cloud Application Default Credentials (ADC)..."
HOME="${TEST_HOME}" CLOUDSDK_CONFIG="${GCLOUD_CONFIG}" \
  gcloud auth login --update-adc "$@"

echo ""
echo "[2/2] Initializing Antigravity CLI session for ${DEV_EMAIL}..."
echo "Please complete the interactive sign-in prompt and project selection when prompted."
echo ""
HOME="${TEST_HOME}" CLOUDSDK_CONFIG="${GCLOUD_CONFIG}" \
  agy

echo ""
echo "✅ Developer 2 (${DEV_EMAIL}) successfully authenticated in ${TEST_HOME}."

