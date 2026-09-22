"""Pure reporter regressions included in Mutmut selection."""

import json

import pytest

import quality_report


def report(mutation):
    return {
        "identity": {"head_sha": "same-head"},
        "tests": {"available": True, "failed": 0},
        "coverage": {"available": True, "gate_passed": True},
        "mutation": mutation,
    }


@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "mutation.json"
    path.write_text(
        json.dumps(
            {
                "killed": 10,
                "survived": 0,
                "timeouts": 0,
                "suspicious": 0,
                "not_covered": 0,
                "total": 10,
            }
        )
    )
    return str(path)


@pytest.mark.parametrize(
    "result,required,available,expected",
    [
        ("failure", "", False, "FAIL"),
        ("failure", "false", False, "FAIL"),
        ("failure", "true", True, "FAIL"),
        ("cancelled", "", False, "FAIL"),
        ("cancelled", "false", True, "FAIL"),
        ("skipped", "false", False, "INCOMPLETE"),
        ("", "false", False, "INCOMPLETE"),
        ("unknown", "true", True, "INCOMPLETE"),
        ("success", "", False, "INCOMPLETE"),
        ("success", "unknown", True, "INCOMPLETE"),
        ("success", "true", False, "INCOMPLETE"),
        ("success", "true", True, "PASS"),
        ("success", "false", False, "PASS"),
    ],
)
def test_execution_and_policy_fail_closed(
    evidence, result, required, available, expected
):
    mutation = quality_report.mutation_evidence(
        evidence if available else None, required, result
    )
    assert quality_report.aggregate_gate(report(mutation))["status"] == expected
    if required not in {"true", "false"}:
        assert mutation["required"] is True
        assert mutation["policy_status"] == "unknown"


@pytest.mark.parametrize(
    "result,required", [("failure", ""), ("cancelled", "true"), ("success", "true")]
)
def test_current_missing_evidence_replaces_old_pass(evidence, result, required):
    old = report(quality_report.mutation_evidence(evidence, "true", "success"))
    current = report(quality_report.mutation_evidence(None, required, result))
    merged = quality_report.merge_reports(old, current)
    assert merged["mutation"] == current["mutation"]
    assert quality_report.aggregate_gate(merged)["status"] != "PASS"


def test_partial_legacy_publisher_cannot_erase_execution_failure(evidence):
    old = report(quality_report.mutation_evidence(None, "", "failure"))
    current = report(quality_report.mutation_evidence(evidence, "false"))
    assert (
        quality_report.aggregate_gate(quality_report.merge_reports(old, current))[
            "status"
        ]
        == "FAIL"
    )


def test_successful_trusted_retry_can_replace_failure(evidence):
    old = report(quality_report.mutation_evidence(None, "", "failure"))
    current = report(quality_report.mutation_evidence(evidence, "true", "success"))
    assert (
        quality_report.aggregate_gate(quality_report.merge_reports(old, current))[
            "status"
        ]
        == "PASS"
    )


def test_standalone_compatibility():
    assert (
        quality_report.aggregate_gate(
            report(quality_report.mutation_evidence(None, "false"))
        )["status"]
        == "PASS"
    )
