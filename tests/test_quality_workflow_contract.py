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
    assert "git init -q .policy" in content
    assert "git init -q pr" in content
    assert "actions/checkout@" not in content
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


def test_language_templates_make_quality_failures_blocking():
    root = Path(__file__).resolve().parents[1]
    for language in ("python", "go", "node"):
        content = (root / "templates" / language / "ci-branch-pipeline.yml").read_text()
        quality_index = content.index("quality-security:")
        gate_index = content.index("- name: Enforce quality and security gate")

        assert gate_index > quality_index
        assert content.count("- name: Enforce quality and security gate") == 1
        assert (
            "if: always() && needs.tests.result == 'success' && "
            "needs.quality-security.result == 'success'"
        ) in content

    for language in ("python", "go"):
        content = (root / "templates" / language / "ci-branch-pipeline.yml").read_text()
        assert 'fail-on-coverage: "true"' in content


def test_mutation_verify_is_read_only_and_scoped_to_changed_functions():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    verify = content.split("  mutation-verify:", 1)[1]
    assert "issues: write" not in verify
    assert "pull-requests: write" not in verify
    assert "vars.UNTRUSTED_RUNNER || 'ubuntu-latest'" in verify
    assert "git\", \"-C\", str(repo), \"diff\", \"--unified=0\"" in verify
    assert "mutation gate failed for changed functions" in verify
    assert "scoped-mutation-evidence-" in verify
    assert "quality-report@main" not in verify


def test_python_security_action_propagates_requested_check_failures():
    root = Path(__file__).resolve().parents[1]
    content = (
        root / ".github" / "actions" / "python-quality-security" / "action.yml"
    ).read_text()

    assert "id: mypy" in content
    assert "id: trufflehog" in content
    assert "id: safety" in content
    assert "steps.mypy.outputs.status" in content
    assert "steps.trufflehog.outcome" in content
    assert "steps.safety.outputs.status" in content
    assert "SECURITY_ISSUES=$((SECURITY_ISSUES + 1))" in content


def test_mutation_policy_locks_engine_configuration_to_protected_base():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "Verify mutation configuration is unchanged" in content
    assert "Mutation-engine configuration differs from the protected base" in content
    assert 'result["pyproject.toml"] = mutmut' in content
    assert 'result["setup.cfg"] = dict(parser.items("mutmut"))' in content


def test_mutation_policy_rejects_changed_functions_without_mutants():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "source_paths is part of the protected policy" in content
    assert "in_trusted_source_path" in content
    assert "changed functions produced no mutation evidence" in content
    assert "pragma: no mutate" in content


def test_issue_59_mutation_bootstrap_is_uv_only_and_arc_portable():
    root = Path(__file__).resolve().parents[1]
    workflow = (
        root / ".github" / "workflows" / "mutation-policy.yml"
    ).read_text()
    runner = (root / ".ci" / "mutation.sh").read_text()

    assert "astral-sh/setup-uv@v3" in workflow
    assert "python3 -m venv" not in workflow
    assert "uv venv --python 3.12 --seed" in workflow
    assert "run: python " not in workflow
    assert "uv run --no-project --python 3.12 python" in workflow

    assert "python3 -m venv" not in runner
    assert "python -m pip" not in runner
    assert "uv venv --python" in runner
    assert "uv pip install --python" in runner


def test_issue_59_mutation_uses_exact_pr_base_and_head():
    root = Path(__file__).resolve().parents[1]
    content = (
        root / ".github" / "workflows" / "mutation-policy.yml"
    ).read_text()

    assert "MUTATION_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in content
    assert "MUTATION_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in content
    assert 'MUTATION_BASE_SHA="$MUTATION_BASE_SHA"' in content
    assert 'MUTATION_HEAD_SHA="$MUTATION_HEAD_SHA"' in content
    assert 'f"{base}..{head}"' in content
    assert 'f"{base}...{head}"' not in content


def test_issue_59_gitlink_safe_checkout_does_not_traverse_submodules():
    root = Path(__file__).resolve().parents[1]
    mutation = (
        root / ".github" / "workflows" / "mutation-policy.yml"
    ).read_text()
    evidence = (
        root / ".github" / "workflows" / "quality-evidence.yml"
    ).read_text()

    assert "actions/checkout@" not in mutation
    assert "git init -q .policy" in mutation
    assert "git init -q pr" in mutation
    assert "http.extraheader=AUTHORIZATION: basic" in mutation

    assert "Fetch caller repository without submodule traversal" in evidence
    assert "git init -q ." in evidence
    assert "http.extraheader=AUTHORIZATION: basic" in evidence


def test_issue_59_quality_report_provisions_python_with_uv():
    root = Path(__file__).resolve().parents[1]
    reporter = (
        root / ".github" / "actions" / "quality-report" / "action.yml"
    ).read_text()
    python_tests = (
        root / ".github" / "actions" / "run-python-tests" / "action.yml"
    ).read_text()

    assert "Setup uv for quality reporter" in reporter
    assert "astral-sh/setup-uv@v3" in reporter
    assert "uv run --no-project --python 3.12 python" in reporter

    assert "working-directory: ${{ inputs.working-directory }}" in python_tests
    assert (
        "uv run --no-project --python ${{ inputs.python-version }} python"
        in python_tests
    )
