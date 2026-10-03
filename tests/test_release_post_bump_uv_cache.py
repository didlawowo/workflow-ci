"""Exercise the release post-bump shell with real cache write probes."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def post_bump_step():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]["steps"]
    return next(step for step in steps if step.get("name") == "Post-bump command")


def run_post_bump(
    tmp_path,
    *,
    requested=None,
    runner_temp=None,
    exit_code=0,
    path_prefix=None,
):
    child = tmp_path / "child command.sh"
    child.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n%s\\n" "$UV_CACHE_DIR" "$NEW_VERSION" > "$RESULT_FILE"\n'
        'exit "$CHILD_EXIT_CODE"\n'
    )
    result_file = tmp_path / "child result"
    env = {
        "PATH": (
            str(path_prefix) + os.pathsep + os.environ["PATH"]
            if path_prefix is not None
            else os.environ["PATH"]
        ),
        "POST_BUMP_COMMAND": f"bash {shlex.quote(str(child))}",
        "NEW_VERSION": "v1.2.3",
        "RESULT_FILE": str(result_file),
        "CHILD_EXIT_CODE": str(exit_code),
    }
    if requested is not None:
        env["UV_CACHE_DIR"] = str(requested)
    if runner_temp is not None:
        env["RUNNER_TEMP"] = str(runner_temp)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", post_bump_step()["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, result_file


def assert_no_probes(cache):
    assert list(cache.glob(".workflow-ci-write-test.*")) == []


def test_writable_requested_cache_is_ignored_for_release(tmp_path):
    requested = tmp_path / "requested cache"
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, output = run_post_bump(
        tmp_path, requested=requested, runner_temp=runner_temp
    )

    fallback = runner_temp / "workflow-ci-uv-cache"
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(fallback), "v1.2.3"]
    assert not requested.exists()
    assert_no_probes(fallback)


def test_release_does_not_probe_shared_cache(tmp_path):
    requested = tmp_path / "requested cache"
    requested.mkdir()
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, output = run_post_bump(
        tmp_path,
        requested=requested,
        runner_temp=runner_temp,
    )

    fallback = runner_temp / "workflow-ci-uv-cache"
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(fallback), "v1.2.3"]
    assert_no_probes(requested)
    assert_no_probes(fallback)

def test_unusable_requested_cache_falls_back(tmp_path):
    requested = tmp_path / "not a directory"
    requested.write_text("occupied")
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, output = run_post_bump(
        tmp_path, requested=requested, runner_temp=runner_temp
    )

    fallback = runner_temp / "workflow-ci-uv-cache"
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(fallback), "v1.2.3"]
    assert_no_probes(fallback)


def test_read_only_requested_cache_falls_back_for_non_root(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can write despite chmod; ENOTDIR is covered separately")
    requested = tmp_path / "read only cache"
    requested.mkdir()
    requested.chmod(0o500)
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    try:
        result, output = run_post_bump(
            tmp_path, requested=requested, runner_temp=runner_temp
        )
    finally:
        requested.chmod(0o700)

    fallback = runner_temp / "workflow-ci-uv-cache"
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(fallback), "v1.2.3"]
    assert_no_probes(requested)
    assert_no_probes(fallback)


def test_unset_cache_uses_runner_temp(tmp_path):
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, output = run_post_bump(tmp_path, runner_temp=runner_temp)

    fallback = runner_temp / "workflow-ci-uv-cache"
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(fallback), "v1.2.3"]
    assert_no_probes(fallback)


def test_unwritable_fallback_prevents_child_execution(tmp_path):
    runner_temp = tmp_path / "runner temp"
    runner_temp.write_text("not a directory")

    result, output = run_post_bump(tmp_path, runner_temp=runner_temp)

    assert result.returncode != 0
    assert not output.exists()


def test_child_failure_code_is_propagated(tmp_path):
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, output = run_post_bump(tmp_path, runner_temp=runner_temp, exit_code=37)

    assert result.returncode == 37
    assert output.read_text().splitlines() == [str(runner_temp / "workflow-ci-uv-cache"), "v1.2.3"]
    assert_no_probes(runner_temp / "workflow-ci-uv-cache")


def test_existing_release_guard_and_local_scope_are_unchanged():
    step = post_bump_step()
    assert step["if"] == (
        "steps.recovery.outputs.pending != 'true' && "
        "steps.bump.outputs.released == 'true' && "
        "inputs.post-bump-command != ''"
    )
    assert step["env"]["NEW_VERSION"] == "${{ steps.bump.outputs.version }}"
    assert "GITHUB_ENV" not in step["run"]
    assert "HOME=" not in step["run"]
    assert 'requested="${UV_CACHE_DIR:-}"' not in step["run"]
    assert "timeout 3 bash -c" not in step["run"]
    assert 'cache="${RUNNER_TEMP:-/tmp}/workflow-ci-uv-cache"' in step["run"]
