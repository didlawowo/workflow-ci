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


@pytest.mark.parametrize(
    "validation,reviewed,expected",
    [
        (
            {"status": "pass", "unresolved": 0, "unreviewed_survivors": 0},
            ["core.x_f__mutmut_1"],
            "PASS",
        ),
        (
            {"status": "fail", "unresolved": 0, "unreviewed_survivors": 0},
            ["core.x_f__mutmut_1"],
            "FAIL",
        ),
        (
            {"status": "pass", "unresolved": 1, "unreviewed_survivors": 0},
            ["core.x_f__mutmut_1"],
            "FAIL",
        ),
        (
            {"status": "pass", "unresolved": 0, "unreviewed_survivors": 1},
            ["core.x_f__mutmut_1"],
            "FAIL",
        ),
        ({"status": "pass", "unresolved": 0, "unreviewed_survivors": 0}, [], "FAIL"),
        (
            {"status": "pass", "unresolved": 0, "unreviewed_survivors": 0},
            ["same", "same"],
            "FAIL",
        ),
        ({}, ["core.x_f__mutmut_1"], "FAIL"),
        (None, ["core.x_f__mutmut_1"], "FAIL"),
        (
            {"status": "pass", "unresolved": 0, "unreviewed_survivors": 0},
            "invalid",
            "FAIL",
        ),
        ({"status": "pass", "unresolved": 0, "unreviewed_survivors": 0}, [3], "FAIL"),
    ],
)
def test_reviewed_equivalents_keep_raw_counters_and_require_trusted_verdict(
    tmp_path, validation, reviewed, expected
):
    path = tmp_path / "mutation.json"
    path.write_text(
        json.dumps(
            {
                "stats": {
                    "killed": 9,
                    "survived": 1,
                    "timeouts": 0,
                    "suspicious": 0,
                    "total": 10,
                },
                "reviewed_equivalents": reviewed,
                "validation": validation,
            }
        )
    )
    mutation = quality_report.mutation_evidence(str(path), "true", "success")
    assert mutation["survived"] == 1
    assert mutation["killed"] == 9
    assert mutation["score"] == 90
    assert quality_report.aggregate_gate(report(mutation))["status"] == expected


def test_equivalence_never_excuses_timeout(tmp_path):
    path = tmp_path / "mutation.json"
    path.write_text(
        json.dumps(
            {
                "stats": {
                    "killed": 8,
                    "survived": 1,
                    "timeouts": 1,
                    "suspicious": 0,
                    "total": 10,
                },
                "reviewed_equivalents": ["core.x_f__mutmut_1"],
                "validation": {
                    "status": "pass",
                    "unresolved": 0,
                    "unreviewed_survivors": 0,
                },
            }
        )
    )
    mutation = quality_report.mutation_evidence(str(path), "true", "success")
    assert quality_report.aggregate_gate(report(mutation))["status"] == "FAIL"


def test_parser_preserves_complete_engine_evidence(tmp_path):
    path = tmp_path / "mutation.json"
    cases = [
        (
            {
                "stats": {
                    "killed": 70,
                    "survived": 10,
                    "timeouts": 3,
                    "suspicious": 4,
                    "not_covered": 13,
                    "not_viable": 2,
                    "total": 102,
                }
            },
            {
                "total": 102,
                "killed": 70,
                "survived": 10,
                "timeouts": 3,
                "suspicious": 4,
                "not_covered": 13,
                "not_viable": 2,
                "no_targets": False,
                "score": 70.0,
            },
        ),
        (
            {"stats": {"killed": 8, "survived": 2, "timeouts": 0, "suspicious": 0}},
            {
                "total": 10,
                "killed": 8,
                "survived": 2,
                "timeouts": 0,
                "suspicious": 0,
                "not_covered": None,
                "not_viable": None,
                "no_targets": False,
                "score": 80.0,
            },
        ),
        (
            {
                "killed": 0,
                "survived": 0,
                "timeouts": 0,
                "suspicious": 0,
                "total": 0,
                "scope": {"no_targets": True},
            },
            {
                "total": 0,
                "killed": 0,
                "survived": 0,
                "timeouts": 0,
                "suspicious": 0,
                "not_covered": None,
                "not_viable": None,
                "no_targets": True,
                "score": None,
            },
        ),
        (
            {"stats": {"killed": 3, "survived": 1}, "mutation_score": 0.75},
            {
                "total": None,
                "killed": 3,
                "survived": 1,
                "timeouts": None,
                "suspicious": None,
                "not_covered": None,
                "not_viable": None,
                "no_targets": False,
                "score": 75.0,
            },
        ),
        (
            {"stats": {"killed": 3, "survived": 1}, "score": 60.0},
            {
                "total": None,
                "killed": 3,
                "survived": 1,
                "timeouts": None,
                "suspicious": None,
                "not_covered": None,
                "not_viable": None,
                "no_targets": False,
                "score": 60.0,
            },
        ),
        (
            {"mutationScore": 0.25},
            {
                "total": None,
                "killed": None,
                "survived": None,
                "timeouts": None,
                "suspicious": None,
                "not_covered": None,
                "not_viable": None,
                "no_targets": False,
                "score": 25.0,
            },
        ),
    ]
    for payload, expected in cases:
        path.write_text(json.dumps(payload))
        assert quality_report.parse_mutation(str(path)) == {
            "available": True,
            "file": str(path),
            "reviewed_equivalents": 0,
            **expected,
        }
    assert quality_report.parse_mutation(None) == {
        "available": False,
        "file": None,
        "total": None,
        "killed": None,
        "survived": None,
        "timeouts": None,
        "suspicious": None,
        "not_covered": None,
        "not_viable": None,
        "no_targets": False,
        "score": None,
    }
    path.unlink()
    assert quality_report.parse_mutation(str(path))["available"] is False
    path.write_text("[]")
    with pytest.raises(ValueError, match="JSON object"):
        quality_report.parse_mutation(str(path))


def test_gate_decision_table_checks_all_required_sections_and_failure_precedence():
    import copy

    passing = {
        "tests": {"available": True, "failed": 0, "status": "success"},
        "coverage": {"available": True, "gate_passed": True},
        "quality": {"available": True, "required": True, "status": "success"},
        "security": {"available": True, "required": True, "status": "success"},
        "sonar": {"available": True, "required": True, "status": "success"},
        "mutation": {
            "available": True,
            "required": True,
            "no_targets": False,
            "killed": 3,
            "survived": 0,
            "timeouts": 0,
            "suspicious": 0,
            "not_covered": 0,
            "execution_status": "success",
            "policy_status": "true",
        },
    }
    assert quality_report.aggregate_gate(passing) == {
        "status": "PASS",
        "failures": [],
        "missing": [],
    }
    cases = [
        ("tests", {"available": False}, [], ["tests"]),
        ("tests", {"failed": 2}, ["tests"], []),
        ("tests", {"status": "failure"}, ["tests"], []),
        ("coverage", {"available": False}, [], ["coverage"]),
        ("coverage", {"gate_passed": False}, ["coverage"], []),
        ("quality", {"available": False}, [], ["quality"]),
        ("quality", {"status": "failure"}, ["quality"], []),
        ("quality", {"required": False, "available": False}, [], []),
        ("security", {"available": False}, [], ["security"]),
        ("security", {"status": "failure"}, ["security"], []),
        ("security", {"required": False, "available": False}, [], []),
        ("sonar", {"available": False}, [], ["sonar"]),
        ("sonar", {"status": "failure"}, ["sonar"], []),
        ("sonar", {"required": False, "available": False}, [], []),
        ("mutation", {"available": False}, [], ["mutation"]),
        ("mutation", {"survived": 1}, ["mutation"], []),
        ("mutation", {"timeouts": 1}, ["mutation"], []),
        ("mutation", {"suspicious": 1}, ["mutation"], []),
        ("mutation", {"not_covered": 1}, ["mutation"], []),
        ("mutation", {"required": False, "available": False}, [], []),
        ("mutation", {"execution_status": "cancelled"}, ["mutation-execution"], []),
        ("mutation", {"execution_status": "failure"}, ["mutation-execution"], []),
        ("mutation", {"execution_status": "unknown"}, [], ["mutation-execution"]),
        ("mutation", {"policy_status": "unknown"}, [], ["mutation-policy"]),
        ("mutation", {"policy_status": "false"}, [], []),
        ("mutation", {"no_targets": True, "survived": 1}, [], []),
        ("mutation", {"survived": 2, "reviewed_equivalents": 1}, ["mutation"], []),
        ("mutation", {"survived": 1, "reviewed_equivalents": 1}, [], []),
    ]
    for section, changes, failures, missing in cases:
        data = copy.deepcopy(passing)
        data[section].update(changes)
        assert quality_report.aggregate_gate(data) == {
            "status": "FAIL" if failures else "INCOMPLETE" if missing else "PASS",
            "failures": failures,
            "missing": missing,
        }, (section, changes)
    combined = copy.deepcopy(passing)
    combined["tests"]["failed"] = 1
    combined["coverage"]["available"] = False
    assert quality_report.aggregate_gate(combined) == {
        "status": "FAIL",
        "failures": ["tests"],
        "missing": ["coverage"],
    }


def test_mutation_parser_accepts_legacy_counter_aliases_and_score_boundaries(tmp_path):
    path = tmp_path / "mutation.json"
    aliases = {
        "killed": ("killed", "killed_mutants", "mutants_killed"),
        "survived": (
            "survived",
            "surviving",
            "survived_mutants",
            "mutants_survived",
            "mutants_lived",
        ),
        "timeouts": ("timeouts", "timeout", "timed_out"),
        "suspicious": ("suspicious", "suspicious_mutants"),
        "not_covered": ("not_covered", "no_tests", "mutants_not_covered"),
        "not_viable": ("not_viable", "mutants_not_viable"),
        "total": ("total", "total_mutants", "mutants", "mutants_total"),
    }
    for field, spellings in aliases.items():
        for alias in spellings:
            path.write_text(json.dumps({alias: 7}))
            assert quality_report.parse_mutation(str(path))[field] == 7, alias
    for field in ("mutation_score", "score", "mutationScore"):
        for score, expected in [(0.2, 20), (1.0, 100), (1.5, 1.5)]:
            path.write_text(json.dumps({"killed": 3, "survived": 1, field: score}))
            assert quality_report.parse_mutation(str(path))["score"] == expected
    path.write_text(json.dumps({"killed": 7, "survived": 2}))
    assert quality_report.parse_mutation(str(path))["score"] == 77.8
    path.write_text(
        json.dumps(
            {
                "killed": 7,
                "survived": 2,
                "timeouts": 1,
                "suspicious": 2,
                "not_viable": 3,
            }
        )
    )
    assert quality_report.parse_mutation(str(path))["total"] == 15
    path.write_text("[]")
    with pytest.raises(ValueError) as error:
        quality_report.parse_mutation(str(path))
    assert str(error.value) == "mutation report must be a JSON object"


def test_missing_survivor_count_cannot_grant_equivalence_credit(tmp_path):
    path = tmp_path / "mutation.json"
    path.write_text(
        json.dumps(
            {
                "stats": {"killed": 9, "total": 9},
                "reviewed_equivalents": ["core.x_f__mutmut_1"],
                "validation": {
                    "status": "pass",
                    "unresolved": 0,
                    "unreviewed_survivors": 0,
                },
            }
        )
    )
    result = quality_report.parse_mutation(str(path))
    assert result["survived"] is None
    assert result["reviewed_equivalents"] == 0


def test_spurious_equivalence_credit_never_covers_unresolved_mutants():
    mutation = {
        "required": True,
        "available": True,
        "survived": 0,
        "timeouts": 1,
        "reviewed_equivalents": 1,
    }
    assert quality_report.aggregate_gate(report(mutation))["status"] == "FAIL"
