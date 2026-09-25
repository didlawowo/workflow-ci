from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / ".ci" / "mutation-node.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "node-mutation-evidence.yml"


def test_node_mutation_runner_is_diff_scoped_and_depth_aware() -> None:
    script = RUNNER.read_text(encoding="utf-8")

    assert 'DEPTH="${MUTATION_DEPTH:-medium}"' in script
    assert 'medium|high' in script
    assert 'git diff --name-only --diff-filter=ACMR "$BASE_SHA...$HEAD_SHA"' in script
    assert "grep -E '\\.(js|jsx|ts|tsx)$'" in script
    assert "grep -Ev" in script and ".(test|spec)" in script
    assert 'if [[ "$DEPTH" == "high" ]]' in script
    assert 'mutate_entries+=("$project_file")' in script
    assert 'mutate_entries+=("$project_file:$start-$((start + count - 1))")' in script


def test_node_mutation_runner_pins_stryker_and_emits_normalized_evidence() -> None:
    script = RUNNER.read_text(encoding="utf-8")

    assert '"@stryker-mutator/core": "9.6.1"' in script
    assert '"typescript": "5.6.3"' in script
    assert "Path('.quality/stryker.json')" in script
    assert "'engine': {'name': 'stryker', 'version': '9.6.1'}" in script
    assert "'killed': killed" in script
    assert "'survived': survived" in script
    assert "'timeouts': timeouts" in script
    assert "'not_covered': not_covered" in script
    assert "Node mutation gate failed" in script


def test_node_mutation_workflow_keeps_execution_read_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert "permissions:" in workflow
    assert "contents: read" in workflow
    assert "pull-requests: read" in workflow
    assert "pull-requests: write" not in workflow
    assert "persist-credentials: false" in workflow
    assert "MUTATION_DEPTH: ${{ steps.policy.outputs.depth }}" in workflow
    assert "bash .workflow-ci/.ci/mutation-node.sh" in workflow


def test_node_mutation_workflow_is_parallel_consumer_capability() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "working-directory:" in workflow
    assert "package-manager:" in workflow
    assert "test-command:" in workflow
    assert "node-production-change" in workflow
    assert "value: ${{ jobs.node-mutation.outputs.status }}" in workflow


def test_node_mutation_diagnostic_artifact_is_best_effort() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    block = workflow.split("- name: Upload Node mutation diagnostics", 1)[1].split(
        "- name: Enforce Node mutation verdict", 1
    )[0]

    assert "continue-on-error: true" in block
    assert "actions/upload-artifact@v6" in block
    assert "if-no-files-found: ignore" in block
