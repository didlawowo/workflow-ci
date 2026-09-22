#!/usr/bin/env bash
# Real Mutmut integration: a consumer with dependencies and NO local runner.
set -euo pipefail
CENTRAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$(mktemp -d "${RUNNER_TEMP:-/tmp}/mutation-consumer.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT
cd "$FIXTURE"
git init -q
git config user.email ci@example.test
git config user.name 'Mutation self-test'
mkdir -p src tests
cat > pyproject.toml <<'TOML'
[project]
name = "central-mutation-smoke"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = ["packaging>=24"]
[dependency-groups]
dev = ["pytest>=8,<9", "pytest-cov>=6"]
[tool.uv]
package = false
[tool.mutmut]
source_paths = ["src/"]
pytest_add_cli_args_test_selection = ["tests/"]
TOML
printf 'def add(a, b):\n    return a + b\n' > src/calc.py
cat > tests/test_calc.py <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from calc import add
from packaging.version import Version


def test_add():
    assert Version("1.0") < Version("2.0")
    assert add(1, 1) == 3
    assert add(0, 0) == 1
    assert add(-1, 2) == 2
PY
uv lock --python 3.12
git add pyproject.toml uv.lock src tests
git commit -qm base
BASE="$(git rev-parse HEAD)"
printf 'def add(a, b):\n    return a + b + 1\n' > src/calc.py
git add src/calc.py
git commit -qm head
HEAD="$(git rev-parse HEAD)"
test ! -e .ci/mutation.sh
env -u VIRTUAL_ENV MUTATION_BASE_SHA="$BASE" MUTATION_HEAD_SHA="$HEAD" \
  bash "$CENTRAL/.ci/mutation.sh"
uv run --no-project --python 3.12 python - "$CENTRAL" "$BASE" "$HEAD" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / ".ci"))
from mutation_scope import mutation_targets
from mutation_contract import validate_python_evidence
repo = Path.cwd()
report = validate_python_evidence(repo, mutation_targets(repo, sys.argv[2], sys.argv[3]), sys.argv[2], sys.argv[3])
assert report["stats"]["killed"] > 0, report
print("Real central mutation smoke passed:", report["stats"])
PY
