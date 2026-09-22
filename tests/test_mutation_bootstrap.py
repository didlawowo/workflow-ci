"""Bootstrap behavior moved with its owner from GitHub Manager to Workflow CI."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / ".github" / "scripts" / "mutation_bootstrap.sh"


def _fake_python(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "-m venv" ]]; then
  if [[ "${FAKE_VENV_FAIL:-0}" == "1" ]]; then
    echo "ensurepip is not available" >&2
    exit 1
  fi
  target="$3"
  mkdir -p "$target/bin"
  cp "$0" "$target/bin/python"
  exit 0
fi
if [[ "$1 $2 $3" == "-m pip --version" ]]; then
  exit 0
fi
if [[ "$1 $2" == "-m pip" && "$3" == "install" ]]; then
  target=""
  for ((i=1; i<=$#; i++)); do
    if [[ "${!i}" == "--target" ]]; then
      j=$((i+1))
      target="${!j}"
      break
    fi
  done
  if [[ -z "$target" ]]; then
    target="$(dirname "$0")/.."
  fi
  mkdir -p "$target/bin"
  cat > "$target/bin/mutmut" <<'EOF'
#!/usr/bin/env bash
echo "mutmut $*" >> "$MUTMUT_LOG"
if [[ "${FAKE_MUTMUT_SURVIVES:-0}" == "1" ]]; then
  echo "survived=1"
  exit 7
fi
echo "killed=1 survived=0"
EOF
  chmod +x "$target/bin/mutmut"
  exit 0
fi
if [[ "$1" == "-c" ]]; then
  exit 0
fi
exit 0
"""
    )
    path.chmod(0o755)


def _run_bootstrap(tmp_path: Path, *, venv_fails: bool = False, survives: bool = False):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _fake_python(fake_bin / "python3")
    mutation = tmp_path / ".ci" / "mutation.sh"
    mutation.parent.mkdir()
    mutation.write_text("#!/usr/bin/env bash\nset -euo pipefail\nmutmut run\n")
    mutation.chmod(0o755)
    mutmut_log = tmp_path / "mutmut.log"
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(runner_temp),
        "MUTATION_SCRIPT": str(mutation),
        "MUTATION_POLICY_LABELS": "priority:high",
        "MUTMUT_LOG": str(mutmut_log),
        "FAKE_VENV_FAIL": "1" if venv_fails else "0",
        "FAKE_MUTMUT_SURVIVES": "1" if survives else "0",
    }
    result = subprocess.run(
        ["bash", str(BOOTSTRAP)], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    return result, mutmut_log, runner_temp


def test_bootstrap_uses_venv_and_executes_engine_command(tmp_path):
    result, mutmut_log, runner_temp = _run_bootstrap(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "bootstrap mode=venv" in result.stdout
    assert mutmut_log.read_text().strip() == "mutmut run"
    assert "result=success" in result.stdout
    assert not list(runner_temp.glob("mutation-bootstrap.*"))


def test_bootstrap_falls_back_without_python3_venv_and_executes_engine(tmp_path):
    result, mutmut_log, runner_temp = _run_bootstrap(tmp_path, venv_fails=True)
    assert result.returncode == 0, result.stderr
    assert "bootstrap mode=target" in result.stdout
    assert mutmut_log.read_text().strip() == "mutmut run"
    assert "result=success" in result.stdout
    assert not list(runner_temp.glob("mutation-bootstrap.*"))


def test_surviving_mutants_cannot_turn_high_risk_gate_green(tmp_path):
    result, mutmut_log, _runner_temp = _run_bootstrap(tmp_path, survives=True)
    assert result.returncode == 7
    assert mutmut_log.read_text().strip() == "mutmut run"
    assert "ERROR[MUTATION_SURVIVORS]" in result.stderr
