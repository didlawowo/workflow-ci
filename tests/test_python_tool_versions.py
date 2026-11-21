"""Tool versions come from the caller and the project, never from what a runner holds."""

import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / ".github/actions/setup-python-env/sync-python-args.sh"
INCOMPATIBLE = (
    "warning: The requested interpreter resolved to Python 3.12.13, which is "
    "incompatible with the project's Python requirement: `>=3.13`"
)


# setup-uv runs there at the root of the consumer checkout: the uv version the
# project requires (`required-version`) keeps precedence over a central pin.
CONSUMER_ROOT = {
    ".github/actions/precommit-contract/action.yml",
    ".forgejo/actions/mutation-policy/action.yml",
    ".forgejo/actions/release/action.yml",
}


def uv_installations():
    """(file, version or None) for every setup-uv step of actions and workflows."""
    found = []

    def visit(node, path):
        if isinstance(node, dict):
            if "astral-sh/setup-uv@" in str(node.get("uses", "")):
                found.append((path, (node.get("with") or {}).get("version")))
            for value in node.values():
                visit(value, path)
        elif isinstance(node, list):
            for value in node:
                visit(value, path)

    for root in (".github", ".forgejo"):
        for path in sorted((ROOT / root).rglob("*.y*ml")):
            visit(yaml.safe_load(path.read_text()), str(path.relative_to(ROOT)))
    return found


def test_every_central_uv_installation_is_pinned_to_one_exact_version():
    central = [item for item in uv_installations() if item[0] not in CONSUMER_ROOT]
    assert len(central) == 16, central
    versions = {version for _, version in central}
    assert len(versions) == 1, central
    assert re.fullmatch(r"\d+\.\d+\.\d+", versions.pop() or ""), central


def test_consumer_root_installations_keep_the_project_requirement():
    consumer = [item for item in uv_installations() if item[0] in CONSUMER_ROOT]
    assert sorted(path for path, _ in consumer) == sorted(CONSUMER_ROOT)
    assert all(version is None for _, version in consumer), consumer


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


@pytest.mark.parametrize("action", ["setup-python-env", "run-python-tests"])
def test_every_sync_of_the_python_actions_receives_the_selection(action):
    content = (ROOT / ".github/actions" / action / "action.yml").read_text()
    syncs = re.findall(r"^\s+uv sync.*$", content, re.M)
    assert len(syncs) == 2, syncs
    assert all('"${PYTHON_ARGS[@]}"' in sync for sync in syncs), syncs
    assert "setup-python-env/sync-python-args.sh" in content.replace(
        "$GITHUB_ACTION_PATH/sync-python-args.sh",
        "setup-python-env/sync-python-args.sh",
    )
