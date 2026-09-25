#!/usr/bin/env python3
"""Central runner for workflow-ci hidden evaluators."""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent
COMMON = ROOT / "common"
sys.path.insert(0, str(COMMON))

from report import fresh_seed, seed_id, write_replay_capsule, write_report  # noqa: E402


def load_registry() -> dict[str, Any]:
    value = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("hidden evaluator registry schema_version=1 required")
    evaluators = value.get("evaluators")
    if not isinstance(evaluators, dict):
        raise ValueError("hidden evaluator registry missing evaluators")
    return value


def load_spec(evaluator: str, repository: str) -> dict[str, Any]:
    registry = load_registry()
    spec = registry["evaluators"].get(evaluator)
    if not isinstance(spec, dict):
        raise ValueError(f"unknown hidden evaluator: {evaluator}")
    expected_repository = spec.get("repository")
    if expected_repository != repository:
        raise ValueError(
            f"evaluator {evaluator} is bound to {expected_repository}, not {repository}"
        )
    entrypoint = spec.get("entrypoint")
    paths = spec.get("paths")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise ValueError(f"evaluator {evaluator} has no entrypoint")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise ValueError(f"evaluator {evaluator} has invalid paths")
    return spec


def matching_paths(spec: dict[str, Any], changed_files: list[str]) -> list[str]:
    patterns = [str(item) for item in spec["paths"]]
    return sorted(
        {
            path
            for path in changed_files
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
        }
    )


def load_evaluator(entrypoint: str) -> ModuleType:
    path = (ROOT.parent / entrypoint).resolve()
    trusted_root = ROOT.parent.resolve()
    try:
        path.relative_to(trusted_root)
    except ValueError as exc:
        raise ValueError("hidden evaluator entrypoint escaped trusted root") from exc
    if not path.is_file():
        raise ValueError(f"hidden evaluator entrypoint missing: {entrypoint}")
    spec = importlib.util.spec_from_file_location("workflow_ci_hidden_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load hidden evaluator: {entrypoint}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "evaluate", None)):
        raise ValueError(f"hidden evaluator {entrypoint} must expose evaluate(candidate, seed)")
    return module


def normalize_checks(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("hidden evaluator returned no checks")
    checks: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("hidden evaluator check must be an object")
        name = raw.get("name")
        status = raw.get("status")
        if not isinstance(name, str) or not name:
            raise ValueError("hidden evaluator check name is required")
        if status not in {"pass", "fail", "error", "skipped"}:
            raise ValueError(f"hidden evaluator check {name} has invalid status")
        check = {"name": name, "status": status}
        detail = raw.get("detail")
        if detail is not None:
            check["detail"] = str(detail)[:600]
        checks.append(check)
    return checks


def run_evaluator(args: argparse.Namespace) -> int:
    candidate = Path(args.candidate).resolve()
    if not candidate.is_dir():
        raise SystemExit(f"candidate checkout missing: {candidate}")
    spec = load_spec(args.evaluator, args.repository)

    replay_seed = os.environ.get("HIDDEN_EVALUATOR_REPLAY_SEED", "").strip()
    try:
        seed = int(replay_seed) if replay_seed else fresh_seed()
    except ValueError as exc:
        raise SystemExit("HIDDEN_EVALUATOR_REPLAY_SEED must be an integer") from exc

    started = time.monotonic()
    checks: list[dict[str, str]] = []
    status = "error"
    try:
        module = load_evaluator(str(spec["entrypoint"]))
        checks = normalize_checks(module.evaluate(candidate, seed))
        statuses = {check["status"] for check in checks}
        if "error" in statuses:
            status = "error"
        elif "fail" in statuses:
            status = "fail"
        else:
            status = "pass"
    except AssertionError as exc:
        status = "fail"
        checks = [{"name": "evaluator", "status": "fail", "detail": str(exc)[:600]}]
    except BaseException as exc:  # candidate imports can raise SystemExit too
        status = "error"
        checks = [
            {
                "name": "evaluator-runtime",
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}"[:600],
            }
        ]

    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    report = {
        "schema_version": 1,
        "evaluator": args.evaluator,
        "repository": args.repository,
        "base_sha": args.base_sha,
        "head_sha": args.head_sha,
        "seed_id": seed_id(args.evaluator, seed),
        "status": status,
        "checks": checks,
        "duration_ms": duration_ms,
    }
    write_report(Path(args.report), report)
    write_replay_capsule(
        Path(args.replay),
        evaluator=args.evaluator,
        repository=args.repository,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        seed=seed,
    )
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return {"pass": 0, "fail": 2, "error": 3}[status]


def scope_evaluator(args: argparse.Namespace) -> int:
    spec = load_spec(args.evaluator, args.repository)
    changed = [
        line.strip()
        for line in Path(args.changed_files).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matched = matching_paths(spec, changed)
    print(json.dumps({"required": bool(matched), "matched": matched}, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    scope = sub.add_parser("scope")
    scope.add_argument("--evaluator", required=True)
    scope.add_argument("--repository", required=True)
    scope.add_argument("--changed-files", required=True)
    scope.set_defaults(func=scope_evaluator)

    run = sub.add_parser("run")
    run.add_argument("--evaluator", required=True)
    run.add_argument("--repository", required=True)
    run.add_argument("--candidate", required=True)
    run.add_argument("--base-sha", required=True)
    run.add_argument("--head-sha", required=True)
    run.add_argument("--report", required=True)
    run.add_argument("--replay", required=True)
    run.set_defaults(func=run_evaluator)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
