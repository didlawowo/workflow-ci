from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
VALID_STATUSES = {"pass", "fail", "error", "skipped"}


def fresh_seed() -> int:
    """Return an unpredictable 64-bit seed for one evaluator execution."""
    return secrets.randbits(64)


def seed_id(evaluator: str, seed: int) -> str:
    """Return a stable opaque identifier without exposing the raw seed."""
    payload = f"{evaluator}:{seed}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def validate_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("hidden report schema_version=1 required")
    if report.get("status") not in VALID_STATUSES:
        raise ValueError("hidden report status is invalid")
    for key in ("evaluator", "repository", "base_sha", "head_sha", "seed_id"):
        if not isinstance(report.get(key), str) or not report[key]:
            raise ValueError(f"hidden report missing/invalid {key}")
    duration = report.get("duration_ms")
    if not isinstance(duration, int) or duration < 0:
        raise ValueError("hidden report duration_ms must be a non-negative integer")
    checks = report.get("checks")
    if not isinstance(checks, list):
        raise ValueError("hidden report checks must be a list")
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("hidden report check must be an object")
        if not isinstance(check.get("name"), str) or not check["name"]:
            raise ValueError("hidden report check name is required")
        if check.get("status") not in {"pass", "fail", "error", "skipped"}:
            raise ValueError("hidden report check status is invalid")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_report(path: Path, report: dict[str, Any]) -> None:
    validate_report(report)
    write_json(path, report)


def write_replay_capsule(
    path: Path, *, evaluator: str, repository: str, base_sha: str, head_sha: str, seed: int
) -> None:
    """Write replay material for trusted operators.

    The reusable workflow uploads this file best-effort as a short-lived diagnostic
    artifact. It is never passed to the candidate process and is not required for
    the gate verdict.
    """
    write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "evaluator": evaluator,
            "repository": repository,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "seed": seed,
            "seed_id": seed_id(evaluator, seed),
        },
    )
