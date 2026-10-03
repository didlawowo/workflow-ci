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
    step = next(
        s for s in action["runs"]["steps"] if s["name"] == "Build quality evidence"
    )
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
        "RUNNER_TEMP": str(tmp_path),
        # setup-python's Linux interpreter needs its shared library directory.
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", ""),
    }
    result = subprocess.run(
        ["bash", "-e", "-c", command],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / ".quality/quality-report.json").read_text())
    assert report["gate"]["status"] == "FAIL"
    assert "mutation-execution" in report["gate"]["failures"]
    assert report["security"]["scan_errors"] == int(scan_errors or "0")
    assert "FAIL" in (tmp_path / ".quality/quality-report.md").read_text()


@pytest.mark.parametrize(
    "message,failures,expected_calls,expected_status",
    [
        ("HTTP Error 503", 1, 2, 0),
        ("TimeoutError", 5, 3, 17),
        ("invalid input", 1, 1, 17),
    ],
)
def test_reporter_retry_and_log_isolation(
    tmp_path, message, failures, expected_calls, expected_status
):
    action = yaml.safe_load(
        (ROOT / ".github/actions/quality-report/action.yml").read_text()
    )
    step = next(
        s for s in action["runs"]["steps"] if s["name"] == "Build quality evidence"
    )
    inputs = {
        key: str(value.get("default", "")) for key, value in action["inputs"].items()
    }
    command = re.sub(
        r"\$\{\{ inputs\.([\w-]+) \}\}", lambda m: inputs[m[1]], step["run"]
    )
    uv = tmp_path / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        'n=$(cat "$RUNNER_TEMP/calls" 2>/dev/null || echo 0)\n'
        'n=$((n+1)); echo "$n" > "$RUNNER_TEMP/calls"\n'
        'if (( n <= FAILURES )); then echo "$MESSAGE" >&2; exit 17; fi\n'
    )
    uv.chmod(0o700)
    # The previous fixed filename must never be opened or removed.
    collision = tmp_path / "quality-reporter-local.log"
    collision.mkdir()
    result = subprocess.run(
        ["bash", "-e", "-c", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_ACTION_PATH": str(ROOT / ".github/actions/quality-report"),
            "MESSAGE": message,
            "FAILURES": str(failures),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == expected_status, result.stdout + result.stderr
    assert int((tmp_path / "calls").read_text()) == expected_calls
    assert collision.is_dir()
    assert not list(tmp_path.glob("quality-reporter.*"))


def test_quality_report_resolves_a_writable_uv_cache():
    action_path = ROOT / ".github/actions/quality-report/action.yml"
    action = yaml.safe_load(action_path.read_text())
    steps = action["runs"]["steps"]
    resolver = next(
        step
        for step in steps
        if step["name"] == "Resolve writable uv cache for quality reporter"
    )
    assert resolver["uses"] == "$/.github/actions/resolve-uv-cache"

    exporter = next(
        step
        for step in steps
        if step["name"] == "Export resolved uv cache"
    )
    assert 'steps.uv-cache.outputs.path' in exporter["run"]
    assert "GITHUB_ENV" in exporter["run"]

    installer = next(
        step for step in steps if step["name"] == "Install uv for quality reporter"
    )
    assert installer["with"]["cache-local-path"] == "${{ steps.uv-cache.outputs.path }}"
    assert installer["with"]["prune-cache"] is False
