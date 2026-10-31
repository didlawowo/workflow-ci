#!/usr/bin/env bash
# Central campaign plan entrypoint: writes the content-bound plan JSON to $1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ne 1 || -z "$1" ]]; then
  echo "::error::mutation-plan.sh requires exactly one output path." >&2
  exit 1
fi
if [[ -z "${MUTATION_BASE_SHA:-}" || -z "${MUTATION_HEAD_SHA:-}" ]]; then
  echo "::error::mutation-plan.sh requires MUTATION_BASE_SHA and MUTATION_HEAD_SHA." >&2
  exit 1
fi

source "$SCRIPT_DIR/mutation_campaign_bootstrap.sh"
"$CAMPAIGN_PYTHON" "$SCRIPT_DIR/mutation_campaign_inventory.py" plan "$1"
