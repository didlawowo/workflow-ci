"""Decide whether a pull request only changes declared documentation paths.

Fail closed: an empty pattern list, a non pull_request event, an empty diff or
any Git error keeps the full verification (documentation-only=false).
"""

import os
import subprocess
from pathlib import Path

from image_changes import app_changed, git, paths


# Never documentation, whatever the consumer patterns say (for example `**`):
# CI and policy configuration, dependency manifests and lockfiles.
PROTECTED_PATHS = [
    ".github/**",
    ".ci/**",
    ".forgejo/**",
    ".gitea/**",
    ".gitmodules",
    ".gitattributes",
    "sonar-project.properties",
    "**/.pre-commit-config.yaml",
    "**/pyproject.toml",
    "**/uv.lock",
    "**/requirements*.txt",
    "**/package.json",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/go.mod",
    "**/go.sum",
    "**/Cargo.toml",
    "**/Cargo.lock",
]
# Symlinks and submodules can make code look like a documentation path.
LINK_MODES = {"120000", "160000"}


def changes_links(base, head):
    raw = git("diff", "--raw", "--no-renames", "--no-ext-diff", base, head)
    for line in raw.splitlines():
        modes = line.split("\t", 1)[0].split()[:2]
        if any(mode.lstrip(":") in LINK_MODES for mode in modes):
            return True
    return False


def documentation_only(base, head, patterns):
    if not patterns:
        return False
    merge_base = git("merge-base", base, head)
    if not app_changed(merge_base, head, ["**"], []):
        return False
    if app_changed(merge_base, head, PROTECTED_PATHS, []):
        return False
    if changes_links(merge_base, head):
        return False
    return not app_changed(merge_base, head, ["**"], patterns)


def decide(environ):
    patterns = paths(environ.get("DOCUMENTATION_PATHS", ""))
    if not patterns or environ.get("EVENT_NAME") != "pull_request":
        return False
    base, head = environ.get("BASE_SHA", ""), environ.get("HEAD_SHA", "")
    if not base or not head:
        return False
    try:
        return documentation_only(base, head, patterns)
    except (subprocess.CalledProcessError, ValueError) as error:
        print(
            f"::warning::Documentation scope unavailable, full verification kept: {error}"
        )
        return False


def main():
    result = str(decide(os.environ)).lower()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"documentation-only={result}\n")
    print(f"documentation-only={result}")


if __name__ == "__main__":
    main()
