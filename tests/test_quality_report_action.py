"""Exercise the real composite shell and reporter with absent upstream outputs."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scan_errors", ["", "0", "1"])
def test_absent_scanner_output_still_publishes_mutation_failure(tmp_path, scan_errors):
    action_path = ROOT / ".github/actions/quality-report"
    action = yaml.safe_load((action_path / "action.yml").read_text())
    inputs = {name: item.get("default", "") for name, item in action["inputs"].items()}
    inputs.update(
        {
            "comment": "false",
            "mutation-result": "failure",
            "mutation-required": "unknown",
            "security-scan-errors": scan_errors,
        }
    )
    step = next(s for s in action["runs"]["steps"] if s["name"] == "Build quality evidence")
    command = re.sub(
        r"\$\{\{ inputs\.([\w-]+) \}\}",
        lambda match: str(inputs[match.group(1)]),
        step["run"],
    )
    assert "${{" not in command
    # Provisioning is not under test: invoke the same reporter with this Python.
    uv = tmp_path / "uv"
    uv.write_text('#!/usr/bin/env bash\nshift 5\nexec "$QUALITY_TEST_PYTHON" "$@"\n')
    uv.chmod(0o700)
    env = {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_ACTION_PATH": str(action_path),
        "QUALITY_TEST_PYTHON": sys.executable,
    }
    result = subprocess.run(
        ["bash", "-e", "-c", command],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / ".quality/quality-report.json").read_text())
    assert report["gate"]["status"] == "FAIL"
    assert "mutation-execution" in report["gate"]["failures"]
    assert report["security"]["scan_errors"] == int(scan_errors or "0")
    assert "FAIL" in (tmp_path / ".quality/quality-report.md").read_text()
