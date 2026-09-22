import io
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
            "8\t0\ttests/test_service.py\n"
            "2\t1\t.github/workflows/ci.yml\n"
            "3\t0\t.ci/mutation.sh\n"
            "1\t0\tsonar-project.properties\n"
        )
        completed = type("Result", (), {"stdout": diff})()
        with patch.object(quality_report.subprocess, "run", return_value=completed):
            result = quality_report.diff_stats("base", "head")

        self.assertEqual(result["files"], 5)
        self.assertEqual(result["production_additions"], 10)
        self.assertEqual(result["test_additions"], 8)
        self.assertEqual(
            result["suspicious_files"],
            [".github/workflows/ci.yml", ".ci/mutation.sh", "sonar-project.properties"],
        )


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
            "sonar": {"available": True, "marker": "old-sonar"},
        }
        current = {
            "schema_version": 2,
            "identity": {"head_sha": "same-head", "run_id": "new"},
            "tests": {"available": True, "marker": "new-tests"},
            "coverage": {"available": True, "marker": "new-coverage"},
            "mutation": {"available": True, "marker": "new-mutation"},
            "diff": {"available": True, "marker": "new-diff"},
            "history": {"available": True, "marker": "new-history"},
            "sonar": {"available": True, "marker": "new-sonar"},
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
                "sonar",
            },
        )
        self.assertEqual(merged["schema_version"], 2)
        self.assertEqual(merged["identity"], current["identity"])
        for section in ("tests", "coverage", "mutation", "sonar", "diff", "history"):
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

    def test_upsert_comment_fails_closed_on_forbidden_write(self):
        report = {
            "schema_version": 1,
            "identity": {"head_sha": "head"},
            "tests": {"available": False},
            "coverage": {"available": False},
            "mutation": {"available": False},
            "diff": {"available": False, "suspicious_files": []},
            "history": {"available": False, "current_run_attempt": 1},
        }
        error = quality_report.urllib.error.HTTPError(
            url="https://api.github.com/repos/example/repo/issues/1/comments",
            code=403,
            msg="Forbidden",
            hdrs={"X-Accepted-GitHub-Permissions": "pull_requests=write"},
            fp=io.BytesIO(b'{"message":"Resource not accessible by integration"}'),
        )

        with patch.object(quality_report, "_api_json", return_value=[]), patch.object(
            quality_report.urllib.request, "urlopen", side_effect=error
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "HTTP 403.*pull_requests=write.*Resource not accessible by integration",
            ):
                quality_report.upsert_comment(
                    report,
                    "https://api.github.com",
                    "example/repo",
                    1,
                    "token",
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
            "sonar": {
                "available": True,
                "status": "success",
                "url": "https://sonarqube.dc-tech.work/dashboard?id=example",
            },
        }
        markdown = quality_report.render_markdown(report)

        self.assertIn("## CI Quality Report", markdown)
        self.assertIn("8 passed · 1 failed", markdown)
        self.assertIn("90.0%", markdown)
        self.assertIn(".ci/mutation.sh", markdown)
        self.assertIn("2 failed", markdown)
        self.assertIn("SonarQube", markdown)
        self.assertIn("Quality Gate passed", markdown)
        self.assertIn("https://sonarqube.dc-tech.work/dashboard?id=example", markdown)


if __name__ == "__main__":
    unittest.main()
