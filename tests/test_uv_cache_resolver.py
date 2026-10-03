"""Regression tests for fail-safe uv cache selection on ARC/NFS runners."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

ACTIONS = [
    (".github/actions/run-python-tests/action.yml", "Resolve writable uv cache"),
    (".github/actions/setup-python-env/action.yml", "Resolve writable uv cache"),
    (
        ".github/actions/quality-report/action.yml",
        "Resolve writable uv cache for quality reporter",
    ),
]


def _resolver_command(path: str, step_name: str) -> str:
    action = yaml.safe_load((ROOT / path).read_text())
    step = next(step for step in action["runs"]["steps"] if step["name"] == step_name)
    return step["run"]


def _run_resolver(tmp_path: Path, command: str, *, fake_timeout: bool):
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    requested = tmp_path / "shared-uv-cache"
    requested.mkdir()
    github_env = tmp_path / "github-env"
    github_output = tmp_path / "github-output"

    path = os.environ["PATH"]
    if fake_timeout:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        timeout = bin_dir / "timeout"
        timeout.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'count_file="$RUNNER_TEMP/timeout-calls"\n'
            'n="$(cat "$count_file" 2>/dev/null || echo 0)"\n'
            'n=$((n + 1)); printf "%s\\n" "$n" > "$count_file"\n'
            'if [[ "$n" -eq 1 ]]; then\n'
            "  shift\n"
            '  "$@"\n'
            "  exit $?\n"
            "fi\n"
            'echo "mkdir: cannot stat '"'"'$UV_CACHE_DIR'"'"': Remote I/O error" >&2\n'
            "exit 74\n"
        )
        timeout.chmod(0o700)
        path = str(bin_dir) + os.pathsep + path

    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", command],
        env={
            **os.environ,
            "PATH": path,
            "RUNNER_TEMP": str(runner_temp),
            "UV_CACHE_DIR": str(requested),
            "GITHUB_ENV": str(github_env),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, requested, runner_temp, github_env, github_output


@pytest.mark.parametrize(("path", "step_name"), ACTIONS)
def test_shared_uv_cache_is_used_when_stable(tmp_path, path, step_name):
    result, requested, runner_temp, github_env, github_output = _run_resolver(
        tmp_path, _resolver_command(path, step_name), fake_timeout=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"UV_CACHE_DIR={requested}" in github_env.read_text()
    assert f"path={requested}" in github_output.read_text()
    assert "Using shared uv cache" in result.stdout
    assert not (runner_temp / "uv-cache").exists()
    assert not list(requested.glob(".workflow-ci-write-test.*"))


@pytest.mark.parametrize(("path", "step_name"), ACTIONS)
def test_nfs_failure_between_probe_and_commit_falls_back_locally(
    tmp_path, path, step_name
):
    result, requested, runner_temp, github_env, github_output = _run_resolver(
        tmp_path, _resolver_command(path, step_name), fake_timeout=True
    )

    fallback = runner_temp / "uv-cache"
    assert result.returncode == 0, result.stdout + result.stderr
    assert (runner_temp / "timeout-calls").read_text().strip() == "2"
    assert f"UV_CACHE_DIR={fallback}" in github_env.read_text()
    assert f"path={fallback}" in github_output.read_text()
    assert "became unavailable during commit probe" in result.stdout
    assert "Remote I/O error" in result.stdout
    assert fallback.is_dir()
    assert not list(requested.glob(".workflow-ci-write-test.*"))
    assert not list(fallback.glob(".workflow-ci-write-test.*"))
