from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "quality-evidence.yml"


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
    assert 'default: "v1.7.0"' in content
    assert "repository: didlawowo/workflow-ci" in content
    assert "ref: ${{ inputs.workflow-ci-ref }}" in content
    assert "ubuntu-latest" not in content
    assert "@main" not in content


def test_quality_evidence_dependency_chain_has_no_workflow_ci_main_refs():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / ".github" / "actions" / "run-python-tests" / "action.yml",
        root / ".github" / "actions" / "run-go-tests" / "action.yml",
        root / ".github" / "actions" / "run-node-tests" / "action.yml",
    ]
    for path in paths:
        content = path.read_text()
        assert "didlawowo/workflow-ci/" in content
        assert "@main" not in content
        assert "@v1.7.0" in content


def test_mutation_policy_separates_untrusted_execution_from_trusted_verification():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "pull_request_target:" not in content
    assert "pull_request:" in content
    assert "path: .policy" in content
    assert "path: pr" in content
    assert "vars.UNTRUSTED_RUNNER || 'ubuntu-latest'" in content
    assert 'bash "$GITHUB_WORKSPACE/.policy/.ci/mutation.sh"' in content
    assert "needs: [mutation-run]" in content
    assert "actions/download-artifact@v6" in content


def test_mutation_policy_requires_machine_readable_evidence_and_zero_survivors():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "Mandatory mutation run produced no supported engine-native evidence" in content
    assert "mutation evidence is missing killed/survived counters" in content
    assert "mutation evidence contains no measured mutants" in content
    assert "if survived or timeouts or suspicious:" in content
