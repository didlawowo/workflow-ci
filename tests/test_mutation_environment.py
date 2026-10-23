"""Isolation preserves runner routing without leaking credentials or command files."""

import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ci"))
environment = importlib.import_module("mutation_environment")


def test_proxy_routes_survive_while_tokens_and_action_files_are_dropped():
    source = {
        "PATH": "/safe/bin",
        "HOME": "/scratch/home",
        "UV_CACHE_DIR": "/scratch/uv",
        "GOCACHE": "/scratch/go",
        "GOMODCACHE": "/scratch/mod",
        "UV_DEFAULT_INDEX": "http://proxpi.svc.cluster.local/simple",
        "PIP_INDEX_URL": "http://proxpi.svc.cluster.local/simple",
        "GOPROXY": "http://athens.svc.cluster.local",
        "NPM_CONFIG_REGISTRY": "http://verdaccio.svc.cluster.local",
        "GITHUB_TOKEN": "secret",
        "POLICY_TOKEN": "secret",
        "GITHUB_OUTPUT": "/private/action-file",
        "SSH_AUTH_SOCK": "/private/agent",
    }
    child = environment.execution_environment(source)
    for key in (
        "UV_DEFAULT_INDEX",
        "PIP_INDEX_URL",
        "GOPROXY",
        "NPM_CONFIG_REGISTRY",
        "UV_CACHE_DIR",
        "GOCACHE",
        "GOMODCACHE",
    ):
        assert child[key] == source[key]
    assert (
        not {"GITHUB_TOKEN", "POLICY_TOKEN", "GITHUB_OUTPUT", "SSH_AUTH_SOCK"}
        & child.keys()
    )
    assert child["TZ"] == "Europe/Paris"


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@proxy/simple",
        "https://proxy/simple?token=secret",
        "https://proxy/simple\nOTHER=secret",
    ],
)
def test_proxy_credentials_are_never_passed_to_candidate(value):
    with pytest.raises(ValueError):
        environment.execution_environment({"UV_DEFAULT_INDEX": value})


def test_no_endpoint_is_invented_when_runner_has_no_registry_configuration():
    assert environment.proxy_environment({}) == {}
