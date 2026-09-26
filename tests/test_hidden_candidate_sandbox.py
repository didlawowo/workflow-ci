from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_PATH = ROOT / "hidden-evaluators" / "common" / "sandbox.py"
SPEC = importlib.util.spec_from_file_location("hidden_candidate_sandbox", SANDBOX_PATH)
assert SPEC and SPEC.loader
sandbox_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sandbox_module)


def test_candidate_sandbox_enforces_real_process_boundary(tmp_path: Path, monkeypatch) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "README.md").write_text("candidate\n", encoding="utf-8")

    sentinel = ROOT / ".hidden-sandbox-trusted-sentinel"
    sentinel.write_text("trusted-secret\n", encoding="utf-8")
    parent_netns = os.stat("/proc/self/ns/net").st_ino
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-cross")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "must-not-cross")

    code = f"""
import json
import os
from pathlib import Path

trusted = Path({str(sentinel)!r})
try:
    trusted.read_text(encoding="utf-8")
    trusted_readable = True
except (PermissionError, OSError):
    trusted_readable = False

print(json.dumps({{
    "uid": os.geteuid(),
    "gid": os.getegid(),
    "netns": os.stat("/proc/self/ns/net").st_ino,
    "trusted_readable": trusted_readable,
    "github_token": os.environ.get("GITHUB_TOKEN"),
    "kubernetes_service_host": os.environ.get("KUBERNETES_SERVICE_HOST"),
}}))
"""

    try:
        with sandbox_module.CandidateSandbox(candidate) as sandbox:
            completed = sandbox.run([sys.executable, "-c", code])
    finally:
        sentinel.unlink(missing_ok=True)

    observed = json.loads(completed.stdout)
    assert observed["uid"] == sandbox_module.SANDBOX_UID
    assert observed["gid"] == sandbox_module.SANDBOX_UID
    assert observed["netns"] != parent_netns
    assert observed["trusted_readable"] is False
    assert observed["github_token"] is None
    assert observed["kubernetes_service_host"] is None
