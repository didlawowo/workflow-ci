"""Exercise release post-bump wiring to the centralized uv-cache resolver."""

import os
import shlex
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"


def release_steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]["steps"]


def resolver_step():
    return next(
        step
        for step in release_steps()
        if step.get("name") == "Resolve writable uv cache for post-bump"
    )


def post_bump_step():
    return next(step for step in release_steps() if step.get("name") == "Post-bump command")


def run_post_bump(tmp_path, cache, *, exit_code=0):
    child = tmp_path / "child command.sh"
    child.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n%s\\n" "$UV_CACHE_DIR" "$NEW_VERSION" > "$RESULT_FILE"\n'
        'exit "$CHILD_EXIT_CODE"\n'
    )
    result_file = tmp_path / "child result"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", post_bump_step()["run"]],
        env={
            "PATH": os.environ["PATH"],
            "POST_BUMP_COMMAND": f"bash {shlex.quote(str(child))}",
            "NEW_VERSION": "v1.2.3",
            "UV_CACHE_DIR": str(cache),
            "RESULT_FILE": str(result_file),
            "CHILD_EXIT_CODE": str(exit_code),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, result_file


def test_release_resolves_cache_before_post_bump():
    resolver = resolver_step()
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
    assert "probe_cache()" not in post_bump["run"]
    assert "timeout 3 bash -c" not in post_bump["run"]
    assert "GITHUB_ENV" not in post_bump["run"]


def test_post_bump_receives_resolved_cache(tmp_path):
    cache = tmp_path / "resolved cache"
    cache.mkdir()

    result, output = run_post_bump(tmp_path, cache)

    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [str(cache), "v1.2.3"]


def test_child_failure_code_is_propagated(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()

    result, output = run_post_bump(tmp_path, cache, exit_code=37)

    assert result.returncode == 37
    assert output.read_text().splitlines() == [str(cache), "v1.2.3"]
