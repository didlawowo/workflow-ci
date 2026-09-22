"""Execute the real range/scan wrapper against git fixtures and a fake Docker CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/actions/python-quality-security/trufflehog.sh"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.invalid")
    (work / "a").write_text("first\n")
    git(work, "add", ".")
    git(work, "commit", "-qm", "base")
    base = git(work, "rev-parse", "HEAD")
    (work / "a").write_text("second\n")
    git(work, "commit", "-qam", "head")
    head = git(work, "rev-parse", "HEAD")
    return work, base, head


def scan(tmp_path, repo, event_name, event, *, code=0, payload="", sha=None):
    work, _, head = repo
    tools = tmp_path / "bin"
    tools.mkdir(exist_ok=True)
    docker = tools / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
from pathlib import Path
Path(os.environ['DOCKER_CALL']).write_text(json.dumps(sys.argv[1:]))
sys.stdout.write(os.environ['FAKE_JSON'])
sys.stderr.write('not-a-real-secret-marker')
sys.exit(int(os.environ['FAKE_EXIT']))
"""
    )
    docker.chmod(0o755)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    output = tmp_path / "output"
    call = tmp_path / "docker-call"
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(work),
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_SHA": sha or head,
        "GITHUB_OUTPUT": str(output),
        "RUNNER_TEMP": str(tmp_path),
        "DOCKER_CALL": str(call),
        "FAKE_JSON": payload,
        "FAKE_EXIT": str(code),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, check=False, capture_output=True, text=True
    )
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    args = json.loads(call.read_text()) if call.exists() else None
    assert "not-a-real-secret-marker" not in result.stdout + result.stderr
    assert not list(tmp_path.glob("trufflehog.*"))
    return result, values, args


@pytest.mark.parametrize("action", ["opened", "synchronize", "edited", "labeled"])
def test_pr_scans_real_base_and_head_not_merge_or_before(tmp_path, repo, action):
    _, base, head = repo
    event = {
        "action": action,
        "before": head,
        "pull_request": {"base": {"sha": base}, "head": {"sha": head}},
    }
    result, values, args = scan(tmp_path, repo, "pull_request", event, sha="f" * 40)
    assert result.returncode == 0
    assert values["status"] == "success"
    assert values["mode"] == "range"
    assert args[args.index("--branch") + 1] == head
    assert args[args.index("--since-commit") + 1] == base
    assert "--fail-on-scan-errors" in args
    assert "--only-verified" in args


def test_existing_branch_push(tmp_path, repo):
    _, base, head = repo
    result, values, args = scan(tmp_path, repo, "push", {"before": base, "after": head})
    assert result.returncode == 0
    assert values["mode"] == "range"
    assert args[args.index("--since-commit") + 1] == base


@pytest.mark.parametrize(
    "kind", ["new-branch", "equal", "force-push", "workflow_dispatch", "schedule"]
)
def test_full_history_paths_invoke_scanner(tmp_path, repo, kind):
    _, _, head = repo
    before = "0" * 40 if kind == "new-branch" else head if kind == "equal" else "e" * 40
    name = kind if kind in {"workflow_dispatch", "schedule"} else "push"
    result, values, args = scan(tmp_path, repo, name, {"before": before, "after": head})
    assert result.returncode == 0
    assert values["mode"] == "full"
    assert "--since-commit" not in args
    assert args[args.index("--branch") + 1] == head


@pytest.mark.parametrize(
    "code,payload,status,findings",
    [
        (0, "", "success", "0"),
        (183, '{"Verified":true}\n', "findings", "1"),
        (125, "", "error", "0"),
        (1, "", "error", "0"),
        (0, "invalid json", "error", ""),
        (183, "", "error", "0"),
        (0, '{"Verified":true}\n', "error", "1"),
        (183, '{"Verified":false}\n', "error", ""),
    ],
)
def test_errors_are_not_secret_findings(
    tmp_path, repo, code, payload, status, findings
):
    result, values, _ = scan(
        tmp_path, repo, "workflow_dispatch", {}, code=code, payload=payload
    )
    assert values["status"] == status
    assert values["findings"] == findings
    assert (result.returncode == 0) is (status == "success")
    if status == "error":
        assert "not a confirmed secret finding" in result.stdout


def test_missing_pr_base_fails_before_docker(tmp_path, repo):
    _, _, head = repo
    event = {"pull_request": {"base": {"sha": "e" * 40}, "head": {"sha": head}}}
    result, values, args = scan(tmp_path, repo, "pull_request", event)
    assert result.returncode != 0
    assert values["status"] == "error"
    assert args is None


def test_shallow_checkout_is_not_reported_clean(tmp_path, repo):
    work, base, head = repo
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--quiet", "--depth=1", work.as_uri(), str(shallow)],
        check=True,
    )
    result, values, args = scan(
        tmp_path, (shallow, base, head), "workflow_dispatch", {}
    )
    assert result.returncode != 0
    assert values["status"] == "error"
    assert args is None


def test_python_action_wires_the_trusted_wrapper():
    data = yaml.safe_load((SCRIPT.parent / "action.yml").read_text())
    step = next(s for s in data["runs"]["steps"] if s.get("id") == "trufflehog")
    assert step["run"] == 'bash "$GITHUB_ACTION_PATH/trufflehog.sh"'
    assert step["if"] == "inputs.run-trufflehog == 'true'"
    assert "security-scan-errors" in data["outputs"]
