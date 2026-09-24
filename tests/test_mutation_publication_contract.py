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
    "result,required,inline,report_present,success",
    [
        ("success", "false", "skipped", "false", True),
        ("success", "true", "success", "true", True),
        ("success", "true", "failure", "true", False),
        ("success", "true", "success", "false", False),
        ("success", "", "skipped", "false", False),
        ("failure", "false", "skipped", "false", False),
        ("cancelled", "false", "success", "false", False),
    ],
)
def test_publication_guard(result, required, inline, report_present, success):
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
            "INLINE_RESULT": inline,
            "REPORT_PRESENT": report_present,
        },
        check=False,
        capture_output=True,
    )
    assert (completed.returncode == 0) is success


def test_workflow_propagates_result_independently_and_publishes_missing_artifacts():
    data = yaml.safe_load((ROOT / ".github/workflows/quality-evidence.yml").read_text())
    steps = data["jobs"]["publish-evidence"]["steps"]
    materialize = next(s for s in steps if s.get("id") == "mutation-evidence-inline")
    publisher = next(
        s for s in steps if s.get("name") == "Publish trusted quality evidence"
    )
    assert materialize["if"] == "needs.mutation.outputs.required == 'true'"
    assert "REPORT_B64" not in materialize.get("env", {})
    assert "${{ needs.mutation.outputs.report-b64 }}" in materialize["run"]
    assert "B64_REPORT" in materialize["run"]
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

def test_mutation_policy_does_not_inject_large_base64_outputs_into_environment():
    data = yaml.safe_load((ROOT / ".github/workflows/mutation-policy.yml").read_text())
    steps = data["jobs"]["mutation-verify"]["steps"]
    materialize = next(
        s for s in steps if s.get("name") == "Materialize mutation evidence from job outputs"
    )
    env = materialize.get("env", {})
    assert "EVIDENCE_B64" not in env
    assert "RESULTS_B64" not in env
    assert "${{ needs.mutation-run.outputs.evidence-b64 }}" in materialize["run"]
    assert "${{ needs.mutation-run.outputs.results-b64 }}" in materialize["run"]
    assert "B64_EVIDENCE" in materialize["run"]
    assert "B64_RESULTS" in materialize["run"]


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



@pytest.mark.parametrize(
    "workflow",
    [
        ROOT / ".github/workflows/mutation-policy.yml",
        ROOT / "templates/forgejo/mutation-policy.yml",
    ],
)
def test_scoped_mutation_validator_imports_configparser(workflow):
    text = workflow.read_text()
    marker = "Validate mutation evidence for changed Python functions"
    scoped = text.split(marker, 1)[1]
    validator = scoped.split("PY", 1)[1]
    assert "import configparser" in validator



@pytest.mark.parametrize(
    "workflow",
    [
        ROOT / ".github/workflows/mutation-policy.yml",
        ROOT / "templates/forgejo/mutation-policy.yml",
    ],
)
def test_capture_preserves_trusted_runner_mutation_diagnostics(workflow):
    text = workflow.read_text()
    marker = "Capture mutmut diagnostics"
    capture = text.split(marker, 1)[1].split("- name:", 1)[0]
    assert '[[ -s .quality/mutmut-results.txt ]]' in capture
    assert "Preserving mutation diagnostics produced by the trusted runner" in capture
