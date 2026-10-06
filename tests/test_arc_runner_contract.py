from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_arc_runner_contract_targets_1_3_3():
    assert (ROOT / ".arc-runner-version").read_text().strip() == "v1.3.3"


def test_arc_runner_fast_path_tools_have_portable_fallbacks():
    docker = (ROOT / ".github/actions/docker-build-push/action.yml").read_text()
    python_quality = (
        ROOT / ".github/actions/python-quality-security/action.yml"
    ).read_text()

    assert "Trivy" not in docker
    assert "inputs.scan" not in docker
    assert "Detect preinstalled Cosign" in docker
    assert "sigstore/cosign-installer@v3" in docker
    assert "trufflehog.sh" in python_quality


def test_language_cache_selftests_require_standard_runner():
    workflows = (
        ".github/workflows/consumer-integration-selftest.yml",
        ".github/workflows/mutation-delegation-selftest.yml",
        ".github/workflows/quality-report-selftest.yml",
    )
    for workflow in workflows:
        source = (ROOT / workflow).read_text()
        assert "vars.LIGHT_RUNNER" not in source
        assert "arc-runner-workflow-ci" in source or "vars.RUNNER" in source

    release = (ROOT / ".github/workflows/release-main.yml").read_text()
    assert "runs-on: arc-runner-workflow-ci" in release
    assert "LIGHT_RUNNER" not in release

    bench = (ROOT / ".github/workflows/bench-uv-cache.yml").read_text()
    assert "runs-on: arc-runner-workflow-ci" in bench
    assert "LIGHT_RUNNER" not in bench
