#!/usr/bin/env bash
set -euo pipefail

# Ensure CLOUDSDK_CONFIG is pointed to the user's gcloud directory
export CLOUDSDK_CONFIG="${CLOUDSDK_CONFIG:-${HOME}/.config/gcloud}"

echo "============================================================"
echo "Authenticating Application Default Credentials (ADC) with:"
echo " - Google Cloud Platform (cloud-platform)"
echo " - Workspace Directory Users (admin.directory.user)"
echo " - Workspace Directory Groups (admin.directory.group)"
echo "============================================================"
echo ""

SCOPES="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/admin.directory.user,https://www.googleapis.com/auth/admin.directory.group"

gcloud auth application-default login --scopes="${SCOPES}" "$@"

echo ""
echo "✅ Application Default Credentials successfully updated with Workspace Admin scopes."
