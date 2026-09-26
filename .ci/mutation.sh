#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_VERSION="${MUTATION_PYTHON_VERSION:-3.12}"
MUTATION_DEPTH="${MUTATION_DEPTH:-medium}"
case "$MUTATION_DEPTH" in
  medium|high) ;;
  *) echo "::error::Unsupported MUTATION_DEPTH=$MUTATION_DEPTH"; exit 1 ;;
esac

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
  if ! "$PYTHON" "$SCRIPT_DIR/mutation_scope.py" --repo "$PWD" --base "$MUTATION_BASE_SHA" --head "$MUTATION_HEAD_SHA" --depth "$MUTATION_DEPTH" > "$TARGETS_FILE"; then
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

  printf 'Mutation targets (%s) for %s...%s:\n' "$MUTATION_DEPTH" "$MUTATION_BASE_SHA" "$MUTATION_HEAD_SHA"
  printf '  - %s\n' "${MUTATION_TARGETS[@]}"
else
  echo "::notice::No PR base/head scope supplied; running the full configured mutation scope."
fi

# Consumer dependencies belong in the isolated environment too. A provisioner
# must not maintain a private `uv sync`/Mutmut runner merely to install them.
if command -v uv >/dev/null 2>&1 && [[ -f pyproject.toml ]]; then
  SYNC_FILE="$(mktemp)"
  trap 'rm -f "$SYNC_FILE"' EXIT
  "$PYTHON" "$SCRIPT_DIR/mutation_contract.py" > "$SYNC_FILE"
  mapfile -d '' -t SYNC_ARGS < "$SYNC_FILE"
  UV_PROJECT_ENVIRONMENT="$VIRTUAL_ENV" uv "${SYNC_ARGS[@]}"
  rm -f "$SYNC_FILE"
  trap - EXIT
elif command -v uv >/dev/null 2>&1 && [[ -f requirements.txt ]]; then
  uv pip install --python "$PYTHON" -r requirements.txt
fi

if command -v uv >/dev/null 2>&1; then
  if ! uv pip install --python "$PYTHON" -q pytest pytest-cov 'mutmut>=3,<4'; then
    bootstrap_error "uv failed to install pytest/mutmut into the isolated environment."
    exit 1
  fi
else
  if ! "$PYTHON" -m pip install -q pytest pytest-cov 'mutmut>=3,<4'; then
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

python_args=("$MUTATION_DEPTH" "${MUTATION_BASE_SHA:-}" "${MUTATION_HEAD_SHA:-}")
"$PYTHON" - "${python_args[@]}" <<'PY'
import json
import sys
from pathlib import Path

path = Path("mutants/mutmut-cicd-stats.json")
payload = json.loads(path.read_text(encoding="utf-8"))
payload["depth"] = sys.argv[1]
payload["scope"] = {
    "base_sha": sys.argv[2],
    "head_sha": sys.argv[3],
    "no_targets": False,
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

# mutmut 3.x 'results' intentionally omits killed mutants from its text output.
# The trusted verifier needs per-mutant statuses, so reconstruct the omitted
# killed rows from the generated mutant registry and cross-check the count
# against the machine-readable CI/CD stats before publishing diagnostics.
mkdir -p .quality
RAW_RESULTS=".quality/mutmut-results.raw.txt"
mutmut results --all true > "$RAW_RESULTS" || true
"$PYTHON" - "$RAW_RESULTS" mutants/mutmut-cicd-stats.json .quality/mutmut-results.txt "${MUTATION_TARGETS[@]}" <<'PY'
from fnmatch import fnmatchcase
import json
import re
import sys
from pathlib import Path

raw_path = Path(sys.argv[1])
stats_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])
target_patterns = tuple(sys.argv[4:])

def in_scope(mutant_id: str) -> bool:
    return not target_patterns or any(
        fnmatchcase(mutant_id, pattern) for pattern in target_patterns
    )

stats = json.loads(stats_path.read_text(encoding="utf-8"))
expected_total = int(stats.get("total", 0))
expected_killed = int(stats.get("killed", 0))

status_re = re.compile(r"^\s*(\S+):\s+([a-z_ ]+)\s*$")
raw_statuses: dict[str, str] = {}
for line in raw_path.read_text(encoding="utf-8").splitlines():
    match = status_re.match(line)
    if not match:
        continue
    mutant_id, status = match.groups()
    if in_scope(mutant_id):
        raw_statuses[mutant_id] = status.strip().replace(" ", "_")

assignment_re = re.compile(r"mutants_[^\[]+\['([^']+__mutmut_\d+)'\]")
all_mutants: set[str] = set()
for source in Path("mutants").rglob("*.py"):
    relative = source.relative_to("mutants").with_suffix("")
    parts = list(relative.parts)
    # Keep the registry IDs aligned with mutation_scope._module_name():
    # Mutmut stores src-layout files under mutants/src/, but reports IDs
    # without the leading "src." package prefix.
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    module = ".".join(parts)
    if not module:
        continue
    for local_id in assignment_re.findall(source.read_text(encoding="utf-8")):
        mutant_id = f"{module}.{local_id}"
        if in_scope(mutant_id):
            all_mutants.add(mutant_id)

# Scoped runs: `mutmut export-cicd-stats` reports total as the full project
# collection, not the requested target scope, so only full runs can be
# cross-checked against it; scoped runs are cross-checked via the killed count.
if not target_patterns and expected_total and len(all_mutants) != expected_total:
    raise SystemExit(
        f"mutmut diagnostics mismatch: generated={len(all_mutants)} total={expected_total}"
    )

already_killed = {mid for mid, status in raw_statuses.items() if status == "killed"}
missing = all_mutants - set(raw_statuses)
expected_missing_killed = expected_killed - len(already_killed)
if expected_missing_killed < 0 or len(missing) != expected_missing_killed:
    raise SystemExit(
        "mutmut diagnostics mismatch: "
        f"raw_killed={len(already_killed)} inferred={len(missing)} "
        f"expected_killed={expected_killed}"
    )

complete = dict(raw_statuses)
for mutant_id in missing:
    complete[mutant_id] = "killed"

with out_path.open("w", encoding="utf-8") as handle:
    for mutant_id in sorted(complete):
        handle.write(f"{mutant_id}: {complete[mutant_id]}\\n")
PY
rm -f "$RAW_RESULTS"
