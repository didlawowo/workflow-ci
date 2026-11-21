"""Tool versions come from the caller and the project, never from what a runner holds."""

import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / ".github/actions/setup-python-env/sync-python-args.sh"
INCOMPATIBLE = (
    "warning: The requested interpreter resolved to Python 3.12.13, which is "
    "incompatible with the project's Python requirement: `>=3.13`"
)


def test_every_uv_installation_is_pinned_to_one_exact_version():
    versions = {}
    for path in sorted(ROOT.glob(".github/**/*.yml")) + sorted(
        ROOT.glob(".forgejo/**/*.yml")
    ):
        steps = re.split(r"\n\s*- ", path.read_text())
        for step in steps:
            if "astral-sh/setup-uv@" not in step:
                continue
            match = re.search(r'^\s+version: "([^"]+)"$', step, re.M)
            versions[f"{path.relative_to(ROOT)}"] = match.group(1) if match else None
    assert len(versions) >= 12
    assert set(versions.values()) == {next(iter(versions.values()))}, versions
    assert re.fullmatch(r"\d+\.\d+\.\d+", next(iter(versions.values()))), versions


def select(tmp_path, pin_status, find_stderr):
    """Run the selector against a uv that answers `python pin` and `python find`."""
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    (tmp_path / "find.stderr").write_text(find_stderr)
    fake.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$UV_LOG"\n'
        f'[ "$2" = pin ] && exit {pin_status}\n'
        '[ "$2" = find ] && cat "$FIND_STDERR" >&2 && echo /usr/bin/python3\n'
        "exit 0\n"
    )
    fake.chmod(0o755)
    log = tmp_path / "uv.log"
    result = subprocess.run(
        ["bash", str(SELECTOR), "3.12"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
            "UV_LOG": str(log),
            "FIND_STDERR": str(tmp_path / "find.stderr"),
        },
    )
    assert result.returncode == 0, result.stderr
    return result, log.read_text().splitlines()


def test_requested_version_is_forced_without_project_convention(tmp_path):
    result, calls = select(tmp_path, pin_status=2, find_stderr="")
    assert result.stdout.splitlines() == ["--python", "3.12"]
    assert calls == ["python pin", "python find 3.12"]


def test_project_pin_keeps_precedence(tmp_path):
    result, calls = select(tmp_path, pin_status=0, find_stderr="")
    assert result.stdout == ""
    assert calls == ["python pin"]


def test_requires_python_excluding_the_request_keeps_uv_discovery(tmp_path):
    result, _ = select(tmp_path, pin_status=2, find_stderr=INCOMPATIBLE)
    assert result.stdout == ""
    assert "::warning::python-version 3.12" in result.stderr


def test_selector_requires_a_version():
    result = subprocess.run(["bash", str(SELECTOR)], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize("sync", ['uv sync "${PYTHON_ARGS[@]}"\n', '"${SYNC_ARGS[@]}"'])
def test_both_sync_commands_receive_the_selection(sync):
    action = (ROOT / ".github/actions/setup-python-env/action.yml").read_text()
    assert sync in action
    assert len(re.findall(r"uv sync(?! \"\$\{PYTHON_ARGS\[@\]\}\")", action)) == 0
