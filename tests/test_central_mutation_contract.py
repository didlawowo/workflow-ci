"""Behavioral regressions for mutation delegated by GitHub Manager and Forgejo."""
import json
import os
import subprocess
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.ci'))
import mutation_contract as contract
import forgejo_mutation as adapter


def write(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_engine_selection_ignores_any_local_consumer_runner(tmp_path):
    (tmp_path / '.ci').mkdir()
    (tmp_path / '.ci/mutation.sh').write_text('exit 0')
    with pytest.raises(ValueError, match='No central'):
        contract.select_engine(tmp_path)
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="consumer"\n')
    assert contract.select_engine(tmp_path) == 'python'
    (tmp_path / 'go.mod').write_text('module consumer\ngo 1.24\n')
    assert contract.select_engine(tmp_path) == 'go'


@pytest.mark.parametrize('locked', [False, True])
def test_project_dependencies_preserve_lock_and_both_dev_schemas(tmp_path, locked):
    (tmp_path / 'pyproject.toml').write_text('[project.optional-dependencies]\ndev=["pytest"]\n[dependency-groups]\ndev=["pytest-cov"]\n')
    if locked:
        (tmp_path / 'uv.lock').touch()
    args = contract.project_sync_args(tmp_path)
    assert ('--locked' in args) is locked
    assert args[-4:] == ['--group', 'dev', '--extra', 'dev']


def test_python_evidence_covers_unicode_methods_and_ignores_outside_scope(tmp_path):
    write(tmp_path, 'mutants/src/service.py.meta', {'exit_code_by_key': {'service.xǁWorkerǁrun__mutmut_1': 1, 'other.x_unrelated__mutmut_1': 0}})
    report = contract.validate_python_evidence(tmp_path, ('service.*Worker*run__mutmut_*',), 'a', 'b')
    assert report['stats']['killed'] == 1
    assert report['stats']['survived'] == 0


@pytest.mark.parametrize('status', [0, None, -9, 2, 3, 4, 5, 124, 255, True, '1'])
def test_every_non_killed_status_fails_closed(tmp_path, status):
    write(tmp_path, 'mutants/a.meta', {'exit_code_by_key': {'a.x_func__mutmut_1': status}})
    with pytest.raises(ValueError, match='non-killed'):
        contract.validate_python_evidence(tmp_path, ('a.*func__mutmut_*',), 'a', 'b')


def test_missing_function_evidence_is_not_a_green_gate(tmp_path):
    write(tmp_path, 'mutants/a.meta', {'exit_code_by_key': {'a.x_func__mutmut_1': 1}})
    with pytest.raises(ValueError, match='no mutation evidence'):
        contract.validate_python_evidence(tmp_path, ('a.*func__mutmut_*', 'a.*other__mutmut_*'), 'a', 'b')


def test_duplicate_or_malformed_metadata_fails(tmp_path):
    write(tmp_path, 'mutants/a.meta', {'exit_code_by_key': {'a.x_func__mutmut_1': 1}})
    write(tmp_path, 'mutants/b.meta', {'exit_code_by_key': {'a.x_func__mutmut_1': 1}})
    with pytest.raises(ValueError, match='Duplicate'):
        contract.validate_python_evidence(tmp_path, ('a.*func__mutmut_*',), 'a', 'b')
    write(tmp_path, 'mutants/b.meta', {'wrong': True})
    with pytest.raises(ValueError, match='Malformed'):
        contract.validate_python_evidence(tmp_path, ('a.*func__mutmut_*',), 'a', 'b')


def test_no_target_proof_requires_exact_scope_and_zero_counts(tmp_path):
    evidence = {'scope': {'base_sha': 'a', 'head_sha': 'b', 'no_targets': True}, 'stats': dict(killed=0, survived=0, timeouts=0, suspicious=0, total=0)}
    write(tmp_path, '.quality/mutation-no-targets.json', evidence)
    assert contract.validate_python_evidence(tmp_path, (), 'a', 'b') == evidence
    with pytest.raises(ValueError, match='scope'):
        contract.validate_python_evidence(tmp_path, (), 'a', 'c')
    evidence['stats']['killed'] = 1
    write(tmp_path, '.quality/mutation-no-targets.json', evidence)
    with pytest.raises(ValueError, match='counters'):
        contract.validate_python_evidence(tmp_path, (), 'a', 'b')


@pytest.mark.parametrize('has_targets', [True, False])
def test_go_requires_bound_and_consistent_results(tmp_path, has_targets):
    n = 2 if has_targets else 0
    evidence = {'scope': {'base_sha': 'a', 'head_sha': 'b', 'no_targets': not has_targets}, 'stats': dict(killed=n, survived=0, timeouts=0, suspicious=0, total=n)}
    write(tmp_path, '.quality/gremlins.json', evidence)
    assert contract.validate_go_evidence(tmp_path, has_targets, 'a', 'b') == evidence
    with pytest.raises(ValueError):
        contract.validate_go_evidence(tmp_path, not has_targets, 'a', 'b')
    evidence['stats']['total'] += 1
    write(tmp_path, '.quality/gremlins.json', evidence)
    with pytest.raises(ValueError, match='Inconsistent'):
        contract.validate_go_evidence(tmp_path, has_targets, 'a', 'b')


def test_evidence_cannot_escape_the_sandbox(tmp_path):
    repo = tmp_path / 'pr'
    repo.mkdir()
    secret = tmp_path / 'trusted'
    write(secret, 'gremlins.json', {})
    (repo / '.quality').symlink_to(secret)
    with pytest.raises(ValueError, match='escapes'):
        contract.validate_go_evidence(repo, False, 'a', 'b')


def test_sandbox_drops_identity_privileges_tokens_and_command_files(tmp_path, monkeypatch):
    monkeypatch.setenv('POLICY_TOKEN', 'secret-sentinel')
    monkeypatch.setenv('FORGEJO_TOKEN', 'secret-sentinel')
    monkeypatch.setenv('GITHUB_OUTPUT', '/private/output')
    command = adapter.sandbox_command(tmp_path, 'python', 'a', 'b')
    assert '--reuid=65532' in command and '--regid=65532' in command
    assert '--no-new-privs' in command and '--bounding-set=-all' in command
    assert command[command.index('env'):command.index('env') + 2] == ['env', '-i']
    assert 'secret-sentinel' not in str(command) and '/private/output' not in str(command)
    assert command[-2:] == ['bash', str(tmp_path / 'engine/mutation.sh')]


def test_fetch_rejects_non_exact_sha_and_external_server_before_git(tmp_path, monkeypatch):
    git = Mock()
    monkeypatch.setattr(adapter, 'git', git)
    for server, repo, sha in [('https://x@evil.example', 'a/b', 'a'*40), ('https://forge.test', '../bad', 'a'*40), ('https://forge.test', 'a/b', 'main')]:
        with pytest.raises(ValueError):
            adapter.fetch(tmp_path, server, repo, sha, 'secret')
    git.assert_not_called()


def test_capture_output_restores_environment_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'real-output'))
    with pytest.raises(RuntimeError):
        with adapter.isolated_output() as captured:
            assert os.environ['GITHUB_OUTPUT'] == str(captured)
            raise RuntimeError('probe')
    assert os.environ['GITHUB_OUTPUT'] == str(tmp_path / 'real-output')


def test_config_changes_are_rejected_before_git_or_execution(tmp_path, monkeypatch):
    base, head = tmp_path / 'base', tmp_path / 'head'
    base.mkdir(); head.mkdir()
    (base / 'pyproject.toml').write_text('[tool.mutmut]\nsource_paths=["src"]\n')
    (head / 'pyproject.toml').write_text('[tool.mutmut]\nsource_paths=[]\n')
    run = Mock()
    monkeypatch.setattr(contract.subprocess, 'run', run)
    with pytest.raises(ValueError, match='protected base'):
        contract.protect_config(base, head, 'a', 'b')
    run.assert_not_called()



def test_python_runner_requests_all_mutmut_statuses():
    script = (ROOT / ".ci" / "mutation.sh").read_text(encoding="utf-8")
    assert 'mutmut results --all > "$RAW_RESULTS"' in script


def test_mutmut_diagnostics_reconstruction_uses_exact_requested_scope(tmp_path):
    script = (ROOT / ".ci" / "mutation.sh").read_text(encoding="utf-8")
    marker = (
        '"$PYTHON" - "$RAW_RESULTS" mutants/mutmut-cicd-stats.json '
        '.quality/mutmut-results.txt "${MUTATION_TARGETS[@]}" <<\'PY\''
    )
    embedded = script.split(marker, 1)[1].split("\nPY\n", 1)[0].lstrip("\n")

    mutants = tmp_path / "mutants"
    source_dir = mutants / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "service.py").write_text(
        "\n".join(
            [
                "mutants_service['x_target__mutmut_1'] = None",
                "mutants_service['x_target__mutmut_2'] = None",
                "mutants_service['x_unrelated__mutmut_1'] = None",
                "mutants_service['x_unrelated__mutmut_2'] = None",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raw = tmp_path / "raw.txt"
    raw.write_text(
        "service.x_target__mutmut_1: killed\n"
        "service.x_target__mutmut_2: survived\n",
        encoding="utf-8",
    )
    stats = mutants / "mutmut-cicd-stats.json"
    stats.write_text(
        json.dumps(
            {
                "total": 2,
                "killed": 1,
                "survived": 1,
                "timeouts": 0,
                "suspicious": 0,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "results.txt"

    subprocess.run(
        [
            sys.executable,
            "-",
            str(raw),
            str(stats),
            str(output),
            "service.*target__mutmut_*",
        ],
        input=embedded,
        text=True,
        cwd=tmp_path,
        check=True,
    )

    result = output.read_text(encoding="utf-8")
    assert "service.x_target__mutmut_1: killed" in result
    assert "service.x_target__mutmut_2: survived" in result
    assert "unrelated" not in result
