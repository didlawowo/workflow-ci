#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d "${RUNNER_TEMP:-/tmp}/image-change-mutmut.XXXXXX")"
cleanup() {
  local status=$?
  if [[ "$status" -eq 0 ]]; then
    rm -rf -- "$SCRATCH"
  else
    echo "Mutation diagnostics retained in $SCRATCH" >&2
  fi
}
trap cleanup EXIT
mkdir -p "$SCRATCH/tests" "$SCRATCH/.github/actions/detect-image-changes"
cp "$ROOT/image_changes.py" "$SCRATCH/"
cp "$ROOT/tests/test_image_changes.py" "$SCRATCH/tests/"
cp "$ROOT/.github/actions/detect-image-changes/action.yml" "$SCRATCH/.github/actions/detect-image-changes/"
cat > "$SCRATCH/pyproject.toml" <<'TOML'
[tool.mutmut]
source_paths = ["image_changes.py"]
pytest_add_cli_args_test_selection = ["tests/test_image_changes.py"]
also_copy = ["tests", ".github"]
TOML
cd "$SCRATCH"
mutmut run
mutmut export-cicd-stats
mutmut results
python3 - <<'PY'
import json
from pathlib import Path
stats = json.loads(Path("mutants/mutmut-cicd-stats.json").read_text())
print(stats)
if not stats["killed"] or any(stats[key] for key in ("survived", "timeout", "suspicious", "no_tests", "skipped", "segfault", "check_was_interrupted_by_user")):
    raise SystemExit("Image detection has surviving or unresolved mutants")
PY
