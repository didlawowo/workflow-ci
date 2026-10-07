"""Exercise the release post-bump shell without dependency-cache assumptions."""

import os
import shlex
import subprocess
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def post_bump_step():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]["steps"]
    return next(step for step in steps if step.get("name") == "Post-bump command")


def run_post_bump(tmp_path, *, exit_code=0):
    child = tmp_path / "child command.sh"
    child.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$NEW_VERSION" > "$RESULT_FILE"\n'
        'exit "$CHILD_EXIT_CODE"\n'
    )
    result_file = tmp_path / "child result"
    env = {
        **os.environ,
        "POST_BUMP_COMMAND": f"bash {shlex.quote(str(child))}",
        "NEW_VERSION": "v1.2.3",
        "RESULT_FILE": str(result_file),
        "CHILD_EXIT_CODE": str(exit_code),
    }
    for variable in ("UV_CACHE_DIR", "GOCACHE", "GOMODCACHE"):
        env.pop(variable, None)

    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", post_bump_step()["run"]],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, result_file


def test_post_bump_runs_without_language_cache_environment(tmp_path):
    result, output = run_post_bump(tmp_path)

    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == "v1.2.3"


def test_child_failure_code_is_propagated(tmp_path):
    result, output = run_post_bump(tmp_path, exit_code=37)

    assert result.returncode == 37
    assert output.read_text().strip() == "v1.2.3"


def test_post_bump_has_no_dependency_cache_preflight():
    run = post_bump_step()["run"]
    assert "require_nfs_cache" not in run
    assert "nfs-cache.sh" not in run


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
