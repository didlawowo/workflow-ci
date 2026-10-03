"""Exercise the shared uv-cache resolver and release post-bump wiring."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
RESOLVER = ROOT / ".github/actions/resolve-uv-cache/action.yml"


def release_steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]["steps"]


def post_bump_step():
    return next(step for step in release_steps() if step.get("name") == "Post-bump command")


def release_resolver_step():
    return next(
        step
        for step in release_steps()
        if step.get("name") == "Resolve writable uv cache for post-bump"
    )


def resolver_command():
    action = yaml.safe_load(RESOLVER.read_text())
    return action["runs"]["steps"][0]["run"]


def _path_env(path_prefix=None):
    return (
        str(path_prefix) + os.pathsep + os.environ["PATH"]
        if path_prefix is not None
        else os.environ["PATH"]
    )


def run_resolver(tmp_path, *, requested=None, runner_temp=None, path_prefix=None):
    output = tmp_path / "github-output"
    env = {
        "PATH": _path_env(path_prefix),
        "GITHUB_OUTPUT": str(output),
        "REQUESTED_UV_CACHE": str(requested) if requested is not None else "",
    }
    if runner_temp is not None:
        env["RUNNER_TEMP"] = str(runner_temp)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", resolver_command()],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    values = {}
    if output.exists():
        for line in output.read_text().splitlines():
            key, value = line.split("=", 1)
            values[key] = value
    return result, values


def run_post_bump(tmp_path, cache, *, exit_code=0):
    child = tmp_path / "child command.sh"
    child.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n%s\\n" "$UV_CACHE_DIR" "$NEW_VERSION" > "$RESULT_FILE"\n'
        'exit "$CHILD_EXIT_CODE"\n'
    )
    result_file = tmp_path / "child result"
    env = {
        "PATH": os.environ["PATH"],
        "POST_BUMP_COMMAND": f"bash {shlex.quote(str(child))}",
        "NEW_VERSION": "v1.2.3",
        "UV_CACHE_DIR": str(cache),
        "RESULT_FILE": str(result_file),
        "CHILD_EXIT_CODE": str(exit_code),
    }
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


def test_writable_requested_cache_is_preserved_with_spaces(tmp_path):
    requested = tmp_path / "requested cache"
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    resolved, values = run_resolver(
        tmp_path, requested=requested, runner_temp=runner_temp
    )

    assert resolved.returncode == 0, resolved.stderr
    assert values == {
        "path": str(requested),
        "mode": "shared",
        "reason": "shared-ok",
    }
    assert_no_probes(requested)
    assert not (runner_temp / "uv-cache").exists()

    result, output = run_post_bump(tmp_path, values["path"])
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(requested), "v1.2.3"]


def test_requested_cache_probe_timeout_falls_back_under_errexit(tmp_path):
    requested = tmp_path / "requested cache"
    requested.mkdir()
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    timeout = bin_dir / "timeout"
    timeout.write_text("#!/usr/bin/env bash\nexit 74\n")
    timeout.chmod(0o700)

    result, values = run_resolver(
        tmp_path,
        requested=requested,
        runner_temp=runner_temp,
        path_prefix=bin_dir,
    )

    fallback = runner_temp / "uv-cache"
    assert result.returncode == 0, result.stderr
    assert values == {
        "path": str(fallback),
        "mode": "local",
        "reason": "requested-unavailable",
    }
    assert "using local fallback" in result.stdout
    assert_no_probes(fallback)


def test_unusable_requested_cache_falls_back(tmp_path):
    requested = tmp_path / "not a directory"
    requested.write_text("occupied")
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, values = run_resolver(
        tmp_path, requested=requested, runner_temp=runner_temp
    )

    fallback = runner_temp / "uv-cache"
    assert result.returncode == 0, result.stderr
    assert values["path"] == str(fallback)
    assert values["mode"] == "local"
    assert values["reason"] == "requested-unavailable"
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
        result, values = run_resolver(
            tmp_path, requested=requested, runner_temp=runner_temp
        )
    finally:
        requested.chmod(0o700)

    fallback = runner_temp / "uv-cache"
    assert result.returncode == 0, result.stderr
    assert values["path"] == str(fallback)
    assert values["reason"] == "requested-unavailable"
    assert_no_probes(requested)
    assert_no_probes(fallback)


def test_unset_cache_uses_runner_temp(tmp_path):
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    result, values = run_resolver(tmp_path, runner_temp=runner_temp)

    fallback = runner_temp / "uv-cache"
    assert result.returncode == 0, result.stderr
    assert values == {
        "path": str(fallback),
        "mode": "local",
        "reason": "not-requested",
    }
    assert_no_probes(fallback)


def test_unwritable_fallback_fails_resolution(tmp_path):
    runner_temp = tmp_path / "runner temp"
    runner_temp.write_text("not a directory")

    result, values = run_resolver(tmp_path, runner_temp=runner_temp)

    assert result.returncode != 0
    assert values == {}


def test_child_failure_code_is_propagated(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()

    result, output = run_post_bump(tmp_path, cache, exit_code=37)

    assert result.returncode == 37
    assert output.read_text().splitlines() == [str(cache), "v1.2.3"]


def test_resolver_uses_unique_probes_and_release_consumes_its_output():
    command = resolver_command()
    assert 'mktemp "$requested/.workflow-ci-write-test.XXXXXX"' in command
    assert 'mktemp "$cache/.workflow-ci-write-test.XXXXXX"' in command
    assert '.workflow-ci-write-test.$"' not in command

    resolver = release_resolver_step()
    post_bump = post_bump_step()
    expected_if = (
        "steps.recovery.outputs.pending != 'true' && "
        "steps.bump.outputs.released == 'true' && "
        "inputs.post-bump-command != ''"
    )
    assert resolver["if"] == expected_if
    assert resolver["uses"] == "./.workflow-ci/.github/actions/resolve-uv-cache"
    assert post_bump["if"] == expected_if
    assert post_bump["env"]["NEW_VERSION"] == "${{ steps.bump.outputs.version }}"
    assert (
        post_bump["env"]["UV_CACHE_DIR"]
        == "${{ steps.post-bump-uv-cache.outputs.path }}"
    )
    assert "timeout 3 bash -c" not in post_bump["run"]
    assert "GITHUB_ENV" not in post_bump["run"]
    assert "HOME=" not in post_bump["run"]
