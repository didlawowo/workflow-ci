"""Mutation jobs inherit the configured ARC runner without hosted fallbacks."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("filename", "job", "expression"),
    [
        (
            "mutation-policy.yml",
            "mutation-run",
            (
                "vars.UNTRUSTED_RUNNER || vars.RUNNER || "
                "format('arc-runner-{0}', github.event.repository.name)"
            ),
        ),
        (
            "mutation-policy.yml",
            "mutation-verify",
            (
                "inputs.trusted-runner || vars.RUNNER || "
                "format('arc-runner-{0}', github.event.repository.name)"
            ),
        ),
        (
            "mutation-issue-policy.yml",
            "notify",
            (
                "inputs.runner || vars.RUNNER || "
                "format('arc-runner-{0}', github.event.repository.name)"
            ),
        ),
        (
            "mutation-issue-policy.yml",
            "refresh-linked-prs",
            (
                "inputs.runner || vars.RUNNER || "
                "format('arc-runner-{0}', github.event.repository.name)"
            ),
        ),
    ],
)
def test_runner_precedence_and_self_hosted_fallback(filename, job, expression):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
    expected = "$" + "{{ " + expression + " }}"
    assert workflow["jobs"][job]["runs-on"] == expected


def test_runner_change_keeps_untrusted_execution_read_only():
    path = ROOT / ".github/workflows/mutation-policy.yml"
    content = path.read_text()
    workflow = yaml.safe_load(content)
    assert workflow["jobs"]["mutation-run"]["permissions"] == {
        "contents": "read",
        "issues": "read",
        "pull-requests": "read",
    }
    assert "env -i" in content
    assert "pull_request_target:" not in content
    assert "ubuntu-latest" not in content
