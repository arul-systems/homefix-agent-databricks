#!/usr/bin/env bash
# Load .env and deploy the AI Gateway model provider + model services bundle.
# Usage: ./scripts/deploy.sh [extra `databricks bundle deploy` args, e.g. -t dev]
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$BUNDLE_DIR/.env}"
cd "$BUNDLE_DIR"

# Locally, settings come from .env. In CI there is no .env; the same names come from the environment.
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
elif [[ -z "${CI:-}" ]]; then
  echo "error: $ENV_FILE not found (cp .env.example .env and fill it in)" >&2
  exit 1
fi

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set (in $ENV_FILE, or in the environment in CI)}"

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

# The deployed agent app runs as its own service principal; grant it access to the model services.
AGENT_APP_NAME="${AGENT_APP_NAME:-agent-bricks-list}"
if [[ -z "${AGENT_APP_PRINCIPAL:-}" ]]; then
  AGENT_APP_PRINCIPAL="$(databricks apps get "$AGENT_APP_NAME" -o json \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("service_principal_client_id",""))')" || true
fi
if [[ -z "${AGENT_APP_PRINCIPAL:-}" ]]; then
  echo "error: couldn't find the service principal of app '$AGENT_APP_NAME'." >&2
  echo "       Deploy the agent first, or set AGENT_APP_NAME / AGENT_APP_PRINCIPAL in $ENV_FILE." >&2
  exit 1
fi
export BUNDLE_VAR_agent_principal="$AGENT_APP_PRINCIPAL"
echo "==> Granting access to app '$AGENT_APP_NAME' (service principal $AGENT_APP_PRINCIPAL)"

echo "==> Validating bundle"
databricks bundle validate "$@"
echo "==> Deploying bundle (schema, OpenAI secret, model provider service, model services)"
databricks bundle deploy "$@"

# Let the web frontend's service principal (agent-ui) call the agent app. A recreated app starts with
# only its owner on the permission list, so grant this on every deploy. update-permissions adds to the
# existing list rather than replacing it.
if [[ -n "${AGENT_UI_CLIENT_ID:-}" ]]; then
  for client_id in ${AGENT_UI_CLIENT_ID//,/ }; do
    echo "==> Granting CAN_USE on app '$AGENT_APP_NAME' to service principal $client_id"
    databricks apps update-permissions "$AGENT_APP_NAME" -o json --json \
      "{\"access_control_list\":[{\"service_principal_name\":\"$client_id\",\"permission_level\":\"CAN_USE\"}]}" >/dev/null
  done
else
  echo "==> AGENT_UI_CLIENT_ID not set; skipping the agent-ui CAN_USE grant on app '$AGENT_APP_NAME'"
fi

echo "==> Done."
databricks bundle summary "$@" | sed -n '/Resources:/,$p'
