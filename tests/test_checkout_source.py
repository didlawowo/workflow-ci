"""Behavioral regression for #75; only local Git repos, no GitHub credentials."""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / '.github/actions/checkout-source/checkout.sh'


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def source(tmp_path):
    repo = tmp_path / 'server' / 'owner' / 'repo.git'
    repo.mkdir(parents=True)
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'CI')
    git(repo, 'config', 'user.email', 'ci@example.invalid')
    (repo / 'app.py').write_text('value = 1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-index', '--add', '--cacheinfo', f'160000,{base},.workflow-ci')
    (repo / 'app.py').write_text('value = 2\n')
    git(repo, 'add', 'app.py')
    git(repo, 'commit', '-qm', 'orphan gitlink and change')
    return repo, base, git(repo, 'rev-parse', 'HEAD')


def checkout(tmp_path, sha, **overrides):
    env = {**os.environ, 'GITHUB_WORKSPACE': str(tmp_path / 'workspace'),
           'RUNNER_TEMP': str(tmp_path / 'runner-temp'),
           'GITHUB_SERVER_URL': (tmp_path / 'server').as_uri(),
           'CHECKOUT_REPOSITORY': 'owner/repo', 'CHECKOUT_REF': sha,
           'CHECKOUT_TOKEN': 'secret-test-token', 'GITHUB_OUTPUT': str(tmp_path / 'output')}
    env.update(overrides)
    return subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20)


def test_orphan_gitlink_full_history_clean_reuse_and_no_credentials(tmp_path):
    _, base, head = source(tmp_path)
    result = checkout(tmp_path, head)
    assert result.returncode == 0, result.stderr
    workspace = tmp_path / 'workspace'
    assert git(workspace, 'rev-parse', 'HEAD') == head
    assert git(workspace, 'rev-parse', f'{base}^{{commit}}') == base
    assert '.workflow-ci' in git(workspace, 'ls-files', '--stage')
    assert not (workspace / '.gitmodules').exists()
    assert (workspace / 'app.py').read_text() == 'value = 2\n'
    assert 'without submodule traversal' in result.stdout
    assert 'secret-test-token' not in (workspace / '.git/config').read_text()
    assert 'extraheader' not in (workspace / '.git/config').read_text()
    assert not list((tmp_path / 'runner-temp').iterdir())
    (workspace / 'stale').write_text('remove me')
    result = checkout(tmp_path, base)
    assert result.returncode == 0, result.stderr
    assert not (workspace / 'stale').exists()
    assert (workspace / 'app.py').read_text() == 'value = 1\n'


def test_bad_ref_and_failed_fetch_do_not_destroy_workspace(tmp_path):
    source(tmp_path)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'keep').write_text('safe')
    for ref in ('main', 'a' * 40, 'x; echo dangerous'):
        result = checkout(tmp_path, ref)
        assert result.returncode != 0
        assert (workspace / 'keep').read_text() == 'safe'


@pytest.mark.parametrize('option,value', [('CHECKOUT_PERSIST_CREDENTIALS', 'true'), ('CHECKOUT_FETCH_DEPTH', '1')])
def test_security_contract_cannot_be_weakened(tmp_path, option, value):
    _, _, head = source(tmp_path)
    result = checkout(tmp_path, head, **{option: value})
    assert result.returncode != 0


def test_prospective_merge_is_checked_out_not_replaced_with_pr_head(tmp_path):
    repo, base, head = source(tmp_path)
    git(repo, 'checkout', '-qb', 'other', base)
    (repo / 'base-change').write_text('also included')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base advanced')
    git(repo, 'merge', '--no-ff', '-m', 'merge', head)
    merge = git(repo, 'rev-parse', 'HEAD')
    result = checkout(tmp_path, merge)
    assert result.returncode == 0, result.stderr
    assert git(tmp_path / 'workspace', 'rev-parse', 'HEAD') == merge
    assert (tmp_path / 'workspace/base-change').exists()


def test_checkout_retains_other_branch_commits_for_trusted_event_diffs(tmp_path):
    repo, base, head = source(tmp_path)
    git(repo, 'checkout', '-qb', 'event-base', base)
    (repo / 'event-base.txt').write_text('base event commit\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'event base')
    event_base = git(repo, 'rev-parse', 'HEAD')

    git(repo, 'checkout', '-q', 'master')
    result = checkout(tmp_path, head)
    assert result.returncode == 0, result.stderr
    workspace = tmp_path / 'workspace'
    assert git(workspace, 'rev-parse', f'{event_base}^{{commit}}') == event_base
    assert git(workspace, 'rev-parse', 'refs/remotes/origin/event-base') == event_base
