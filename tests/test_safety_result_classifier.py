import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / ".github" / "scripts" / "classify_safety_result.py"


def classify(tmp_path: Path, exit_code: int, payload: object | None, raw: str | None = None) -> str:
    report = tmp_path / "safety-report.json"
    if raw is not None:
        report.write_text(raw, encoding="utf-8")
    elif payload is not None:
        report.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        ["python3", str(CLASSIFIER), str(exit_code), str(report)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_safety_clean_exit_is_passed_even_without_report(tmp_path: Path):
    assert classify(tmp_path, 0, None) == "passed"


def test_safety_vulnerability_exit_with_v3_findings_is_findings(tmp_path: Path):
    payload = {
        "report_meta": {"safety_version": "3.8.1"},
        "vulnerabilities": [{"package_name": "example", "vulnerability_id": "123"}],
    }
    assert classify(tmp_path, 64, payload) == "findings"


def test_safety_vulnerability_exit_with_legacy_findings_is_findings(tmp_path: Path):
    payload = {
        "report_meta": {"safety_version": "3.8.1"},
        "affected_packages": {"example": {"name": "example", "version": "1.0"}},
        "remediations": {"example": {"vulns_found": 1}},
    }
    assert classify(tmp_path, 64, payload) == "findings"


def test_safety_non_findings_exit_with_valid_error_json_is_error(tmp_path: Path):
    assert classify(tmp_path, 1, {"error": {"message": "network failure"}}) == "error"


def test_safety_vulnerability_exit_without_actual_findings_is_error(tmp_path: Path):
    payload = {
        "report_meta": {"safety_version": "3.8.1"},
        "vulnerabilities": [],
        "affected_packages": {},
        "remediations": {},
    }
    assert classify(tmp_path, 64, payload) == "error"


def test_safety_vulnerability_exit_with_invalid_output_is_error(tmp_path: Path):
    assert classify(tmp_path, 64, None, raw="not-json") == "error"
