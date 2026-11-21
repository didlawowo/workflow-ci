"""The Forgejo sandbox root lives in disposable scratch, never in a dependency cache."""

import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
adapter = importlib.import_module("forgejo_mutation")

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root owns and traverses every directory"
)

OWNER = os.getuid()
# Neither owner nor group of the directories created below: "other" bits decide,
# as they do for the sandbox UID below a root-owned scratch directory.
SANDBOX = max(os.getuid(), os.getgid()) + 1000


@pytest.fixture
def public(monkeypatch):
    """World-traversable area: pytest's own tmp_path is private to its owner."""
    directory = Path(tempfile.mkdtemp(prefix="sandbox-parent-", dir="/tmp")).resolve()
    directory.chmod(0o755)
    monkeypatch.setattr(
        adapter.tempfile, "gettempdir", lambda: str(directory / "absent")
    )
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


def make(parent, name, mode):
    directory = parent / name
    directory.mkdir()
    directory.chmod(mode)
    return directory


@pytest.fixture
def system_temp(public, monkeypatch):
    directory = make(public, "system-temp", 0o755)
    monkeypatch.setattr(adapter.tempfile, "gettempdir", lambda: str(directory))
    return directory


def test_public_directory_is_reachable(public):
    assert adapter.reachable_by(make(public, "scratch", 0o755), SANDBOX)


def test_directory_without_search_permission_is_unreachable(public):
    assert not adapter.reachable_by(make(public, "private", 0o754), SANDBOX)


def test_one_private_ancestor_blocks_a_public_directory(public):
    blocked = make(make(public, "private-parent", 0o754), "child", 0o755)
    open_ = make(make(public, "public-parent", 0o755), "child", 0o755)
    assert not adapter.reachable_by(blocked, SANDBOX)
    assert adapter.reachable_by(open_, SANDBOX)


def test_owner_bits_take_precedence_over_other_bits(public):
    directory = make(public, "owner-locked", 0o077)
    try:
        assert adapter.reachable_by(directory, SANDBOX)
        assert not adapter.reachable_by(directory, OWNER)
    finally:
        directory.chmod(0o700)


def test_group_bits_apply_to_the_matching_gid(public):
    directory = make(public, "group-locked", 0o705)
    group = directory.stat().st_gid
    if group == OWNER:
        pytest.skip("owner and group share the same number")
    assert adapter.reachable_by(directory, SANDBOX)
    assert not adapter.reachable_by(directory, group)


def test_job_scratch_is_preferred_over_system_temp(public, system_temp):
    scratch = make(public, "runner-temp", 0o755)
    chosen = adapter.sandbox_parent({"RUNNER_TEMP": str(scratch)}, SANDBOX)
    assert chosen == scratch


def test_no_cache_variable_is_required(system_temp):
    assert adapter.sandbox_parent({}, SANDBOX) == system_temp


def test_unreachable_job_scratch_falls_back_to_system_temp(public, system_temp):
    scratch = make(public, "runner-temp", 0o750)
    assert adapter.sandbox_parent({"RUNNER_TEMP": str(scratch)}, SANDBOX) == system_temp


def test_relative_job_scratch_is_ignored(public, system_temp, monkeypatch):
    monkeypatch.chdir(public)
    make(public, "relative-scratch", 0o755)
    chosen = adapter.sandbox_parent({"RUNNER_TEMP": "relative-scratch"}, SANDBOX)
    assert chosen == system_temp


def test_world_writable_scratch_needs_the_sticky_bit(public, system_temp):
    loose = make(public, "loose", 0o777)
    sticky = make(public, "sticky", 0o1777)
    assert adapter.sandbox_parent({"RUNNER_TEMP": str(loose)}, SANDBOX) == system_temp
    assert adapter.sandbox_parent({"RUNNER_TEMP": str(sticky)}, SANDBOX) == sticky


def test_scratch_owned_by_the_sandbox_uid_is_refused(public):
    owned = make(public, "owned", 0o1755)
    with pytest.raises(RuntimeError, match="scratch directory"):
        adapter.sandbox_parent({"RUNNER_TEMP": str(owned)}, OWNER)


def test_no_usable_scratch_fails_closed(public):
    with pytest.raises(RuntimeError, match="scratch directory"):
        adapter.sandbox_parent({"RUNNER_TEMP": str(public / "missing")}, SANDBOX)
