#!/usr/bin/env python3
"""Classify whether conventional commits require a semantic release.

The default workflow-ci release policy is intentionally narrower than the
git-cliff changelog grouping: docs/tests/ci/chore/style may appear in a
changelog when bundled with a real release, but they must never create a
release by themselves.
"""

from __future__ import annotations

import re
import sys

_NON_RELEASING_TYPES = {"chore", "ci", "docs", "style", "test"}
_HEADER = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9-]*)(?:\([^\r\n)]*\))?(?P<breaking>!)?:[ \t]+",
)
_BREAKING = re.compile(r"(?m)^BREAKING(?: |-)?CHANGE:[ \t]*")


def message_requires_release(message: str) -> bool:
    """Return True when one conventional commit should trigger a release."""
    if _BREAKING.search(message):
        return True

    first_line = message.splitlines()[0] if message.splitlines() else ""
    match = _HEADER.match(first_line)
    if match is None:
        # Default cliff.toml uses filter_unconventional=true.
        return False

    if match.group("breaking"):
        return True

    return match.group("type").casefold() not in _NON_RELEASING_TYPES


def any_message_requires_release(messages: list[str]) -> bool:
    return any(message_requires_release(message) for message in messages)


def _messages_from_stdin(raw: str) -> list[str]:
    # The action uses ASCII record separator so multi-line commit bodies remain
    # intact and BREAKING CHANGE footers can be inspected.
    return [item.strip() for item in raw.split("\x1e") if item.strip()]


def main() -> int:
    messages = _messages_from_stdin(sys.stdin.read())
    releasable = any_message_requires_release(messages)
    print("true" if releasable else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
