"""Campaign bootstrap interpreter contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / ".ci" / "mutation_campaign_bootstrap.sh"
FAKE_UV = """#!/usr/bin/env bash
set -euo pipefail
echo "uv $*" >> "$UV_LOG"
if [[ "$1" == "venv" ]]; then
  mkdir -p "$2/bin"
  printf '#!/usr/bin/env bash\\n' > "$2/bin/python"
  chmod +x "$2/bin/python"
fi
"""


def _bootstrap(
    tmp_path: Path, version: str | None, virtual_env: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    if version is not None:
        (project / ".python-version").write_text(version, encoding="utf-8")
    tools = tmp_path / "bin"
    tools.mkdir(parents=True)
    uv = tools / "uv"
    uv.write_text(FAKE_UV, encoding="utf-8")
    uv.chmod(0o755)
    log = tmp_path / "uv.log"
    env = os.environ.copy()
    for name in ("VIRTUAL_ENV", "MUTATION_PYTHON_VERSION"):
        env.pop(name, None)
    env |= {
        "PATH": f"{tools}:{env['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "UV_LOG": str(log),
    }
    if virtual_env is not None:
        env["VIRTUAL_ENV"] = virtual_env
    result = subprocess.run(
        ["bash", "-c", f'source "{BOOTSTRAP}"'],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def test_consumer_python_314_is_provisioned(tmp_path: Path) -> None:
    result, calls = _bootstrap(tmp_path, "3.14\n")
    assert result.returncode == 0, result.stderr
    assert "uv python install 3.14" in calls
    assert "--python 3.14" in calls


def test_consumer_without_python_version_keeps_312(tmp_path: Path) -> None:
    result, calls = _bootstrap(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert "uv python install 3.12" in calls


def test_unsupported_version_fails_before_provisioning(tmp_path: Path) -> None:
    result, calls = _bootstrap(tmp_path, "3.99\n")
    assert result.returncode != 0
    assert "unsupported consumer .python-version" in result.stderr
    assert "3.99" not in result.stderr
    assert calls == ""


def test_version_file_content_is_never_evaluated(tmp_path: Path) -> None:
    marker = tmp_path / "unsafe"
    result, calls = _bootstrap(tmp_path, f"3.14; touch {marker}\n")
    assert result.returncode != 0
    assert not marker.exists()
    assert calls == ""


def test_python_313_is_provisioned(tmp_path: Path) -> None:
    result, calls = _bootstrap(tmp_path, "3.13")
    assert result.returncode == 0, result.stderr
    assert "uv python install 3.13" in calls


def test_empty_or_crlf_version_fails_closed(tmp_path: Path) -> None:
    for name, content in (("empty", ""), ("crlf", "3.14\r\n")):
        result, calls = _bootstrap(tmp_path / name, content)
        assert result.returncode != 0
        assert calls == ""


def test_symlinked_version_file_is_rejected_without_being_read(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("private-value\n", encoding="utf-8")
    (project / ".python-version").symlink_to(secret)
    result, calls = _bootstrap(tmp_path, None)
    assert result.returncode != 0
    assert "regular file" in result.stderr
    assert "private-value" not in result.stderr
    assert calls == ""


def test_existing_virtual_env_ignores_version_file(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    python.chmod(0o755)
    result, calls = _bootstrap(tmp_path, "3.99\n", virtual_env=str(venv))
    assert result.returncode == 0, result.stderr
    assert "uv python install" not in calls


def test_toolchain_keeps_consumer_pytest_and_requires_pytest9_safe_mutmut(
    tmp_path: Path,
) -> None:
    """A consumer on pytest>=9 must not be unresolvable: the only pytest 9.2
    incompatibility (mutmut #615) is fixed in mutmut 3.8.1, so the bootstrap
    raises the mutmut floor instead of capping pytest."""
    result, calls = _bootstrap(tmp_path, None)
    assert result.returncode == 0, result.stderr
    install = next(line for line in calls.splitlines() if "pip install" in line)
    assert "pytest<9" not in install
    assert "mutmut>=3.8.1,<4" in install
