"""Exercise the release post-bump shell with real cache write probes."""

import os
import shlex
import subprocess
from pathlib import Path

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
    bin_dir = tmp_path / "mount-bin"
    bin_dir.mkdir()
    findmnt = bin_dir / "findmnt"
    findmnt.write_text("#!/bin/sh\necho nfs4\n")
    findmnt.chmod(0o700)
    env = {
        "PATH": (
            str(path_prefix) + os.pathsep + os.environ["PATH"]
            if path_prefix is not None
            else str(bin_dir) + os.pathsep + os.environ["PATH"]
        ),
        "POST_BUMP_COMMAND": f"bash {shlex.quote(str(child))}",
        "NEW_VERSION": "v1.2.3",
        "RESULT_FILE": str(result_file),
        "CHILD_EXIT_CODE": str(exit_code),
    }
    if requested is not None:
        env["UV_CACHE_DIR"] = str(requested)
        if not requested.exists():
            requested.mkdir()
    for variable in ("GOCACHE", "GOMODCACHE"):
        directory = tmp_path / variable
        directory.mkdir()
        env[variable] = str(directory)
    if runner_temp is not None:
        env["RUNNER_TEMP"] = str(runner_temp)
    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            post_bump_step()["run"].replace(
                ".workflow-ci/.ci/nfs-cache.sh",
                str(WORKFLOW.parents[2] / ".ci/nfs-cache.sh"),
            ),
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, result_file


def assert_no_probes(cache):
    assert list(cache.glob(".workflow-ci-write-test.*")) == []


def test_writable_requested_cache_is_preserved_with_spaces(tmp_path):
    requested = tmp_path / "requested cache"
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, output = run_post_bump(
        tmp_path, requested=requested, runner_temp=runner_temp
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(requested), "v1.2.3"]
    assert_no_probes(requested)
    assert not (runner_temp / "uv-cache").exists()


def test_requested_cache_probe_failure_falls_back_under_errexit(tmp_path):
    requested = tmp_path / "requested cache"
    requested.mkdir()
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    timeout = bin_dir / "timeout"
    timeout.write_text("#!/usr/bin/env bash\nexit 74\n")
    timeout.chmod(0o700)

    result, output = run_post_bump(
        tmp_path,
        requested=requested,
        runner_temp=runner_temp,
        path_prefix=bin_dir,
    )

    assert result.returncode != 0
    assert not output.exists()
    assert not (runner_temp / "uv-cache").exists()


def test_unset_cache_blocks_child(tmp_path):
    result, output = run_post_bump(tmp_path)
    assert result.returncode != 0
    assert not output.exists()


def test_unusable_cache_blocks_child(tmp_path):
    requested = tmp_path / "file"
    requested.write_text("occupied")
    result, output = run_post_bump(tmp_path, requested=requested)
    assert result.returncode != 0
    assert not output.exists()


def test_child_failure_code_is_propagated(tmp_path):
    requested = tmp_path / "nfs"
    result, output = run_post_bump(tmp_path, requested=requested, exit_code=37)
    assert result.returncode == 37
    assert output.read_text().splitlines() == [str(requested), "v1.2.3"]
    assert_no_probes(requested)


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
