#!/usr/bin/env bash
# Shared bootstrap for the central campaign entrypoints (plan and shard).
# Provisions one isolated environment, syncs consumer dependencies and makes
# the mutmut toolchain available. Executed with cwd set to the PR project.
set -euo pipefail

CAMPAIGN_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_VERSION="${MUTATION_PYTHON_VERSION:-3.12}"

bootstrap_error() {
  echo "::error::Campaign bootstrap failure: $*" >&2
}

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    bootstrap_error "uv is required to provision the isolated campaign environment."
    exit 1
  fi
  MUTATION_VENV="${RUNNER_TEMP:-/tmp}/workflow-ci-campaign-venv"
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

WORKING_DIRECTORY="${MUTATION_WORKING_DIRECTORY:-.}"
if [[ "$WORKING_DIRECTORY" == /* ]]; then
  echo "::error::Campaign working-directory must be relative." >&2
  exit 1
fi
REPOSITORY_ROOT="$(pwd -P)"
PROJECT_ROOT="$(cd "$REPOSITORY_ROOT/$WORKING_DIRECTORY" && pwd -P)"
case "$PROJECT_ROOT" in
  "$REPOSITORY_ROOT"|"$REPOSITORY_ROOT"/*) ;;
  *) echo "::error::Campaign working-directory escapes the checkout." >&2; exit 1 ;;
esac
cd "$PROJECT_ROOT"

if command -v uv >/dev/null 2>&1 && [[ -f pyproject.toml ]]; then
  if [[ -f uv.lock ]]; then
    if ! env -u UV_DEFAULT_INDEX -u UV_INDEX_URL -u UV_EXTRA_INDEX_URL \
      uv lock --check --offline; then
      bootstrap_error "uv.lock is not consistent with pyproject.toml; run \`uv lock\` in the consumer."
      exit 1
    fi
  fi
  SYNC_FILE="$(mktemp)"
  trap 'rm -f "$SYNC_FILE"' EXIT
  "$PYTHON" "$CAMPAIGN_SCRIPT_DIR/mutation_contract.py" > "$SYNC_FILE"
  mapfile -d '' -t SYNC_ARGS < "$SYNC_FILE"
  UV_PROJECT_ENVIRONMENT="$VIRTUAL_ENV" uv "${SYNC_ARGS[@]}"
  rm -f "$SYNC_FILE"
  trap - EXIT
elif command -v uv >/dev/null 2>&1 && [[ -f requirements.txt ]]; then
  uv pip install --python "$PYTHON" -r requirements.txt
fi

if command -v uv >/dev/null 2>&1; then
  if ! uv pip install --python "$PYTHON" -q 'pytest<9' pytest-cov 'mutmut>=3,<4'; then
    bootstrap_error "uv failed to install pytest/mutmut into the isolated environment."
    exit 1
  fi
else
  if ! "$PYTHON" -m pip install -q 'pytest<9' pytest-cov 'mutmut>=3,<4'; then
    bootstrap_error "pip failed to install pytest/mutmut into the pre-provisioned environment."
    exit 1
  fi
fi

export CAMPAIGN_PYTHON="$PYTHON"
