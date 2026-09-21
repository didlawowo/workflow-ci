#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_VERSION="${MUTATION_PYTHON_VERSION:-3.12}"

bootstrap_error() {
  echo "::error::Mutation bootstrap failure: $*" >&2
}

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    bootstrap_error "uv is required to provision the isolated mutation environment."
    exit 1
  fi

  MUTATION_VENV="${RUNNER_TEMP:-/tmp}/workflow-ci-mutation-venv"
  rm -rf "$MUTATION_VENV"

  if ! uv python install "$PYTHON_VERSION"; then
    bootstrap_error "uv could not provision Python $PYTHON_VERSION."
    exit 1
  fi
  if ! uv venv "$MUTATION_VENV" --python "$PYTHON_VERSION"; then
    bootstrap_error "uv could not create $MUTATION_VENV."
    exit 1
  fi

  export VIRTUAL_ENV="$MUTATION_VENV"
  export PATH="$MUTATION_VENV/bin:$PATH"
fi

PYTHON="$VIRTUAL_ENV/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  bootstrap_error "isolated Python is missing at $PYTHON."
  exit 1
fi

MUTATION_TARGETS=()
if [[ -n "${MUTATION_BASE_SHA:-}" || -n "${MUTATION_HEAD_SHA:-}" ]]; then
  if [[ -z "${MUTATION_BASE_SHA:-}" || -z "${MUTATION_HEAD_SHA:-}" ]]; then
    echo "::error::Mutation scope failure: both MUTATION_BASE_SHA and MUTATION_HEAD_SHA are required." >&2
    exit 1
  fi
  if ! git cat-file -e "${MUTATION_BASE_SHA}^{commit}" 2>/dev/null; then
    echo "::error::Mutation scope failure: base commit $MUTATION_BASE_SHA is not available locally." >&2
    exit 1
  fi
  if ! git cat-file -e "${MUTATION_HEAD_SHA}^{commit}" 2>/dev/null; then
    echo "::error::Mutation scope failure: head commit $MUTATION_HEAD_SHA is not available locally." >&2
    exit 1
  fi

  TARGETS_FILE="$(mktemp)"
  trap 'rm -f "$TARGETS_FILE"' EXIT
  if ! "$PYTHON" "$SCRIPT_DIR/mutation_scope.py" --repo "$PWD" --base "$MUTATION_BASE_SHA" --head "$MUTATION_HEAD_SHA" > "$TARGETS_FILE"; then
    echo "::error::Mutation scope failure: unable to compute changed Python functions." >&2
    exit 1
  fi
  mapfile -t MUTATION_TARGETS < "$TARGETS_FILE"
  rm -f "$TARGETS_FILE"
  trap - EXIT

  if [[ "${#MUTATION_TARGETS[@]}" -eq 0 ]]; then
    mkdir -p .quality
    "$PYTHON" - "$MUTATION_BASE_SHA" "$MUTATION_HEAD_SHA" <<'PY'
import json
import sys
from pathlib import Path

payload = {
    "stats": {
        "killed": 0,
        "survived": 0,
        "timeouts": 0,
        "suspicious": 0,
        "total": 0,
    },
    "scope": {
        "base_sha": sys.argv[1],
        "head_sha": sys.argv[2],
        "no_targets": True,
    },
}
Path(".quality/mutation-no-targets.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
    echo "::notice::No mutation targets in $MUTATION_BASE_SHA...$MUTATION_HEAD_SHA; mutation engine skipped."
    exit 0
  fi

  printf 'Mutation targets for %s...%s:\n' "$MUTATION_BASE_SHA" "$MUTATION_HEAD_SHA"
  printf '  - %s\n' "${MUTATION_TARGETS[@]}"
else
  echo "::notice::No PR base/head scope supplied; running the full configured mutation scope."
fi

if command -v uv >/dev/null 2>&1; then
  if ! uv pip install --python "$PYTHON" -q pytest mutmut; then
    bootstrap_error "uv failed to install pytest/mutmut into the isolated environment."
    exit 1
  fi
else
  if ! "$PYTHON" -m pip install -q pytest mutmut; then
    bootstrap_error "pip failed to install pytest/mutmut into the pre-provisioned environment."
    exit 1
  fi
fi

rm -rf mutants
if [[ "${#MUTATION_TARGETS[@]}" -gt 0 ]]; then
  mutmut run "${MUTATION_TARGETS[@]}"
else
  mutmut run
fi

mutmut export-cicd-stats
test -f mutants/mutmut-cicd-stats.json
