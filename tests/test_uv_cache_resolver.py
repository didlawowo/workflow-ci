"""Execute the actual NFS validator with controlled mount and fault responses."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def run_validator(
    tmp_path,
    *,
    filesystem="nfs4",
    configured=True,
    failure=False,
    variable="UV_CACHE_DIR",
    blocked=False,
    command=None,
):
    cache = tmp_path / "NFS cache"
    if blocked:
        cache.write_text("occupied")
    else:
        cache.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    findmnt = bin_dir / "findmnt"
    findmnt.write_text(f"#!/bin/sh\nprintf '%s\\n' {filesystem}\n")
    findmnt.chmod(0o700)
    if failure:
        timeout = bin_dir / "timeout"
        timeout.write_text("#!/bin/sh\necho 'Remote I/O error' >&2\nexit 74\n")
        timeout.chmod(0o700)
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    env.pop(variable, None)
    if configured:
        env[variable] = str(cache)
    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            command
            or f'source "{ROOT}/.ci/nfs-cache.sh"; require_nfs_cache {variable}; printf "%s" "${variable}"',
        ],
        env=env,
        text=True,
        check=False,
        capture_output=True,
        timeout=10,
    )
    return result, cache


@pytest.mark.parametrize("variable", ["UV_CACHE_DIR", "GOCACHE", "GOMODCACHE"])
@pytest.mark.parametrize("filesystem", ["nfs", "nfs4"])
def test_nfs_cache_is_preserved_and_probes_cleaned(tmp_path, filesystem, variable):
    result, cache = run_validator(tmp_path, filesystem=filesystem, variable=variable)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(cache)
    if cache.is_dir():
        assert not list(cache.iterdir())


@pytest.mark.parametrize("variable", ["UV_CACHE_DIR", "GOCACHE", "GOMODCACHE"])
@pytest.mark.parametrize(
    "options",
    [
        {"filesystem": "ext4"},
        {"configured": False},
        {"failure": True},
        {"blocked": True},
    ],
)
def test_invalid_cache_stops_without_fallback(tmp_path, options, variable):
    result, cache = run_validator(tmp_path, variable=variable, **options)
    assert result.returncode != 0
    assert "::error::" in result.stderr
    assert not result.stdout
    if cache.is_dir():
        assert not list(cache.iterdir())


def test_local_profile_accepts_local_cache_and_rejects_nfs(tmp_path):
    command = f'source "{ROOT}/.ci/nfs-cache.sh"; require_runner_cache UV_CACHE_DIR; printf "%s" "$UV_CACHE_DIR"'
    result, cache = run_validator(tmp_path, filesystem="ext4", command=command)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", command],
        env={**os.environ, "UV_CACHE_DIR": str(cache), "WORKFLOW_CI_CACHE_PROFILE": "local", "PATH": os.environ["PATH"]},
        text=True, capture_output=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(cache)

def test_invalid_cache_profile_is_rejected(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", f'source "{ROOT}/.ci/nfs-cache.sh"; require_runner_cache UV_CACHE_DIR'],
        env={**os.environ, "UV_CACHE_DIR": str(cache), "WORKFLOW_CI_CACHE_PROFILE": "bogus"},
        text=True, capture_output=True, check=False, timeout=10,
    )
    assert result.returncode != 0
    assert "WORKFLOW_CI_CACHE_PROFILE" in result.stderr

def test_mutation_cache_namespaces_remain_on_nfs():
    content = (ROOT / ".github/workflows/mutation-policy.yml").read_text()
    for variable in ("UV_CACHE_DIR", "GOCACHE", "GOMODCACHE"):
        assert f"require_nfs_cache {variable}" in content
        assert f'mktemp -d "${variable}/mutation.XXXXXX"' in content
    assert "${RUNNER_TEMP:-/tmp}/mutation-uv-cache" not in content


def test_mutation_loads_trusted_cache_helper_from_pr_directory(tmp_path):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/mutation-policy.yml").read_text()
    )
    step = next(
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Run trusted mutation runner against PR code"
    )
    workspace = tmp_path / "workspace with spaces"
    trusted = workspace / ".workflow-ci/.ci"
    trusted.mkdir(parents=True)
    (trusted / "nfs-cache.sh").write_text('printf "trusted-helper"\n')
    pr = workspace / step["working-directory"]
    untrusted = pr / ".workflow-ci/.ci"
    untrusted.mkdir(parents=True)
    (untrusted / "nfs-cache.sh").write_text("exit 99\n")
    command = next(
        line.strip()
        for line in step["run"].splitlines()
        if line.strip().startswith("source ")
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", command],
        cwd=pr,
        env={**os.environ, "GITHUB_WORKSPACE": str(workspace)},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "trusted-helper"
