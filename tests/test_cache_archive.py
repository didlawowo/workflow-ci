"""Round-trip, safety and namespace contracts for bounded cache archives."""

import importlib.util
import io
import os
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("cache_archive", ROOT / ".ci/cache_archive.py")
CA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CA)
KINDS = ("uv", "go-build", "go-mod", "npm")


def make_job(tmp_path, name):
    job = tmp_path / name
    job.mkdir()
    for kind in KINDS:
        (job / kind).mkdir()
    return job


def configure(monkeypatch, tmp_path, job, *, protected=False, archive=True):
    archive_root = tmp_path / "archive"
    if archive:
        archive_root.mkdir(exist_ok=True)
        monkeypatch.setenv("WORKFLOW_ARCHIVE_ROOT", str(archive_root))
    else:
        monkeypatch.delenv("WORKFLOW_ARCHIVE_ROOT", raising=False)
    values = {
        "WORKFLOW_CACHE_PROFILE": "local",
        "WORKFLOW_CACHE_JOB_ROOT": str(job),
        "UV_CACHE_DIR": str(job / "uv"),
        "GOCACHE": str(job / "go-build"),
        "GOMODCACHE": str(job / "go-mod"),
        "NPM_CONFIG_CACHE": str(job / "npm"),
        "GITHUB_REPOSITORY": "didlawowo/workflow-ci",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
        "GITHUB_REF_PROTECTED": "true" if protected else "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return archive_root


def make_archive(path, member):
    with tarfile.open(path, "w") as archive:
        archive.addfile(member)


# ── low level ───────────────────────────────────────────────────────────────


def test_key_is_stable_and_input_sensitive():
    base = CA.key("r/n", "Linux", "X64", "uv", "0.12", "fp")
    assert base == CA.key("r/n", "Linux", "X64", "uv", "0.12", "fp")
    assert base != CA.key("r/n", "Linux", "ARM64", "uv", "0.12", "fp")
    assert base != CA.key("r/n", "Linux", "X64", "npm", "0.12", "fp")
    assert base != CA.key("r/n", "Linux", "X64", "uv", "0.13", "fp")
    assert base != CA.key("r/n", "Linux", "X64", "uv", "0.12", "fp2")
    with pytest.raises(ValueError):
        CA.key("", "Linux", "X64", "uv", "0.12", "fp")
    with pytest.raises(ValueError):
        CA.key("r/n", "Linux", "X64", "rust", "0.12", "fp")


def test_pack_unpack_round_trip(tmp_path):
    source = tmp_path / "source"
    (source / "pkg" / "bin").mkdir(parents=True)
    (source / "pkg" / "bin" / "tool").write_bytes(b"payload")
    (source / "pkg" / "link").symlink_to("bin/tool")
    blob = tmp_path / "out" / "cache.tar"
    info = CA.pack(source, blob)
    assert info["files"] == 4

    destination = tmp_path / "destination"
    destination.mkdir()
    CA.unpack(blob, destination)
    assert (destination / "pkg" / "bin" / "tool").read_bytes() == b"payload"
    assert (destination / "pkg" / "link").is_symlink()


def test_pack_flattens_hardlinks_to_regular_members(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "a"
    original.write_bytes(b"shared")
    os.link(original, source / "b")
    blob = tmp_path / "cache.tar"
    CA.pack(source, blob)
    with tarfile.open(blob) as archive:
        members = archive.getmembers()
    assert {m.name for m in members} == {"a", "b"}
    assert all(m.type == tarfile.REGTYPE for m in members)


def test_pack_rejects_symlink_escape(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "leak").symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        CA.pack(source, tmp_path / "cache.tar")


@pytest.mark.parametrize("case", ["traversal", "absolute", "nested", "hardlink", "device"])
def test_unpack_rejects_unsafe_members(tmp_path, case):
    names = {
        "traversal": "../escape",
        "absolute": "/abs",
        "nested": "ok/../escape",
        "hardlink": "hard",
        "device": "device",
    }
    member = tarfile.TarInfo(name=names[case])
    if case == "hardlink":
        member.type = tarfile.LNKTYPE
        member.linkname = "target"
    elif case == "device":
        member.type = tarfile.CHRTYPE
    archive = tmp_path / "cache.tar"
    make_archive(archive, member)
    destination = tmp_path / "cache"
    destination.mkdir()
    with pytest.raises((ValueError, tarfile.TarError)):
        CA.unpack(archive, destination)


def test_unpack_rejects_escaping_symlink(tmp_path):
    member = tarfile.TarInfo(name="evil")
    member.type = tarfile.SYMTYPE
    member.linkname = "../../etc/passwd"
    archive = tmp_path / "cache.tar"
    make_archive(archive, member)
    destination = tmp_path / "cache"
    destination.mkdir()
    with pytest.raises(ValueError):
        CA.unpack(archive, destination)


def test_unpack_requires_empty_destination(tmp_path):
    archive = tmp_path / "cache.tar"
    member = tarfile.TarInfo(name="file")
    member.size = 1
    with tarfile.open(archive, "w") as tar:
        tar.addfile(member, io.BytesIO(b"x"))
    destination = tmp_path / "cache"
    destination.mkdir()
    (destination / "existing").write_text("keep")
    with pytest.raises(ValueError):
        CA.unpack(archive, destination)
    assert (destination / "existing").read_text() == "keep"


# ── high level, shared archive root ──────────────────────────────────────────


def test_missing_archive_root_is_a_cold_noop(monkeypatch, tmp_path):
    job = make_job(tmp_path, "job")
    configure(monkeypatch, tmp_path, job, archive=False)
    assert CA.restore_cache("uv", "0.12", "fp")["available"] == "false"
    assert CA.publish_cache("uv", "0.12", "fp")["published"] == "false"


def test_publish_then_restore_round_trip(monkeypatch, tmp_path):
    publisher = make_job(tmp_path, "publisher")
    configure(monkeypatch, tmp_path, publisher)
    (publisher / "uv" / "artifact").write_text("cached")
    assert CA.publish_cache("uv", "0.12", "fp")["published"] == "true"

    consumer = make_job(tmp_path, "consumer")
    configure(monkeypatch, tmp_path, consumer)
    restored = CA.restore_cache("uv", "0.12", "fp")
    assert restored["available"] == "true"
    assert (consumer / "uv" / "artifact").read_text() == "cached"


def test_a_different_fingerprint_is_a_miss(monkeypatch, tmp_path):
    publisher = make_job(tmp_path, "publisher")
    configure(monkeypatch, tmp_path, publisher)
    (publisher / "uv" / "artifact").write_text("cached")
    CA.publish_cache("uv", "0.12", "fp-1")

    consumer = make_job(tmp_path, "consumer")
    configure(monkeypatch, tmp_path, consumer)
    assert CA.restore_cache("uv", "0.12", "fp-2")["available"] == "false"


def test_publish_is_first_writer_wins(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    destination = CA.archive_blob(root, "didlawowo/workflow-ci", True, "k")
    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    assert CA.store_archive(destination, first) is True
    assert CA.store_archive(destination, second) is False
    assert destination.read_bytes() == b"one"


def test_ephemeral_cannot_poison_protected_namespace(monkeypatch, tmp_path):
    publisher = make_job(tmp_path, "pr-publisher")
    configure(monkeypatch, tmp_path, publisher, protected=False)
    (publisher / "uv" / "artifact").write_text("from-pr")
    assert CA.publish_cache("uv", "0.12", "fp")["published"] == "true"

    # A protected job never restores the ephemeral archive.
    protected = make_job(tmp_path, "protected")
    configure(monkeypatch, tmp_path, protected, protected=True)
    assert CA.restore_cache("uv", "0.12", "fp")["available"] == "false"


def test_ephemeral_may_warm_start_from_protected(monkeypatch, tmp_path):
    publisher = make_job(tmp_path, "protected-publisher")
    configure(monkeypatch, tmp_path, publisher, protected=True)
    (publisher / "uv" / "artifact").write_text("trusted")
    assert CA.publish_cache("uv", "0.12", "fp")["published"] == "true"

    consumer = make_job(tmp_path, "pr")
    configure(monkeypatch, tmp_path, consumer, protected=False)
    restored = CA.restore_cache("uv", "0.12", "fp")
    assert restored["available"] == "true"
    assert (consumer / "uv" / "artifact").read_text() == "trusted"
    # Restore alone never writes: the ephemeral namespace stays absent.
    ephemeral = CA.archive_blob(
        tmp_path / "archive", "didlawowo/workflow-ci", False, restored["key"]
    )
    assert not ephemeral.exists()


def test_archive_root_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_ARCHIVE_ROOT", "relative")
    with pytest.raises(ValueError):
        CA.archive_root()
    monkeypatch.setenv("WORKFLOW_ARCHIVE_ROOT", "/")
    with pytest.raises(ValueError):
        CA.archive_root()
    monkeypatch.setenv("WORKFLOW_ARCHIVE_ROOT", str(tmp_path / "nope"))
    with pytest.raises(ValueError):
        CA.archive_root()


def test_archive_blob_rejects_traversal_repository(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    for repository in ("../evil", "a/b/c", "", "a//b"):
        with pytest.raises(ValueError):
            CA.archive_blob(root, repository, True, "key")


def test_runner_cache_action_declares_restore_and_publish():
    action = (ROOT / ".github/actions/runner-cache/action.yml").read_text()
    assert "mode" in action and "restore or publish" in action
    for kind, variable in (
        ("uv", "UV_CACHE_DIR"),
        ("npm", "NPM_CONFIG_CACHE"),
        ("go-mod", "GOMODCACHE"),
        ("go-build", "GOCACHE"),
    ):
        assert f"{kind}) require_runner_cache {variable}" in action
    assert "env.WORKFLOW_CACHE_PROFILE == 'local'" in action
    assert ".ci/cache_archive.py" in action
