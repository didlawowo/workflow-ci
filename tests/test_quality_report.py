import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quality_report


class QualityReportTests(unittest.TestCase):
    def test_parse_junit_counts_failures_errors_and_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "junit.xml"
            report.write_text(
                '<testsuite tests="10" failures="2" errors="1" skipped="3" time="4.2"/>'
            )
            result = quality_report.parse_junit([str(report)])

        self.assertEqual(result["total"], 10)
        self.assertEqual(result["passed"], 4)
        self.assertEqual(result["failed"], 3)
        self.assertEqual(result["skipped"], 3)
        self.assertEqual(result["pass_rate"], 40.0)

    def test_parse_coverage_reads_cobertura_line_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "coverage.xml"
            report.write_text('<coverage line-rate="0.873"/>')
            result = quality_report.parse_coverage([str(report)])

        self.assertEqual(result["percentage"], 87.3)

    def test_parse_mutation_computes_score_from_engine_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "mutation.json"
            report.write_text(
                json.dumps(
                    {
                        "stats": {
                            "killed": 18,
                            "survived": 1,
                            "timeouts": 1,
                            "suspicious": 0,
                        }
                    }
                )
            )
            result = quality_report.parse_mutation(str(report))

        self.assertEqual(result["total"], 20)
        self.assertEqual(result["score"], 90.0)

    def test_diff_stats_flags_ci_and_mutation_policy_changes(self):
        diff = (
            "10\t2\tsrc/service.py\n"
            "4\t1\tsrc/other.py\n"
            "2\t1\tsrc/file with spaces.py\n"
            "1\t1\tsrc/name\twithtab.py\n"
            "8\t0\ttests/test_service.py\n"
            "2\t1\ttests/test_other.py\n"
            "5\t1\tdocs/readme.md\n"
            "2\t1\t.github/workflows/ci.yml\n"
            "3\t0\t.ci/mutation.sh\n"
            "-\t-\tassets/logo.png\n"
            "\n"
        )
        completed = type("Result", (), {"stdout": diff})()

        with patch.object(
            quality_report.subprocess, "run", return_value=completed
        ) as run:
            result = quality_report.diff_stats("base", "head")

        run.assert_called_once_with(
            ["git", "diff", "--numstat", "base..head"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result,
            {
                "available": True,
                "files": 10,
                "additions": 37,
                "deletions": 8,
                "production_additions": 17,
                "test_additions": 10,
                "suspicious_files": [
                    ".github/workflows/ci.yml",
                    ".ci/mutation.sh",
                ],
            },
        )

    def test_diff_stats_without_complete_range_never_runs_git(self):
        expected = {
            "available": False,
            "files": 0,
            "additions": 0,
            "deletions": 0,
            "production_additions": 0,
            "test_additions": 0,
            "suspicious_files": [],
        }

        with patch.object(quality_report.subprocess, "run") as run:
            for base, head in (
                (None, "head"),
                ("base", None),
                ("", "head"),
                ("base", ""),
            ):
                with self.subTest(base=base, head=head):
                    self.assertEqual(
                        quality_report.diff_stats(base, head),
                        expected,
                    )

        run.assert_not_called()


    def test_gremlins_report_is_parsed_from_engine_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "gremlins.json"
            report.write_text(
                json.dumps(
                    {
                        "mutants_total": 25,
                        "mutants_killed": 20,
                        "mutants_lived": 5,
                        "mutants_not_covered": 0,
                    }
                )
            )
            result = quality_report.parse_mutation(str(report))

        self.assertEqual(result["total"], 25)
        self.assertEqual(result["killed"], 20)
        self.assertEqual(result["survived"], 5)
        self.assertEqual(result["score"], 80.0)

    def test_merge_reports_keeps_existing_sections_and_replaces_new_evidence(self):
        existing = {
            "schema_version": 1,
            "tests": {"available": True, "total": 10},
            "coverage": {"available": True, "percentage": 80.0},
            "mutation": {"available": False},
            "diff": {"available": True, "files": 2},
            "history": {"available": True, "commits": 2},
        }
        current = {
            "schema_version": 1,
            "tests": {"available": False},
            "coverage": {"available": False},
            "mutation": {"available": True, "score": 95.0},
            "diff": {"available": False},
            "history": {"available": False},
        }

        merged = quality_report.merge_reports(existing, current)

        self.assertEqual(merged["tests"]["total"], 10)
        self.assertEqual(merged["coverage"]["percentage"], 80.0)
        self.assertEqual(merged["mutation"]["score"], 95.0)

    def test_merge_reports_discards_legacy_state_without_head_identity(self):
        existing = {
            "schema_version": 1,
            "tests": {"available": True, "total": 99},
            "coverage": {"available": True, "percentage": 99.0},
            "mutation": {"available": True, "score": 100.0},
            "diff": {"available": True, "files": 9},
            "history": {"available": True, "commits": 9},
        }
        current = {
            "schema_version": 1,
            "identity": {"head_sha": "new-head"},
            "tests": {"available": False},
            "coverage": {"available": False},
            "mutation": {"available": False},
            "diff": {"available": True, "files": 1},
            "history": {"available": False},
        }

        merged = quality_report.merge_reports(existing, current)

        self.assertEqual(merged["identity"]["head_sha"], "new-head")
        self.assertFalse(merged["tests"]["available"])
        self.assertFalse(merged["coverage"]["available"])
        self.assertFalse(merged["mutation"]["available"])

    def test_merge_reports_drops_stale_evidence_when_head_changes(self):
        existing = {
            "schema_version": 1,
            "identity": {"head_sha": "old-head"},
            "tests": {"available": True, "total": 42},
            "coverage": {"available": True, "percentage": 91.0},
            "mutation": {"available": True, "score": 100.0},
            "diff": {"available": True, "files": 4},
            "history": {"available": True, "commits": 2},
        }
        current = {
            "schema_version": 1,
            "identity": {"head_sha": "new-head"},
            "tests": {"available": False},
            "coverage": {"available": False},
            "mutation": {"available": False},
            "diff": {"available": True, "files": 1},
            "history": {"available": False},
        }

        merged = quality_report.merge_reports(existing, current)

        self.assertEqual(merged["identity"]["head_sha"], "new-head")
        self.assertFalse(merged["tests"]["available"])
        self.assertFalse(merged["coverage"]["available"])
        self.assertFalse(merged["mutation"]["available"])

    def test_merge_reports_combines_sections_for_same_head(self):
        existing = {
            "schema_version": 1,
            "identity": {"head_sha": "same-head"},
            "tests": {"available": True, "total": 10},
            "coverage": {"available": True, "percentage": 80.0},
            "mutation": {"available": False},
            "diff": {"available": True, "files": 2},
            "history": {"available": True, "commits": 2},
        }
        current = {
            "schema_version": 1,
            "identity": {"head_sha": "same-head"},
            "tests": {"available": False},
            "coverage": {"available": False},
            "mutation": {"available": True, "score": 100.0},
            "diff": {"available": False},
            "history": {"available": False},
        }

        merged = quality_report.merge_reports(existing, current)

        self.assertEqual(merged["tests"]["total"], 10)
        self.assertEqual(merged["coverage"]["percentage"], 80.0)
        self.assertEqual(merged["mutation"]["score"], 100.0)

    def test_merge_reports_preserves_full_structural_contract(self):
        existing = {
            "schema_version": 7,
            "identity": {"head_sha": "same-head", "run_id": "old"},
            "tests": {"available": True, "marker": "old-tests"},
            "coverage": {"available": True, "marker": "old-coverage"},
            "mutation": {"available": True, "marker": "old-mutation"},
            "diff": {"available": True, "marker": "old-diff"},
            "history": {"available": True, "marker": "old-history"},
        }
        current = {
            "schema_version": 2,
            "identity": {"head_sha": "same-head", "run_id": "new"},
            "tests": {"available": True, "marker": "new-tests"},
            "coverage": {"available": True, "marker": "new-coverage"},
            "mutation": {"available": True, "marker": "new-mutation"},
            "diff": {"available": True, "marker": "new-diff"},
            "history": {"available": True, "marker": "new-history"},
        }

        merged = quality_report.merge_reports(existing, current)

        self.assertEqual(
            set(merged),
            {
                "schema_version",
                "identity",
                "tests",
                "coverage",
                "mutation",
                "diff",
                "history",
            },
        )
        self.assertEqual(merged["schema_version"], 2)
        self.assertEqual(merged["identity"], current["identity"])
        for section in ("tests", "coverage", "mutation", "diff", "history"):
            self.assertEqual(merged[section], current[section])

    def test_merge_reports_schema_version_fallback_contract(self):
        base_sections = {
            "tests": {"available": False},
            "coverage": {"available": False},
            "mutation": {"available": False},
            "diff": {"available": False},
            "history": {"available": False},
        }

        existing = {
            "schema_version": 7,
            "identity": {"head_sha": "same-head"},
            **base_sections,
        }
        current = {
            "identity": {"head_sha": "same-head"},
            **base_sections,
        }
        self.assertEqual(
            quality_report.merge_reports(existing, current)["schema_version"],
            7,
        )

        existing_without_schema = {
            "identity": {"head_sha": "same-head"},
            **base_sections,
        }
        self.assertEqual(
            quality_report.merge_reports(existing_without_schema, current)[
                "schema_version"
            ],
            1,
        )

    def test_markdown_exposes_failures_and_policy_files(self):
        report = {
            "tests": {
                "available": True,
                "passed": 8,
                "failed": 1,
                "skipped": 1,
                "total": 10,
                "pass_rate": 80.0,
            },
            "coverage": {"available": True, "percentage": 75.0},
            "mutation": {
                "available": True,
                "score": 90.0,
                "killed": 18,
                "survived": 1,
                "timeouts": 1,
            },
            "diff": {
                "available": True,
                "files": 2,
                "additions": 20,
                "deletions": 3,
                "production_additions": 10,
                "test_additions": 8,
                "suspicious_files": [".ci/mutation.sh"],
            },
            "history": {
                "available": True,
                "commits": 3,
                "workflow_runs": 5,
                "failed_runs": 2,
                "reruns": 1,
                "current_run_attempt": 1,
            },
        }
        markdown = quality_report.render_markdown(report)

        self.assertIn("8 passed · 1 failed", markdown)
        self.assertIn("90.0%", markdown)
        self.assertIn(".ci/mutation.sh", markdown)
        self.assertIn("2 failed", markdown)


if __name__ == "__main__":
    unittest.main()
