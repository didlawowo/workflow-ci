from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_arc_runner_contract_targets_1_3_3():
    assert (ROOT / ".arc-runner-version").read_text().strip() == "v1.3.3"


def test_arc_runner_fast_path_tools_have_portable_fallbacks():
    docker = (ROOT / ".github/actions/docker-build-push/action.yml").read_text()
    python_quality = (
        ROOT / ".github/actions/python-quality-security/action.yml"
    ).read_text()

    assert "Detect preinstalled Trivy" in docker
    assert "aquasecurity/trivy-action@v0.36.0" in docker
    assert "Detect preinstalled Cosign" in docker
    assert "sigstore/cosign-installer@v3" in docker
    assert "trufflehog.sh" in python_quality
