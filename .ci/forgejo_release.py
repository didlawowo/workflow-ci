#!/usr/bin/env python3
"""Release engine behind the Forgejo release action.

Same version policy as ``.github/workflows/release.yml``: conventional commits
since the last tag decide the next version (``release_commit_gate.py`` then
git-cliff), ``CHANGELOG.md`` and the declared version files are rewritten, and
a release commit, a tag and a forge release are published.

``prepare`` never needs a token: it computes everything, records a plan and
leaves no tag behind. ``publish`` executes the plan; every step first checks
what already exists, so a later run completes what an interrupted one left:
a release commit pushed without its tag is tagged and released before any new
version is computed, and a tag pushed without its forge release gets it.
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
SEMVER = r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?"
BOT_NAME = "forgejo-actions"
CHANGELOG = "CHANGELOG.md"
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
        command = next((arg for arg in args if not arg.startswith("-")), "command")
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"git {command} failed: {detail}")
    return result


def identity_args() -> tuple[str, ...]:
    server = os.environ.get("GITHUB_SERVER_URL", "")
    host = urllib.parse.urlsplit(server).hostname or "localhost"
    return (
        "-c",
        f"user.name={BOT_NAME}",
        "-c",
        f"user.email={BOT_NAME}@noreply.{host}",
    )


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


def state_path() -> Path:
    base = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
    directory = Path(base) / "forgejo-release"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "plan.json"


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

FilePlan = list[tuple[Path, "re.Pattern[str]", str]]


def parse_file_rules(raw: str, name: str) -> list[tuple[str, str]]:
    """Parse ``path:regex`` lines. The regex is kept verbatim: leading spaces
    after an anchor are significant."""
    rules: list[tuple[str, str]] = []
    for raw_line in raw.splitlines():
        line = raw_line.rstrip("\r")
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


def load_text(path: Path) -> tuple[str, list[str]]:
    """Return the text with LF line endings and the original ending of each line.

    Line-anchored regexes must behave the same whatever the line endings; the
    original endings are restored line by line on write.
    """
    with open(path, encoding="utf-8", newline="") as handle:
        parts = re.split(r"(\r\n|\n)", handle.read())
    return "\n".join(parts[0::2]), parts[1::2]


def store_text(path: Path, content: str, endings: list[str]) -> None:
    lines = content.split("\n")
    if len(lines) != len(endings) + 1:
        raise ReleaseError(f"a replacement changed the number of lines of {path.name}")
    restored = "".join(line + ending for line, ending in zip(lines, endings))
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(restored + lines[-1])


def replace_groups(source: str, matches: list[re.Match[str]], value: str) -> str:
    result = source
    for match in sorted(matches, key=lambda item: item.start(1), reverse=True):
        start, end = match.span(1)
        result = result[:start] + value + result[end:]
    return result


def plan_version_files(workspace: Path, rules: list[tuple[str, str]]) -> FilePlan:
    """Validate the version files. A missing file is skipped with a warning,
    as in release.yml; a regex that does not match fails."""
    planned: FilePlan = []
    for relative, regex in rules:
        target = resolve_in_workspace(workspace, relative)
        pattern = compile_rule(regex, relative)
        if not target.is_file():
            warning(f"version-file '{relative}' not found, skipping")
            continue
        if not pattern.search(load_text(target)[0]):
            raise ReleaseError(f"regex did not match in {relative}")
        planned.append((target, pattern, relative))
    return planned


def apply_version_files(planned: FilePlan, version: str) -> None:
    """Write ``version`` into the first match of each file, as release.yml does."""
    for target, pattern, relative in planned:
        source, crlf = load_text(target)
        match = pattern.search(source)
        if match is None:
            raise ReleaseError(f"regex did not match in {relative}")
        store_text(target, replace_groups(source, [match], version), crlf)


def patch_version_files(
    workspace: Path, rules: list[tuple[str, str]], version: str
) -> list[str]:
    planned = plan_version_files(workspace, rules)
    apply_version_files(planned, version)
    return [relative for _, _, relative in planned]


def plan_build_number(
    workspace: Path, rules: list[tuple[str, str]]
) -> tuple[FilePlan, int] | None:
    """Validate the build-number files and compute the next build number.

    Every match in every file must be an integer. The highest value wins:
    all of them become ``highest + 1`` so the number never goes backwards
    anywhere. A missing file, a regex that does not match or a non-integer
    capture fails.
    """
    if not rules:
        return None
    planned: FilePlan = []
    current: list[int] = []
    for relative, regex in rules:
        target = resolve_in_workspace(workspace, relative)
        pattern = compile_rule(regex, relative)
        if not target.is_file():
            raise ReleaseError(f"build-number file '{relative}' not found")
        matches = list(pattern.finditer(load_text(target)[0]))
        if not matches:
            raise ReleaseError(f"build-number regex did not match in {relative}")
        for match in matches:
            captured = match.group(1) or ""
            if not re.fullmatch(r"[0-9]+", captured):
                raise ReleaseError(
                    f"build number in {relative} is not an integer: {captured!r}"
                )
            current.append(int(captured))
        planned.append((target, pattern, relative))
    return planned, max(current) + 1


def apply_build_number(planned: FilePlan, following: int) -> None:
    for target, pattern, _ in planned:
        source, crlf = load_text(target)
        matches = list(pattern.finditer(source))
        store_text(target, replace_groups(source, matches, str(following)), crlf)


def bump_build_number(workspace: Path, rules: list[tuple[str, str]]) -> int | None:
    plan = plan_build_number(workspace, rules)
    if plan is None:
        return None
    planned, following = plan
    apply_build_number(planned, following)
    return following


# --- prepare -----------------------------------------------------------------


def ensure_full_history(workspace: Path) -> None:
    shallow = git(workspace, "rev-parse", "--is-shallow-repository").stdout.strip()
    if shallow == "true":
        raise ReleaseError(
            "the checkout is shallow: fetch the full history and tags "
            "(fetch-depth: 0) before the release action"
        )


def tag_commit(workspace: Path, version: str) -> str:
    """Commit a local tag points to; empty when the tag does not exist."""
    result = git(
        workspace,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/tags/{version}^{{commit}}",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def announced_version(subject: str, prefix: str) -> str | None:
    """Version announced by a release commit subject, if it is one of ours.

    The value comes from a commit message: only a semantic version carrying
    the requested prefix is accepted as a tag name.
    """
    match = RELEASE_SUBJECT.match(subject.strip())
    if match is None:
        return None
    version = match.group(1)
    if re.fullmatch(re.escape(prefix) + SEMVER, version) is None:
        return None
    return version


def version_key(version: str, prefix: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(re.escape(prefix) + r"([0-9]+)\.([0-9]+)\.([0-9]+).*", version)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def valid_tag_name(workspace: Path, version: str) -> bool:
    result = git(workspace, "check-ref-format", f"refs/tags/{version}", check=False)
    return result.returncode == 0 and not version.startswith("-")


def orphan_release_commits(
    workspace: Path, last: str, prefix: str
) -> list[tuple[str, str]]:
    """Release commits that reached the branch without their tag, oldest first.

    With a previous tag, release commits after it are candidates. Without any
    tag, only HEAD is: an imported history must not be re-tagged wholesale.
    A candidate that cannot be tagged safely (name refused by git, tag already
    used by another commit, version not above the previous one) is ignored
    with a warning: it must never block the following releases.
    """
    if last:
        listing = git(
            workspace, "log", "--reverse", "--format=%H%x1f%s", f"{last}..HEAD"
        ).stdout
    else:
        listing = git(workspace, "log", "-1", "--format=%H%x1f%s").stdout
    floor = version_key(last, prefix) if last else None
    orphans: list[tuple[str, str]] = []
    for line in listing.splitlines():
        sha, _, subject = line.partition("\x1f")
        version = announced_version(subject, prefix)
        if version is None:
            continue
        if not valid_tag_name(workspace, version):
            warning(f"release commit {sha[:12]} ignored: {version!r} is not a tag name")
            continue
        existing = tag_commit(workspace, version)
        if existing == sha:
            continue
        if existing:
            warning(
                f"release commit {sha[:12]} ignored: tag {version} already "
                f"points to {existing[:12]}"
            )
            continue
        key = version_key(version, prefix)
        if floor is not None and key is not None and key <= floor:
            warning(
                f"release commit {sha[:12]} ignored: {version} is not above "
                "the previous version"
            )
            continue
        orphans.append((sha, version))
        floor = key or floor
    return orphans


def last_release_cut_here(workspace: Path, last: str, prefix: str) -> bool:
    """Whether the last tag sits on a release commit announcing that version.

    Such a tag was pushed by this action; its forge release may still be
    missing if the run stopped between the two. A hand-made tag never
    qualifies.
    """
    if not last:
        return False
    subject = git(
        workspace, "log", "-1", "--format=%s", f"refs/tags/{last}^{{commit}}"
    ).stdout
    return announced_version(subject, prefix) == last


def create_local_tag(workspace: Path, version: str, commit: str) -> None:
    git(workspace, "check-ref-format", f"refs/tags/{version}")
    existing = tag_commit(workspace, version)
    if existing and existing != commit:
        raise ReleaseError(
            f"tag {version} points to {existing}, expected {commit}: "
            "resolve by hand before releasing"
        )
    if not existing:
        git(
            workspace,
            *identity_args(),
            "tag",
            "-a",
            version,
            commit,
            "-m",
            f"Release {version}",
        )


def recover_orphans(
    workspace: Path, config: Path, prefix: str, created: list[str]
) -> list[dict[str, str]]:
    """Tag locally every orphan release commit and return what to publish.

    The local tags only serve the computation: ``created`` lists them so the
    caller removes them, and ``publish`` creates the real ones.
    """
    previous = last_tag(workspace, prefix)
    recovered: list[dict[str, str]] = []
    for commit, version in orphan_release_commits(workspace, previous, prefix):
        notice(f"Release commit without tag found: {version} at {commit[:12]}")
        create_local_tag(workspace, version, commit)
        created.append(version)
        revision_range = f"{previous}..{commit}" if previous else commit
        try:
            notes = git_cliff(workspace, config, "--strip", "header", revision_range)
        except ReleaseError as error:
            warning(f"release notes unavailable for {version}: {error}")
            notes = ""
        recovered.append({"version": version, "commit": commit, "notes": notes})
        previous = version
    return recovered


def ensure_not_ignored(workspace: Path, files: list[str]) -> None:
    """Fail before any write when a file of the release commit is git-ignored."""
    for relative in files:
        ignored = git(workspace, "check-ignore", "-q", "--", relative, check=False)
        if ignored.returncode == 0:
            raise ReleaseError(
                f"{relative} is ignored by git and cannot enter the release commit"
            )


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def prepare() -> int:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    created: list[str] = []
    try:
        return prepare_plan(workspace, created)
    finally:
        # prepare leaves no tag behind, dry-run or not: publish creates them.
        for version in created:
            git(workspace, "tag", "-d", version, check=False)


def prepare_plan(workspace: Path, created: list[str]) -> int:
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
    head = git(workspace, "rev-parse", "HEAD").stdout.strip()

    # A tag pushed by an earlier run may still lack its forge release.
    previous = last_tag(workspace, prefix)
    verify: dict[str, str] | None = None
    if last_release_cut_here(workspace, previous, prefix):
        before = git(
            workspace,
            "describe",
            "--tags",
            "--abbrev=0",
            "--match",
            tag_pattern(prefix),
            f"refs/tags/{previous}^",
            check=False,
        )
        verify = {
            "version": previous,
            "previous": before.stdout.strip() if before.returncode == 0 else "",
        }

    recovered = recover_orphans(workspace, config, prefix, created)
    last = last_tag(workspace, prefix)
    release: dict[str, object] | None = None
    build_number: int | None = None

    # A caller-provided cliff config may define another release policy: leave
    # the decision to git-cliff, as git-cliff-bump does.
    version = None
    if not bundled or commits_require_release(workspace, last):
        version = normalise_version(
            git_cliff(workspace, config, "--bumped-version"), last, prefix
        )
    if version is None:
        notice(
            f"No releasable commit since {last or 'repository start'}: no new release."
        )
    else:
        if not valid_tag_name(workspace, version):
            raise ReleaseError(f"computed version {version!r} is not a tag name")
        if tag_commit(workspace, version):
            raise ReleaseError(f"tag {version} already exists")
        # Validate every declared file before anything is written.
        version_plan = plan_version_files(workspace, version_rules)
        build_plan = plan_build_number(workspace, build_rules)
        files = [CHANGELOG, *(relative for _, _, relative in version_plan)]
        if build_plan is not None:
            files.extend(relative for _, _, relative in build_plan[0])
        files = unique(files)
        ensure_not_ignored(workspace, files)

        if (workspace / CHANGELOG).is_file():
            git_cliff(
                workspace,
                config,
                "--tag",
                version,
                "--unreleased",
                "--prepend",
                CHANGELOG,
            )
        else:
            git_cliff(workspace, config, "--tag", version, "--output", CHANGELOG)
        notes = git_cliff(
            workspace, config, "--tag", version, "--unreleased", "--strip", "header"
        )
        apply_version_files(version_plan, bare_version(version, prefix))
        if build_plan is not None:
            build_files, build_number = build_plan
            apply_build_number(build_files, build_number)
        release = {"version": version, "notes": notes, "files": files}

        print(f"Next version: {version}")
        if build_number is not None:
            print(f"Next build number: {build_number}")
        print("Files changed by the release:")
        print(git(workspace, "status", "--short").stdout.rstrip() or "(none)")
        if dry_run:
            notice(f"DRY RUN: would have released {version}")
            print("----- CHANGELOG.md (first lines) -----")
            changelog = load_text(workspace / CHANGELOG)[0].splitlines()
            print("\n".join(changelog[:DRY_RUN_CHANGELOG_LINES]))
            print("----- release notes -----")
            print(notes)

    if dry_run and recovered:
        names = ", ".join(item["version"] for item in recovered)
        notice(f"DRY RUN: would have tagged and released {names}")

    plan = {
        "head": head,
        "config": str(config),
        "verify": verify,
        "recovered": recovered,
        "release": release,
    }
    state_path().write_text(json.dumps(plan), encoding="utf-8")

    # ``released`` and ``version`` describe the checked-out branch tip: a new
    # version, or the version of a release commit completed at HEAD itself.
    completed_at_head = next(
        (item["version"] for item in recovered if item["commit"] == head), ""
    )
    announced = version or completed_at_head
    pending = verify is not None or bool(recovered) or release is not None
    write_outputs(
        {
            "released": "true" if announced else "false",
            "publish": "true" if pending and not dry_run else "false",
            "version": announced,
            "build-number": "" if build_number is None else str(build_number),
        }
    )
    return 0


# --- publish -----------------------------------------------------------------


def git_auth_env(server_url: str, token: str) -> dict[str, str]:
    """Authenticate git over HTTP(S) with the job token for this process only.

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


def publish_tag(
    workspace: Path, version: str, commit: str, auth: dict[str, str]
) -> None:
    """Make sure the remote tag exists at ``commit``; never move a tag."""
    create_local_tag(workspace, version, commit)
    remote = remote_tag_commit(workspace, version, auth)
    if not remote:
        pushed = git(
            workspace, "push", "origin", f"refs/tags/{version}", env=auth, check=False
        )
        if pushed.returncode == 0:
            return
        # A concurrent run may have pushed the same tag first.
        remote = remote_tag_commit(workspace, version, auth)
        if not remote:
            raise ReleaseError(f"could not push tag {version}")
    if remote != commit:
        raise ReleaseError(
            f"remote tag {version} points to {remote}, expected {commit}"
        )
    notice(f"Remote tag {version} already exists at the expected commit")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the Authorization header must stay on the forge."""

    def redirect_request(self, *args, **kwargs):
        return None


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
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:  # nosec B310
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


def load_plan() -> dict:
    path = state_path()
    if not path.is_file():
        raise ReleaseError("no release plan found: the prepare step must run first")
    return json.loads(path.read_text(encoding="utf-8"))


def complete_last_release(
    workspace: Path, plan: dict, api_url: str, repository: str, token: str
) -> None:
    """Create the forge release of the last tag when an earlier run left it out.

    Best effort: nothing new depends on it and the next run tries again, so a
    failure here is a warning, never a failed run.
    """
    verify = plan["verify"]
    if verify is None:
        return
    version, previous = verify["version"], verify["previous"]
    try:
        if release_exists(api_url, repository, version, token):
            return
        notice(f"Tag {version} has no forge release yet: creating it")
        # Explicit range: git-cliff --latest would pick the newest tag of the
        # whole repository, whatever its branch or prefix.
        revision_range = f"{previous}..{version}" if previous else version
        try:
            notes = git_cliff(
                workspace, Path(plan["config"]), "--strip", "header", revision_range
            )
        except ReleaseError as error:
            warning(f"release notes unavailable for {version}: {error}")
            notes = ""
        ensure_release(api_url, repository, version, notes, token)
    except ReleaseError as error:
        warning(f"could not complete the forge release of {version}: {error}")


def publish() -> int:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    if parse_bool(os.environ.get("RELEASE_DRY_RUN", "false"), "dry-run"):
        notice("DRY RUN: nothing is published")
        return 0
    plan = load_plan()
    token = required_env("RELEASE_TOKEN")
    server_url = required_env("GITHUB_SERVER_URL")
    api_url = required_env("GITHUB_API_URL").rstrip("/")
    repository = required_env("GITHUB_REPOSITORY")
    if api_url.startswith("http://") and not server_url.startswith("http://"):
        raise ReleaseError("the forge API URL must use https like the forge itself")
    if git(workspace, "rev-parse", "HEAD").stdout.strip() != plan["head"]:
        raise ReleaseError("the release plan was computed for another commit")

    complete_last_release(workspace, plan, api_url, repository, token)
    release = plan["release"]
    if not plan["recovered"] and release is None:
        return 0

    ref = required_env("GITHUB_REF")
    if not ref.startswith("refs/heads/"):
        raise ReleaseError(f"a release is only cut from a branch, not from {ref}")
    auth = git_auth_env(server_url, token)

    for item in plan["recovered"]:
        publish_tag(workspace, item["version"], item["commit"], auth)
        ensure_release(api_url, repository, item["version"], item["notes"], token)

    if release is None:
        return 0
    version = release["version"]

    # Only the changelog and the declared files: whatever else the calling
    # job left in the workspace never reaches the released branch.
    git(workspace, "add", "--", *release["files"])
    if git(workspace, "diff", "--cached", "--quiet", check=False).returncode == 0:
        warning("no changes staged: releasing the current HEAD")
    else:
        git(
            workspace,
            *identity_args(),
            "commit",
            "-m",
            f"chore(release): {version} {SKIP_MARKER}",
        )
    pushed = git(workspace, "push", "origin", f"HEAD:{ref}", env=auth, check=False)
    if pushed.returncode != 0:
        raise ReleaseError(
            f"push refused: {ref} moved since checkout or the token cannot write; "
            "no tag or release was created. Run the workflow again from the "
            "branch tip rather than re-running this job."
        )
    head = git(workspace, "rev-parse", "HEAD").stdout.strip()
    publish_tag(workspace, version, head, auth)
    ensure_release(api_url, repository, version, release["notes"], token)
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
