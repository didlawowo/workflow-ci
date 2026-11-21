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
    "content",
    ["3.11\n", "3.15\n", "3.14.2\n", "", "\n3.14\n", " 3.14\n", "3.14\r\n", "$(id)\n"],
)
def test_any_other_pin_keeps_the_default(tmp_path, content):
    """Other languages and other pin forms must not fail the trusted analysis."""
    assert resolver.analysis_python(project(tmp_path, content)) == "3.12"


def test_symlinked_pin_is_ignored(tmp_path):
    (tmp_path / "elsewhere").write_text("3.14\n")
    (tmp_path / ".python-version").symlink_to(tmp_path / "elsewhere")
    assert resolver.analysis_python(tmp_path) == "3.12"


def command_line(*arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, arguments)],
        capture_output=True,
        text=True,
    )


def test_command_line_prints_the_version_or_fails(tmp_path):
    (tmp_path / "app").mkdir()
    project(tmp_path / "app", "3.14\n")
    good = command_line(tmp_path, "app")
    assert (good.returncode, good.stdout) == (0, "3.14\n")
    (tmp_path / "app" / ".python-version").write_text("2.7\n")
    other = command_line(tmp_path, "app")
    assert (other.returncode, other.stdout) == (0, "3.12\n")


@pytest.mark.parametrize("directory", ["..", "missing", "/etc", "link"])
def test_command_line_validates_the_directory_before_reading(tmp_path, directory):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    project(tmp_path, "3.14\n")
    (checkout / "link").symlink_to(tmp_path)
    result = command_line(checkout, directory)
    assert result.returncode != 0
    assert result.stdout == ""


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
    resolved = re.findall(r"analysis_python\.py\"? (\S+) (\S+)\)", content)
    assert len(resolved) >= len(lines), resolved
    assert all(checkout.strip('"').endswith("pr") for checkout, _ in resolved), resolved


def test_forgejo_adapter_parses_in_process_when_versions_match(monkeypatch, tmp_path):
    scope = importlib.import_module("mutation_scope")
    monkeypatch.setattr(adapter, "running_python", lambda: "3.12")
    seen = {}

    def targets(repo, base, head, *, source_root):
        seen.update(repo=repo, base=base, head=head, source_root=source_root)
        return ("calc.*add__mutmut_*",)

    monkeypatch.setattr(scope, "mutation_targets", targets)
    trusted = tmp_path / "trusted"
    result = adapter.python_targets(tmp_path, trusted, "base", "head", "3.12")
    assert result == ["calc.*add__mutmut_*"]
    assert seen == {
        "repo": tmp_path,
        "base": "base",
        "head": "head",
        "source_root": trusted,
    }


def test_forgejo_adapter_delegates_to_the_project_python(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "running_python", lambda: "3.12")
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(
            command, 0, "a.*f__mutmut_*\nb.*g__mutmut_*\n"
        )

    monkeypatch.setattr(adapter.subprocess, "run", run)
    trusted = tmp_path / "trusted"
    result = adapter.python_targets(tmp_path, trusted, "base", "head", "3.14")
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
    assert options["check"] is False


def test_forgejo_adapter_reports_the_analysis_error(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "running_python", lambda: "3.12")

    def run(command, **options):
        return subprocess.CompletedProcess(command, 1, "", "Cannot compute scope: bad")

    monkeypatch.setattr(adapter.subprocess, "run", run)
    with pytest.raises(ValueError, match="Python 3.13: Cannot compute scope: bad"):
        adapter.python_targets(tmp_path, tmp_path, "base", "head", "3.13")


def test_forgejo_sandbox_runs_the_project_python(tmp_path):
    default = adapter.sandbox_command(tmp_path, "python", "a", "b")
    pinned = adapter.sandbox_command(tmp_path, "python", "a", "b", "3.14")
    assert "MUTATION_PYTHON_VERSION=3.12" in default
    assert "MUTATION_PYTHON_VERSION=3.14" in pinned


def git(repo, *arguments):
    return subprocess.run(
        ["git", "-C", str(repo), *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_scope_command_line_reads_the_layout_from_the_source_root(tmp_path):
    """The scope of the candidate follows the source paths of the trusted tree."""
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    (trusted / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths = ["lib/"]\n')
    repo = tmp_path / "candidate"
    (repo / "lib").mkdir(parents=True)
    (repo / "other").mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "ci@example.test")
    git(repo, "config", "user.name", "Scope test")
    (repo / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths = ["other/"]\n')
    for folder in ("lib", "other"):
        (repo / folder / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    for folder in ("lib", "other"):
        (repo / folder / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    git(repo, "commit", "-qam", "head")
    head = git(repo, "rev-parse", "HEAD")
    arguments = [sys.executable, str(ROOT / ".ci" / "mutation_scope.py")]
    arguments += ["--repo", str(repo), "--base", base, "--head", head]
    own = subprocess.run(arguments, capture_output=True, text=True, check=True)
    trusted_layout = subprocess.run(
        [*arguments, "--source-root", str(trusted)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert own.stdout.split() == ["other.calc.*add__mutmut_*"]
    assert trusted_layout.stdout.split() == ["lib.calc.*add__mutmut_*"]
