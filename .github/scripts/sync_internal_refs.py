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


def expected_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise SystemExit(f"invalid workflow-ci version contract: {version!r}")
    return version


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
    version = expected_version()
    stale: list[str] = []
    changed: list[str] = []

    for path in iter_files():
        source = path.read_text(encoding="utf-8")
        updated = PATTERN.sub(lambda match: f"{match.group(1)}@{version}", source)
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
