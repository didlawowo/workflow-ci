#!/usr/bin/env python3
"""Label-driven mutation testing policy shared by GitHub and Forgejo."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COMPLEXITY_LABELS = ("complexity:low", "complexity:medium", "complexity:high")
MUTATION_REQUIRED_LABELS = ("complexity:medium", "complexity:high")
COMPLEXITY_ORDER = {label: index for index, label in enumerate(COMPLEXITY_LABELS)}
AUTO_HIGH_CHANGED_CODE_LINES = 500
AUTO_HIGH_CHANGED_CODE_FILES = 15
DEPENDABOT_LOGIN = "dependabot[bot]"
COMMENT_MARKER = "<!-- github-manager:mutation-policy:v1 -->"

_DEPENDENCY_ONLY_FILENAMES = {
    "go.mod",
    "go.sum",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
}
_DEPENDENCY_ONLY_PATTERNS = (
    re.compile(r"^requirements(?:[-_.][^/]+)?\.txt$"),
    re.compile(r"^\.github/(?:dependabot\.ya?ml|workflows/[^/]+\.ya?ml)$"),
)

_LINKED_ISSUE_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?|refs?)\s*:?\s*#(\d+)\b"
)

MUTATION_GUIDANCE = f"""{COMMENT_MARKER}
### Mutation testing required

This issue is classified as medium/high complexity. Mutation testing is mandatory before the pull request can pass the policy gate.

- Add or strengthen automated tests for the changed production code.
- Add `.ci/mutation.sh` if the repository does not already have one.
- The script must run a real mutation engine and exit non-zero when relevant mutants survive.
- Recommended engines: `mutmut` (Python), `gremlins` (Go), `Stryker` (JS/TS), `cargo-mutants` (Rust), PIT (JVM), Infection (PHP).
- Do not silence, exclude, or mock away changed production code merely to make the mutation gate pass.
- Equivalent or unreachable mutants may be documented explicitly; all other mutants in changed code must be killed.
- Link the pull request to this issue with `Closes #<issue>` or `Fixes #<issue>` so the gate can inherit these labels.
"""


def label_names(labels: object) -> set[str]:
    """Normalize GitHub/Forgejo label payloads into a set of names."""
    if not isinstance(labels, list):
        return set()

    names: set[str] = set()
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"])
    return names


def linked_issue_numbers(text: str | None) -> tuple[int, ...]:
    """Return same-repository issue references from a PR body, preserving order."""
    if not text:
        return ()

    seen: set[int] = set()
    numbers: list[int] = []
    for match in _LINKED_ISSUE_RE.finditer(text):
        number = int(match.group(1))
        if number not in seen:
            seen.add(number)
            numbers.append(number)
    return tuple(numbers)


def mutation_reasons(
    event: dict,
    fetch_issue: Callable[[int], dict],
) -> tuple[str, ...]:
    """Return the highest explicit complexity inherited from the PR/issues.

    Priority labels are intentionally ignored: urgency is not technical risk.
    """

    pull_request = event.get("pull_request") or {}
    labels = label_names(pull_request.get("labels"))

    for number in linked_issue_numbers(pull_request.get("body")):
        issue = fetch_issue(number)
        labels.update(label_names(issue.get("labels")))

    complexity = [label for label in COMPLEXITY_LABELS if label in labels]
    if not complexity:
        return ()
    highest = max(complexity, key=COMPLEXITY_ORDER.__getitem__)
    return (highest,)


def _is_dependency_only_path(path: str) -> bool:
    normalized = path.strip("/")
    if not normalized:
        return False
    if Path(normalized).name in _DEPENDENCY_ONLY_FILENAMES:
        return True
    return any(pattern.fullmatch(normalized) for pattern in _DEPENDENCY_ONLY_PATTERNS)


def _pull_request_file_entries(event: dict) -> list[dict] | None:
    """Return complete PR file metadata, or None when completeness is unknown."""
    pull_request = event.get("pull_request") or {}
    number = pull_request.get("number") or event.get("number")
    if not isinstance(number, int):
        return None

    changed_count = pull_request.get("changed_files")
    if isinstance(changed_count, int) and changed_count < 0:
        return None

    entries: list[dict] = []
    page = 1
    while True:
        suffix = "per_page=100" if page == 1 else f"per_page=100&page={page}"
        payload = _api_request("GET", f"pulls/{number}/files?{suffix}") or []
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            return None

        page_entries: list[dict] = []
        for item in payload:
            filename = str(item.get("filename") or "")
            if not filename:
                return None
            page_entries.append(
                {
                    "filename": filename,
                    "additions": max(0, int(item.get("additions") or 0)),
                    "deletions": max(0, int(item.get("deletions") or 0)),
                }
            )
        entries.extend(page_entries)

        if isinstance(changed_count, int) and len(entries) >= changed_count:
            break
        if len(payload) < 100:
            break
        page += 1
        if page > 100:
            return None

    if isinstance(changed_count, int) and len(entries) != changed_count:
        return None
    return entries


def _pull_request_files(event: dict) -> list[str] | None:
    entries = _pull_request_file_entries(event)
    if entries is None:
        return None
    return [str(entry["filename"]) for entry in entries]


def _is_python_production_path(path: str) -> bool:
    """Treat Python outside obvious test/docs/generated trees as production code."""
    normalized = path.strip("/")
    if not normalized.endswith(".py"):
        return False

    parts = Path(normalized).parts
    if not parts:
        return False

    lowered = {part.lower() for part in parts}
    if lowered.intersection(
        {
            "tests",
            "test",
            "docs",
            "examples",
            "example",
            ".venv",
            "venv",
            "build",
            "dist",
        }
    ):
        return False

    name = Path(normalized).name.lower()
    if name.startswith("test_") or name.endswith("_test.py"):
        return False

    return True


def _is_go_production_path(path: str) -> bool:
    normalized = path.strip("/")
    if not normalized.endswith(".go") or normalized.endswith("_test.go"):
        return False
    parts = Path(normalized).parts
    return bool(parts) and parts[0] not in {"vendor", "testdata", "tests"}


def _is_supported_production_path(path: str) -> bool:
    return _is_python_production_path(path) or _is_go_production_path(path)


def production_change_reasons(event: dict) -> tuple[str, ...]:
    """Return supported production-language reasons without deciding complexity."""
    files = _pull_request_files(event)
    if files is None:
        return ("changed-files-unverified",)
    reasons: list[str] = []
    if any(_is_python_production_path(path) for path in files):
        reasons.append("python-production-change")
    if any(_is_go_production_path(path) for path in files):
        reasons.append("go-production-change")
    return tuple(reasons)


def automatic_complexity_reasons(event: dict) -> tuple[str, ...]:
    """Force high complexity for large supported production-code changes."""
    entries = _pull_request_file_entries(event)
    if entries is None:
        return ("changed-files-unverified",)

    production = [
        entry for entry in entries if _is_supported_production_path(str(entry["filename"]))
    ]
    changed_lines = sum(
        int(entry["additions"]) + int(entry["deletions"]) for entry in production
    )
    reasons: list[str] = []
    if len(production) > AUTO_HIGH_CHANGED_CODE_FILES:
        reasons.append(f"auto-high:files>{AUTO_HIGH_CHANGED_CODE_FILES}")
    if changed_lines > AUTO_HIGH_CHANGED_CODE_LINES:
        reasons.append(f"auto-high:lines>{AUTO_HIGH_CHANGED_CODE_LINES}")
    return tuple(reasons)


def _dependabot_dependency_only(event: dict) -> bool:
    """Return True only for Dependabot PRs changing dependency metadata/workflows."""
    pull_request = event.get("pull_request") or {}
    user = pull_request.get("user") or {}
    sender = event.get("sender") or {}
    login = str(user.get("login") or sender.get("login") or "")
    if login != DEPENDABOT_LOGIN:
        return False

    # An explicit high-risk PR label always overrides the automation exemption.
    direct_labels = label_names(pull_request.get("labels"))
    if direct_labels.intersection(MUTATION_REQUIRED_LABELS):
        return False

    filenames = _pull_request_files(event)
    if not filenames:
        return False

    return all(_is_dependency_only_path(path) for path in filenames)


def _api_request(method: str, path: str, payload: dict | None = None) -> object:
    base_url = os.environ["POLICY_API_URL"].rstrip("/")
    repository = os.environ["POLICY_REPOSITORY"]
    token = os.environ["POLICY_TOKEN"]
    provider = os.environ.get("POLICY_PROVIDER", "github").lower()

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if provider == "forgejo":
        headers["Authorization"] = f"token {token}"
    else:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"

    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{base_url}/repos/{repository}/{path.lstrip('/')}",
        data=data,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=20) as response:
        raw = response.read().decode()
    return json.loads(raw) if raw else None


def _load_event() -> dict:
    event_path = os.environ.get("POLICY_EVENT_PATH") or os.environ.get(
        "GITHUB_EVENT_PATH"
    ) or os.environ.get("FORGEJO_EVENT_PATH")
    if not event_path:
        raise RuntimeError("No workflow event path available")
    return json.loads(Path(event_path).read_text())


def _write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT") or os.environ.get("FORGEJO_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def notify(event: dict) -> int:
    """Post one idempotent mutation-policy comment when a high-risk label is added."""
    label = event.get("label") or {}
    label_name = label.get("name")
    if label_name not in MUTATION_REQUIRED_LABELS:
        print(f"Label {label_name!r} does not require mutation testing")
        return 0

    issue = event.get("issue") or {}
    number = issue.get("number") or event.get("number")
    if not number:
        raise RuntimeError("Issue event does not contain an issue number")

    comments = _api_request("GET", f"issues/{number}/comments") or []
    if isinstance(comments, list) and any(
        COMMENT_MARKER in str(comment.get("body", ""))
        for comment in comments
        if isinstance(comment, dict)
    ):
        print("Mutation policy comment already present")
        return 0

    _api_request("POST", f"issues/{number}/comments", {"body": MUTATION_GUIDANCE})
    print(f"Posted mutation policy guidance on issue #{number}")
    return 0


def classify(event: dict) -> int:
    """Expose whether the PR must run mutation testing.

    - explicit complexity:low skips mutation unless the change auto-promotes high;
    - explicit complexity:medium/high requires mutation;
    - no complexity label defaults to medium for supported production code;
    - priority labels never affect technical complexity;
    - large production changes auto-promote to high.
    """

    if _dependabot_dependency_only(event):
        reasons: tuple[str, ...] = ()
        print("Mutation testing skipped for dependency-only Dependabot PR")
    else:
        explicit = mutation_reasons(
            event,
            lambda number: _api_request("GET", f"issues/{number}") or {},
        )
        production = production_change_reasons(event)
        automatic = automatic_complexity_reasons(event)

        if "changed-files-unverified" in production or "changed-files-unverified" in automatic:
            reasons = ("changed-files-unverified",)
        elif automatic:
            reasons = ("complexity:high", *automatic, *production)
        elif explicit == ("complexity:low",):
            reasons = ()
        elif explicit:
            reasons = (*explicit, *production)
        elif production:
            reasons = ("complexity:medium(default)", *production)
        else:
            reasons = ()

    required = bool(reasons)
    _write_output("required", "true" if required else "false")
    _write_output("labels", ",".join(reasons))
    if required:
        print(f"Mutation testing required by: {', '.join(reasons)}")
    else:
        print("Mutation testing not required: low complexity or no supported production code change")
    return 0



def _open_pull_requests() -> list[dict]:
    payload = _api_request("GET", "pulls?state=open&per_page=100") or []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _mutation_runs_for_head(head_sha: str) -> list[dict]:
    provider = os.environ.get("POLICY_PROVIDER", "github").lower()
    params = {
        "event": "pull_request",
        "head_sha": head_sha,
        ("limit" if provider == "forgejo" else "per_page"): "100",
    }
    payload = _api_request("GET", f"actions/runs?{urlencode(params)}") or {}
    if not isinstance(payload, dict):
        return []
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        runs = payload.get("runs")
    return [item for item in (runs or []) if isinstance(item, dict)]


def _is_mutation_policy_run(run: dict, pr_number: int, head_sha: str) -> bool:
    if str(run.get("head_sha") or "") != head_sha:
        return False

    path = str(run.get("path") or "")
    name = str(run.get("name") or "")
    valid_workflow_paths = (
        ".github/workflows/mutation-policy.yml",
        ".forgejo/workflows/mutation-policy.yml",
        # Managed consumers call the reusable mutation gate through this wrapper.
        ".github/workflows/trusted-quality-evidence.yml",
    )
    if path and not path.endswith(valid_workflow_paths):
        return False
    if not path and name and name not in {
        "Mutation testing policy",
        "Trusted quality evidence",
    }:
        return False

    pull_requests = run.get("pull_requests")
    if isinstance(pull_requests, list) and pull_requests:
        numbers = {
            int(item["number"])
            for item in pull_requests
            if isinstance(item, dict) and str(item.get("number", "")).isdigit()
        }
        if numbers and pr_number not in numbers:
            return False
    return True


def refresh(event: dict) -> int:
    """Re-run the current-head mutation gate when a linked issue risk label changes."""
    label = event.get("label") or {}
    label_name = label.get("name")
    if label_name not in MUTATION_REQUIRED_LABELS:
        print(f"Label {label_name!r} does not affect mutation policy")
        return 0

    issue = event.get("issue") or {}
    issue_number = issue.get("number") or event.get("number")
    if not issue_number:
        raise RuntimeError("Issue event does not contain an issue number")
    issue_number = int(issue_number)

    refreshed = 0
    for pull_request in _open_pull_requests():
        pr_number = pull_request.get("number")
        head = pull_request.get("head") or {}
        head_sha = head.get("sha")
        if not isinstance(pr_number, int) or not isinstance(head_sha, str) or not head_sha:
            continue
        if issue_number not in linked_issue_numbers(pull_request.get("body")):
            continue

        candidates = [
            run
            for run in _mutation_runs_for_head(head_sha)
            if _is_mutation_policy_run(run, pr_number, head_sha)
            and str(run.get("status") or "").lower() == "completed"
        ]
        if not candidates:
            print(
                f"No completed mutation-policy run found for PR #{pr_number} "
                f"at {head_sha}; nothing to re-run"
            )
            continue

        run = max(candidates, key=lambda item: int(item.get("id") or 0))
        run_id = int(run["id"])
        _api_request("POST", f"actions/runs/{run_id}/rerun")
        refreshed += 1
        print(
            f"Re-ran mutation policy for PR #{pr_number} at {head_sha} "
            f"after {event.get('action', 'label')} of {label_name}"
        )

    print(f"Recomputed mutation policy for {refreshed} linked pull request(s)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("notify", "classify", "refresh"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    event = _load_event()
    if args.command == "notify":
        return notify(event)
    if args.command == "refresh":
        return refresh(event)
    return classify(event)


if __name__ == "__main__":
    raise SystemExit(main())
