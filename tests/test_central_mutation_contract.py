"""Behavioral regressions for mutation delegated by GitHub Manager and Forgejo."""

import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
contract = importlib.import_module("mutation_contract")
adapter = importlib.import_module("forgejo_mutation")


def write(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_engine_selection_ignores_any_local_consumer_runner(tmp_path):
    (tmp_path / ".ci").mkdir()
    (tmp_path / ".ci/mutation.sh").write_text("exit 0")
    with pytest.raises(ValueError, match="No central"):
        contract.select_engine(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="consumer"\n')
    assert contract.select_engine(tmp_path) == "python"
    (tmp_path / "go.mod").write_text("module consumer\ngo 1.24\n")
    assert contract.select_engine(tmp_path) == "go"


@pytest.mark.parametrize("locked", [False, True])
def test_project_dependencies_preserve_lock_and_both_dev_schemas(tmp_path, locked):
    (tmp_path / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ndev=["pytest"]\n[dependency-groups]\ndev=["pytest-cov"]\n'
    )
    if locked:
        (tmp_path / "uv.lock").touch()
    args = contract.project_sync_args(tmp_path)
    assert ("--locked" in args) is locked
    assert args[-4:] == ["--group", "dev", "--extra", "dev"]


def test_python_evidence_covers_unicode_methods_and_ignores_outside_scope(tmp_path):
    write(
        tmp_path,
        "mutants/src/service.py.meta",
        {
            "exit_code_by_key": {
                "service.xǁWorkerǁrun__mutmut_1": 1,
                "other.x_unrelated__mutmut_1": 0,
            }
        },
    )
    report = contract.validate_python_evidence(
        tmp_path, ("service.*Worker*run__mutmut_*",), "a", "b"
    )
    assert report["stats"]["killed"] == 1
    assert report["stats"]["survived"] == 0


@pytest.mark.parametrize("status", [0, None, -9, 2, 3, 4, 5, 124, 255, True, "1"])
def test_every_non_killed_status_fails_closed(tmp_path, status):
    write(
        tmp_path, "mutants/a.meta", {"exit_code_by_key": {"a.x_func__mutmut_1": status}}
    )
    with pytest.raises(ValueError, match="non-killed"):
        contract.validate_python_evidence(tmp_path, ("a.*func__mutmut_*",), "a", "b")


def test_missing_function_evidence_is_not_a_green_gate(tmp_path):
    write(tmp_path, "mutants/a.meta", {"exit_code_by_key": {"a.x_func__mutmut_1": 1}})
    with pytest.raises(ValueError, match="no mutation evidence"):
        contract.validate_python_evidence(
            tmp_path, ("a.*func__mutmut_*", "a.*other__mutmut_*"), "a", "b"
        )


def test_duplicate_or_malformed_metadata_fails(tmp_path):
    write(tmp_path, "mutants/a.meta", {"exit_code_by_key": {"a.x_func__mutmut_1": 1}})
    write(tmp_path, "mutants/b.meta", {"exit_code_by_key": {"a.x_func__mutmut_1": 1}})
    with pytest.raises(ValueError, match="Duplicate"):
        contract.validate_python_evidence(tmp_path, ("a.*func__mutmut_*",), "a", "b")
    write(tmp_path, "mutants/b.meta", {"wrong": True})
    with pytest.raises(ValueError, match="Malformed"):
        contract.validate_python_evidence(tmp_path, ("a.*func__mutmut_*",), "a", "b")


def test_no_target_proof_requires_exact_scope_and_zero_counts(tmp_path):
    evidence = {
        "scope": {"base_sha": "a", "head_sha": "b", "no_targets": True},
        "stats": dict(killed=0, survived=0, timeouts=0, suspicious=0, total=0),
    }
    write(tmp_path, ".quality/mutation-no-targets.json", evidence)
    assert contract.validate_python_evidence(tmp_path, (), "a", "b") == evidence
    with pytest.raises(ValueError, match="scope"):
        contract.validate_python_evidence(tmp_path, (), "a", "c")
    evidence["stats"]["killed"] = 1
    write(tmp_path, ".quality/mutation-no-targets.json", evidence)
    with pytest.raises(ValueError, match="counters"):
        contract.validate_python_evidence(tmp_path, (), "a", "b")


@pytest.mark.parametrize("has_targets", [True, False])
def test_go_requires_bound_and_consistent_results(tmp_path, has_targets):
    n = 2 if has_targets else 0
    evidence = {
        "scope": {"base_sha": "a", "head_sha": "b", "no_targets": not has_targets},
        "stats": dict(killed=n, survived=0, timeouts=0, suspicious=0, total=n),
    }
    write(tmp_path, ".quality/gremlins.json", evidence)
    assert contract.validate_go_evidence(tmp_path, has_targets, "a", "b") == evidence
    with pytest.raises(ValueError):
        contract.validate_go_evidence(tmp_path, not has_targets, "a", "b")
    evidence["stats"]["total"] += 1
    write(tmp_path, ".quality/gremlins.json", evidence)
    with pytest.raises(ValueError, match="Inconsistent"):
        contract.validate_go_evidence(tmp_path, has_targets, "a", "b")


def test_evidence_cannot_escape_the_sandbox(tmp_path):
    repo = tmp_path / "pr"
    repo.mkdir()
    secret = tmp_path / "trusted"
    write(secret, "gremlins.json", {})
    (repo / ".quality").symlink_to(secret)
    with pytest.raises(ValueError, match="escapes"):
        contract.validate_go_evidence(repo, False, "a", "b")


def test_sandbox_drops_identity_privileges_tokens_and_command_files(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("POLICY_TOKEN", "secret-sentinel")
    monkeypatch.setenv("FORGEJO_TOKEN", "secret-sentinel")
    monkeypatch.setenv("GITHUB_OUTPUT", "/private/output")
    command = adapter.sandbox_command(tmp_path, "python", "a", "b")
    assert "--reuid=65532" in command and "--regid=65532" in command
    assert "--no-new-privs" in command and "--bounding-set=-all" in command
    assert command[command.index("env") : command.index("env") + 2] == ["env", "-i"]
    assert "secret-sentinel" not in str(command) and "/private/output" not in str(
        command
    )
    assert f"UV_CACHE_DIR={tmp_path / 'home/uv-cache'}" in command
    assert f"GOCACHE={tmp_path / 'home/go-build'}" in command
    assert f"GOMODCACHE={tmp_path / 'home/go-mod'}" in command
    assert command[-2:] == ["bash", str(tmp_path / "engine/mutation.sh")]


def test_fetch_rejects_non_exact_sha_and_external_server_before_git(
    tmp_path, monkeypatch
):
    git = Mock()
    monkeypatch.setattr(adapter, "git", git)
    for server, repo, sha in [
        ("https://x@evil.example", "a/b", "a" * 40),
        ("https://forge.test", "../bad", "a" * 40),
        ("https://forge.test", "a/b", "main"),
    ]:
        with pytest.raises(ValueError):
            adapter.fetch(tmp_path, server, repo, sha, "secret")
    git.assert_not_called()


def test_capture_output_restores_environment_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "real-output"))
    with pytest.raises(RuntimeError):
        with adapter.isolated_output() as captured:
            assert os.environ["GITHUB_OUTPUT"] == str(captured)
            raise RuntimeError("probe")
    assert os.environ["GITHUB_OUTPUT"] == str(tmp_path / "real-output")


def test_config_changes_are_rejected_before_git_or_execution(tmp_path, monkeypatch):
    base, head = tmp_path / "base", tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths=["src"]\n')
    (head / "pyproject.toml").write_text("[tool.mutmut]\nsource_paths=[]\n")
    run = Mock()
    monkeypatch.setattr(contract.subprocess, "run", run)
    with pytest.raises(ValueError, match="protected base"):
        contract.protect_config(base, head, "a", "b")
    run.assert_not_called()


def test_github_mutation_workspaces_are_runner_local_scratch():
    workflow = (ROOT / ".github" / "workflows" / "mutation-policy.yml").read_text(
        encoding="utf-8"
    )
    assert 'ISOLATED_ROOT="${RUNNER_TEMP:-/tmp}/mutation-scratch"' in workflow
    assert 'mktemp -d "$ISOLATED_ROOT/go-build.XXXXXX"' in workflow
    assert 'mktemp -d "$ISOLATED_ROOT/go-mod.XXXXXX"' in workflow
    assert 'mktemp -d "$GOCACHE/mutation.XXXXXX"' not in workflow
    assert 'mktemp -d "$GOMODCACHE/mutation.XXXXXX"' not in workflow
    assert 'chmod -R u+w "$ISOLATED_ROOT"' in workflow
    assert "trap cleanup_mutation_scratch EXIT" in workflow


def test_python_runner_publishes_native_metadata_instead_of_inferring_killed_rows():
    script = (ROOT / ".ci/mutation.sh").read_text()
    assert '"$SCRIPT_DIR/mutation_contract.py" diagnostics' in script
    assert "assignment_re" not in script


@pytest.mark.parametrize("project_directory", [".", "src-é"])
def test_go_runner_handles_raw_unicode_paths_and_keeps_evidence_at_repo_root(
    tmp_path, project_directory
):
    repo = tmp_path / "consumer"
    module = repo if project_directory == "." else repo / project_directory
    tools = tmp_path / "tools"
    module.mkdir(parents=True)
    tools.mkdir()

    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "ci@example.test"], check=True
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", "Mutation self-test"], check=True
    )
    (module / "go.mod").write_text("module example.test/consumer\ngo 1.25\n")
    source = module / "café.go"
    source.write_text("package consumer\n\nfunc Add(a, b int) int { return a + b }\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True
    ).strip()
    source.write_text(
        "package consumer\n\nfunc Add(a, b int) int { return a + b + 1 }\n"
    )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "head"], check=True)
    head = subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True
    ).strip()

    (tools / "uname").write_text(
        '#!/bin/sh\n[ "$1" = -s ] && echo Linux || echo arm64\n'
    )
    (tools / "gremlins").write_text(
        """#!/bin/sh
if [ "$1" = --version ]; then echo 0.6.0; exit 0; fi
[ "$PWD" = "$EXPECTED_GREMLINS_CWD" ] || exit 42
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then output="$2"; shift 2; else shift; fi
done
cat > "$output" <<'JSON'
{"files":[{"mutations":[{"status":"KILLED"}]}]}
JSON
"""
    )
    (tools / "uv").write_text(
        "#!/bin/sh\n"
        'while [ "$1" != python ]; do shift; done\n'
        "shift\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    for executable in tools.iterdir():
        executable.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tools}:{env['PATH']}"
    env["MUTATION_BASE_SHA"] = base
    env["MUTATION_HEAD_SHA"] = head
    env["MUTATION_WORKING_DIRECTORY"] = project_directory
    env["EXPECTED_GREMLINS_CWD"] = str(module)
    completed = subprocess.run(
        ["bash", ROOT / ".ci" / "mutation-go.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads((repo / ".quality" / "gremlins.json").read_text())
    assert evidence["stats"] == {
        "killed": 1,
        "survived": 0,
        "timeouts": 0,
        "suspicious": 0,
        "total": 1,
    }
    if module != repo:
        assert not (module / ".quality").exists()


def test_native_diagnostics_use_exact_scope_and_real_newlines(tmp_path):
    write(
        tmp_path,
        "mutants/src/service.py.meta",
        {
            "exit_code_by_key": {
                "service.x_target__mutmut_1": 1,
                "service.x_target__mutmut_2": 0,
                "service.x_unrelated__mutmut_1": None,
            }
        },
    )
    output = tmp_path / "results.txt"
    contract.export_python_diagnostics(tmp_path, ("service.*target__mutmut_*",), output)
    assert output.read_text().splitlines() == [
        "service.x_target__mutmut_1: killed",
        "service.x_target__mutmut_2: survived",
    ]


def test_native_diagnostics_preserve_unresolved_statuses(tmp_path):
    write(
        tmp_path,
        "mutants/src/service.py.meta",
        {
            "exit_code_by_key": {
                "service.x_method__mutmut_1": None,
                "service.x_method__mutmut_2": 5,
                "service.x_method__mutmut_3": -24,
            }
        },
    )
    output = tmp_path / "results.txt"
    contract.export_python_diagnostics(tmp_path, (), output)
    assert output.read_text().splitlines() == [
        "service.x_method__mutmut_1: skipped",
        "service.x_method__mutmut_2: no_tests",
        "service.x_method__mutmut_3: timeouts",
    ]


def test_native_counts_include_unselected_and_unexecuted_test_fixtures(tmp_path):
    write(
        tmp_path,
        "mutants/builds/test_fixture.py.meta",
        {
            "exit_code_by_key": {
                "builds.test_fixture.x_target__mutmut_1": 5,
                "builds.test_fixture.x_target__mutmut_2": 1,
                "builds.test_fixture.x_other__mutmut_1": None,
            }
        },
    )
    output = tmp_path / "results.txt"
    counts = contract.export_python_diagnostics(
        tmp_path, ("builds.test_fixture.*target__mutmut_*",), output
    )
    assert counts["total"] == 3
    assert counts["no_tests"] == counts["killed"] == counts["skipped"] == 1
    assert output.read_text().splitlines() == [
        "builds.test_fixture.x_target__mutmut_1: no_tests",
        "builds.test_fixture.x_target__mutmut_2: killed",
    ]
    report = write(
        tmp_path,
        "mutants/mutmut-cicd-stats.json",
        {
            "total": 3,
            "no_tests": 1,
            "skipped": 0,
            "killed": 1,
            "survived": 0,
            "depth": "high",
            "scope": {"no_targets": False},
        },
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / ".ci/mutation_contract.py"),
            "diagnostics",
            str(output),
            "builds.test_fixture.*target__mutmut_*",
        ],
        cwd=tmp_path,
        check=True,
    )
    captured = json.loads(report.read_text())
    assert (captured["total"], captured["no_tests"], captured["skipped"]) == (3, 1, 1)
    assert captured["depth"] == "high"


@pytest.mark.parametrize("directory", [".", "tools/app"])
def test_standalone_pytest_ini_is_staged_so_mutmut_hits_instrumented_sources(
    tmp_path,
    directory,
):
    repo = tmp_path / "consumer"
    (repo / ".ci").mkdir(parents=True)
    for name in ("mutation.sh", "mutation_scope.py", "mutation_contract.py"):
        shutil.copy2(ROOT / ".ci" / name, repo / ".ci" / name)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "ci@example.test"], check=True
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", "Mutation self-test"], check=True
    )
    project = repo / directory
    project.mkdir(parents=True, exist_ok=True)
    (project / "src").mkdir()
    (project / "tests").mkdir()
    source = project / "src" / "service.py"
    source.write_text(
        "def is_retryable(status):\n    return status == 429\n", encoding="utf-8"
    )
    (project / "tests" / "test_service.py").write_text(
        "from service import is_retryable\n"
        "\n"
        "def test_retryable_on_rate_limit():\n"
        "    assert is_retryable(429) is True\n"
        "\n"
        "def test_not_retryable_on_server_error():\n"
        "    assert is_retryable(500) is False\n",
        encoding="utf-8",
    )
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\npythonpath = src\n", encoding="utf-8"
    )
    (project / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\n\n'
        '[tool.mutmut]\nsource_paths = ["src/"]\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True
    ).strip()
    source.write_text(
        "def is_retryable(status):\n    return status in (429, 503)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "head"], check=True)
    head = subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True
    ).strip()

    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["RUNNER_TEMP"] = str(tmp_path / "runner-temp")
    env["MUTATION_BASE_SHA"] = base
    env["MUTATION_HEAD_SHA"] = head
    env["MUTATION_WORKING_DIRECTORY"] = directory

    completed = subprocess.run(
        ["bash", ".ci/mutation.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "could not find any test case" not in output
    assert (project / "mutants" / "pytest.ini").is_file()
    stats = json.loads((project / "mutants" / "mutmut-cicd-stats.json").read_text())
    assert stats["killed"] >= 1
    assert stats["total"] >= stats["killed"]
    results = (project / ".quality" / "mutmut-results.txt").read_text()
    assert "service.x_is_retryable__mutmut_" in results
