"""Contracts for proxy-first dependency setup and explicit NFS-only legacy storage."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_nfs_helper_has_no_local_profile_or_public_registry_fallback() -> None:
    helper = (ROOT / ".ci/nfs-cache.sh").read_text()
    assert "WORKFLOW_CACHE_PROFILE" not in helper
    assert "WORKFLOW_LOCAL_ROOT" not in helper
    assert "python_index_fallback_if_unreachable" not in helper
    assert "pypi.org/simple" not in helper
    assert "require_nfs_cache()" in helper


def test_generic_language_actions_do_not_require_managed_dependency_caches() -> None:
    python_setup = (ROOT / ".github/actions/setup-python-env/action.yml").read_text()
    python_tests = (ROOT / ".github/actions/run-python-tests/action.yml").read_text()
    go = (ROOT / ".github/actions/setup-go-env/action.yml").read_text()
    node = (ROOT / ".github/actions/setup-node-env/action.yml").read_text()

    for content in (python_setup, python_tests, go, node):
        assert "WORKFLOW_CACHE_PROFILE" not in content
        assert "WORKFLOW_LOCAL_ROOT" not in content

    assert "require_nfs_cache" not in python_setup
    assert "require_nfs_cache" not in python_tests
    assert "require_nfs_cache" not in go
    assert "require_runner_cache" not in node
    assert "cache: $" + "{{ inputs.package-manager }}" not in node


def test_cache_profile_documentation_declares_proxy_first_policy() -> None:
    docs = (ROOT / "docs/cache-profiles.md").read_text()
    assert "Proxpi" in docs
    assert "Verdaccio" in docs
    assert "Athens" in docs
    assert "proxpi.arc-system.svc.cluster.local" in docs
    assert "verdaccio.arc-system.svc.cluster.local" in docs
    assert "athens.arc-system.svc.cluster.local" in docs
    assert "system/out-of-cluster runners use LAN DNS" in docs
