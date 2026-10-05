"""Bounded, verified archives for local runner caches; no workspace snapshots.

Active caches stay on local disk. Only a single bounded tar crosses the shared
archive root, and publication is atomic and first-writer-wins.
"""

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 100_000
ARCHIVE_NAME = "cache.tar"
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
VARIABLES = {
    "uv": "UV_CACHE_DIR",
    "npm": "NPM_CONFIG_CACHE",
    "go-mod": "GOMODCACHE",
    "go-build": "GOCACHE",
}


def cache_path(kind):
    if os.environ.get("WORKFLOW_CACHE_PROFILE") != "local":
        raise ValueError("Archive caches require the explicit local profile")
    root = Path(os.environ["WORKFLOW_CACHE_JOB_ROOT"])
    path = Path(os.environ[VARIABLES[kind]])
    if root.resolve() != root or path.resolve() != path or path.parent != root:
        raise ValueError("Cache must be a direct, canonical child of the job root")
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError("Cache must be an owned directory")
    return path


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or len(name) > 4096:
        raise ValueError("Invalid archive path")
    if str(path) != name or name == ".":
        raise ValueError("Non-canonical archive path")
    return path


def check_members(archive):
    members = []
    names = {}
    size = 0
    for member in archive:
        path = safe_name(member.name)
        if member.name in names or len(names) >= MAX_FILES:
            raise ValueError("Duplicate path or too many archive entries")
        if not (member.isdir() or member.isfile() or member.issym()):
            raise ValueError("Unsupported archive entry (including hardlinks/devices)")
        if member.size < 0 or (not member.isfile() and member.size):
            raise ValueError("Invalid archive entry size")
        size += member.size
        if size > MAX_BYTES:
            raise ValueError("Archive exceeds uncompressed size limit")
        if member.issym():
            target = member.linkname
            if not target or target.startswith("/") or "\\" in target:
                raise ValueError("Unsafe symlink")
            normalized = posixpath.normpath(str(path.parent / target))
            if normalized == ".." or normalized.startswith("../"):
                raise ValueError("Symlink escapes cache")
            # Links only point to a real archive member; chains and cycles are refused.
            member.linkname = target
            member.pax_headers = {}
        names[member.name] = member
        members.append(member)
    for member in members:
        for parent in PurePosixPath(member.name).parents:
            if str(parent) != "." and (str(parent) not in names or not names[str(parent)].isdir()):
                raise ValueError("Entry parent is absent or not a directory")
        if member.issym():
            target = posixpath.normpath(str(PurePosixPath(member.name).parent / member.linkname))
            if target not in names or names[target].issym():
                raise ValueError("Dangling or chained symlink")
    return members, size


def pack(source, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=".cache-")
    os.close(fd)
    try:
        with tarfile.open(temporary, "w", dereference=False) as archive:
            # Build regular members explicitly: tarfile must not turn shared
            # uv/go hardlink inodes into unsafe hardlink entries.
            count = size = 0
            for path in sorted(source.rglob("*")):
                name = path.relative_to(source).as_posix()
                safe_name(name)
                info = archive.gettarinfo(str(path), arcname=name)
                if path.is_symlink():
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(source):
                        raise ValueError("Source symlink escapes cache")
                elif path.is_file():
                    info.type = tarfile.REGTYPE
                    info.linkname = ""
                    info.size = path.stat().st_size
                elif not path.is_dir():
                    raise ValueError("Unsupported cache file")
                count += 1
                size += info.size
                if count > MAX_FILES or size > MAX_BYTES:
                    raise ValueError("Cache exceeds archive limits")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.mode &= 0o777
                with path.open("rb") if info.isfile() else open(os.devnull, "rb") as data:
                    archive.addfile(info, data if info.isfile() else None)
        with tarfile.open(temporary) as archive:
            check_members(archive)
        with open(temporary, "rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        os.replace(temporary, output)
        return {"digest": digest, "files": count, "bytes": size, "archive_bytes": output.stat().st_size}
    finally:
        Path(temporary).unlink(missing_ok=True)


def unpack(source, destination):
    if source.stat().st_size > MAX_BYTES + MAX_FILES * 4096:
        raise ValueError("Archive exceeds transfer size limit")
    if any(destination.iterdir()):
        raise ValueError("Restore requires an empty cache; existing state is never overwritten")
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".restore-") as temporary:
        staging = Path(temporary)
        with tarfile.open(source, "r:") as archive:
            members, size = check_members(archive)
            for member in members:
                target = staging / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as data, target.open("xb") as output:
                        shutil.copyfileobj(data, output)
                    target.chmod(member.mode & 0o777)
            for member in members:
                if member.issym():
                    (staging / member.name).symlink_to(member.linkname)
        # Same local filesystem; publish only after the whole archive validates.
        destination.rmdir()
        try:
            staging.rename(destination)
        except BaseException:
            destination.mkdir(exist_ok=True)
            raise
        return {"files": len(members), "bytes": size}


def key(repository, os_name, architecture, kind, version, fingerprint):
    if not all((repository, os_name, architecture, kind, version, fingerprint)) or kind not in VARIABLES:
        raise ValueError("Cache key requires repository, platform, tool version and lock/input fingerprint")
    encoded = json.dumps([repository, os_name, architecture, kind, version, fingerprint], separators=(",", ":"))
    return "runner-cache-v1-" + hashlib.sha256(encoded.encode()).hexdigest()


def archive_root():
    """Shared archive root, or None when persistence is not configured."""
    root = os.environ.get("WORKFLOW_ARCHIVE_ROOT", "")
    if not root:
        return None
    path = Path(root)
    if not root.startswith("/") or root == "/" or not path.is_dir():
        raise ValueError("WORKFLOW_ARCHIVE_ROOT must be an absolute existing directory")
    if path.resolve() != path or path.is_symlink():
        raise ValueError("WORKFLOW_ARCHIVE_ROOT must be canonical and not a symlink")
    return path


def is_protected_ref():
    return os.environ.get("GITHUB_REF_PROTECTED", "false") == "true"


def archive_blob(root, repository, protected, archive_key):
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid repository name")
    ref_class = "protected" if protected else "ephemeral"
    directory = root / repository.replace("/", "__") / ref_class / archive_key
    return directory / ARCHIVE_NAME


def valid_blob(path):
    if not path.is_file() or path.is_symlink():
        return False
    return path.stat().st_size <= MAX_BYTES + MAX_FILES * 4096


def restore_cache(kind, version, fingerprint):
    cache = cache_path(kind)
    repository = os.environ["GITHUB_REPOSITORY"]
    archive_key = key(repository, os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"], kind, version, fingerprint)
    result = {"key": archive_key, "kind": kind, "available": "false"}
    root = archive_root()
    if root is None:
        return result
    protected = is_protected_ref()
    candidates = [archive_blob(root, repository, protected, archive_key)]
    if not protected:
        # A pull request may warm-start from the protected archive, never writes to it.
        candidates.append(archive_blob(root, repository, True, archive_key))
    source = next((path for path in candidates if valid_blob(path)), None)
    if source is None:
        return result
    with tempfile.TemporaryDirectory(dir=cache.parent, prefix=".fetch-") as temporary:
        local = Path(temporary) / ARCHIVE_NAME
        shutil.copyfile(source, local)
        result.update(unpack(local, cache))
    result["available"] = "true"
    return result


def publish_cache(kind, version, fingerprint):
    cache = cache_path(kind)
    repository = os.environ["GITHUB_REPOSITORY"]
    archive_key = key(repository, os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"], kind, version, fingerprint)
    result = {"key": archive_key, "kind": kind, "published": "false"}
    root = archive_root()
    if root is None:
        return result
    destination = archive_blob(root, repository, is_protected_ref(), archive_key)
    with tempfile.TemporaryDirectory(dir=cache.parent, prefix=".pack-") as temporary:
        blob = Path(temporary) / ARCHIVE_NAME
        result.update(pack(cache, blob))
        result["published"] = "true" if store_archive(destination, blob) else "false"
    return result


def store_archive(destination, blob):
    """Atomic, immutable, first-writer-wins publication of a key directory."""
    key_dir = destination.parent
    key_dir.parent.mkdir(parents=True, exist_ok=True)
    # The key directory is the immutable unit; an existing one is never rewritten.
    if key_dir.exists() or key_dir.is_symlink():
        return False
    staging = Path(tempfile.mkdtemp(dir=key_dir.parent, prefix=".publish-"))
    try:
        published = staging / ARCHIVE_NAME
        shutil.copyfile(blob, published)
        with open(published, "rb") as handle:
            os.fsync(handle.fileno())
        published.chmod(0o444)
        try:
            staging.rename(key_dir)
        except OSError:
            # Another writer published first; keep the immutable existing copy.
            if key_dir.exists():
                return False
            raise
        return True
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["restore", "publish"])
    parser.add_argument("--kind", required=True, choices=VARIABLES)
    parser.add_argument("--version", required=True)
    parser.add_argument("--fingerprint", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    try:
        if args.operation == "restore":
            result = restore_cache(args.kind, args.version, args.fingerprint)
        else:
            result = publish_cache(args.kind, args.version, args.fingerprint)
    except (ValueError, OSError, tarfile.TarError) as error:
        # Dependency caches are optional; malformed or absent archives never block.
        print(f"::warning::Cache {args.operation} skipped: {error}")
        result = {"kind": args.kind, "available": "false", "published": "false"}
    result["seconds"] = round(time.monotonic() - started, 3)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for name, value in result.items():
                print(f"{name}={value}", file=output)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
