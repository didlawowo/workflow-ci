#!/usr/bin/env python3
"""Fail-closed integrity check for CI policy files in pull requests."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PROTECTED_PATHS = (
    ".github/workflows/trusted-quality-evidence.yml",
    ".github/workflows/trusted-policy-integrity.yml",
    ".github/workflows/mutation-policy.yml",
    ".ci/mutation.sh",
    ".ci/check_mutation_report.py",
    "sonar-project.properties",
)

WRAPPER = ".github/workflows/trusted-quality-evidence.yml"

ACTION_REF_RE = re.compile(
    r"(didlawowo/workflow-ci/[^@\s\"']+@)"
    r"(v\d+\.\d+\.\d+)"
)
INPUT_REF_RE = re.compile(
    r"(?m)^(\s*workflow-ci-ref:\s*[\"']?)"
    r"(v\d+\.\d+\.\d+)"
    r"([\"']?\s*(?:#.*)?)$"
)
SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _read(root: Path, relative: str) -> str | None:
    path = root / relative
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _versions(text: str) -> list[str]:
    refs = [match.group(2) for match in ACTION_REF_RE.finditer(text)]
    refs.extend(match.group(2) for match in INPUT_REF_RE.finditer(text))
    return refs


def _normalize_wrapper(text: str) -> str:
    text = ACTION_REF_RE.sub(r"\1__WORKFLOW_CI_VERSION__", text)
    return INPUT_REF_RE.sub(r"\1__WORKFLOW_CI_VERSION__\3", text)


def _semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid workflow-ci semver: {value}")
    return tuple(int(part) for part in match.groups())


def safe_wrapper_upgrade(base_text: str, candidate_text: str) -> tuple[bool, str]:
    """Allow only a consistent forward semver ref bump and no other wrapper edits."""
    base_refs = _versions(base_text)
    candidate_refs = _versions(candidate_text)
    if not base_refs or not candidate_refs:
        return False, "trusted wrapper must contain workflow-ci semantic refs"
    if len(set(base_refs)) != 1 or len(set(candidate_refs)) != 1:
        return False, "trusted wrapper must use one consistent workflow-ci version"

    old = base_refs[0]
    new = candidate_refs[0]
    if _semver(new) <= _semver(old):
        return False, f"workflow-ci migration must move forward ({old} -> {new})"
    if _normalize_wrapper(base_text) != _normalize_wrapper(candidate_text):
        return False, "trusted wrapper changed beyond workflow-ci semantic refs"
    return True, f"allowed workflow-ci migration {old} -> {new}"


def evaluate(base: Path, candidate: Path) -> dict[str, Any]:
    changed: list[str] = []
    allowed: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []

    for relative in PROTECTED_PATHS:
        before = _read(base, relative)
        after = _read(candidate, relative)
        if before == after:
            continue
        changed.append(relative)

        if relative == WRAPPER and before is not None and after is not None:
            ok, reason = safe_wrapper_upgrade(before, after)
            if ok:
                allowed.append({"path": relative, "reason": reason})
                continue
            violations.append({"path": relative, "reason": reason})
            continue

        if before is None:
            reason = "protected policy file was introduced by the candidate"
        elif after is None:
            reason = "protected policy file was removed by the candidate"
        else:
            reason = "protected policy file changed outside the trusted base"
        violations.append({"path": relative, "reason": reason})

    return {
        "schema_version": 1,
        "status": "fail" if violations else "pass",
        "changed": changed,
        "allowed": allowed,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    report = evaluate(args.base.resolve(), args.candidate.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))

    if report["violations"]:
        for violation in report["violations"]:
            print(
                f"::error file={violation['path']}::Trusted policy integrity: "
                f"{violation['reason']}"
            )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
