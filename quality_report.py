#!/usr/bin/env python3
"""Build a trusted, machine-readable pull-request quality report."""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

COMMENT_MARKER = "<!-- workflow-ci:quality-report:v1 -->"
STATE_PREFIX = "<!-- workflow-ci:quality-state:"
SUSPICIOUS_PREFIXES = (
    ".github/workflows/",
    ".forgejo/workflows/",
    ".ci/",
)
SUSPICIOUS_NAMES = {
    "pyproject.toml",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    ".coveragerc",
    "jest.config.js",
    "jest.config.ts",
    "vitest.config.js",
    "vitest.config.ts",
    "stryker.conf.json",
}


def _paths(patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        for value in glob.glob(pattern, recursive=True):
            path = Path(value)
            if path.is_file() and path not in found:
                found.append(path)
    return found


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_junit(patterns: list[str]) -> dict[str, Any]:
    total = failures = errors = skipped = 0
    duration = 0.0
    files = _paths(patterns)

    for path in files:
        root = ET.parse(path).getroot()
        suites: list[ET.Element]
        if root.tag == "testsuite":
            suites = [root]
        elif root.tag == "testsuites" and "tests" in root.attrib:
            suites = [root]
        else:
            suites = list(root.findall("./testsuite"))

        for suite in suites:
            total += int(_number(suite.attrib.get("tests")))
            failures += int(_number(suite.attrib.get("failures")))
            errors += int(_number(suite.attrib.get("errors")))
            skipped += int(
                _number(suite.attrib.get("skipped", suite.attrib.get("disabled", 0)))
            )
            duration += _number(suite.attrib.get("time"))

    passed = max(total - failures - errors - skipped, 0)
    failed = failures + errors
    pass_rate = (passed / total * 100.0) if total else None
    return {
        "files": [str(path) for path in files],
        "total": total,
        "passed": passed,
        "failed": failed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "duration_seconds": round(duration, 3),
        "pass_rate": round(pass_rate, 1) if pass_rate is not None else None,
        "available": bool(files),
    }


def fallback_tests(
    tests: dict[str, Any],
    total: str | None,
    failed: str | None,
    skipped: str | None,
    status: str | None,
) -> dict[str, Any]:
    """Use trusted action outputs when a framework has no JUnit reporter."""
    if tests.get("available"):
        return tests
    if not status and total is None:
        return tests

    total_n = int(total) if total and total.isdigit() else None
    failed_n = int(failed) if failed and failed.isdigit() else (0 if status == "success" else None)
    skipped_n = int(skipped) if skipped and skipped.isdigit() else 0
    passed_n = None
    pass_rate = None
    if total_n is not None and failed_n is not None:
        passed_n = max(total_n - failed_n - skipped_n, 0)
        pass_rate = (passed_n / total_n * 100.0) if total_n else None

    return {
        "files": [],
        "total": total_n,
        "passed": passed_n,
        "failed": failed_n,
        "failures": failed_n,
        "errors": 0,
        "skipped": skipped_n,
        "duration_seconds": None,
        "pass_rate": round(pass_rate, 1) if pass_rate is not None else None,
        "available": True,
        "status": status or ("success" if failed_n == 0 else "failure"),
        "source": "trusted-action-output",
    }


def parse_coverage(patterns: list[str]) -> dict[str, Any]:
    files = _paths(patterns)
    if not files:
        return {"available": False, "percentage": None, "file": None}

    root = ET.parse(files[0]).getroot()
    line_rate = root.attrib.get("line-rate")
    if line_rate is None:
        lines_valid = _number(root.attrib.get("lines-valid"))
        lines_covered = _number(root.attrib.get("lines-covered"))
        percentage = (lines_covered / lines_valid * 100.0) if lines_valid else None
    else:
        percentage = _number(line_rate) * 100.0

    return {
        "available": percentage is not None,
        "percentage": round(percentage, 1) if percentage is not None else None,
        "file": str(files[0]),
    }


def _mutation_value(data: dict[str, Any], aliases: tuple[str, ...]) -> int | None:
    candidates = [data]
    for key in ("stats", "summary", "results", "mutations"):
        nested = data.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for alias in aliases:
            value = candidate.get(alias)
            if isinstance(value, (int, float)):
                return int(value)
    return None


def fallback_coverage(
    coverage: dict[str, Any], percentage: str | None
) -> dict[str, Any]:
    """Use the trusted test action percentage when no Cobertura XML exists."""
    if coverage.get("available") or percentage in (None, ""):
        return coverage
    try:
        value = float(percentage)
    except ValueError:
        return coverage
    return {
        "available": True,
        "percentage": round(value, 1),
        "file": None,
        "source": "trusted-action-output",
    }


def parse_mutation(path: str | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {
            "available": False,
            "file": path,
            "total": None,
            "killed": None,
            "survived": None,
            "timeouts": None,
            "suspicious": None,
            "score": None,
        }

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("mutation report must be a JSON object")

    killed = _mutation_value(
        data, ("killed", "killed_mutants", "mutants_killed")
    )
    survived = _mutation_value(
        data,
        (
            "survived",
            "surviving",
            "survived_mutants",
            "mutants_survived",
            "mutants_lived",
        ),
    )
    timeouts = _mutation_value(data, ("timeouts", "timeout", "timed_out"))
    suspicious = _mutation_value(data, ("suspicious", "suspicious_mutants"))
    total = _mutation_value(
        data, ("total", "total_mutants", "mutants", "mutants_total")
    )
    score_raw = None
    for key in ("mutation_score", "score", "mutationScore"):
        if isinstance(data.get(key), (int, float)):
            score_raw = float(data[key])
            break

    measured = [value for value in (killed, survived, timeouts, suspicious) if value is not None]
    if total is None and len(measured) == 4:
        total = sum(measured)
    denominator = sum(
        value or 0 for value in (killed, survived, timeouts, suspicious)
    )
    score = score_raw
    if score is None and killed is not None and denominator:
        score = killed / denominator * 100.0
    if score is not None and score <= 1.0:
        score *= 100.0

    return {
        "available": True,
        "file": path,
        "total": total,
        "killed": killed,
        "survived": survived,
        "timeouts": timeouts,
        "suspicious": suspicious,
        "score": round(score, 1) if score is not None else None,
    }


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    parts = lowered.split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or "test" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _is_non_production(path: str) -> bool:
    lowered = path.lower()
    return (
        _is_test_path(path)
        or lowered.startswith("docs/")
        or lowered.startswith(".github/")
        or lowered.startswith(".forgejo/")
        or lowered.startswith(".ci/")
        or lowered.endswith(".md")
    )


def _is_suspicious(path: str) -> bool:
    lowered = path.lower()
    name = lowered.split("/")[-1]
    return (
        any(lowered.startswith(prefix) for prefix in SUSPICIOUS_PREFIXES)
        or name in SUSPICIOUS_NAMES
        or "coverage" in name
        or "mutation" in name
    )


def diff_stats(base: str | None, head: str | None) -> dict[str, Any]:
    if not base or not head:
        return {
            "available": False,
            "files": 0,
            "additions": 0,
            "deletions": 0,
            "production_additions": 0,
            "test_additions": 0,
            "suspicious_files": [],
        }

    result = subprocess.run(
        ["git", "diff", "--numstat", f"{base}...{head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    additions = deletions = production_additions = test_additions = 0
    files = 0
    suspicious: list[str] = []

    for raw in result.stdout.splitlines():
        if not raw.strip():
            continue
        added, deleted, path = raw.split("\t", 2)
        files += 1
        add_n = int(added) if added.isdigit() else 0
        del_n = int(deleted) if deleted.isdigit() else 0
        additions += add_n
        deletions += del_n
        if _is_test_path(path):
            test_additions += add_n
        elif not _is_non_production(path):
            production_additions += add_n
        if _is_suspicious(path):
            suspicious.append(path)

    return {
        "available": True,
        "files": files,
        "additions": additions,
        "deletions": deletions,
        "production_additions": production_additions,
        "test_additions": test_additions,
        "suspicious_files": suspicious,
    }


def _api_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def ci_history(
    api_url: str | None,
    repository: str | None,
    pr_number: int | None,
    head_ref: str | None,
    token: str | None,
) -> dict[str, Any]:
    result = {
        "available": False,
        "commits": None,
        "workflow_runs": None,
        "failed_runs": None,
        "successful_runs": None,
        "reruns": None,
        "current_run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
    }
    if not all((api_url, repository, pr_number, head_ref, token)):
        return result

    encoded_branch = urllib.parse.quote(str(head_ref), safe="")
    commits_url = f"{api_url}/repos/{repository}/pulls/{pr_number}/commits?per_page=100"
    runs_url = (
        f"{api_url}/repos/{repository}/actions/runs"
        f"?event=pull_request&branch={encoded_branch}&per_page=100"
    )
    try:
        commits = _api_json(commits_url, str(token))
        runs_payload = _api_json(runs_url, str(token))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return result

    runs = runs_payload.get("workflow_runs", []) if isinstance(runs_payload, dict) else []
    matching = []
    for run in runs:
        prs = run.get("pull_requests") or []
        if not prs or any(pr.get("number") == pr_number for pr in prs if isinstance(pr, dict)):
            matching.append(run)

    conclusions = [run.get("conclusion") for run in matching if run.get("status") == "completed"]
    result.update(
        {
            "available": True,
            "commits": len(commits) if isinstance(commits, list) else None,
            "workflow_runs": len(matching),
            "failed_runs": sum(1 for value in conclusions if value == "failure"),
            "successful_runs": sum(1 for value in conclusions if value == "success"),
            "reruns": sum(max(int(run.get("run_attempt", 1)) - 1, 0) for run in matching),
        }
    )
    return result


def _state_marker(report: dict[str, Any]) -> str:
    raw = json.dumps(report, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    return f"{STATE_PREFIX}{encoded} -->"


def _extract_state(body: str) -> dict[str, Any] | None:
    start = body.find(STATE_PREFIX)
    if start < 0:
        return None
    start += len(STATE_PREFIX)
    end = body.find(" -->", start)
    if end < 0:
        return None
    try:
        raw = base64.urlsafe_b64decode(body[start:end].encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def merge_reports(existing: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return current

    existing_identity = existing.get("identity")
    current_identity = current.get("identity")
    existing_head = (
        existing_identity.get("head_sha")
        if isinstance(existing_identity, dict)
        else None
    )
    current_head = (
        current_identity.get("head_sha")
        if isinstance(current_identity, dict)
        else None
    )

    # Evidence is only mergeable when it belongs to the same PR head. Carrying
    # coverage or mutation results across commits makes an old successful run
    # look authoritative for code that was never measured.
    if current_head and existing_head != current_head:
        return current

    merged = dict(existing)
    merged["schema_version"] = current.get("schema_version", existing.get("schema_version", 1))
    if current_identity is not None:
        merged["identity"] = current_identity
    for section in ("tests", "coverage", "mutation", "diff", "history"):
        candidate = current.get(section)
        previous = existing.get(section)
        if isinstance(candidate, dict) and candidate.get("available"):
            merged[section] = candidate
        elif previous is not None:
            merged[section] = previous
        elif candidate is not None:
            merged[section] = candidate
    return merged


def render_markdown(report: dict[str, Any]) -> str:
    tests = report["tests"]
    coverage = report["coverage"]
    mutation = report["mutation"]
    diff = report["diff"]
    history = report["history"]

    test_summary = "N/A"
    if tests["available"]:
        if tests.get("total") is None:
            test_summary = f"{tests.get('status', 'unknown')} · count unavailable"
        else:
            test_summary = (
                f"{tests['passed']} passed · {tests['failed']} failed · "
                f"{tests['skipped']} skipped / {tests['total']} total "
                f"({tests['pass_rate']}%)"
            )

    coverage_summary = (
        f"{coverage['percentage']}%" if coverage["available"] else "N/A"
    )
    mutation_summary = "not measured"
    if mutation["available"]:
        mutation_summary = (
            f"{mutation['score'] if mutation['score'] is not None else 'N/A'}% · "
            f"{mutation['killed'] if mutation['killed'] is not None else '?'} killed · "
            f"{mutation['survived'] if mutation['survived'] is not None else '?'} survived · "
            f"{mutation['timeouts'] if mutation['timeouts'] is not None else '?'} timeout"
        )

    lines = [
        COMMENT_MARKER,
        _state_marker(report),
        "## CI Quality Evidence",
        "",
        "| Signal | Evidence |",
        "|---|---|",
        f"| Tests | {test_summary} |",
        f"| Coverage | {coverage_summary} |",
        f"| Mutation | {mutation_summary} |",
    ]
    if diff["available"]:
        lines.append(
            f"| Diff | {diff['files']} files · +{diff['additions']} / -{diff['deletions']} · "
            f"+{diff['production_additions']} production · +{diff['test_additions']} tests |"
        )
    else:
        lines.append("| Diff | N/A |")

    if history["available"]:
        lines.append(
            f"| CI history | {history['commits']} commits · {history['workflow_runs']} runs · "
            f"{history['failed_runs']} failed · {history['reruns']} reruns |"
        )
    else:
        lines.append(
            f"| CI history | current run attempt {history['current_run_attempt']} |"
        )

    suspicious = diff.get("suspicious_files") or []
    if suspicious:
        lines.extend(
            [
                "",
                "### ⚠️ Quality-policy files changed",
                "",
                *[f"- `{path}`" for path in suspicious],
                "",
                "These changes deserve explicit review because they can alter what CI measures.",
            ]
        )

    lines.extend(
        [
            "",
            "<sub>Generated from machine-readable JUnit/Cobertura/mutation output and git/API metadata. "
            "The PR author does not supply the displayed counters.</sub>",
        ]
    )
    return "\n".join(lines) + "\n"


def upsert_comment(
    report: dict[str, Any],
    api_url: str,
    repository: str,
    pr_number: int,
    token: str,
) -> None:
    comments_url = f"{api_url}/repos/{repository}/issues/{pr_number}/comments?per_page=100"
    comments = _api_json(comments_url, token)
    existing_id = None
    existing_report = None
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict) and COMMENT_MARKER in str(comment.get("body", "")):
                existing_id = comment.get("id")
                existing_report = _extract_state(str(comment.get("body", "")))
                break

    merged = merge_reports(existing_report, report)
    markdown = render_markdown(merged)
    payload = json.dumps({"body": markdown}).encode("utf-8")
    if existing_id:
        url = f"{api_url}/repos/{repository}/issues/comments/{existing_id}"
        method = "PATCH"
    else:
        url = f"{api_url}/repos/{repository}/issues/{pr_number}/comments"
        method = "POST"
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            print(
                f"::warning::PR quality comment could not be written (HTTP {exc.code}); "
                "GITHUB_STEP_SUMMARY and the JSON artifact remain authoritative."
            )
            return
        raise

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", action="append", default=[])
    parser.add_argument("--coverage", action="append", default=[])
    parser.add_argument("--mutation")
    parser.add_argument("--coverage-percentage")
    parser.add_argument("--tests-total")
    parser.add_argument("--tests-failed")
    parser.add_argument("--tests-skipped")
    parser.add_argument("--test-status")
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--output-json", default=".quality/quality-report.json")
    parser.add_argument("--output-markdown", default=".quality/quality-report.md")
    parser.add_argument("--comment", action="store_true")
    args = parser.parse_args()

    pr_number_raw = os.environ.get("QUALITY_PR_NUMBER")
    pr_number = int(pr_number_raw) if pr_number_raw and pr_number_raw.isdigit() else None

    tests = fallback_tests(
        parse_junit(args.junit),
        args.tests_total,
        args.tests_failed,
        args.tests_skipped,
        args.test_status,
    )
    coverage = fallback_coverage(
        parse_coverage(args.coverage),
        args.coverage_percentage,
    )
    report = {
        "schema_version": 1,
        "identity": {
            "base_sha": args.base,
            "head_sha": args.head,
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
        },
        "tests": tests,
        "coverage": coverage,
        "mutation": parse_mutation(args.mutation),
        "diff": diff_stats(args.base, args.head),
        "history": ci_history(
            os.environ.get("GITHUB_API_URL"),
            os.environ.get("GITHUB_REPOSITORY"),
            pr_number,
            os.environ.get("QUALITY_HEAD_REF"),
            os.environ.get("GITHUB_TOKEN"),
        ),
    }
    markdown = render_markdown(report)

    json_path = Path(args.output_json)
    markdown_path = Path(args.output_markdown)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown)

    if args.comment:
        token = os.environ.get("GITHUB_TOKEN")
        api_url = os.environ.get("GITHUB_API_URL")
        repository = os.environ.get("GITHUB_REPOSITORY")
        if token and api_url and repository and pr_number:
            upsert_comment(report, api_url, repository, pr_number, token)

    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
