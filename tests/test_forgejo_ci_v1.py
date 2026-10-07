"""Contracts for the Forgejo V1 entry points and their runner cache policy."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / f".forgejo/workflows/{name}.yaml").read_text())


def test_python_inherits_runner_package_proxy_without_nfs_cache_gate():
    steps = workflow("python-postgres")["jobs"]["tests"]["steps"]
    names = [step.get("name") for step in steps]
    assert "Validate runner NFS cache" not in names
    install_uv = next(i for i, step in enumerate(steps) if step.get("name") == "Install uv")
    install_deps = next(
        i for i, step in enumerate(steps) if step.get("name") == "Install dependencies"
    )
    assert install_uv < install_deps
    assert "persist-credentials" in steps[0]["with"]
    assert steps[0]["with"]["persist-credentials"] is False
    text = (ROOT / ".forgejo/workflows/python-postgres.yaml").read_text()
    assert "WORKFLOW_CACHE_PROFILE" not in text
    assert "require_nfs_cache" not in text


def test_postgres_service_and_disposable_database_contract():
    job = workflow("python-postgres")["jobs"]["tests"]
    service = job["services"]["postgres"]
    assert service["image"] == "postgres:16-alpine"
    assert "pg_isready" in service["options"]
    tests = next(s for s in job["steps"] if s.get("name") == "Run tests with coverage")
    assert tests["env"]["TEST_DATABASE_URL"].endswith("@postgres:5432/ci_test")
    assert tests["env"]["DATABASE_URL"] == ""
    assert "--cov" in tests["run"]


def test_node_build_retains_lockfile_and_optional_script_behavior():
    steps = workflow("node-build")["jobs"]["build"]["steps"]
    install = next(s for s in steps if s.get("name") == "Install dependencies")
    assert "npm ci" in install["run"] and "npm install" in install["run"]
    verify = next(s for s in steps if s.get("name") == "Verify built output")
    assert verify["if"] == "inputs.build-test-script != ''"
    assert verify["run"] == 'npm run "$BUILD_TEST_SCRIPT"'


def test_template_pins_published_v1_and_keeps_taskfile_gate():
    template = yaml.safe_load(
        (ROOT / "templates/forgejo/moto-tracker-ci.yaml").read_text()
    )
    jobs = template["jobs"]
    for name in ("taskfiles", "backend-tests", "pwa-build"):
        assert jobs[name]["uses"].endswith("@v1.16.0")
        assert "runs-on" not in jobs[name]
    for name in ("backend-tests", "pwa-build"):
        assert jobs[name]["needs"] == ["taskfiles"]
    for name in ("taskfiles", "python-postgres", "node-build"):
        job = next(iter(workflow(name)["jobs"].values()))
        assert job["runs-on"] == "${{ inputs.runner }}"
