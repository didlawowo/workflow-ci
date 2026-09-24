"""Structural contract for the additive single-execution CI workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")


def section(name: str, next_name: str | None = None) -> str:
    start = TEXT.split(f"  {name}:\n", 1)[1]
    if next_name:
        return start.split(f"  {next_name}:\n", 1)[0]
    return start


def test_tests_execute_once_per_language_and_compare_protected_main_baseline():
    tests = section("tests", "quality")
    assert tests.count("uses: $/.github/actions/run-python-tests") == 1
    assert tests.count("uses: $/.github/actions/run-go-tests") == 1
    assert tests.count("uses: $/.github/actions/run-node-tests") == 1
    assert "Compare with committed main coverage" in tests
    assert "base-sha: ${{ github.event.pull_request.base.sha }}" in tests
    assert "coverage-baseline" in tests


def test_quality_mutation_and_docker_are_parallel_to_tests():
    quality = section("quality", "mutation")
    mutation = section("mutation", "docker")
    docker = section("docker", "commit-main-coverage")

    assert "\n    needs:" not in quality
    assert "\n    needs:" not in mutation
    assert "\n    needs:" not in docker
    assert "uses: $/.github/workflows/mutation-policy.yml" in mutation
    assert "uses: $/.github/actions/docker-build-push" in docker


def test_main_coverage_is_committed_after_successful_merge_run():
    baseline = section("commit-main-coverage", "summary")
    assert "github.event_name == 'push'" in baseline
    assert "github.ref_name == 'main'" in baseline
    assert "needs: [tests]" in baseline
    assert "contents: write" in baseline
    assert "mode: write" in baseline
    assert 'git commit -m "chore(ci): update main coverage baseline [skip ci]"' in baseline
    assert "git push origin HEAD:main" in baseline


def test_old_trusted_workflow_remains_available_during_migration():
    legacy = ROOT / ".github" / "workflows" / "quality-evidence.yml"
    assert legacy.is_file()
    legacy_text = legacy.read_text(encoding="utf-8")
    assert "Independent quality execution" in legacy_text
    assert "mutation-policy.yml" in legacy_text
