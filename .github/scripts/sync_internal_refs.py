#!/usr/bin/env python3
"""Synchronize internal workflow-ci action/workflow refs with .workflow-ci-version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = ROOT / ".workflow-ci-version"
SEARCH_ROOTS = (
    ROOT / ".github" / "actions",
    ROOT / ".github" / "workflows",
    ROOT / "templates",
)
EXTRA_FILES = (ROOT / "README.md",)
PATTERN = re.compile(
    r"(didlawowo/workflow-ci/(?:\.github/(?:actions|workflows)/)[^\s\"'\x60]+)@"
    r"([A-Za-z0-9._/-]+)"
)


def expected_ref() -> str:
    ref = VERSION_FILE.read_text(encoding="utf-8").strip()
    is_release = re.fullmatch(r"v\d+\.\d+\.\d+", ref)
    is_commit = re.fullmatch(r"[0-9a-f]{40}", ref)
    if not (is_release or is_commit):
        raise SystemExit(f"invalid workflow-ci immutable ref contract: {ref!r}")
    return ref


def sync_workflow_ref_defaults(source: str, ref: str) -> str:
    lines = source.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() != "workflow-ci-ref:":
            index += 1
            continue

        indent = len(line) - len(line.lstrip())
        cursor = index + 1
        while cursor < len(lines):
            child = lines[cursor]
            stripped = child.strip()
            child_indent = len(child) - len(child.lstrip())
            if stripped and child_indent <= indent:
                break
            if stripped.startswith("default:"):
                prefix = child[: len(child) - len(child.lstrip())]
                newline = "\n" if child.endswith("\n") else ""
                lines[cursor] = f'{prefix}default: "{ref}"{newline}'
                break
            cursor += 1
        index = cursor

    return "".join(lines)


def iter_files() -> list[Path]:
    files: set[Path] = set(EXTRA_FILES)
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".yml", ".yaml", ".md"}:
                files.add(path)
    return sorted(files)


def sync(check: bool) -> int:
    version = expected_ref()
    stale: list[str] = []
    changed: list[str] = []

    for path in iter_files():
        source = path.read_text(encoding="utf-8")
        updated = PATTERN.sub(lambda match: f"{match.group(1)}@{version}", source)
        if path in {
            ROOT / ".github" / "workflows" / "quality-evidence.yml",
            ROOT / ".github" / "workflows" / "release.yml",
        }:
            updated = sync_workflow_ref_defaults(updated, version)
        if updated == source:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if check:
            stale.append(relative)
        else:
            path.write_text(updated, encoding="utf-8")
            changed.append(relative)

    if stale:
        print(
            "Internal workflow-ci refs are out of sync with "
            f"{VERSION_FILE.name} ({version}):"
        )
        for relative in stale:
            print(f"  - {relative}")
        return 1

    for relative in changed:
        print(f"updated {relative} -> {version}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return sync(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
