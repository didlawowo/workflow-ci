"""Exercise the explicit runner storage profile (NFS default, local opt-in)."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".ci/nfs-cache.sh"
LOCAL_VARIABLES = (
    "UV_CACHE_DIR",
    "GOCACHE",
    "GOMODCACHE",
    "NPM_CONFIG_CACHE",
    "UV_PROJECT_ENVIRONMENT",
    "TMPDIR",
    "UV_PYTHON_INSTALL_DIR",
)
REMOVED = tuple(LOCAL_VARIABLES) + ("UV_LINK_MODE", "WORKFLOW_LOCAL_ROOT")


def base_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    # Never let subprocess tests append cache variables to the real GitHub job
    # environment file; that would leak temporary pytest paths into later steps.
    env.pop("GITHUB_ENV", None)
    env.pop("GITHUB_OUTPUT", None)
    return bin_dir, env


def mock_findmnt(bin_dir, fstype):
    findmnt = bin_dir / "findmnt"
    findmnt.write_text(f'#!/bin/sh\nprintf "%s\\n" "{fstype}"\n')
    findmnt.chmod(0o700)


def run_helper(
    tmp_path,
    *,
    profile=None,
    variable="UV_CACHE_DIR",
    fstype="ext4",
    root="dir",
    tail="",
):
    bin_dir, env = base_env(tmp_path)
    mock_findmnt(bin_dir, fstype)

    local_root = tmp_path / "local"
    if root == "dir":
        local_root.mkdir()
    elif root == "symlink":
        (tmp_path / "real-root").mkdir()
        local_root.symlink_to(tmp_path / "real-root")

    for name in REMOVED:
        env.pop(name, None)
    if profile is not None:
        env["WORKFLOW_CACHE_PROFILE"] = profile
    if root != "absent":
        env["WORKFLOW_LOCAL_ROOT"] = str(local_root)

    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            f'source "{HELPER}"; require_nfs_cache {variable}; printf "%s" "${{{variable}}}"{tail}',
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result, local_root


def test_local_profile_isolates_every_cache_under_the_job_root(tmp_path):
    printer = '; printf "\\n"; ' + "; ".join(
        f'printf "%s=%s\\n" {name} "${{{name}}}"' for name in LOCAL_VARIABLES
    )
    result, local_root = run_helper(tmp_path, profile="local", tail=printer)
    assert result.returncode == 0, result.stderr
    first_line, rest = result.stdout.split("\n", 1)
    values = dict(line.split("=", 1) for line in rest.strip().splitlines())

    observed = Path(first_line)
    job_root = observed.parent
    assert job_root.name.startswith("job.")
    assert job_root.parent == local_root
    for name in LOCAL_VARIABLES:
        assert Path(values[name]).is_relative_to(local_root), (name, values[name])
        assert Path(values[name]).stat().st_mode & 0o777 == 0o700
    # No probe file is left behind in the job root.
    assert not list(job_root.rglob(".workflow-ci-write-test.*"))


def test_local_profile_rejects_nfs_backed_root(tmp_path):
    result, _ = run_helper(tmp_path, profile="local", fstype="nfs4")
    assert result.returncode != 0
    assert "::error::" in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("root", ["absent", "symlink"])
def test_local_profile_rejects_missing_or_indirect_root(tmp_path, root):
    result, _ = run_helper(tmp_path, profile="local", root=root)
    assert result.returncode != 0
    assert "::error::" in result.stderr
    assert not result.stdout


def test_unknown_profile_fails_explicitly(tmp_path):
    result, _ = run_helper(tmp_path, profile="tmpfs")
    assert result.returncode != 0
    assert "Unknown WORKFLOW_CACHE_PROFILE" in result.stderr
    assert not result.stdout


def test_nfs_default_refuses_a_local_filesystem(tmp_path):
    bin_dir, env = base_env(tmp_path)
    mock_findmnt(bin_dir, "ext4")
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in REMOVED:
        env.pop(name, None)
    env.pop("WORKFLOW_CACHE_PROFILE", None)
    env["UV_CACHE_DIR"] = str(cache)
    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            f'source "{HELPER}"; require_nfs_cache UV_CACHE_DIR; printf "%s" "$UV_CACHE_DIR"',
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "::error::" in result.stderr
    assert not result.stdout


def test_nfs_profile_still_accepts_runner_provided_cache(tmp_path):
    bin_dir, env = base_env(tmp_path)
    mock_findmnt(bin_dir, "nfs4")
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in REMOVED:
        env.pop(name, None)
    env["WORKFLOW_CACHE_PROFILE"] = "nfs"
    env["UV_CACHE_DIR"] = str(cache)
    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            f'source "{HELPER}"; require_nfs_cache UV_CACHE_DIR; printf "%s" "$UV_CACHE_DIR"',
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(cache)
    assert not list(cache.iterdir())


def test_composite_actions_declare_the_profile_contract():
    node = (ROOT / ".github/actions/setup-node-env/action.yml").read_text()
    assert "require_runner_cache NPM_CONFIG_CACHE" in node
    assert "env.WORKFLOW_CACHE_PROFILE == 'local'" in node
    for action in ("setup-python-env", "setup-go-env"):
        content = (ROOT / f".github/actions/{action}/action.yml").read_text()
        assert "nfs-cache.sh" in content
        assert "require_nfs_cache" in content or "require_runner_cache" in content


def test_python_setup_retries_proxpi_failures_on_public_pypi():
    content = (ROOT / ".github/actions/setup-python-env/action.yml").read_text()
    assert 'case "$CURRENT_INDEX" in' in content
    assert '*proxpi*)' in content
    assert 'retrying once with public PyPI' in content
    assert 'UV_DEFAULT_INDEX="https://pypi.org/simple"' in content
    assert 'uv sync "${SYNC_ARGS[@]}"' in content
