#!/usr/bin/env python3
"""Classify Safety CLI results without confusing scanner failures with findings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SAFETY_VULNERABILITIES_FOUND = 64


def _has_findings(report: object) -> bool:
    if not isinstance(report, dict):
        return False

    vulnerabilities = report.get("vulnerabilities")
    if isinstance(vulnerabilities, list) and vulnerabilities:
        return True

    affected_packages = report.get("affected_packages")
    if isinstance(affected_packages, dict) and affected_packages:
        return True

    remediations = report.get("remediations")
    if isinstance(remediations, dict):
        for remediation in remediations.values():
            if not isinstance(remediation, dict):
                continue
            vulns_found = remediation.get("vulns_found", 0)
            try:
                if int(vulns_found) > 0:
                    return True
            except (TypeError, ValueError):
                continue

    return False


def _is_report(report: object) -> bool:
    """Require recognizable result fields, not an error or arbitrary JSON."""
    if not isinstance(report, dict):
        return False
    fields = {"vulnerabilities": list, "affected_packages": dict, "remediations": dict}
    present = [key for key in fields if key in report]
    return bool(present) and all(isinstance(report[key], fields[key]) for key in present)


def classify(exit_code: int, report_path: Path) -> str:
    if exit_code not in (0, SAFETY_VULNERABILITIES_FOUND):
        return "error"

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "error"

    if not _is_report(report):
        return "error"
    if exit_code == 0:
        # Safety applies the consumer's policy, including explicitly ignored findings.
        return "passed"
    return "findings" if _has_findings(report) else "error"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: classify_safety_result.py EXIT_CODE REPORT_PATH", file=sys.stderr)
        return 2

    try:
        exit_code = int(sys.argv[1])
    except ValueError:
        print("error", end="")
        return 0

    print(classify(exit_code, Path(sys.argv[2])), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
