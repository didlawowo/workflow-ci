from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_go_uses_runner_nfs_without_github_archive():
    content = (ROOT / ".github/actions/setup-go-env/action.yml").read_text()
    assert "cache: false" in content
    assert "actions/cache@" not in content
    assert "require_runner_cache GOCACHE" in content
    assert "require_runner_cache GOMODCACHE" in content
    assert 'value: "false"' in content
