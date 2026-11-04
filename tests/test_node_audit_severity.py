"""Execute the actual audit action with synthetic package-manager responses."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "counts,level,status,expected",
    [
        ({"moderate": 21}, "high", 0, 0),
        ({"moderate": 21, "high": 2, "critical": 1}, "high", 1, 3),
        ({"moderate": 21, "high": 2, "critical": 1}, "critical", 1, 1),
        ({"low": 4, "moderate": 3}, "moderate", 1, 3),
        ({"low": 4, "moderate": 3}, "low", 1, 7),
        ({}, "high", 0, 0),
        ({}, "high", 1, 1),
        ({"high": 2}, "invalid", 0, 1),
    ],
)
def test_audit_counts_only_requested_severity(
    tmp_path, counts, level, status, expected
):
    levels = {name: 0 for name in ("info", "low", "moderate", "high", "critical")}
    levels.update(counts)
    levels["total"] = sum(levels.values())
    payload = json.dumps({"metadata": {"vulnerabilities": levels}})
    assert run_audit(tmp_path, payload, level, status) == expected


@pytest.mark.parametrize("payload", ["invalid json", "", '{"error":{"code":"E500"}}'])
def test_invalid_audit_response_cannot_report_clean(tmp_path, payload):
    assert run_audit(tmp_path, payload, "high", 0) == 1


def run_audit(tmp_path, payload, level, status):
    action = yaml.safe_load(
        (ROOT / ".github/actions/node-quality-security/action.yml").read_text()
    )
    script = next(s["run"] for s in action["runs"]["steps"] if s.get("id") == "audit")
    script = script.replace("${{ inputs.package-manager }}", "npm")
    script = script.replace("${{ inputs.audit-level }}", level)
    npm = tmp_path / "npm"
    npm.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$FIXTURE_AUDIT"\nexit "$FIXTURE_STATUS"\n'
    )
    npm.chmod(0o755)
    output = tmp_path / "output"
    env = os.environ | {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "FIXTURE_AUDIT": payload,
        "FIXTURE_STATUS": str(status),
        "GITHUB_OUTPUT": str(output),
    }
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env=env,
        check=True,
        capture_output=True,
    )
    return int(output.read_text().strip().split("=", 1)[1])
