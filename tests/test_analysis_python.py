"""Trusted source analysis runs with the Python version the project declares."""

import importlib
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
resolver = importlib.import_module("analysis_python")
adapter = importlib.import_module("forgejo_mutation")
SCRIPT = ROOT / ".ci" / "analysis_python.py"


def project(tmp_path, content=None):
    if content is not None:
        (tmp_path / ".python-version").write_text(content)
    return tmp_path


def test_project_without_pin_uses_the_default(tmp_path):
    assert resolver.analysis_python(project(tmp_path)) == "3.12"


@pytest.mark.parametrize("version", ["3.12", "3.13", "3.14"])
def test_supported_pin_is_followed(tmp_path, version):
    assert resolver.analysis_python(project(tmp_path, f"{version}\n")) == version


@pytest.mark.parametrize(
    "content", ["3.11\n", "3.15\n", "3.14.2\n", "", "\n3.14\n", " 3.14\n", "$(id)\n"]
)
def test_any_other_pin_is_refused(tmp_path, content):
    with pytest.raises(ValueError, match="Unsupported consumer .python-version"):
        resolver.analysis_python(project(tmp_path, content))


def test_symlinked_pin_is_refused(tmp_path):
    (tmp_path / "elsewhere").write_text("3.14\n")
    (tmp_path / ".python-version").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="regular file"):
        resolver.analysis_python(tmp_path)


def test_command_line_prints_the_version_or_fails(tmp_path):
    good = subprocess.run(
        [sys.executable, str(SCRIPT), str(project(tmp_path, "3.14\n"))],
        capture_output=True,
        text=True,
    )
    assert (good.returncode, good.stdout) == (0, "3.14\n")
    (tmp_path / ".python-version").write_text("2.7\n")
    bad = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True
    )
    assert bad.returncode != 0
    assert bad.stdout == ""


@pytest.mark.parametrize(
    ("workflow", "script"),
    [
        ("mutation-campaign.yml", "mutation_campaign.py plan"),
        ("mutation-campaign.yml", "mutation_campaign.py aggregate"),
        ("mutation-policy.yml", "mutation_evidence.py"),
    ],
)
def test_trusted_verifiers_run_with_the_resolved_version(workflow, script):
    content = (ROOT / ".github/workflows" / workflow).read_text()
    lines = [line for line in content.splitlines() if script in line]
    assert lines, script
    assert all('--python "$ANALYSIS_PYTHON"' in line for line in lines), lines
    assert len(re.findall(r"analysis_python\.py", content)) >= len(lines)


def test_forgejo_adapter_parses_in_process_when_versions_match(monkeypatch, tmp_path):
    scope = importlib.import_module("mutation_scope")
    monkeypatch.setattr(resolver, "running_python", lambda: "3.12")
    monkeypatch.setattr(adapter, "running_python", lambda: "3.12")
    seen = {}

    def targets(repo, base, head, *, source_root):
        seen.update(repo=repo, base=base, head=head, source_root=source_root)
        return ("calc.*add__mutmut_*",)

    monkeypatch.setattr(scope, "mutation_targets", targets)
    trusted = tmp_path / "trusted"
    result = adapter.python_targets(tmp_path, trusted, "base", "head")
    assert result == ["calc.*add__mutmut_*"]
    assert seen == {
        "repo": tmp_path,
        "base": "base",
        "head": "head",
        "source_root": trusted,
    }


def test_forgejo_adapter_delegates_to_the_project_python(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "running_python", lambda: "3.12")
    project(tmp_path, "3.14\n")
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(
            command, 0, "a.*f__mutmut_*\nb.*g__mutmut_*\n"
        )

    monkeypatch.setattr(adapter.subprocess, "run", run)
    trusted = tmp_path / "trusted"
    result = adapter.python_targets(tmp_path, trusted, "base", "head")
    assert result == ["a.*f__mutmut_*", "b.*g__mutmut_*"]
    ((command, options),) = calls
    assert command[:6] == ["uv", "run", "--no-project", "--python", "3.14", "python"]
    assert command[6:] == [
        str(ROOT / ".ci" / "mutation_scope.py"),
        "--repo",
        str(tmp_path),
        "--base",
        "base",
        "--head",
        "head",
        "--source-root",
        str(trusted),
    ]
    assert options["check"] is True
