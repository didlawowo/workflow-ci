#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "::error title=Mutation bootstrap failure::uv is required by the mutation gate."
  exit 1
fi

PYTHON_VERSION="${MUTATION_PYTHON_VERSION:-3.12}"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  MUTATION_VENV="${RUNNER_TEMP:-/tmp}/workflow-ci-mutation-venv"
  rm -rf "$MUTATION_VENV"
  echo "::group::mutation bootstrap (uv)"
  uv python install "$PYTHON_VERSION"
  uv venv --python "$PYTHON_VERSION" --seed "$MUTATION_VENV"
  echo "::endgroup::"
  export VIRTUAL_ENV="$MUTATION_VENV"
  export PATH="$MUTATION_VENV/bin:$PATH"
fi

mkdir -p .quality
uv pip install --python "$VIRTUAL_ENV/bin/python" -q pytest mutmut
rm -rf mutants

echo "::notice::Running mutmut with base=${MUTATION_BASE_SHA:-unknown} head=${MUTATION_HEAD_SHA:-unknown}"
set +e
mutmut run
STATUS=$?
set -e

mutmut results > .quality/mutmut-results.txt || true
mutmut export-cicd-stats || true

REPORT="mutants/mutmut-cicd-stats.json"
if [[ ! -s "$REPORT" ]]; then
  echo "::error title=Mutation evidence failure::mutmut did not produce $REPORT."
  exit "${STATUS:-1}"
fi

# Preserve the engine exit code. Survivors are classified from the JSON by the
# trusted verification job, rather than being confused with bootstrap failures.
exit "$STATUS"
