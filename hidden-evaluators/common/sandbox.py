from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

SANDBOX_UID = 65532
RESULT_PREFIX = "__WORKFLOW_CI_RESULT__="
TRUSTED_REPO = Path(__file__).resolve().parents[2]


class CandidateSandbox:
    """Execute candidate code outside the trusted verifier process."""

    def __init__(self, candidate: Path) -> None:
        self.original = candidate.resolve()
        self.root: Path | None = None
        self.candidate: Path | None = None
        self.home: Path | None = None
        self._trusted_mode: int | None = None
        self._candidate_mode: int | None = None

    def __enter__(self) -> "CandidateSandbox":
        if os.geteuid() != 0:
            raise RuntimeError("hidden sandbox requires a root ARC runner")
        for binary in ("setpriv", "unshare"):
            if shutil.which(binary) is None:
                raise RuntimeError(f"hidden sandbox requires {binary}")

        self.root = Path(tempfile.mkdtemp(prefix="workflow-ci-hidden-"))
        self.root.chmod(0o711)
        self.candidate = self.root / "candidate"
        shutil.copytree(self.original, self.candidate, symlinks=True)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)

        for directory, dirs, files in os.walk(self.candidate, followlinks=False):
            os.chown(directory, SANDBOX_UID, SANDBOX_UID, follow_symlinks=False)
            for name in dirs + files:
                path = Path(directory) / name
                os.chown(path, SANDBOX_UID, SANDBOX_UID, follow_symlinks=False)
        os.chown(self.home, SANDBOX_UID, SANDBOX_UID)

        self._trusted_mode = TRUSTED_REPO.stat().st_mode & 0o777
        self._candidate_mode = self.original.stat().st_mode & 0o777
        TRUSTED_REPO.chmod(0o700)
        self.original.chmod(0o700)
        return self

    def __exit__(self, *_: object) -> None:
        if self._trusted_mode is not None:
            TRUSTED_REPO.chmod(self._trusted_mode)
        if self._candidate_mode is not None:
            self.original.chmod(self._candidate_mode)
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)

    def _command(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        network: bool = False,
    ) -> list[str]:
        assert self.root is not None and self.home is not None
        command = []
        if not network:
            command += ["unshare", "--net", "--"]
        command += [
            "setpriv",
            f"--reuid={SANDBOX_UID}",
            f"--regid={SANDBOX_UID}",
            "--clear-groups",
            "--no-new-privs",
            "--bounding-set=-all",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            "env",
            "-i",
            f"PATH={os.environ['PATH']}",
            f"HOME={self.home}",
            f"TMPDIR={self.home}",
            "CI=true",
            "PYTHONNOUSERSITE=1",
        ]
        for key, value in sorted((env or {}).items()):
            if any(token in key.upper() for token in ("TOKEN", "SECRET", "PASSWORD", "KEY")):
                raise ValueError(f"refusing secret-like sandbox environment key: {key}")
            command.append(f"{key}={value}")
        return command + argv

    def run(
        self,
        argv: list[str],
        *,
        input_text: str = "",
        env: dict[str, str] | None = None,
        timeout: int = 120,
        network: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert self.candidate is not None
        process = subprocess.Popen(
            self._command(argv, env=env, network=network),
            cwd=self.candidate,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            raise RuntimeError("candidate sandbox timed out")
        if process.returncode:
            detail = (stdout + "\n" + stderr).strip()
            raise RuntimeError(
                f"candidate sandbox exited {process.returncode}: {detail[-3000:]}"
            )
        return subprocess.CompletedProcess(
            process.args, process.returncode, stdout, stderr
        )

    def request_python(self, bridge: str, request: dict, *, timeout: int = 120) -> dict:
        completed = self.run(
            [sys.executable, "-c", bridge],
            input_text=json.dumps(request, allow_nan=False),
            timeout=timeout,
        )
        return self._result(completed.stdout)

    def prepare_go(self) -> None:
        """Populate an isolated module cache before network is disabled for candidate tests."""
        assert self.home is not None
        self.run(
            ["go", "mod", "download"],
            env={
                "GOMODCACHE": str(self.home / "go-mod"),
                "GOCACHE": str(self.home / "go-cache"),
            },
            timeout=180,
            network=True,
        )

    def request_go_test(self, bridge_source: str, request: dict, *, timeout: int = 180) -> dict:
        assert self.candidate is not None and self.home is not None
        bridge = self.candidate / "pkg" / "handlers" / "workflow_ci_bridge_test.go"
        if not bridge.parent.is_dir():
            raise RuntimeError("candidate does not expose pkg/handlers")
        bridge.write_text(bridge_source, encoding="utf-8")
        os.chown(bridge, SANDBOX_UID, SANDBOX_UID)
        try:
            completed = self.run(
                [
                    "go",
                    "test",
                    "./pkg/handlers",
                    "-run",
                    "^TestWorkflowCIBridge$",
                    "-count=1",
                    "-v",
                ],
                env={
                    "GOMODCACHE": str(self.home / "go-mod"),
                    "GOCACHE": str(self.home / "go-cache"),
                    "WORKFLOW_CI_REQUEST": json.dumps(request, allow_nan=False),
                },
                timeout=timeout,
            )
        finally:
            bridge.unlink(missing_ok=True)
        return self._result(completed.stdout)

    @staticmethod
    def _result(stdout: str) -> dict:
        values = [
            line.strip().removeprefix(RESULT_PREFIX)
            for line in stdout.splitlines()
            if line.strip().startswith(RESULT_PREFIX)
        ]
        if len(values) != 1:
            raise RuntimeError("candidate sandbox returned no unique protocol result")
        value = json.loads(values[0])
        if not isinstance(value, dict):
            raise RuntimeError("candidate sandbox protocol result must be an object")
        return value
