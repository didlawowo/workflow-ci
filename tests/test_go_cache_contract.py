from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_go_uses_runner_proxy_without_github_archive_or_nfs_cache_contract():
    content = (ROOT / ".github/actions/setup-go-env/action.yml").read_text()
    assert "cache: false" in content
    assert "actions/cache@" not in content
    assert "require_nfs_cache GOCACHE" not in content
    assert "require_nfs_cache GOMODCACHE" not in content
    assert "go mod download" in content
    assert 'value: "false"' in content
