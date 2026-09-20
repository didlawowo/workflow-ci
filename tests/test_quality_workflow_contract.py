from pathlib import Path

WORKFLOW = Path(".github/workflows/quality-evidence.yml")


def test_quality_evidence_is_reusable_and_not_recursive():
    content = WORKFLOW.read_text()

    assert "workflow_call:" in content
    assert "pull_request:" not in content
    assert "uses: didlawowo/workflow-ci/.github/workflows/quality-evidence.yml@" not in content


def test_quality_evidence_requires_explicit_runner_and_pinned_actions():
    content = WORKFLOW.read_text()

    assert "runner:" in content
    assert "runs-on: ${{ inputs.runner }}" in content
    assert "workflow-ci-ref:" in content
    assert 'default: "v1.7.1"' in content
    assert "repository: didlawowo/workflow-ci" in content
    assert "ref: ${{ inputs.workflow-ci-ref }}" in content
    assert "ubuntu-latest" not in content
    assert "@main" not in content
