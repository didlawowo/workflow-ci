"""Execute the actual NFS validator with controlled mount and fault responses."""

import os
import subprocess
from pathlib import Path

import pytest

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


def test_mutation_uses_runner_local_scratch_not_shared_dependency_caches():
    content = (ROOT / ".github/workflows/mutation-policy.yml").read_text()
    assert 'ISOLATED_ROOT="${RUNNER_TEMP:-/tmp}/mutation-scratch"' in content
    assert 'mktemp -d "$ISOLATED_ROOT/uv.XXXXXX"' in content
    assert 'mktemp -d "$ISOLATED_ROOT/go-build.XXXXXX"' in content
    assert 'mktemp -d "$ISOLATED_ROOT/go-mod.XXXXXX"' in content
    assert 'mktemp -d "$UV_CACHE_DIR/mutation.XXXXXX"' not in content
    assert 'mktemp -d "$GOCACHE/mutation.XXXXXX"' not in content
    assert 'mktemp -d "$GOMODCACHE/mutation.XXXXXX"' not in content


def test_mutation_pr_code_receives_only_isolated_dependency_cache_paths():
    content = (ROOT / ".github/workflows/mutation-policy.yml").read_text()
    step = content.split("- name: Run trusted mutation runner against PR code", 1)[1]
    step = step.split("- name: Capture mutmut diagnostics", 1)[0]
    assert "env -i" in step
    assert 'UV_CACHE_DIR="$ISOLATED_UV_CACHE"' in step
    assert 'GOCACHE="$ISOLATED_GO_CACHE"' in step
    assert 'GOMODCACHE="$ISOLATED_GO_MODCACHE"' in step
    assert 'source "$GITHUB_WORKSPACE/.workflow-ci/.ci/nfs-cache.sh"' not in step
    assert "require_nfs_cache" not in step
