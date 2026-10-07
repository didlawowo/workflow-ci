"""Contracts for the Forgejo V1 entry points and their runner cache policy."""

from pathlib import Path

import pytest
import yaml
from test_uv_cache_resolver import run_validator

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / f".forgejo/workflows/{name}.yaml").read_text())


def test_python_checks_nfs_before_installing_or_running_uv():
    steps = workflow("python-postgres")["jobs"]["tests"]["steps"]
    validation = next(s for s in steps if s.get("name") == "Validate runner NFS cache")
    canonical = (ROOT / ".ci/nfs-cache.sh").read_text().splitlines()[1:]
    assert (
        validation["run"]
        == "\n".join(canonical + ["require_nfs_cache UV_CACHE_DIR"]) + "\n"
    )
    assert steps.index(validation) < next(
        i for i, s in enumerate(steps) if s.get("name") == "Install uv"
    )
    assert "persist-credentials" in steps[0]["with"]
    assert steps[0]["with"]["persist-credentials"] is False


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"filesystem": "ext4"},
        {"configured": False},
        {"failure": True},
        {"blocked": True},
    ],
)
def test_forgejo_nfs_guard_executes_actual_yaml(tmp_path, options):
    steps = workflow("python-postgres")["jobs"]["tests"]["steps"]
    command = next(
        s["run"] for s in steps if s.get("name") == "Validate runner NFS cache"
    )
    result, cache = run_validator(
        tmp_path, command=command + 'printf "%s" "$UV_CACHE_DIR"', **options
    )
    assert (result.returncode == 0) == (not options), result.stderr
    if not options:
        assert result.stdout == str(cache)
    else:
        assert "::error::" in result.stderr


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
