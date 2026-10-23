"""Real Stryker smoke: nested npm consumer, killed/survived/no-target evidence."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
from mutation_evidence import validate  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(
        prefix="node-mutation-", dir=os.environ.get("RUNNER_TEMP")
    ) as temporary:
        root = Path(temporary)
        repo, trusted = root / "repo", root / "trusted"
        project = repo / "app"
        project.mkdir(parents=True)
        (trusted / "app").mkdir(parents=True)

        def run(*args, cwd=repo):
            return subprocess.check_output(args, cwd=cwd, text=True).strip()

        def commit(message):
            run("git", "add", ".")
            run("git", "commit", "-qm", message)
            return run("git", "rev-parse", "HEAD")

        run("git", "init", "-q")
        run("git", "config", "user.name", "Node mutation fixture")
        run("git", "config", "user.email", "fixture@example.test")
        package = {
            "name": "mutation-fixture",
            "version": "1.0.0",
            "private": True,
            "scripts": {"test": "node --test tests/calc.test.js"},
        }
        (project / "package.json").write_text(json.dumps(package))
        (trusted / "app/package.json").write_text(json.dumps(package))
        run(
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            cwd=project,
        )
        (project / "tests").mkdir()
        test = project / "tests/calc.test.js"
        test.write_text(
            "const assert = require('node:assert/strict'); const { test } = require('node:test'); const { add } = require('../calc'); test('add', () => assert.equal(add(2,3), 6));\n"
        )
        source = project / "calc.js"
        source.write_text("exports.add = (a, b) => a + b;\n")
        base = commit("base")
        source.write_text("exports.add = (a, b) => a + b + 1;\n")
        head = commit("changed production")

        def execute(base, head):
            env = {
                **os.environ,
                "MUTATION_BASE_SHA": base,
                "MUTATION_HEAD_SHA": head,
                "MUTATION_WORKING_DIRECTORY": "app",
                "MUTATION_DEPTH": "medium",
                "RUNNER_TEMP": temporary,
                "CI": "true",
            }
            completed = subprocess.run(
                ["bash", str(ROOT / ".ci/mutation-node.sh")],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                timeout=240,
            )
            report = json.loads((repo / ".quality/stryker.json").read_text())
            return completed, report

        completed, report = execute(base, head)
        assert completed.returncode == 0, (
            completed.stdout[-4000:] + completed.stderr[-4000:]
        )
        assert report["stats"]["killed"] > 0
        assert report["stats"]["survived"] == 0
        validate(report, "", repo, trusted, base, head, "app")
        print("Real Node nested consumer: killed mutants and central verifier passed")

        (repo / "README.md").write_text("documentation only\n")
        next_head = commit("docs only")
        completed, report = execute(head, next_head)
        assert completed.returncode == 0
        assert report["scope"]["no_targets"] is True
        validate(report, "", repo, trusted, head, next_head, "app")
        print("Real Node no-target scope passed independent verification")

        test.write_text(
            "const { test } = require('node:test'); const { add } = require('../calc'); test('weak test', () => add(2,3));\n"
        )
        source.write_text("exports.add = (a, b) => a + b + 2;\n")
        final_head = commit("survivors must fail")
        completed, report = execute(next_head, final_head)
        assert completed.returncode != 0
        assert report["stats"]["survived"] > 0
        try:
            validate(report, "", repo, trusted, next_head, final_head, "app")
        except ValueError:
            print("Real Node surviving mutants rejected by execution and verification")
        else:
            raise AssertionError("Surviving Node mutants passed the trusted verifier")


if __name__ == "__main__":
    main()
