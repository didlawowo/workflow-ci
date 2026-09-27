"""Regression coverage for Node gates and release/mutation wiring."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text())


@pytest.mark.parametrize(
    "threshold,coverage,passes",
    [
        ("80", "79.9", False),
        ("80", "80", True),
        ("80", "95", True),
        ("80", "", False),
        ("80", "NaN", False),
        ("80", "101", False),
        ("80", "-1", False),
        ("80", "Infinity", False),
        ("0", "", True),
        ("", "90", False),
        ("wrong", "90", False),
        ("101", "100", False),
        ("-1", "90", False),
    ],
)
def test_node_coverage_gate_executes_real_action_shell(threshold, coverage, passes):
    action = read_yaml(".github/actions/run-node-tests/action.yml")
    step = next(
        s
        for s in action["runs"]["steps"]
        if s.get("name") == "Check coverage threshold"
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        env={
            **os.environ,
            "COVERAGE_THRESHOLD": threshold,
            "COVERAGE_PERCENTAGE": coverage,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) == passes, result.stdout + result.stderr
    if not passes:
        assert "::error::" in result.stderr


@pytest.mark.parametrize("name", ["ci.yml", "quality-evidence.yml"])
def test_node_threshold_reaches_action(name):
    workflow = read_yaml(f".github/workflows/{name}")
    steps = [s for job in workflow["jobs"].values() for s in job.get("steps", [])]
    node = next(s for s in steps if s.get("id") == "node-tests")
    assert node["with"]["coverage-threshold"] == "${{ steps.threshold.outputs.value }}"


def test_ci_mutation_uses_selected_project_directory():
    job = read_yaml(".github/workflows/ci.yml")["jobs"]["mutation"]
    assert job["with"]["working-directory"] == "${{ inputs.working-directory }}"


def test_release_requires_validation_of_the_same_commit():
    jobs = read_yaml(".github/workflows/release-main.yml")["jobs"]
    assert jobs["release"]["needs"] == "validate"
    assert jobs["validate"]["uses"] == "./.github/workflows/quality-report-selftest.yml"
    assert jobs["release"]["with"]["validated-sha"] == "${{ github.sha }}"
    assert jobs["release"]["with"]["workflow-ci-ref"] == "${{ github.sha }}"
    release = read_yaml(".github/workflows/release.yml")
    checkout = release["jobs"]["release"]["steps"][0]
    assert checkout["with"]["ref"] == "${{ inputs.validated-sha || github.ref }}"
    selftest = read_yaml(".github/workflows/quality-report-selftest.yml")
    # PyYAML's YAML 1.1 loader reads the key 'on' as True.
    events = selftest[True]
    assert "workflow_call" in events
    assert "push" not in events
