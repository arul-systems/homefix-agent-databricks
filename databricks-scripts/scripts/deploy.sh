#!/usr/bin/env bash
# Load .env and deploy the AI Gateway model provider + model services bundle.
# Usage: ./scripts/deploy.sh [extra `databricks bundle deploy` args, e.g. -t dev]
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$BUNDLE_DIR/.env}"
cd "$BUNDLE_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found (cp .env.example .env and fill it in)" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set in $ENV_FILE}"

# The CLI reads BUNDLE_VAR_<name> as bundle variables; the key never touches a file in the bundle.
export BUNDLE_VAR_openai_api_key="$OPENAI_API_KEY"
[[ -n "${OPENAI_MODEL:-}" ]] && export BUNDLE_VAR_openai_model="$OPENAI_MODEL"
[[ -n "${UC_CATALOG:-}" ]] && export BUNDLE_VAR_catalog="$UC_CATALOG"
[[ -n "${UC_SCHEMA:-}" ]] && export BUNDLE_VAR_schema="$UC_SCHEMA"

echo "==> Profile: ${DATABRICKS_CONFIG_PROFILE:-<default>}"
if ! databricks current-user me -o json >/dev/null; then
  echo "error: Databricks auth failed for profile '${DATABRICKS_CONFIG_PROFILE:-<default>}'." >&2
  echo "       Set DATABRICKS_CONFIG_PROFILE in $ENV_FILE to a valid profile (see \`databricks auth profiles\`)." >&2
  exit 1
fi

echo "==> Validating bundle"
databricks bundle validate "$@"
echo "==> Deploying bundle (schema, OpenAI secret, model provider service, model services)"
databricks bundle deploy "$@"

echo "==> Done."
databricks bundle summary "$@" | sed -n '/Resources:/,$p'
