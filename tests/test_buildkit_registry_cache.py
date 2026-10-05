"""Exercise cache planning, sequential exports and observable failures."""

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "registry_cache", ROOT / ".github/actions/docker-build-push/cache.py"
)
CACHE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE)
ENV = {
    "CACHE_IMAGE": "registry.example:5000/team/image",
    "CACHE_SCOPE": "release",
    "CACHE_PLATFORMS": "linux/amd64,linux/arm64",
    "CACHE_CONTEXT": "context with spaces",
    "CACHE_DOCKERFILE": "Dockerfile",
    "CACHE_NATIVE": "true",
}
MANIFEST = {"config": {"mediaType": "application/vnd.buildkit.cacheconfig.v0"}}


def test_platform_tags_are_distinct_and_legacy_cache_is_readable():
    sources = CACHE.imports(ENV)
    assert sources == [
        "type=registry,ref=registry.example:5000/team/image:buildcache-release-linux-amd64",
        "type=registry,ref=registry.example:5000/team/image:buildcache-release-linux-arm64",
        "type=registry,ref=registry.example:5000/team/image:buildcache-release",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"CACHE_SCOPE": "release,mode=min"},
        {"CACHE_IMAGE": "image:tag"},
        {"CACHE_PLATFORMS": "linux/amd64, --push"},
        {"CACHE_PLATFORMS": ""},
        {"CACHE_SCOPE": "x" * 128},
    ],
)
def test_invalid_cache_settings_fail_before_execution(changes):
    with pytest.raises(ValueError):
        CACHE.configuration(ENV | changes)


def test_command_preserves_build_inputs_without_shell_interpolation():
    env = ENV | {
        "CACHE_BUILD_ARGS": "VERSION=1\nVALUE=$(touch /bad)",
        "CACHE_LABELS": "org.example.version=1",
        "CACHE_TARGET": "runtime",
    }
    cmd = CACHE.command(env, "linux/amd64", "cache/ref")
    assert cmd[:6] == [
        "docker",
        "buildx",
        "build",
        "--output",
        "type=cacheonly",
        "--platform",
    ]
    assert "--push" not in cmd and "--load" not in cmd
    assert "VERSION=1" in cmd and "VALUE=$(touch /bad)" in cmd
    assert "org.example.version=1" in cmd
    assert cmd[-2:] == ["--", "context with spaces"]
    assert "ignore-error=false" in cmd[cmd.index("--cache-to") + 1]


def test_exports_are_sequential_and_each_manifest_is_verified():
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(MANIFEST))

    assert CACHE.export(ENV, run)
    assert [cmd[2] for cmd in calls] == ["build", "imagetools", "build", "imagetools"]
    assert calls[0][calls[0].index("--platform") + 1] == "linux/amd64"
    assert calls[2][calls[2].index("--platform") + 1] == "linux/arm64"
    assert (
        calls[0][calls[0].index("--cache-to") + 1]
        != calls[2][calls[2].index("--cache-to") + 1]
    )


def test_retry_can_recover_a_transient_export_failure():
    attempts = 0

    def run(cmd, **kwargs):
        nonlocal attempts
        if cmd[2] == "build":
            attempts += 1
            return subprocess.CompletedProcess(cmd, 1 if attempts == 1 else 0)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(MANIFEST))

    assert CACHE.export(ENV, run)
    assert attempts == 3


def test_successful_build_without_cache_manifest_is_not_reported_as_success(capsys):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "{}")

    assert not CACHE.export(ENV, run)
    assert "Registry cache unavailable" in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="Required cache exports failed"):
        CACHE.export(ENV | {"CACHE_REQUIRED": "true"}, run)


def test_http_verification_uses_explicit_http_and_auth_without_logging_credentials():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(MANIFEST).encode()

    with patch.object(
        CACHE.urllib.request, "urlopen", return_value=Response()
    ) as request:
        assert (
            CACHE.inspect_cache(
                ENV
                | {
                    "CACHE_PLAIN_HTTP": "true",
                    "CACHE_USERNAME": "test",
                    "CACHE_PASSWORD": "fixture",
                },
                "registry.example:5000/team/image:buildcache-release-linux-amd64",
                None,
            )
            == MANIFEST
        )
    called = request.call_args.args[0]
    assert (
        called.full_url
        == "http://registry.example:5000/v2/team/image/manifests/buildcache-release-linux-amd64"
    )
    assert called.get_header("Authorization").startswith("Basic ")
    assert request.call_args.kwargs["timeout"] == 15


def test_action_exports_only_after_image_build_and_never_on_pr():
    action = (ROOT / ".github/actions/docker-build-push/action.yml").read_text()
    assert "cache-to:" not in action
    block = action.split("    - name: Export and verify registry caches\n", 1)[1]
    assert "if: inputs.push == 'true'" in block.split("    - name:", 1)[0]
    assert action.index("Export and verify registry caches") > action.index(
        "Enforce native Docker build result"
    )
    assert "steps.cache-plan.outputs.cache-from" in action


@pytest.mark.parametrize("manifest", [None, [], {"config": None}, {"config": []}])
def test_malformed_manifest_is_a_cache_failure(manifest):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(manifest))

    assert not CACHE.export(ENV, run)
    with pytest.raises(RuntimeError, match="Required cache exports failed"):
        CACHE.export(ENV | {"CACHE_REQUIRED": "true"}, run)


def test_missing_docker_is_optional_unless_cache_is_required():
    def run(cmd, **kwargs):
        raise FileNotFoundError("docker unavailable")

    assert not CACHE.export(ENV, run)
    with pytest.raises(RuntimeError, match="Required cache exports failed"):
        CACHE.export(ENV | {"CACHE_REQUIRED": "true"}, run)


@pytest.mark.parametrize("required,code", [("false", 0), ("true", 1)])
def test_helper_exit_status_enforces_cache_policy(tmp_path, required, code):
    import os
    import sys

    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n")
    docker.chmod(0o755)
    output = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / ".github/actions/docker-build-push/cache.py"),
            "export",
        ],
        env=os.environ
        | ENV
        | {
            "PATH": str(tmp_path),
            "CACHE_REQUIRED": required,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == code
    if required == "false":
        assert output.read_text() == "exported=false\n"
