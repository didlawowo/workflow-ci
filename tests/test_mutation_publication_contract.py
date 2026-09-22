"""Publication workflow and reporter CLI contracts for issues 79 and 80."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "result,required,download,success",
    [
        ("success", "false", "skipped", True),
        ("success", "true", "success", True),
        ("success", "true", "failure", False),
        ("success", "", "skipped", False),
        ("failure", "false", "skipped", False),
        ("cancelled", "false", "success", False),
    ],
)
def test_publication_guard(result, required, download, success):
    data = yaml.safe_load((ROOT / ".github/workflows/quality-evidence.yml").read_text())
    steps = data["jobs"]["publish-evidence"]["steps"]
    guard = next(
        s for s in steps if s.get("name") == "Enforce mutation evidence publication"
    )
    completed = subprocess.run(
        ["bash", "-c", guard["run"]],
        env={
            **os.environ,
            "MUTATION_RESULT": result,
            "MUTATION_REQUIRED": required,
            "DOWNLOAD_RESULT": download,
        },
        check=False,
        capture_output=True,
    )
    assert (completed.returncode == 0) is success


def test_workflow_propagates_result_independently_and_publishes_missing_artifacts():
    data = yaml.safe_load((ROOT / ".github/workflows/quality-evidence.yml").read_text())
    steps = data["jobs"]["publish-evidence"]["steps"]
    download = next(s for s in steps if s.get("id") == "mutation-evidence-download")
    publisher = next(
        s for s in steps if s.get("name") == "Publish trusted quality evidence"
    )
    assert download["continue-on-error"] is True
    assert publisher["if"] == "always()"
    assert (
        publisher["with"]["mutation-result"]
        == "${{ needs.mutation.result || 'unknown' }}"
    )
    assert (
        publisher["with"]["mutation-required"]
        == "${{ needs.mutation.outputs.required || 'unknown' }}"
    )
    assert data["jobs"]["independent-verification"]["permissions"] == {
        "contents": "read",
        "actions": "read",
    }


@pytest.mark.parametrize(
    "extra,expected,detail",
    [
        (
            ["--mutation-result", "failure", "--mutation-required", "unknown"],
            "FAIL",
            "execution: failure",
        ),
        (
            ["--mutation-result", "success", "--mutation-required", "false"],
            "PASS",
            "execution: success",
        ),
        (["--security-scan-errors", "1"], "FAIL", "1 scanner execution error(s)"),
    ],
)
def test_cli_json_and_markdown_agree(tmp_path, extra, expected, detail):
    output = tmp_path / "result.json"
    markdown = tmp_path / "result.md"
    args = [
        sys.executable,
        str(ROOT / "quality_report.py"),
        "--tests-total",
        "10",
        "--tests-failed",
        "0",
        "--test-status",
        "success",
        "--coverage-percentage",
        "94.7",
        "--quality-status",
        "true",
        "--security-issues",
        "0",
        "--output-json",
        str(output),
        "--output-markdown",
        str(markdown),
        *extra,
    ]
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "QUALITY_"))
    }
    completed = subprocess.run(
        args, cwd=tmp_path, env=env, check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(output.read_text())
    assert value["gate"]["status"] == expected
    assert f"**{expected}**" in markdown.read_text()
    assert detail in markdown.read_text()
    if "--security-scan-errors" in extra:
        assert value["security"]["issues"] == 0
        assert value["security"]["scan_errors"] == 1
