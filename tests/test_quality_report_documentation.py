"""Documentation-only verdict in the trusted quality report (#284)."""

import pytest

import quality_report

PASSING_SIGNALS = {
    "quality": {"required": True, "available": True, "status": "success"},
    "security": {"required": True, "available": True, "status": "success"},
    "sonar": {"required": True, "available": False, "status": "disabled"},
}


@pytest.mark.parametrize(
    "environ,expected",
    [
        ({"QUALITY_DOCUMENTATION_ONLY": "true"}, True),
        ({"QUALITY_DOCUMENTATION_ONLY": "false"}, False),
        ({"QUALITY_DOCUMENTATION_ONLY": "TRUE"}, False),
        ({}, False),
    ],
)
def test_documentation_only_requested_reads_the_exact_flag(environ, expected):
    assert quality_report.documentation_only_requested(environ) is expected


def test_documentation_only_does_not_count_tests_coverage_and_sonar_as_missing():
    gate = quality_report.aggregate_gate(
        {**PASSING_SIGNALS, "documentation_only": True}
    )
    assert gate == {"status": "PASS", "failures": [], "missing": []}


def test_without_documentation_only_the_same_report_is_incomplete():
    gate = quality_report.aggregate_gate(dict(PASSING_SIGNALS))
    assert gate["status"] == "INCOMPLETE"
    assert gate["missing"] == ["coverage", "sonar", "tests"]


def test_documentation_only_still_requires_quality_and_security():
    report = {
        **PASSING_SIGNALS,
        "security": {"required": True, "available": False},
        "quality": {"required": True, "available": True, "status": "failure"},
        "documentation_only": True,
    }
    gate = quality_report.aggregate_gate(report)
    assert gate["status"] == "FAIL"
    assert gate["failures"] == ["quality"]
    assert gate["missing"] == ["security"]


def test_finalize_report_marks_documentation_only_from_the_environment(monkeypatch):
    monkeypatch.setenv("QUALITY_DOCUMENTATION_ONLY", "true")
    finalized = quality_report.finalize_report(dict(PASSING_SIGNALS))
    assert finalized["documentation_only"] is True
    assert finalized["gate"]["status"] == "PASS"


def test_finalize_report_without_flag_keeps_the_report_unchanged(monkeypatch):
    monkeypatch.delenv("QUALITY_DOCUMENTATION_ONLY", raising=False)
    finalized = quality_report.finalize_report(dict(PASSING_SIGNALS))
    assert "documentation_only" not in finalized
    assert finalized["gate"]["status"] == "INCOMPLETE"
