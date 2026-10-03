"""Regression tests for fail-safe centralized uv cache selection on ARC/NFS runners."""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / ".github/actions/resolve-uv-cache/action.yml"

CONSUMERS = [
    (".github/actions/run-python-tests/action.yml", "Resolve writable uv cache"),
    (".github/actions/setup-python-env/action.yml", "Resolve writable uv cache"),
    (
        ".github/actions/quality-report/action.yml",
        "Resolve writable uv cache for quality reporter",
    ),
]


def _resolver_command() -> str:
    action = yaml.safe_load(RESOLVER.read_text())
    return action["runs"]["steps"][0]["run"]


def _parse_outputs(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        key, value = line.split("=", 1)
        result[key] = value
    return result


def _run_resolver(tmp_path: Path, *, fake_second_probe_failure: bool):
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    requested = tmp_path / "shared-uv-cache"
    requested.mkdir()
    github_output = tmp_path / "github-output"

    path = os.environ["PATH"]
    if fake_second_probe_failure:
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
            'echo "simulated Remote I/O error for $UV_CACHE_DIR" >&2\n'
            "exit 74\n"
        )
        timeout.chmod(0o700)
        path = str(bin_dir) + os.pathsep + path

    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _resolver_command()],
        env={
            **os.environ,
            "PATH": path,
            "RUNNER_TEMP": str(runner_temp),
            "UV_CACHE_DIR": str(requested),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, requested, runner_temp, github_output


def test_shared_uv_cache_is_used_when_stable(tmp_path):
    result, requested, runner_temp, github_output = _run_resolver(
        tmp_path, fake_second_probe_failure=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _parse_outputs(github_output) == {
        "path": str(requested),
        "mode": "shared",
        "reason": "shared-ok",
    }
    assert "Using shared uv cache" in result.stdout
    assert not (runner_temp / "uv-cache").exists()
    assert not list(requested.glob(".workflow-ci-write-test.*"))


def test_nfs_failure_between_probe_and_commit_falls_back_locally(tmp_path):
    result, requested, runner_temp, github_output = _run_resolver(
        tmp_path, fake_second_probe_failure=True
    )

    fallback = runner_temp / "uv-cache"
    assert result.returncode == 0, result.stdout + result.stderr
    assert (runner_temp / "timeout-calls").read_text().strip() == "2"
    assert _parse_outputs(github_output) == {
        "path": str(fallback),
        "mode": "local",
        "reason": "requested-commit-probe-failed",
    }
    assert "became unavailable during commit probe" in result.stdout
    assert "Remote I/O error" in result.stdout
    assert fallback.is_dir()
    assert not list(requested.glob(".workflow-ci-write-test.*"))
    assert not list(fallback.glob(".workflow-ci-write-test.*"))


def test_resolver_uses_unique_bounded_read_write_probes():
    command = _resolver_command()
    assert "timeout 3 bash -c" in command
    assert command.count("probe_cache") >= 3
    assert 'mktemp "$requested/.workflow-ci-write-test.XXXXXX"' in command
    assert 'mktemp "$fallback/.workflow-ci-write-test.XXXXXX"' in command
    assert 'printf "%s\\\\n" "workflow-ci" > "$probe"' in command
    assert 'cat "$probe" >/dev/null' in command


def test_python_consumers_delegate_to_central_resolver():
    for path, step_name in CONSUMERS:
        action = yaml.safe_load((ROOT / path).read_text())
        steps = action["runs"]["steps"]
        resolver = next(step for step in steps if step["name"] == step_name)
        assert resolver["uses"] == "$/.github/actions/resolve-uv-cache"

        exporter = next(
            step for step in steps if step["name"].startswith("Export resolved uv cache")
        )
        assert 'steps.uv-cache.outputs.path' in exporter["run"]
        assert "GITHUB_ENV" in exporter["run"]

        source = (ROOT / path).read_text()
        assert "probe_cache()" not in source
        assert "timeout 3 bash -c" not in source
