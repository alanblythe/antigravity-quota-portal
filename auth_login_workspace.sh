#!/usr/bin/env bash
set -euo pipefail

# Ensure CLOUDSDK_CONFIG is pointed to the user's gcloud directory
export CLOUDSDK_CONFIG="${CLOUDSDK_CONFIG:-${HOME}/.config/gcloud}"

echo "============================================================"
echo "Authenticating Application Default Credentials (ADC) with:"
echo " - Google Cloud Platform (cloud-platform)"
echo " - Cloud Identity Groups (cloud-identity.groups)"
echo " - Cloud Monitoring Read (monitoring.read)"
echo "============================================================"
echo ""

SCOPES="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/cloud-identity.groups,https://www.googleapis.com/auth/monitoring.read"

gcloud auth application-default login --scopes="${SCOPES}" "$@"

echo ""
echo "✅ Application Default Credentials successfully updated with Cloud Identity & Monitoring scopes."
