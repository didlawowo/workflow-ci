#!/usr/bin/env bash
# Central campaign shard entrypoint: executes the plan partition for
# MUTATION_SHARD_INDEX and writes the fragment JSON to $1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ne 1 || -z "$1" ]]; then
  echo "::error::mutation-shard.sh requires exactly one output path." >&2
  exit 1
fi
if [[ -z "${MUTATION_PLAN:-}" || ! -f "${MUTATION_PLAN:-}" ]]; then
  echo "::error::mutation-shard.sh requires MUTATION_PLAN pointing at the validated plan." >&2
  exit 1
fi

source "$SCRIPT_DIR/mutation_campaign_bootstrap.sh"
rm -rf mutants
"$CAMPAIGN_PYTHON" "$SCRIPT_DIR/mutation_campaign_inventory.py" shard "$1"
