"""Tests de documentation_scope.py avec de vrais dépôts Git locaux."""

import os
from pathlib import Path

import pytest
import yaml

from documentation_scope import decide, documentation_only, main
from image_changes import git

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["docs/**", "artifact/**", "**/*.md"]
UNKNOWN_SHA = "0" * 40


def commit(*names, content="changed"):
    for name in names:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git("add", ".")
    git("commit", "-qm", "fixture")
    return git("rev-parse", "HEAD")


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-q")
    git("config", "user.name", "CI")
    git("config", "user.email", "ci@example.invalid")
    return commit("src/app.py", "original")


@pytest.mark.parametrize(
    "names",
    [
        ["docs/guide.md"],
        ["artifact/2026-11-17-page.html"],
        ["README.md"],
        ["a/b/NOTES.md", "docs/x.txt"],
    ],
)
def test_documentation_only_true_for_doc_changes(base, names):
    head = commit(*names)
    assert documentation_only(base, head, DOCS)


@pytest.mark.parametrize(
    "name",
    [
        ".github/workflows/ci.yml",
        "sonar-project.properties",
        "pyproject.toml",
        "src/app.py",
        "scripts/run.sh",
    ],
)
def test_documentation_only_false_when_non_doc_changes(base, name):
    commit("docs/guide.md")
    head = commit(name, content="non-doc")
    assert not documentation_only(base, head, DOCS)


def test_documentation_only_false_when_source_moved_to_docs(base):
    Path("docs").mkdir(parents=True, exist_ok=True)
    git("mv", "src/app.py", "docs/app.py")
    git("commit", "-qm", "move")
    head = git("rev-parse", "HEAD")
    assert not documentation_only(base, head, DOCS)


def test_documentation_only_false_for_empty_diff_or_patterns(base):
    assert not documentation_only(base, base, DOCS)
    head = commit("docs/guide.md")
    assert not documentation_only(base, head, [])


def test_documentation_only_uses_merge_base(base):
    origin = git("rev-parse", "--abbrev-ref", "HEAD")
    git("checkout", "-qb", "feature")
    feature_head = commit("docs/guide.md")
    git("checkout", origin)
    start_head = commit("src/other.py")
    assert documentation_only(start_head, feature_head, DOCS)


def test_decide_true_for_pull_request_doc(base):
    head = commit("docs/guide.md")
    environ = {
        "DOCUMENTATION_PATHS": "\n".join(DOCS),
        "EVENT_NAME": "pull_request",
        "BASE_SHA": base,
        "HEAD_SHA": head,
    }
    assert decide(environ)


@pytest.mark.parametrize(
    "overrides",
    [
        {"EVENT_NAME": "push"},
        {"DOCUMENTATION_PATHS": ""},
        {"BASE_SHA": ""},
        {"BASE_SHA": UNKNOWN_SHA},
    ],
)
def test_decide_fail_closed(base, overrides):
    head = commit("docs/guide.md")
    environ = {
        "DOCUMENTATION_PATHS": "\n".join(DOCS),
        "EVENT_NAME": "pull_request",
        "BASE_SHA": base,
        "HEAD_SHA": head,
    }
    environ.update(overrides)
    assert not decide(environ)


@pytest.mark.parametrize(
    "name",
    [".github/workflows/ci.yml", "uv.lock", "web/package.json", "sonar-project.properties"],
)
def test_protected_paths_are_never_documentation_even_with_a_catch_all(base, name):
    head = commit(name)
    assert not documentation_only(base, head, ["**"])


def test_symlink_or_submodule_under_documentation_keeps_full_verification(base):
    Path("docs").mkdir()
    os.symlink("../src/app.py", "docs/link.md")
    git("add", ".")
    git("commit", "-qm", "symlink")
    assert not documentation_only(base, git("rev-parse", "HEAD"), DOCS)


def test_deleted_documentation_file_is_documentation(base):
    commit("docs/old.md")
    middle = git("rev-parse", "HEAD")
    git("rm", "-q", "docs/old.md")
    git("commit", "-qm", "remove")
    assert documentation_only(middle, git("rev-parse", "HEAD"), DOCS)


def test_unknown_head_and_invalid_patterns(base):
    env = {
        "DOCUMENTATION_PATHS": "docs/**",
        "EVENT_NAME": "pull_request",
        "BASE_SHA": base,
        "HEAD_SHA": UNKNOWN_SHA,
    }
    assert not decide(env)
    with pytest.raises(ValueError):
        decide({**env, "DOCUMENTATION_PATHS": ":(exclude)src/**"})


def test_main_writes_the_github_output(base, tmp_path, monkeypatch):
    head = commit("docs/guide.md")
    output = tmp_path / "out.txt"
    for key, value in {
        "DOCUMENTATION_PATHS": "docs/**",
        "EVENT_NAME": "pull_request",
        "BASE_SHA": base,
        "HEAD_SHA": head,
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    main()
    assert output.read_text() == "documentation-only=true\n"
