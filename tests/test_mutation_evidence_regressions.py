"""Execute the verifier wired into the reusable workflow, not a lookalike gate."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def consumer(tmp_path):
    repo = tmp_path / "pr"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    (repo / "src").mkdir()
    (repo / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths=["src/"]\n')
    source = repo / "src/service.py"
    source.write_text("def compute(value):\n    return value + 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    trusted = tmp_path / ".policy"
    trusted.mkdir()
    (trusted / "pyproject.toml").write_bytes((repo / "pyproject.toml").read_bytes())
    source.write_text("def compute(value):\n    return value + 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    return repo, trusted, base, git(repo, "rev-parse", "HEAD")


def verify(tmp_path, consumer, stats, diagnostics="", **extra):
    repo, trusted, base, head = consumer
    evidence = tmp_path / ".mutation-evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "mutmut-cicd-stats.json").write_text(json.dumps(stats))
    if diagnostics:
        (evidence / "mutmut-results.txt").write_text(diagnostics)
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/mutation-policy.yml").read_text()
    )
    step = next(
        s
        for s in workflow["jobs"]["mutation-verify"]["steps"]
        if s.get("id") == "validate"
    )
    run = step["run"]
    if "<<'PY'" in run:
        code = textwrap.dedent(run.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0])
        command = [
            sys.executable,
            "-",
            str(evidence / "mutmut-cicd-stats.json"),
            str(evidence / "mutmut-results.txt") if diagnostics else "",
            str(repo),
            str(ROOT),
            base,
            head,
            str(tmp_path / "output"),
        ]
        return subprocess.run(
            command, input=code, cwd=tmp_path, capture_output=True, text=True
        )
    run = run.replace("uv run --no-project --python 3.12 python", sys.executable)
    env = {
        **os.environ,
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "BASE_SHA": base,
        "HEAD_SHA": head,
        "PROJECT_DIRECTORY": ".",
        "DEPTH": "medium",
        "WORKFLOW_CI_ROOT": str(ROOT),
        **extra,
    }
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", run],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def bound(stats, consumer):
    return {
        "stats": stats,
        "scope": {
            "base_sha": consumer[2],
            "head_sha": consumer[3],
            "no_targets": False,
        },
    }


def test_changed_consumer_survivor_cannot_hide_behind_workflow_ci_scope(
    tmp_path, consumer
):
    result = verify(
        tmp_path,
        consumer,
        bound({"killed": 0, "survived": 1, "total": 1}, consumer),
        "service.x_compute__mutmut_1: survived\n",
    )
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize(
    "extra", [{"no_tests": 1}, {"not_covered": 1}, {"other_bad": 1}, {"skipped": 1}]
)
def test_aggregate_unresolved_status_cannot_pass(tmp_path, consumer, extra):
    result = verify(
        tmp_path,
        consumer,
        bound({"killed": 1, "survived": 0, "total": 2, **extra}, consumer),
    )
    assert result.returncode != 0, result.stdout


def test_aggregate_requires_consistent_counts(tmp_path, consumer):
    result = verify(
        tmp_path, consumer, bound({"killed": 1, "survived": 0, "total": 999}, consumer)
    )
    assert result.returncode != 0, result.stdout


def test_nonempty_report_requires_current_identity(tmp_path, consumer):
    result = verify(
        tmp_path,
        consumer,
        {
            "killed": 1,
            "survived": 0,
            "total": 1,
            "scope": {"base_sha": "stale", "head_sha": "stale", "no_targets": False},
        },
    )
    assert result.returncode != 0, result.stdout


def test_no_targets_is_checked_against_consumer_sources(tmp_path, consumer):
    stats = {"killed": 0, "survived": 0, "total": 0}
    payload = bound(stats, consumer)
    payload["scope"]["no_targets"] = True
    result = verify(tmp_path, consumer, payload)
    assert result.returncode != 0, result.stdout


def test_literal_escaped_diagnostic_newlines_are_rejected(tmp_path, consumer):
    result = verify(
        tmp_path,
        consumer,
        bound({"killed": 1, "survived": 0, "total": 1}, consumer),
        "service.x_compute__mutmut_1: killed\\n",
    )
    assert result.returncode != 0, result.stdout


def test_real_newline_diagnostics_accept_killed_changed_function(tmp_path, consumer):
    result = verify(
        tmp_path,
        consumer,
        bound({"killed": 1, "survived": 0, "total": 1}, consumer),
        "service.x_compute__mutmut_1: killed\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    scoped = json.loads(
        (tmp_path / ".mutation-evidence/scoped-mutation.json").read_text()
    )
    assert scoped["stats"]["killed"] == 1


def test_native_unicode_method_is_verified_with_consumer_policy(tmp_path, consumer):
    repo, trusted, base, _ = consumer
    (repo / "src/service.py").write_text(
        "class Rules:\n    def check(self, value):\n        return value + 2\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "method")
    consumer = repo, trusted, base, git(repo, "rev-parse", "HEAD")
    result = verify(
        tmp_path,
        consumer,
        bound({"killed": 1, "survived": 0, "total": 1}, consumer),
        "service.xǁRulesǁcheck__mutmut_1: killed\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_nested_project_is_resolved_on_both_sides(tmp_path, consumer):
    repo, trusted, base, _ = consumer
    nested = repo / "tools/app"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths=["src/"]\n')
    (nested / "src").mkdir()
    (nested / "src/core.py").write_text("def run(value):\n    return value + 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "nested base")
    base = git(repo, "rev-parse", "HEAD")
    (trusted / "tools/app").mkdir(parents=True)
    (trusted / "tools/app/pyproject.toml").write_bytes(
        (nested / "pyproject.toml").read_bytes()
    )
    (nested / "src/core.py").write_text("def run(value):\n    return value + 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "nested head")
    consumer = repo, trusted, base, git(repo, "rev-parse", "HEAD")
    result = verify(
        tmp_path,
        consumer,
        bound({"killed": 1, "survived": 0, "total": 1}, consumer),
        "core.x_run__mutmut_1: killed\n",
        PROJECT_DIRECTORY="tools/app",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_capture_transport_rejects_path_traversal_and_decompression_bombs(tmp_path):
    import base64
    import gzip
    import importlib

    sys.path.insert(0, str(ROOT / ".ci"))
    capture = importlib.import_module("mutation_capture")
    source, output = tmp_path / "outputs.json", tmp_path / "decoded.json"
    encoded = base64.b64encode(gzip.compress(b'{"stats": {}}')).decode()
    source.write_text(
        json.dumps({"evidence-file": "mutation-evidence.json", "evidence-b64": encoded})
    )
    capture.decode(source, output)
    assert json.loads(output.read_text()) == {"stats": {}}
    source.write_text(
        json.dumps({"evidence-file": "../../trusted.py", "evidence-b64": encoded})
    )
    with pytest.raises(ValueError, match="filename"):
        capture.decode(source, output)
    bomb = base64.b64encode(gzip.compress(b" " * 20_000_001)).decode()
    source.write_text(
        json.dumps({"evidence-file": "mutation-evidence.json", "evidence-b64": bomb})
    )
    with pytest.raises(ValueError, match="decompressed"):
        capture.decode(source, output)


def test_job_output_heredoc_cannot_execute_injected_shell(tmp_path):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/mutation-policy.yml").read_text()
    )
    step = next(
        s
        for s in workflow["jobs"]["mutation-verify"]["steps"]
        if s.get("name") == "Materialize mutation evidence from job outputs"
    )
    run = (
        step["run"].split("          uv run", 1)[0]
        if "          uv run" in step["run"]
        else step["run"].split("uv run", 1)[0]
    )
    sentinel = tmp_path / "must-not-exist"
    attack = f"JSON_MUTATION_OUTPUTS\ntouch {sentinel}\n#"
    payload = {"evidence-file": "mutation-evidence.json", "evidence-b64": attack}
    run = run.replace(
        "${{ toJSON(needs.mutation-run.outputs) }}", json.dumps(payload, indent=2)
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", run],
        cwd=tmp_path,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_RUN_ID": "fixture"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()


def test_resolver_selects_nested_python_node_and_protected_external_runner(tmp_path):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/mutation-policy.yml").read_text()
    )
    step = next(
        s for s in workflow["jobs"]["mutation-run"]["steps"] if s.get("id") == "runner"
    )
    repo = tmp_path / "pr"
    (repo / "tools/app").mkdir(parents=True)
    (tmp_path / ".policy/.ci").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    (repo / "README.md").write_text("fixture\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    sha = git(repo, "rev-parse", "HEAD")
    output = tmp_path / "runner-output"
    env = {
        **os.environ,
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
        "MUTATION_BASE_SHA": sha,
        "MUTATION_HEAD_SHA": sha,
        "MUTATION_WORKING_DIRECTORY": "tools/app",
        "CONSUMER_REPOSITORY": "owner/consumer",
        "CENTRAL_REPOSITORY": "owner/workflow-ci",
    }

    def select():
        output.unlink(missing_ok=True)
        completed = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", step["run"]],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    (repo / "tools/app/pyproject.toml").write_text('[project]\nname="fixture"\n')
    assert select()["source"] == "workflow-ci-python"
    assert select()["working-directory"] == "tools/app"
    (repo / "tools/app/pyproject.toml").unlink()
    (repo / "tools/app/package.json").write_text('{"private":true}')
    assert select()["source"] == "workflow-ci-node"
    # A protected legacy runner stays authoritative for an external consumer.
    custom = tmp_path / ".policy/.ci/mutation.sh"
    custom.write_text(
        "mutmut run\nmutmut export-cicd-stats # mutants/mutmut-cicd-stats.json\n"
    )
    assert select()["source"] == "consumer-base"
    env["CONSUMER_REPOSITORY"] = "owner/workflow-ci"
    assert select()["source"] == "workflow-ci-node"
