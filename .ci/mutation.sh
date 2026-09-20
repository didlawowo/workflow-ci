#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  MUTATION_VENV="${RUNNER_TEMP:-/tmp}/workflow-ci-mutation-venv"
  rm -rf "$MUTATION_VENV"
  python3 -m venv "$MUTATION_VENV"
  export VIRTUAL_ENV="$MUTATION_VENV"
  export PATH="$MUTATION_VENV/bin:$PATH"
fi

python -m pip install -q pytest mutmut
rm -rf mutants
mutmut run
mutmut export-cicd-stats
test -f mutants/mutmut-cicd-stats.json
