"""Contrat d'interpréteur pour la mutation Python isolée (policy centrale)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

POLICY = Path(__file__).resolve().parents[1] / ".github/workflows/mutation-policy.yml"


def _select_python(tmp_path: Path, version: str | None) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "pr"
    project.mkdir()
    if version is not None:
        (project / ".python-version").write_text(version, encoding="utf-8")

    content = POLICY.read_text(encoding="utf-8")
    start = '            PYTHON_VERSION="${MUTATION_PYTHON_VERSION:-3.12}"'
    end = '            rm -rf "$VENV"'
    assert content.count(start) == 1
    body = content.split(start, 1)[1].split(end, 1)[0]
    script = (start + body).replace("${{ inputs.working-directory }}", ".")
    script = "\n".join(line[12:] for line in script.splitlines())
    env = os.environ.copy()
    env.pop("MUTATION_PYTHON_VERSION", None)
    env["GITHUB_WORKSPACE"] = str(tmp_path)
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_consumer_python_314_is_selected(tmp_path: Path) -> None:
    result = _select_python(tmp_path, "3.14\n")
    assert result.returncode == 0, result.stderr
    assert "Using Python 3.14" in result.stdout


def test_legacy_consumer_keeps_312(tmp_path: Path) -> None:
    result = _select_python(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert "Using Python 3.12" in result.stdout


def test_unsupported_version_fails_closed(tmp_path: Path) -> None:
    result = _select_python(tmp_path, "3.99\n")
    assert result.returncode != 0
    assert "Unsupported consumer .python-version" in result.stdout


def test_script_content_cannot_be_evaluated(tmp_path: Path) -> None:
    result = _select_python(tmp_path, "3.14; touch /tmp/mutation-unsafe\n")
    assert result.returncode != 0
    assert "Unsupported consumer .python-version" in result.stdout
