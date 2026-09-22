#!/usr/bin/env python3
"""Forgejo adapter for the SAME policy and engines used by Workflow CI on GitHub.

The provisioner distributes only an action reference. No PR-controlled executable
is run as root or with forge credentials. Requires an isolated root job container
with setpriv; no Docker socket or host filesystem may be mounted in that container.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile

from mutation_contract import protect_config, select_engine, validate_go_evidence, validate_python_evidence

ROOT = Path(__file__).resolve().parents[1]
UID = 65532


def output(name: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("Multiline workflow output rejected")
    destination = os.environ.get("GITHUB_OUTPUT") or os.environ["FORGEJO_OUTPUT"]
    with open(destination, "a") as handle:
        handle.write(f"{name}={value}\n")


@contextmanager
def isolated_output():
    """Capture canonical classify() outputs, without reimplementing its policy."""
    old = os.environ.get("GITHUB_OUTPUT")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "classification"
        os.environ["GITHUB_OUTPUT"] = str(path)
        try:
            yield path
        finally:
            if old is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old


def classify(event: dict) -> bool:
    sys.path.insert(0, str(ROOT / ".github" / "scripts"))
    import mutation_policy
    if "issue" in event and "pull_request" not in event:
        if event.get("action") == "labeled":
            mutation_policy.notify(event)
        mutation_policy.refresh(event)
        return False
    if "pull_request" not in event:
        raise ValueError("Expected a pull request or issue event")
    with isolated_output() as path:
        mutation_policy.classify(event)
        values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    if values.get("required") not in {"true", "false"}:
        raise ValueError("Central policy returned no valid classification")
    return values["required"] == "true"


def git(directory: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), "-c", "core.hooksPath=/dev/null", *args],
        check=True, text=True, capture_output=True, env=env,
    ).stdout.strip()


def fetch(directory: Path, server: str, repository: str, sha: str, token: str) -> None:
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?", server):
        raise ValueError("Invalid Forgejo server URL")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        raise ValueError("Invalid repository name")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Exact base/head SHA required")
    directory.mkdir(exist_ok=True)
    if not (directory / ".git").exists():
        git(directory, "init", "-q")
    # The token only exists in the fetching process environment, not in a URL,
    # git configuration, command argument, or the fetched working tree.
    with tempfile.TemporaryDirectory() as temp:
        askpass = Path(temp) / "askpass"
        askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) echo x-access-token;; *Password*) printf "%s\\n" "$FETCH_TOKEN";; esac\n')
        askpass.chmod(0o700)
        env = {**os.environ, "FETCH_TOKEN": token, "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        git(directory, "-c", "credential.helper=", "fetch", "--no-tags", "--no-recurse-submodules", f"{server}/{repository}.git", sha, env=env)
    git(directory, "checkout", "--detach", "--force", sha)
    if git(directory, "rev-parse", "HEAD") != sha:
        raise ValueError("Checkout did not materialize the requested SHA")


def prepare(event: dict) -> None:
    required = classify(event)
    output("required", str(required).lower())
    if not required:
        return
    if os.geteuid() != 0 or shutil.which("setpriv") is None:
        raise RuntimeError("Mutation requires an isolated root container with setpriv")
    pr = event["pull_request"]
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    server = os.environ["MUTATION_SERVER_URL"].rstrip("/")
    repository = os.environ["POLICY_REPOSITORY"]
    head_repo = pr["head"]["repo"]["full_name"]
    token = os.environ["POLICY_TOKEN"]
    root = Path(tempfile.mkdtemp(prefix="workflow-ci-forgejo-"))
    try:
        trusted, proposed = root / "base", root / "verify"
        fetch(trusted, server, repository, base, token)
        fetch(proposed, server, repository, base, token)
        fetch(proposed, server, head_repo, head, token)
        protect_config(trusted, proposed, base, head)
        engine = select_engine(trusted)
        if engine == "python":
            from mutation_scope import mutation_targets
            targets = list(mutation_targets(proposed, base, head))
        else:
            changed = git(proposed, "diff", "--name-only", f"{base}...{head}", "--", "*.go")
            targets = [name for name in changed.splitlines() if not name.endswith("_test.go")]
        # Root-owned pristine tree and plan are never writable by the test UID.
        trusted.chmod(0o700)
        proposed.chmod(0o700)
        implementation = root / "engine"
        shutil.copytree(ROOT / ".ci", implementation)
        implementation.chmod(0o755)
        for path in implementation.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        work = root / "pr"
        shutil.copytree(proposed, work, symlinks=True)
        work.chmod(0o755)
        for directory, dirs, files in os.walk(work, followlinks=False):
            os.chown(directory, UID, UID, follow_symlinks=False)
            for name in dirs + files:
                os.chown(Path(directory) / name, UID, UID, follow_symlinks=False)
        home = root / "home"
        home.mkdir(mode=0o700)
        os.chown(home, UID, UID)
        state = root / "state.json"
        state.write_text(json.dumps({"engine": engine, "base": base, "head": head, "targets": targets}))
        state.chmod(0o600)
        # Traverse only: the test UID can access its workspace, never list the
        # private plan/pristine trees or alter the root-owned engine files.
        root.chmod(0o711)
        output("state", str(state))
        output("engine", engine)
        output("go-mod", str(trusted / "go.mod"))
    except BaseException:
        shutil.rmtree(root)
        raise


def sandbox_command(root: Path, engine: str, base: str, head: str) -> list[str]:
    script = "mutation-go.sh" if engine == "go" else "mutation.sh"
    # No FORGEJO_TOKEN, POLICY_TOKEN, action command files, SSH material or shared
    # caches are inherited by untrusted test code.
    return ["setpriv", f"--reuid={UID}", f"--regid={UID}", "--clear-groups", "--no-new-privs",
            "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", "env", "-i",
            f"PATH={os.environ['PATH']}", f"HOME={root / 'home'}", f"RUNNER_TEMP={root / 'home'}",
            f"UV_CACHE_DIR={root / 'home' / 'uv-cache'}", "UV_LINK_MODE=copy", "CI=true",
            f"MUTATION_BASE_SHA={base}", f"MUTATION_HEAD_SHA={head}",
            "bash", str(root / "engine" / script)]


def execute(state_path: Path) -> None:
    if os.geteuid() != 0 or state_path.is_symlink() or state_path.stat().st_uid != 0 or state_path.stat().st_mode & 0o077:
        raise RuntimeError("Mutation plan is not private/root-owned")
    root = state_path.parent
    plan = json.loads(state_path.read_text())
    engine, base, head = plan["engine"], plan["base"], plan["head"]
    if engine not in {"python", "go"}:
        raise ValueError("Unsupported mutation engine")
    try:
        process = subprocess.Popen(sandbox_command(root, engine, base, head), cwd=root / "pr", start_new_session=True)
        try:
            code = process.wait(timeout=2400)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if code:
            raise RuntimeError(f"Mutation execution failed with exit code {code}")
        # Read evidence only; never import consumer code in the trusted verifier.
        if engine == "python":
            report = validate_python_evidence(root / "pr", tuple(plan["targets"]), base, head)
        else:
            report = validate_go_evidence(root / "pr", bool(plan["targets"]), base, head)
        print(json.dumps(report, sort_keys=True))
        summary = os.environ.get("GITHUB_STEP_SUMMARY") or os.environ.get("FORGEJO_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as handle:
                handle.write(f"## Central mutation gate\n\n{engine}: **{report['stats']['killed']}/{report['stats']['total']} killed**.\n")
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "execute"))
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(json.loads(Path(os.environ["POLICY_EVENT_PATH"]).read_text()))
    elif args.state:
        execute(args.state)
    else:
        parser.error("execute requires --state")
