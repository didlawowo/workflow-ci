from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_python_actions_honor_nested_projects_and_explicit_evidence():
    setup = read(".github/actions/setup-python-env/action.yml")
    tests = read(".github/actions/run-python-tests/action.yml")
    quality = read(".github/actions/python-quality-security/action.yml")

    assert "Project directory containing pyproject.toml" in setup
    assert "working-directory: ${{ inputs.working-directory }}" in setup
    assert 'data.get("dependency-groups", {})' in setup
    assert "SYNC_ARGS+=(--group dev)" in setup
    assert "SYNC_ARGS+=(--extra dev)" in setup

    assert "junit-report-path:" in tests
    assert 'data.get("dependency-groups", {})' in tests
    assert "SYNC_ARGS+=(--group dev)" in tests
    assert "SYNC_ARGS+=(--extra dev)" in tests
    assert "uv run --with pytest --with pytest-cov pytest" in tests
    assert "coverage-report-path:" in tests
    assert "working-directory: ${{ inputs.working-directory }}" in tests
    assert 'echo "evidence-found=true"' in tests
    assert "Custom test-command succeeded but did not produce the required JUnit evidence" in tests

    assert "working-directory: ${{ inputs.working-directory }}" in quality
    assert "path: ${{ inputs.working-directory }}" in quality


def test_node_actions_honor_nested_projects_and_selected_package_manager():
    setup = read(".github/actions/setup-node-env/action.yml")
    tests = read(".github/actions/run-node-tests/action.yml")
    quality = read(".github/actions/node-quality-security/action.yml")

    assert "cache-dependency-path: ${{ steps.lockfile.outputs.path }}" in setup
    assert "working-directory: ${{ inputs.working-directory }}" in setup

    assert "working-directory:" in tests
    assert "Setup Node.js for nested project" in tests
    assert "Install nested project dependencies" in tests

    assert 'npm audit --audit-level="$LEVEL"' in quality
    assert 'pnpm audit --audit-level="$LEVEL"' in quality
    assert 'yarn npm audit --severity "$LEVEL"' in quality
    assert 'yarn audit --level "$LEVEL"' in quality
    assert "working-directory: ${{ inputs.working-directory }}" in quality


def test_templates_have_one_runner_source_of_truth():
    templates = [
        "templates/common/ai-review.yml",
        "templates/go/cd-production.yml",
        "templates/go/ci-branch-pipeline.yml",
        "templates/go/security-orchestrator.yml",
        "templates/node/ci-branch-pipeline.yml",
        "templates/python/cd-production.yml",
        "templates/python/ci-branch-pipeline.yml",
        "templates/python/security-orchestrator.yml",
    ]
    for path in templates:
        content = read(path)
        assert 'RUNNER: "ubuntu-latest"' not in content
        assert "vars.RUNNER || 'ubuntu-latest'" in content


def test_python_security_analysis_produces_the_output_used_by_issue_creation():
    content = read("templates/python/security-orchestrator.yml")

    assert 'echo "critical-issues=$CRITICAL"' in content
    assert 'echo "total-issues=$TOTAL"' in content
    assert "steps.analyze.outputs.critical-issues != '0'" in content
    assert "Create issue for critical findings" in content


def test_reusable_quality_workflow_routes_nested_projects_for_all_languages():
    workflow = read(".github/workflows/quality-evidence.yml")

    assert "python-test-command:" in workflow
    assert "python-junit-report-path:" in workflow
    assert "python-coverage-report-path:" in workflow
    assert "working-directory: ${{ inputs.working-directory }}" in workflow
    assert "artifact-suffix: ${{ inputs.repo-type }}" in workflow

    selftest = read(".github/workflows/consumer-integration-selftest.yml")
    for language in ("python", "go", "node"):
        assert f"{language}-consumer:" in selftest
        assert f"repo-type: {language}" in selftest
        assert f"tests/fixtures/consumers/{language}" in selftest

    assert "custom-junit.xml" in selftest
    assert "custom-coverage.xml" in selftest


def test_quality_reporter_does_not_depend_on_consumer_python_tooling():
    content = read(".github/actions/quality-report/action.yml")

    assert "Install uv for quality reporter" in content
    assert "uv python install 3.12" in content
    assert "uv run --no-project --python 3.12 python" in content
    assert "include-hidden-files: true" in content
    assert '--output-json "$GITHUB_WORKSPACE/.quality/quality-report.json"' in content
