#!/usr/bin/env bash
# Delete everything deploy.sh created: the model services, the OpenAI model provider service,
# the UC secret, the schema, and local bundle state.
# Usage: ./scripts/undeploy.sh [-y|--yes] [extra `databricks bundle destroy` args, e.g. -t dev]
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$BUNDLE_DIR/.env}"
cd "$BUNDLE_DIR"

ASSUME_YES=false
ARGS=()
for a in "$@"; do
  case "$a" in
    -y|--yes) ASSUME_YES=true ;;
    *) ARGS+=("$a") ;;
  esac
done

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
# Bundle variables must resolve even when destroying; the key value itself isn't needed.
export BUNDLE_VAR_openai_api_key="${OPENAI_API_KEY:-unused-for-destroy}"
[[ -n "${OPENAI_MODEL:-}" ]] && export BUNDLE_VAR_openai_model="$OPENAI_MODEL"
[[ -n "${UC_CATALOG:-}" ]] && export BUNDLE_VAR_catalog="$UC_CATALOG"
[[ -n "${UC_SCHEMA:-}" ]] && export BUNDLE_VAR_schema="$UC_SCHEMA"

echo "==> Profile: ${DATABRICKS_CONFIG_PROFILE:-<default>}"
if ! databricks current-user me -o json >/dev/null; then
  echo "error: Databricks auth failed for profile '${DATABRICKS_CONFIG_PROFILE:-<default>}'." >&2
  exit 1
fi

echo "This will permanently delete:"
echo "  - the GPT 6 sol, Llama 3.1 8B and Llama 3.3 70B model services"
echo "  - the OpenAI model provider service and the UC secret holding the API key"
echo "  - schema '${UC_CATALOG:-agent_workspace_7474653685104191}.${UC_SCHEMA:-homefix_ai}'"
echo "  - the bundle's workspace files and deployment state"
if ! $ASSUME_YES; then
  read -r -p "Continue? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
fi

databricks bundle destroy --auto-approve ${ARGS[@]+"${ARGS[@]}"}
rm -rf "$BUNDLE_DIR/.databricks"
echo "==> Done. Everything created by deploy.sh has been removed (.env was left in place)."
