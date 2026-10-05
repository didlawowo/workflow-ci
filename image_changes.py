"""Detect image-impacting changes between two release tags; never guess a base."""

import os
import re
import subprocess
from pathlib import Path


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def release_tags(prefix):
    pattern = re.compile(re.escape(prefix) + r"[0-9]+\.[0-9]+\.[0-9]+")
    return [tag for tag in git("tag").splitlines() if pattern.fullmatch(tag)]


def resolve_tags(current, previous, prefix):
    if git("rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("Full history and tags required (fetch-depth: 0)")
    tags = release_tags(prefix)
    if current not in tags:
        raise ValueError("Current tag must be an existing stable release tag")
    head = git("rev-parse", f"refs/tags/{current}^{{commit}}")
    if not previous:
        matches = [f"--match={tag}" for tag in tags if tag != current]
        if not matches:
            raise ValueError("No previous release tag; supply previous-tag explicitly")
        previous = git(
            "describe", "--tags", "--first-parent", "--abbrev=0", *matches, f"{head}^"
        )
        base = git("rev-parse", f"refs/tags/{previous}^{{commit}}")
        aliases = set(git("tag", "--points-at", base).splitlines()) & set(tags)
        if len(aliases) != 1:
            raise ValueError(
                "Ambiguous previous release; supply previous-tag explicitly"
            )
    if previous not in tags or previous == current:
        raise ValueError("Previous tag must be a distinct existing release tag")
    base = git("rev-parse", f"refs/tags/{previous}^{{commit}}")
    if base == head:
        raise ValueError("Release tags must point to different commits")
    subprocess.run(["git", "merge-base", "--is-ancestor", base, head], check=True)
    return previous, base, head


def paths(value):
    patterns = [line.strip() for line in value.splitlines() if line.strip()]
    if any(
        pattern.startswith(("/", ":", "!")) or ".." in pattern.split("/")
        for pattern in patterns
    ):
        raise ValueError(
            "Use repository-relative Git glob patterns, without pathspec magic"
        )
    return patterns


def app_changed(base, head, includes, excludes):
    if not includes:
        raise ValueError("app-paths must not be empty")
    pathspec = [f":(glob){p}" for p in includes]
    pathspec.extend(f":(glob,exclude){p}" for p in excludes)
    result = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "--no-ext-diff",
            "--no-renames",
            "--ignore-submodules=none",
            base,
            head,
            "--",
            *pathspec,
        ]
    )
    if result.returncode not in (0, 1):
        raise ValueError("Git comparison failed")
    return result.returncode == 1


def main():
    previous, base, head = resolve_tags(
        os.environ["CURRENT_TAG"], os.environ["PREVIOUS_TAG"], os.environ["TAG_PREFIX"]
    )
    changed = app_changed(
        base, head, paths(os.environ["APP_PATHS"]), paths(os.environ["NON_IMAGE_PATHS"])
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"app-changed={str(changed).lower()}\nprevious-tag={previous}\n")
    print(
        f"Compared {previous}..{os.environ['CURRENT_TAG']}: app-changed={str(changed).lower()}"
    )


if __name__ == "__main__":
    main()
