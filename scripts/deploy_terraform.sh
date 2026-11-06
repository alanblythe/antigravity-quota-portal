#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  source "${ROOT_DIR}/.env"
fi
if [[ -n "${IMAGE_TAG:-}" && -z "${TF_VAR_image_tag:-}" ]]; then
  export TF_VAR_image_tag="${IMAGE_TAG}"
fi

TF_DIR="${ROOT_DIR}/terraform"

TF_CMD="$(command -v tofu 2>/dev/null || command -v terraform 2>/dev/null || true)"
if [[ -z "${TF_CMD}" ]]; then
  echo "Error: Neither 'tofu' nor 'terraform' was found in PATH." >&2
  exit 1
fi

echo "==> Using ${TF_CMD} in ${TF_DIR}..."
cd "${TF_DIR}"
trap 'rm -f "${TF_DIR}/tfplan"' EXIT

# Extract a string variable value from terraform.tfvars if present
extract_tfvar() {
  local key="$1"
  local tfvars_file="${TF_DIR}/terraform.tfvars"
  if [[ -f "${tfvars_file}" ]]; then
    sed -nE "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"([^\"]+)\".*/\1/p" "${tfvars_file}" | tail -n 1
  fi
}

ENABLED_GROUP="${TF_VAR_enabled_group_email:-${ENABLED_GROUP_EMAIL:-$(extract_tfvar enabled_group_email)}}"
DISABLED_GROUP="${TF_VAR_disabled_group_email:-${DISABLED_GROUP_EMAIL:-$(extract_tfvar disabled_group_email)}}"

# Pre-flight check: verify Cloud Identity groups exist before running Terraform
if [[ "${SKIP_GROUP_PREFLIGHT:-false}" != "true" ]] && command -v gcloud >/dev/null 2>&1; then
  if [[ -n "${ENABLED_GROUP}" || -n "${DISABLED_GROUP}" ]]; then
    echo "==> Running pre-flight Cloud Identity group check..."
    MISSING_GROUPS=()
    for grp in "${ENABLED_GROUP}" "${DISABLED_GROUP}"; do
      [[ -z "${grp}" ]] && continue
      if grp_name="$(gcloud identity groups describe "${grp}" --format="value(name)" 2>/dev/null || CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE=false gcloud identity groups describe "${grp}" --format="value(name)" 2>/dev/null)" && [[ -n "${grp_name}" ]]; then
        echo "  + Verified Cloud Identity group: ${grp} (${grp_name})"
      else
        if [[ "${CREATE_MISSING_GROUPS:-false}" == "true" ]]; then
          org_domain="${grp#*@}"
          echo "  * Creating missing Cloud Identity group: ${grp} (domain: ${org_domain})..."
          gcloud identity groups create "${grp}" \
            --organization="${org_domain}" \
            --labels="cloudidentity.googleapis.com/groups.discussion_forum=" 2>/dev/null || \
          CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE=false gcloud identity groups create "${grp}" \
            --organization="${org_domain}" \
            --labels="cloudidentity.googleapis.com/groups.discussion_forum="
        else
          echo "  ! Missing or inaccessible Cloud Identity group: ${grp}" >&2
          MISSING_GROUPS+=("${grp}")
        fi
      fi
    done

    if [[ "${#MISSING_GROUPS[@]}" -gt 0 ]]; then
      org_domain="${MISSING_GROUPS[0]#*@}"
      echo "" >&2
      echo "Error: Pre-flight check failed. The following Cloud Identity group(s) could not be found:" >&2
      for missing in "${MISSING_GROUPS[@]}"; do
        echo "  - ${missing}" >&2
      done
      echo "" >&2
      echo "Create both groups before running Terraform (or re-run with CREATE_MISSING_GROUPS=true):" >&2
      for missing in "${MISSING_GROUPS[@]}"; do
        echo "  gcloud identity groups create \"${missing}\" --organization=\"${org_domain}\" --labels=\"cloudidentity.googleapis.com/groups.discussion_forum=\"" >&2
      done
      echo "To bypass this pre-flight check, set SKIP_GROUP_PREFLIGHT=true." >&2
      exit 1
    fi
  fi
fi

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
