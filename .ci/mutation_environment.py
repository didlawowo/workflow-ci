"""Allowlisted environment for untrusted mutation execution.

Proxy endpoints come from runner infrastructure. Credentials and forge command
files are deliberately absent; authenticated endpoints must be provisioned by
infrastructure without exposing their credentials to candidate code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlsplit

PROXY_KEYS = (
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL",
    "GOPROXY",
    "GOSUMDB",
    "GONOSUMDB",
    "GOPRIVATE",
    "NPM_CONFIG_REGISTRY",
    "npm_config_registry",
)
EXECUTION_KEYS = (
    "PATH",
    "HOME",
    "RUNNER_TEMP",
    "UV_CACHE_DIR",
    "GOCACHE",
    "GOMODCACHE",
    "UV_LINK_MODE",
    "VIRTUAL_ENV",
    "CI",
    "TZ",
    "MUTATION_POLICY_LABELS",
    "MUTATION_DEPTH",
    "MUTATION_BASE_SHA",
    "MUTATION_HEAD_SHA",
    "MUTATION_WORKING_DIRECTORY",
    "MUTATION_NODE_TEST_COMMAND",
    "MUTATION_SHARD_INDEX",
    "MUTATION_SHARD_COUNT",
    "MUTATION_PLAN",
)


def proxy_environment(environment: dict[str, str]) -> dict[str, str]:
    result = {}
    for key in PROXY_KEYS:
        if key not in environment:
            continue
        value = environment[key]
        if any(character in value for character in ("\n", "\r", "\0")):
            raise ValueError(f"Invalid runner proxy setting: {key}")
        for part in value.replace("|", ",").replace(" ", ",").split(","):
            parsed = urlsplit(part)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(
                    f"Credential-bearing runner proxy setting cannot be exposed: {key}"
                )
        result[key] = value
    return result


def execution_environment(environment: dict[str, str]) -> dict[str, str]:
    result = {key: environment[key] for key in EXECUTION_KEYS if key in environment}
    result.update(proxy_environment(environment))
    result["TZ"] = "Europe/Paris"
    return result


if __name__ == "__main__":
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        sys.exit("Mutation execution command is required")
    try:
        sys.exit(subprocess.call(command, env=execution_environment(dict(os.environ))))
    except ValueError as exc:
        sys.exit(str(exc))
