#!/usr/bin/env python3
"""Release engine behind the Forgejo release action.

Same policy as ``.github/workflows/release.yml``: conventional commits since
the last tag decide the next version (``release_commit_gate.py`` then
git-cliff), ``CHANGELOG.md`` and the declared version files are rewritten, and
a release commit, a tag and a forge release are published.

``prepare`` never needs a token. ``publish`` is idempotent: a run started
between the branch push and the tag push converges on the same tag and the
same release instead of cutting a second one.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

from release_commit_gate import any_message_requires_release

# Keep in step with GIT_CLIFF_VERSION in .github/actions/git-cliff-bump.
GIT_CLIFF_VERSION = "2.14.2"
BUNDLED_CLIFF_CONFIG = ROOT / "templates" / "common" / "cliff.toml"
SKIP_MARKER = "[skip ci]"
RELEASE_SUBJECT = re.compile(r"^chore\(release\):\s+(.+)\s+\[skip\sci\]$")
BOT_NAME = "forgejo-actions"
DRY_RUN_CHANGELOG_LINES = 80


class ReleaseError(Exception):
    """A condition that must stop the release without mutating the forge."""


def notice(message: str) -> None:
    print(f"::notice::{message}", flush=True)


def warning(message: str) -> None:
    print(f"::warning::{message}", flush=True)


def git(
    workspace: Path,
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"git {args[0]} failed: {detail}")
    return result


def write_outputs(values: dict[str, str]) -> None:
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise ReleaseError(f"output {key} must be a single line")
    destination = os.environ.get("GITHUB_OUTPUT") or os.environ.get("FORGEJO_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)


def state_dir() -> Path:
    base = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
    path = Path(base) / "forgejo-release"
    path.mkdir(parents=True, exist_ok=True)
    return path


def notes_path() -> Path:
    return state_dir() / "release-notes.md"


def parse_bool(raw: str, name: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "false"}:
        return value == "true"
    if value == "":
        return False
    raise ReleaseError(f"{name} must be 'true' or 'false', got {raw!r}")


# --- version computation -----------------------------------------------------


def tag_pattern(prefix: str) -> str:
    return f"{prefix}[0-9]*" if prefix else "[0-9]*"


def last_tag(workspace: Path, prefix: str) -> str:
    result = git(
        workspace,
        "describe",
        "--tags",
        "--abbrev=0",
        "--match",
        tag_pattern(prefix),
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def commits_require_release(workspace: Path, last: str) -> bool:
    revision_range = f"{last}..HEAD" if last else "HEAD"
    raw = git(workspace, "log", "--format=%B%x1e", revision_range).stdout
    messages = [item.strip() for item in raw.split("\x1e") if item.strip()]
    return any_message_requires_release(messages)


def normalise_version(bumped: str, last: str, prefix: str) -> str | None:
    """Return the tag to cut, or None when git-cliff found nothing to bump.

    git-cliff keeps the prefix of the last tag: strip it before adding the
    requested prefix back exactly once.
    """
    candidate = "".join(bumped.split())
    if not candidate or candidate == last:
        return None
    version = candidate
    if prefix and version.startswith(prefix):
        version = version[len(prefix) :]
    version = version.removeprefix("v")
    return f"{prefix}{version}"


def bare_version(version: str, prefix: str) -> str:
    if prefix and version.startswith(prefix):
        return version[len(prefix) :]
    return version


def git_cliff_command() -> list[str]:
    found = shutil.which("git-cliff")
    if found:
        probe = subprocess.run(
            [found, "--version"], capture_output=True, text=True, check=False
        )
        words = (probe.stdout + probe.stderr).split()
        if words and words[-1] == GIT_CLIFF_VERSION:
            return [found]
    uv = shutil.which("uv")
    if uv is None:
        raise ReleaseError(
            f"git-cliff {GIT_CLIFF_VERSION} is not installed and uv is not "
            "available to provide it"
        )
    return [uv, "tool", "run", "--from", f"git-cliff=={GIT_CLIFF_VERSION}", "git-cliff"]


def git_cliff(workspace: Path, config: Path, *args: str) -> str:
    result = subprocess.run(
        [*git_cliff_command(), "--config", str(config), *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"git-cliff {' '.join(args)} failed: {detail}")
    return result.stdout


def resolve_cliff_config(workspace: Path, requested: str) -> tuple[Path, bool]:
    """Return the config path and whether it is the bundled default."""
    if requested.strip():
        candidate = resolve_in_workspace(workspace, requested.strip())
        if candidate.is_file():
            return candidate, False
    return BUNDLED_CLIFF_CONFIG, True


# --- files -------------------------------------------------------------------


def parse_file_rules(raw: str, name: str) -> list[tuple[str, str]]:
    """Parse ``path:regex`` lines. The regex is kept verbatim: leading spaces
    after an anchor are significant."""
    rules: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        path, separator, regex = line.partition(":")
        if not separator or not path.strip() or not regex:
            raise ReleaseError(f"{name}: expected 'path:regex', got {line!r}")
        rules.append((path.strip(), regex))
    return rules


def resolve_in_workspace(workspace: Path, relative: str) -> Path:
    root = workspace.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ReleaseError(f"path escapes the repository: {relative!r}")
    return target


def compile_rule(regex: str, path: str) -> re.Pattern[str]:
    try:
        pattern = re.compile(regex, re.MULTILINE)
    except re.error as error:
        raise ReleaseError(f"invalid regex for {path}: {error}") from error
    if pattern.groups < 1:
        raise ReleaseError(f"regex for {path} needs one capture group")
    return pattern


def read_text(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def replace_group(source: str, match: re.Match[str], value: str) -> str:
    start, end = match.span(1)
    return source[:start] + value + source[end:]


def patch_version_files(
    workspace: Path, rules: list[tuple[str, str]], version: str
) -> list[str]:
    """Write ``version`` into every declared file. A missing file is skipped
    with a warning, as in release.yml; a regex that does not match fails."""
    planned: list[tuple[Path, re.Pattern[str], str]] = []
    for relative, regex in rules:
        target = resolve_in_workspace(workspace, relative)
        pattern = compile_rule(regex, relative)
        if not target.is_file():
            warning(f"version-file '{relative}' not found, skipping")
            continue
        if not pattern.search(read_text(target)):
            raise ReleaseError(f"regex did not match in {relative}")
        planned.append((target, pattern, relative))
    for target, pattern, relative in planned:
        source = read_text(target)
        match = pattern.search(source)
        if match is None:
            raise ReleaseError(f"regex did not match in {relative}")
        write_text(target, replace_group(source, match, version))
    return [relative for _, _, relative in planned]


def bump_build_number(workspace: Path, rules: list[tuple[str, str]]) -> int | None:
    """Increment the build number by one and write it into every file.

    When files disagree, the highest value wins: every file receives
    ``highest + 1`` so the number never goes backwards anywhere. A missing
    file, a regex that does not match or a non-integer capture fails before
    anything is written.
    """
    if not rules:
        return None
    planned: list[tuple[Path, re.Pattern[str], str]] = []
    current: list[int] = []
    for relative, regex in rules:
        target = resolve_in_workspace(workspace, relative)
        pattern = compile_rule(regex, relative)
        if not target.is_file():
            raise ReleaseError(f"build-number file '{relative}' not found")
        match = pattern.search(read_text(target))
        if match is None:
            raise ReleaseError(f"build-number regex did not match in {relative}")
        captured = match.group(1)
        if not re.fullmatch(r"[0-9]+", captured or ""):
            raise ReleaseError(
                f"build number in {relative} is not an integer: {captured!r}"
            )
        current.append(int(captured))
        planned.append((target, pattern, relative))
    following = max(current) + 1
    for target, pattern, relative in planned:
        source = read_text(target)
        match = pattern.search(source)
        if match is None:
            raise ReleaseError(f"build-number regex did not match in {relative}")
        write_text(target, replace_group(source, match, str(following)))
    return following


# --- prepare -----------------------------------------------------------------


def pending_release(workspace: Path) -> str | None:
    """Version of a release commit already sitting at HEAD, if any."""
    subject = git(workspace, "log", "-1", "--pretty=%s").stdout.strip()
    match = RELEASE_SUBJECT.match(subject)
    return match.group(1) if match else None


def ensure_full_history(workspace: Path) -> None:
    shallow = git(workspace, "rev-parse", "--is-shallow-repository").stdout.strip()
    if shallow == "true":
        raise ReleaseError(
            "the checkout is shallow: fetch the full history and tags "
            "(fetch-depth: 0) before the release action"
        )


def tag_exists(workspace: Path, version: str) -> bool:
    result = git(
        workspace,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/tags/{version}",
        check=False,
    )
    return result.returncode == 0


def recovery_notes(workspace: Path, config: Path, version: str) -> str:
    """Best-effort notes for a release commit found at HEAD."""
    try:
        if tag_exists(workspace, version):
            return git_cliff(workspace, config, "--latest", "--strip", "header")
        return git_cliff(
            workspace, config, "--tag", version, "--unreleased", "--strip", "header"
        )
    except ReleaseError as error:
        warning(f"release notes unavailable for {version}: {error}")
        return ""


def prepare() -> int:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    prefix = os.environ.get("RELEASE_TAG_PREFIX", "v")
    dry_run = parse_bool(os.environ.get("RELEASE_DRY_RUN", "false"), "dry-run")
    version_rules = parse_file_rules(
        os.environ.get("RELEASE_VERSION_FILES", ""), "version-files"
    )
    build_rules = parse_file_rules(
        os.environ.get("RELEASE_BUILD_NUMBER_FILES", ""), "build-number-files"
    )
    config, bundled = resolve_cliff_config(
        workspace, os.environ.get("RELEASE_CLIFF_CONFIG", "")
    )
    ensure_full_history(workspace)

    pending = pending_release(workspace)
    if pending is not None:
        notice(f"Recoverable release commit detected at HEAD: {pending}")
        write_text(notes_path(), recovery_notes(workspace, config, pending))
        write_outputs(
            {
                "released": "true",
                "pending": "true",
                "publish": "false" if dry_run else "true",
                "version": pending,
                "build-number": "",
            }
        )
        return 0

    nothing = {
        "released": "false",
        "pending": "false",
        "publish": "false",
        "version": "",
        "build-number": "",
    }
    last = last_tag(workspace, prefix)
    # A caller-provided cliff config may define another release policy: leave
    # the decision to git-cliff, as git-cliff-bump does.
    if bundled and not commits_require_release(workspace, last):
        notice(
            f"Only non-releasing commits since {last or 'repository start'}: no release."
        )
        write_outputs(nothing)
        return 0

    version = normalise_version(
        git_cliff(workspace, config, "--bumped-version"), last, prefix
    )
    if version is None:
        notice("No version bump computed: no release.")
        write_outputs(nothing)
        return 0
    git(workspace, "check-ref-format", f"refs/tags/{version}")

    changelog = workspace / "CHANGELOG.md"
    if changelog.is_file():
        git_cliff(
            workspace,
            config,
            "--tag",
            version,
            "--unreleased",
            "--prepend",
            "CHANGELOG.md",
        )
    else:
        git_cliff(workspace, config, "--tag", version, "--output", "CHANGELOG.md")
    notes = git_cliff(
        workspace, config, "--tag", version, "--unreleased", "--strip", "header"
    )
    write_text(notes_path(), notes)

    patch_version_files(workspace, version_rules, bare_version(version, prefix))
    build_number = bump_build_number(workspace, build_rules)

    print(f"Next version: {version}")
    if build_number is not None:
        print(f"Next build number: {build_number}")
    print("Files changed by the release:")
    print(git(workspace, "status", "--short").stdout.rstrip() or "(none)")
    if dry_run:
        notice(f"DRY RUN: would have released {version}")
        print("----- CHANGELOG.md (first lines) -----")
        print("\n".join(read_text(changelog).splitlines()[:DRY_RUN_CHANGELOG_LINES]))
        print("----- release notes -----")
        print(notes)
    write_outputs(
        {
            "released": "true",
            "pending": "false",
            "publish": "false" if dry_run else "true",
            "version": version,
            "build-number": "" if build_number is None else str(build_number),
        }
    )
    return 0


# --- publish -----------------------------------------------------------------


def git_auth_env(server_url: str, token: str) -> dict[str, str]:
    """Authenticate git over HTTPS with the job token only for this process.

    The empty first value resets any header persisted by the checkout, so the
    forge never receives two Authorization headers.
    """
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    key = f"http.{server_url.rstrip('/')}/.extraheader"
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": key,
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": key,
        "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {basic}",
    }


def remote_tag_commit(workspace: Path, version: str, auth: dict[str, str]) -> str:
    """Commit a remote tag points to, peeled; empty when the tag is absent."""
    listing = git(
        workspace,
        "ls-remote",
        "origin",
        f"refs/tags/{version}",
        f"refs/tags/{version}^{{}}",
        env=auth,
    ).stdout
    direct = ""
    for line in listing.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.endswith("^{}"):
            return sha
        if ref == f"refs/tags/{version}":
            direct = sha
    return direct


def api_request(
    method: str, url: str, token: str, payload: dict | None = None
) -> tuple[int, bytes]:
    if not url.startswith(("https://", "http://")):
        raise ReleaseError("the forge API URL must be an http(s) URL")
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise ReleaseError(f"forge API unreachable: {error.reason}") from error


def release_exists(api_url: str, repository: str, version: str, token: str) -> bool:
    tag = urllib.parse.quote(version, safe="")
    status, _ = api_request(
        "GET", f"{api_url}/repos/{repository}/releases/tags/{tag}", token
    )
    if status == 200:
        return True
    if status == 404:
        return False
    raise ReleaseError(f"forge API returned HTTP {status} while reading the release")


def ensure_release(
    api_url: str, repository: str, version: str, notes: str, token: str
) -> None:
    if release_exists(api_url, repository, version, token):
        notice(f"Release {version} already exists")
        return
    status, _ = api_request(
        "POST",
        f"{api_url}/repos/{repository}/releases",
        token,
        {
            "tag_name": version,
            "name": version,
            "body": notes,
            "draft": False,
            "prerelease": False,
        },
    )
    if status in {200, 201}:
        notice(f"Release {version} created")
        return
    # A concurrent run may have created it between the read and the write.
    if release_exists(api_url, repository, version, token):
        notice(f"Release {version} was created by a concurrent run")
        return
    raise ReleaseError(f"forge API returned HTTP {status} while creating the release")


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ReleaseError(f"{name} is required")
    return value


def publish() -> int:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    if parse_bool(os.environ.get("RELEASE_DRY_RUN", "false"), "dry-run"):
        notice("DRY RUN: nothing is published")
        return 0
    token = required_env("RELEASE_TOKEN")
    version = required_env("RELEASE_VERSION")
    pending = parse_bool(os.environ.get("RELEASE_PENDING", "false"), "pending")
    server_url = required_env("GITHUB_SERVER_URL")
    api_url = required_env("GITHUB_API_URL").rstrip("/")
    repository = required_env("GITHUB_REPOSITORY")
    ref = required_env("GITHUB_REF")
    if not ref.startswith("refs/heads/"):
        raise ReleaseError(f"a release is only cut from a branch, not from {ref}")
    git(workspace, "check-ref-format", f"refs/tags/{version}")
    if version.startswith("-"):
        raise ReleaseError(f"invalid version {version!r}")
    auth = git_auth_env(server_url, token)
    host = urllib.parse.urlsplit(server_url).hostname or "localhost"
    identity = (
        "-c",
        f"user.name={BOT_NAME}",
        "-c",
        f"user.email={BOT_NAME}@noreply.{host}",
    )

    if not pending:
        git(workspace, "add", "-A")
        if git(workspace, "diff", "--cached", "--quiet", check=False).returncode == 0:
            warning("no changes staged: releasing the current HEAD")
        else:
            git(
                workspace,
                *identity,
                "commit",
                "-m",
                f"chore(release): {version} {SKIP_MARKER}",
            )
        pushed = git(workspace, "push", "origin", f"HEAD:{ref}", env=auth, check=False)
        if pushed.returncode != 0:
            raise ReleaseError(
                f"push refused: {ref} moved since checkout or the token cannot "
                "write; no tag or release was created"
            )

    head = git(workspace, "rev-parse", "HEAD").stdout.strip()
    if tag_exists(workspace, version):
        local = git(
            workspace, "rev-list", "-n", "1", f"refs/tags/{version}"
        ).stdout.strip()
        if local != head:
            raise ReleaseError(
                f"local tag {version} points to {local}, expected {head}"
            )
    else:
        git(workspace, *identity, "tag", "-a", version, "-m", f"Release {version}")

    remote = remote_tag_commit(workspace, version, auth)
    if not remote:
        pushed = git(
            workspace, "push", "origin", f"refs/tags/{version}", env=auth, check=False
        )
        if pushed.returncode != 0:
            # A concurrent run may have pushed the same tag first.
            remote = remote_tag_commit(workspace, version, auth)
            if not remote:
                raise ReleaseError(f"could not push tag {version}")
    if remote and remote != head:
        raise ReleaseError(f"remote tag {version} points to {remote}, expected {head}")
    if remote:
        notice(f"Remote tag {version} already exists at the expected commit")

    notes = read_text(notes_path()) if notes_path().is_file() else ""
    ensure_release(api_url, repository, version, notes, token)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish"))
    arguments = parser.parse_args(argv)
    try:
        return prepare() if arguments.command == "prepare" else publish()
    except ReleaseError as error:
        print(f"::error::{error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
