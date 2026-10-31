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
    ".ci/mutation-equivalents.json",
    ".ci/mutation-plan.sh",
    ".ci/mutation-shard.sh",
    "sonar-project.properties",
)

WRAPPER = ".github/workflows/trusted-quality-evidence.yml"
SEMVER_WRAPPERS = {
    WRAPPER: "quality-evidence.yml",
    ".github/workflows/trusted-policy-integrity.yml": "policy-integrity.yml",
    ".github/workflows/mutation-policy.yml": "mutation-issue-policy.yml",
}
HIDDEN_WORKFLOW_GLOB = ".github/workflows/hidden-*-evidence.yml"
REGISTRY = Path(__file__).resolve().parents[2] / "hidden-evaluators" / "registry.json"

HIDDEN_REF_RE = re.compile(
    r"(?m)^(\s*uses:\s*didlawowo/workflow-ci/\.github/workflows/"
    r"hidden-evidence\.yml@)(v\d+\.\d+\.\d+)(\s*)$"
)
HIDDEN_EVALUATOR_RE = re.compile(r"(?m)^\s*evaluator:\s*([^\s#]+)\s*$")
HIDDEN_RUNNER_RE = re.compile(r"(?m)^\s*runner:\s*([^\s#]+)\s*$")

ACTION_REF_RE = re.compile(
    r"(?m)^(\s*uses:\s*didlawowo/workflow-ci/\.github/workflows/quality-evidence\.yml@)"
    r"(v\d+\.\d+\.\d+)(?=[ \t]*$)",
    re.MULTILINE,
)
# Match only the canonical generated step, not action-looking text embedded in
# a run/script block. No other action or mutable reference may be normalized.
CHECKOUT_REF_RE = re.compile(
    r"(?m)^(      - uses:[ \t]+didlawowo/workflow-ci/\.github/actions/checkout-source@)"
    r"(v\d+\.\d+\.\d+)([ \t]*)$"
)
INPUT_REF_RE = re.compile(
    r"(?m)^(\s*workflow-ci-ref:\s*[\"']?)"
    r"(v\d+\.\d+\.\d+)"
    r"([\"']?\s*(?:#.*)?)$"
)
SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

WORKFLOWS_DIR = ".github/workflows"
# Fail closed without a YAML parser, which the trusted checkout does not install:
# every line is scanned, so a label reached through a list, an anchor, a matrix or
# an input default is caught. Only full-line comments and prose are skipped, and a
# label in a trailing comment is reported rather than risk hiding a real one.
PROSE_KEY_RE = re.compile(
    r"""^(\s*(?:-\s+)?)["']?(?:name|description|if|run|script|body)["']?\s*:(.*)$"""
)
BLOCK_SCALAR_RE = re.compile(r"^\s*[|>][+-]?\d?[+-]?\s*(?:#.*)?$")
# GitHub label shapes only, with a bounded suffix. The lookbehind keeps
# arc-runner-ubuntu-24.04 and paths such as scripts/macos-14 allowed.
HOSTED_LABEL_RE = re.compile(
    r"(?<![\w./-])(?:ubuntu|macos|windows)-(?:latest|slim|\d[\d.]{0,10})"
    r"(?:-[a-z0-9]{1,16}){0,2}(?![\w.-])",
    re.IGNORECASE,
)


def _read(root: Path, relative: str) -> str | None:
    path = root / relative
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _versions(text: str) -> list[str]:
    refs = [match.group(2) for match in ACTION_REF_RE.finditer(text)]
    refs.extend(match.group(2) for match in CHECKOUT_REF_RE.finditer(text))
    refs.extend(match.group(2) for match in INPUT_REF_RE.finditer(text))
    return refs


def _normalize_wrapper(text: str) -> str:
    text = ACTION_REF_RE.sub(r"\1__WORKFLOW_CI_VERSION__", text)
    text = CHECKOUT_REF_RE.sub(r"\1__WORKFLOW_CI_VERSION__\3", text)
    return INPUT_REF_RE.sub(r"\1__WORKFLOW_CI_VERSION__\3", text)


def _semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid workflow-ci semver: {value}")
    return tuple(int(part) for part in match.groups())


def safe_wrapper_upgrade(base_text: str, candidate_text: str) -> tuple[bool, str]:
    """Allow only a consistent forward semver ref bump and no other wrapper edits."""
    if (
        len(INPUT_REF_RE.findall(base_text)) > 1
        or len(INPUT_REF_RE.findall(candidate_text)) > 1
    ):
        return False, "trusted wrapper must contain at most one workflow-ci-ref input"
    base_refs = _versions(base_text)
    candidate_refs = _versions(candidate_text)
    if not base_refs or not candidate_refs:
        return False, "trusted wrapper must contain workflow-ci semantic refs"
    if len(set(candidate_refs)) != 1:
        return False, "trusted wrapper must use one consistent workflow-ci version"

    old = max(base_refs, key=_semver)
    new = candidate_refs[0]
    if _semver(new) <= _semver(old):
        return False, f"workflow-ci migration must move forward ({old} -> {new})"
    if _normalize_wrapper(base_text) != _normalize_wrapper(candidate_text):
        return False, "trusted wrapper changed beyond workflow-ci semantic refs"
    return True, f"allowed workflow-ci migration {old} -> {new}"


def _hidden_enrollment(text: str, repository: str) -> tuple[bool, str]:
    refs = [match.group(2) for match in HIDDEN_REF_RE.finditer(text)]
    evaluators = HIDDEN_EVALUATOR_RE.findall(text)
    runners = HIDDEN_RUNNER_RE.findall(text)
    if len(refs) != 1 or len(evaluators) != 1 or len(runners) != 1:
        return (
            False,
            "hidden enrollment must declare one semantic ref, evaluator and runner",
        )

    evaluator = evaluators[0]
    runner = runners[0]
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    spec = (registry.get("evaluators") or {}).get(evaluator)
    if not isinstance(spec, dict):
        return False, f"hidden evaluator is not registered: {evaluator}"
    if spec.get("repository") != repository:
        return False, f"hidden evaluator {evaluator} is not bound to {repository}"

    expected_runner = "arc-runner-" + repository.split("/", 1)[-1]
    if runner != expected_runner:
        return False, f"hidden runner must be {expected_runner}"
    return True, f"registered hidden enrollment {evaluator} at {refs[0]}"


def _normalize_hidden(text: str) -> str:
    return HIDDEN_REF_RE.sub(r"\1__WORKFLOW_CI_VERSION__\3", text)


def safe_hidden_upgrade(
    base_text: str, candidate_text: str, repository: str
) -> tuple[bool, str]:
    base_ok, base_reason = _hidden_enrollment(base_text, repository)
    if not base_ok:
        return False, "protected base hidden enrollment is invalid: " + base_reason
    candidate_ok, candidate_reason = _hidden_enrollment(candidate_text, repository)
    if not candidate_ok:
        return False, candidate_reason

    old = HIDDEN_REF_RE.search(base_text)
    new = HIDDEN_REF_RE.search(candidate_text)
    assert old and new
    old_version = old.group(2)
    new_version = new.group(2)
    if _semver(new_version) <= _semver(old_version):
        return (
            False,
            f"hidden workflow-ci migration must move forward ({old_version} -> {new_version})",
        )
    if _normalize_hidden(base_text) != _normalize_hidden(candidate_text):
        return False, "hidden enrollment changed beyond workflow-ci semantic ref"
    return True, f"allowed hidden workflow-ci migration {old_version} -> {new_version}"


def _hidden_paths(root: Path) -> set[str]:
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in workflows.glob("hidden-*-evidence.yml")
        if path.is_file()
    }


def _is_prose(value: str) -> bool:
    """Tell a sentence from a value that a matrix or an anchor could reuse."""
    value = re.split(r"[ \t]#", value, maxsplit=1)[0].strip()
    return " " in value and value[0] not in "&*[{"


def hosted_runner_labels(text: str) -> list[tuple[int, str]]:
    """Return the GitHub-hosted labels found outside prose, with their line."""
    found: list[tuple[int, str]] = []
    block_column: int | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if block_column is not None:
            indent = len(line) - len(line.lstrip())
            if not line.strip() or indent > block_column:
                continue
            block_column = None
        if line.lstrip().startswith("#"):
            continue

        prose = PROSE_KEY_RE.match(line)
        if prose and BLOCK_SCALAR_RE.match(prose.group(2)):
            block_column = len(prose.group(1))
            continue
        if prose and _is_prose(prose.group(2)):
            continue
        found.extend(
            (number, match.group(0)) for match in HOSTED_LABEL_RE.finditer(line)
        )
    return found


def _hosted_runner_violations(candidate: Path) -> list[dict[str, str]]:
    workflows = candidate / WORKFLOWS_DIR
    if not workflows.is_dir():
        return []
    root = candidate.resolve()
    violations: list[dict[str, str]] = []
    for path in sorted(workflows.iterdir()):
        if not path.is_file() or path.suffix not in {".yml", ".yaml"}:
            continue
        # A candidate link must not make the trusted job read outside its checkout.
        if not path.resolve().is_relative_to(root):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line, label in hosted_runner_labels(text):
            violations.append(
                {
                    "path": path.relative_to(candidate).as_posix(),
                    "reason": (
                        f"GitHub-hosted runner {label} at line {line} is forbidden; "
                        "use arc-runner-<repo>"
                    ),
                }
            )
    return violations


def evaluate(base: Path, candidate: Path, repository: str = "") -> dict[str, Any]:
    changed: list[str] = []
    allowed: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []

    hidden_paths = _hidden_paths(base) | _hidden_paths(candidate)
    for relative in sorted(hidden_paths):
        before = _read(base, relative)
        after = _read(candidate, relative)
        if before == after:
            continue
        changed.append(relative)

        if not repository:
            violations.append(
                {
                    "path": relative,
                    "reason": "repository identity is required to validate hidden enrollment",
                }
            )
            continue
        if before is None and after is not None:
            ok, reason = _hidden_enrollment(after, repository)
            if ok:
                allowed.append({"path": relative, "reason": reason})
            else:
                violations.append({"path": relative, "reason": reason})
            continue
        if before is not None and after is None:
            violations.append(
                {
                    "path": relative,
                    "reason": "protected hidden enrollment was removed by the candidate",
                }
            )
            continue
        assert before is not None and after is not None
        ok, reason = safe_hidden_upgrade(before, after, repository)
        if ok:
            allowed.append({"path": relative, "reason": reason})
        else:
            violations.append({"path": relative, "reason": reason})

    for relative in PROTECTED_PATHS:
        before = _read(base, relative)
        after = _read(candidate, relative)
        if before == after:
            continue
        changed.append(relative)

        if relative in SEMVER_WRAPPERS and before is not None and after is not None:
            expected = SEMVER_WRAPPERS[relative]
            # Match complete, unquoted reusable workflow references only. A branch,
            # prerelease suffix or substituted workflow must never be normalized.
            ref_pattern = re.compile(
                r"(?m)^(\s*uses:\s*didlawowo/workflow-ci/\.github/workflows/"
                + re.escape(expected)
                + r"@)(v\d+\.\d+\.\d+)([ \t]*)$"
            )
            before_matches = list(ref_pattern.finditer(before))
            after_matches = list(ref_pattern.finditer(after))
            if len(before_matches) != 1 or len(after_matches) != 1:
                ok, reason = (
                    False,
                    "protected wrapper must call its expected semantic workflow",
                )
            elif relative == WRAPPER:
                ok, reason = safe_wrapper_upgrade(before, after)
            elif ref_pattern.sub(r"\1__VERSION__\3", before) == ref_pattern.sub(
                r"\1__VERSION__\3", after
            ):
                old = before_matches[0].group(2)
                new = after_matches[0].group(2)
                ok = _semver(new) > _semver(old)
                reason = f"workflow-ci migration must move forward ({old} -> {new})"
                if ok:
                    reason = f"allowed workflow-ci migration {old} -> {new}"
            else:
                ok, reason = (
                    False,
                    "protected wrapper changed beyond workflow-ci semantic refs",
                )
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

    violations.extend(_hosted_runner_violations(candidate))

    return {
        "schema_version": 1,
        "status": "fail" if violations else "pass",
        "changed": changed,
        "allowed": allowed,
        "violations": violations,
    }


def _annotation_data(value: str) -> str:
    """Escape a value so a candidate file name cannot inject a workflow command."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _annotation_property(value: str) -> str:
    return _annotation_data(value).replace(":", "%3A").replace(",", "%2C")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()

    report = evaluate(args.base.resolve(), args.candidate.resolve(), args.repository)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))

    if report["violations"]:
        for violation in report["violations"]:
            print(
                f"::error file={_annotation_property(violation['path'])}::"
                f"Trusted policy integrity: {_annotation_data(violation['reason'])}"
            )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
