"""Derive the single-job mutation time budget from the exact PR scope.

The central Stryker runner uses the command test runner: every mutant replays
the consumer's whole test command, so the duration grows with the number of
mutants, not with the number of tests. A fixed limit cancels any sizeable Node
PR, and a cancelled job reads as a missing verdict. The mutant count is only
known after Stryker instruments the sources, so the budget is derived from the
lines it is told to mutate (the same ranges ``mutation-node.sh`` passes to
Stryker), with the floor and ceiling of the campaign shard budget.

Other engines keep the floor, which is their previous job limit.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mutation_shard_budget import (  # noqa: E402
    MINIMUM_TIMEOUT_MINUTES,
    shard_timeout_minutes,
)

NODE_SOURCE = "workflow-ci-node"
DEFAULT_PACE_SECONDS = 40
DEFAULT_SETUP_MINUTES = 15
_NODE_FILE = re.compile(r"\.(js|jsx|ts|tsx)$")
_NODE_EXCLUDED = re.compile(
    r"(^|/)(test|tests|__tests__|node_modules|dist|build)/|\.(test|spec)\.(js|jsx|ts|tsx)$"
)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def _file_lines(repo: Path, head: str, path: str) -> int:
    return len(_git(repo, "show", f"{head}:{path}").splitlines())


def node_scope_lines(
    repo: Path, base: str, head: str, directory: str, depth: str
) -> int:
    """Count the lines Stryker mutates for this exact scope."""
    if depth not in {"medium", "high"}:
        raise ValueError(f"Unsupported mutation depth: {depth}")
    changed = _git(
        repo,
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{base}...{head}",
        "--",
        directory or ".",
    ).splitlines()
    total = 0
    for path in changed:
        if not _NODE_FILE.search(path) or _NODE_EXCLUDED.search(path):
            continue
        if depth == "high":
            total += _file_lines(repo, head, path)
            continue
        diff = _git(repo, "diff", "--unified=0", f"{base}...{head}", "--", path)
        hunks = [
            int(count) if count else 1 for _, count in _HUNK.findall(diff)
        ]
        mutated = sum(count for count in hunks if count > 0)
        # Pure deletions make the runner mutate the whole file.
        total += mutated if mutated else _file_lines(repo, head, path)
    return total


def run_budget_minutes(lines: int, pace_seconds: int, setup_minutes: int) -> int:
    """Return the mutation step budget for one job, within the shard bounds."""
    return shard_timeout_minutes(lines, 1, pace_seconds, setup_minutes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--directory", default=".")
    parser.add_argument("--depth", default="medium")
    args = parser.parse_args(argv)
    if args.source != NODE_SOURCE:
        print(f"time-budget={MINIMUM_TIMEOUT_MINUTES}")
        print("scope-lines=")
        return 0
    try:
        pace = int(os.environ.get("MUTATION_NODE_PACE_SECONDS") or DEFAULT_PACE_SECONDS)
        setup = int(
            os.environ.get("MUTATION_NODE_SETUP_MINUTES") or DEFAULT_SETUP_MINUTES
        )
        lines = node_scope_lines(
            Path(args.repo), args.base, args.head, args.directory, args.depth
        )
        minutes = run_budget_minutes(lines, pace, setup)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"::error::Invalid mutation time budget inputs: {error}", file=sys.stderr)
        return 1
    print(f"time-budget={minutes}")
    print(f"scope-lines={lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
