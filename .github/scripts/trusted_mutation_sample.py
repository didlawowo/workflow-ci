#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import secrets
import time


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def normalize_status(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def select(report_path: Path, ids_path: Path, state_path: Path, percent: int, maximum: int) -> int:
    report = load(report_path)
    mutants = report.get("mutants")
    if not isinstance(mutants, dict):
        stats = report.get("stats") or {}
        if int(stats.get("total", 0)) == 0:
            state = {
                "schema_version": 1,
                "status": "not_applicable",
                "sample_count": 0,
                "sample_percent": percent,
                "sample_id": "none",
                "selected": {},
            }
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            ids_path.write_text("", encoding="utf-8")
            return 0
        raise ValueError("mutation evidence has measured mutants but no per-mutant map")

    killed = sorted(
        mutant_id
        for mutant_id, status in mutants.items()
        if normalize_status(status) == "killed"
    )
    if not killed:
        raise ValueError("trusted sample requires at least one killed mutant")

    count = max(1, math.ceil(len(killed) * percent / 100.0))
    count = min(count, maximum, len(killed))
    selected_ids = sorted(secrets.SystemRandom().sample(killed, count))
    selected = {mutant_id: "killed" for mutant_id in selected_ids}
    sample_id = hashlib.sha256("\n".join(selected_ids).encode("utf-8")).hexdigest()[:20]

    ids_path.parent.mkdir(parents=True, exist_ok=True)
    ids_path.write_text("\n".join(selected_ids) + "\n", encoding="utf-8")
    state = {
        "schema_version": 1,
        "status": "selected",
        "sample_count": count,
        "sample_percent": percent,
        "sample_id": sample_id,
        "selected": selected,
    }
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return count


def verify(report_path: Path, state_path: Path, replay_path: Path) -> None:
    report = load(report_path)
    state = load(state_path)
    if state.get("status") == "not_applicable":
        report["attestation"] = {
            "status": "not_applicable",
            "sample_count": 0,
            "sample_percent": int(state["sample_percent"]),
            "sample_id": "none",
            "duration_ms": 0,
        }
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    replay = load(replay_path)
    observed = replay.get("mutants")
    if not isinstance(observed, dict):
        raise ValueError("mutation replay did not return a mutant map")

    expected = state.get("selected")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("trusted mutation sample is empty")

    divergences = {}
    for mutant_id, expected_status in expected.items():
        actual = normalize_status(observed.get(mutant_id))
        if actual != normalize_status(expected_status):
            divergences[mutant_id] = {
                "expected": expected_status,
                "actual": actual or "missing",
            }
    if divergences:
        preview = ", ".join(
            f"{mid}={value['expected']}->{value['actual']}"
            for mid, value in list(divergences.items())[:20]
        )
        raise ValueError("trusted mutation replay diverged: " + preview)

    report["attestation"] = {
        "status": "pass",
        "sample_count": int(state["sample_count"]),
        "sample_percent": int(state["sample_percent"]),
        "sample_id": str(state["sample_id"]),
        "duration_ms": int(replay.get("duration_ms", 0)),
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)

    choose = subs.add_parser("select")
    choose.add_argument("--report", required=True, type=Path)
    choose.add_argument("--ids", required=True, type=Path)
    choose.add_argument("--state", required=True, type=Path)
    choose.add_argument("--percent", type=int, default=10)
    choose.add_argument("--max", dest="maximum", type=int, default=5)

    check = subs.add_parser("verify")
    check.add_argument("--report", required=True, type=Path)
    check.add_argument("--state", required=True, type=Path)
    check.add_argument("--replay", required=True, type=Path)

    args = parser.parse_args()
    started = time.monotonic()
    if args.command == "select":
        if not 1 <= args.percent <= 100:
            raise SystemExit("sample percent must be between 1 and 100")
        if args.maximum < 1:
            raise SystemExit("sample max must be >= 1")
        count = select(args.report, args.ids, args.state, args.percent, args.maximum)
        print(json.dumps({"selected": count, "duration_ms": int((time.monotonic() - started) * 1000)}))
        return 0
    verify(args.report, args.state, args.replay)
    print(json.dumps({"status": "pass", "duration_ms": int((time.monotonic() - started) * 1000)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
