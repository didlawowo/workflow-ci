"""The Forgejo sandbox root lives in disposable scratch, never in a dependency cache."""

import importlib
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
adapter = importlib.import_module("forgejo_mutation")

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root traverses every directory"
)

OWNER = os.getuid()
# Neither owner nor group of the directories created below: "other" bits decide.
FOREIGN = max(os.getuid(), os.getgid()) + 1000


@pytest.fixture
def locked(tmp_path):
    """Directory whose own search permission is removed."""
    directory = tmp_path / "locked"
    directory.mkdir()
    directory.chmod(0o600)
    try:
        yield directory.resolve()
    finally:
        directory.chmod(0o700)


@pytest.fixture
def system_temp(tmp_path, monkeypatch):
    """Reachable stand-in for the system temporary directory."""
    directory = tmp_path / "system-temp"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(adapter.tempfile, "gettempdir", lambda: str(directory))
    return directory.resolve()


def test_owner_reaches_its_private_directory(tmp_path):
    directory = tmp_path / "scratch"
    directory.mkdir(mode=0o700)
    assert adapter.reachable_by(directory.resolve(), OWNER)


def test_directory_without_search_permission_is_unreachable(locked):
    assert not adapter.reachable_by(locked, OWNER)


def test_private_ancestor_blocks_another_uid(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    child = parent / "child"
    child.mkdir()
    child.chmod(0o755)
    assert adapter.reachable_by(child.resolve(), OWNER)
    assert not adapter.reachable_by(child.resolve(), FOREIGN)


def test_job_scratch_is_preferred_over_system_temp(tmp_path, system_temp):
    scratch = tmp_path / "runner-temp"
    scratch.mkdir(mode=0o700)
    chosen = adapter.sandbox_parent({"RUNNER_TEMP": str(scratch)}, OWNER)
    assert chosen == scratch.resolve()
    assert chosen != system_temp


def test_no_cache_variable_is_required(system_temp):
    assert adapter.sandbox_parent({}, OWNER) == system_temp


def test_unreachable_job_scratch_falls_back_to_system_temp(locked, system_temp):
    assert adapter.sandbox_parent({"RUNNER_TEMP": str(locked)}, OWNER) == system_temp


def test_relative_job_scratch_is_ignored(tmp_path, system_temp, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "relative-scratch").mkdir()
    chosen = adapter.sandbox_parent({"RUNNER_TEMP": "relative-scratch"}, OWNER)
    assert chosen == system_temp


def test_no_reachable_scratch_fails_closed(tmp_path, locked, monkeypatch):
    monkeypatch.setattr(adapter.tempfile, "gettempdir", lambda: str(locked))
    with pytest.raises(RuntimeError, match="scratch directory"):
        adapter.sandbox_parent({"RUNNER_TEMP": str(tmp_path / "absent")}, OWNER)
