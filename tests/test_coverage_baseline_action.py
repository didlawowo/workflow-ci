"""Behavioral contract for the committed main coverage baseline."""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "coverage-baseline" / "action.yml"
TEXT = ACTION.read_text(encoding="utf-8")


def shell() -> str:
    match = re.search(r"^      run: \|\n((?:        .*\n|\n)+)", TEXT, re.MULTILINE)
    assert match is not None
    return textwrap.dedent(match.group(1))


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def init_repo(tmp_path: Path, percentage: str = "80.0") -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    baseline = repo / ".ci" / "coverage-main.json"
    baseline.parent.mkdir()
    baseline.write_text(
        '{"coverage_percentage": "' + percentage + '", "schema_version": 1, "source_sha": "base"}\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return repo, git(repo, "rev-parse", "HEAD")


def run_action(
    repo: Path,
    tmp_path: Path,
    *,
    mode: str,
    current: str,
    base_sha: str = "",
    source_sha: str = "",
):
    output = tmp_path / f"output-{mode}-{current.replace('.', '_')}"
    output.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", shell()],
        cwd=repo,
        env={
            **os.environ,
            "MODE": mode,
            "CURRENT": current,
            "BASELINE_PATH": ".ci/coverage-main.json",
            "BASE_SHA": base_sha,
            "SOURCE_SHA": source_sha,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_RUN_ID": "123",
            "RUNNER_TEMP": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output.read_text(encoding="utf-8")


def test_compare_accepts_preserved_or_improved_coverage(tmp_path):
    repo, base = init_repo(tmp_path, "80.0")

    result, output = run_action(repo, tmp_path, mode="compare", current="81.2", base_sha=base)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "baseline-percentage=80.0" in output
    assert "delta=1.2" in output


def test_compare_rejects_coverage_regression(tmp_path):
    repo, base = init_repo(tmp_path, "80.0")

    result, _ = run_action(repo, tmp_path, mode="compare", current="79.9", base_sha=base)

    assert result.returncode != 0
    assert "Coverage decreased from 80.0% on main to 79.9%" in (result.stdout + result.stderr)


def test_compare_reads_protected_base_not_pr_worktree(tmp_path):
    repo, base = init_repo(tmp_path, "80.0")
    (repo / ".ci" / "coverage-main.json").write_text(
        '{"coverage_percentage": "0", "schema_version": 1}\n', encoding="utf-8"
    )

    result, _ = run_action(repo, tmp_path, mode="compare", current="79.9", base_sha=base)

    assert result.returncode != 0
    assert "Coverage decreased from 80.0% on main to 79.9%" in (result.stdout + result.stderr)


def test_write_updates_baseline_once_per_source_commit(tmp_path):
    repo, _ = init_repo(tmp_path, "80.0")

    first, output = run_action(
        repo, tmp_path, mode="write", current="81.5", source_sha="merge-sha"
    )
    assert first.returncode == 0, first.stderr + first.stdout
    assert "changed=true" in output
    content = (repo / ".ci" / "coverage-main.json").read_text(encoding="utf-8")
    assert '"coverage_percentage": "81.5"' in content
    assert '"source_sha": "merge-sha"' in content

    second, output = run_action(
        repo, tmp_path, mode="write", current="81.5", source_sha="merge-sha"
    )
    assert second.returncode == 0
    assert "changed=false" in output



def test_compare_allows_first_consumer_without_baseline(tmp_path):
    repo = tmp_path / "repo-missing"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    (repo / "README.md").write_text("bootstrap\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base without coverage"], cwd=repo, check=True)
    base = git(repo, "rev-parse", "HEAD")

    result, output = run_action(
        repo, tmp_path, mode="compare", current="72.3", base_sha=base
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Coverage baseline bootstrap" in result.stdout
    assert "baseline-missing=true" in output
    assert "baseline-percentage=" in output


def test_compare_marks_existing_baseline_present(tmp_path):
    repo, base = init_repo(tmp_path, "80.0")
    result, output = run_action(repo, tmp_path, mode="compare", current="80.0", base_sha=base)
    assert result.returncode == 0
    assert "baseline-missing=false" in output
