import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "actions"
    / "quality-report"
    / "quality_report.py"
)
SPEC = importlib.util.spec_from_file_location("quality_report", MODULE_PATH)
quality_report = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(quality_report)


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
        )
        completed = type("Result", (), {"stdout": diff})()
        with patch.object(quality_report.subprocess, "run", return_value=completed):
            result = quality_report.diff_stats("base", "head")

        self.assertEqual(result["files"], 4)
        self.assertEqual(result["production_additions"], 10)
        self.assertEqual(result["test_additions"], 8)
        self.assertEqual(
            result["suspicious_files"],
            [".github/workflows/ci.yml", ".ci/mutation.sh"],
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
