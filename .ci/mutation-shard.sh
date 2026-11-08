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
# Same pre-seeding as .ci/mutation.sh: mutmut's default also_copy list stages
# tests/, setup.cfg, pyproject.toml and lockfiles but not standalone pytest ini
# files. Pytest runs with cwd=mutants, so an unstaged pytest.ini falls back to
# the checkout's own configuration, resolves pythonpath/testpaths against the
# uninstrumented sources and every mutant ends up no_tests (regression of #207
# in campaign mode, #258). mutmut's own copy steps keep existing files.
mkdir -p mutants
for pytest_config in pytest.ini .pytest.ini tox.ini setup.cfg pyproject.toml conftest.py; do
  if [[ -f "$pytest_config" ]]; then
    cp "$pytest_config" "mutants/$pytest_config"
  fi
done
"$CAMPAIGN_PYTHON" "$SCRIPT_DIR/mutation_campaign_inventory.py" shard "$1"
