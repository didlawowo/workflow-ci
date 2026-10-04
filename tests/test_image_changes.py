"""Release comparison tests using real local Git repositories, without network."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from image_changes import app_changed, paths, release_tags, resolve_tags

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def commit(name, content="changed"):
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git("add", ".")
    git("commit", "-qm", "fixture")
    return git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Emulate Linux executable case sensitivity even on a macOS filesystem.
    real_git = shutil.which("git")
    tools = tmp_path.parent / (tmp_path.name + "-git-bin")
    tools.mkdir()
    shim = tools / "git"
    shim.write_text(
        '#!/bin/sh\n[ "${0##*/}" = git ] || exit 127\n'
        + f'exec {shlex.quote(real_git)} "$@"\n'
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    monkeypatch.chdir(tmp_path)
    git("init", "-q")
    git("config", "user.name", "CI")
    git("config", "user.email", "ci@example.invalid")
    base = commit("src/app.py", "original")
    git("tag", "-a", "v1.0.0", "-m", "initial")
    return base


@pytest.mark.parametrize(
    "name", ["helm/values.yaml", "docs/guide.md", "config/runtime.yml"]
)
def test_declared_non_image_change_skips_build(repo, name):
    head = commit(name)
    assert not app_changed(repo, head, ["**"], ["helm/**", "docs/**", "config/**"])


@pytest.mark.parametrize(
    "name",
    [
        "Dockerfile",
        "src/app.py",
        "go.mod",
        "uv.lock",
        "nested/file with spaces\nand newline",
    ],
)
def test_image_inputs_and_unknown_files_rebuild(repo, name):
    head = commit(name)
    assert app_changed(repo, head, ["**"], ["helm/**", "docs/**"])


def test_explicit_paths_and_exclusion_precedence(repo):
    head = commit("config/runtime.yml")
    assert not app_changed(repo, head, ["src/**", "Dockerfile"], [])
    assert app_changed(repo, head, ["config/**"], [])
    assert not app_changed(repo, head, ["config/**"], ["config/runtime.yml"])


def test_deletion_and_rename_out_of_image_paths_rebuild(repo):
    Path("docs").mkdir()
    git("mv", "src/app.py", "docs/moved.py")
    git("commit", "-qm", "rename")
    assert app_changed(repo, git("rev-parse", "HEAD"), ["src/**"], ["docs/**"])


def test_nearest_ancestor_not_numerically_largest_or_checkout_head(repo):
    middle = commit("docs/guide.md")
    git("tag", "v1.1.0")
    commit("helm/values.yaml")
    git("tag", "v1.2.0")
    # Unrelated tag family and future release must not become the comparison base.
    git("tag", "chart-v9.0.0")
    commit("src/app.py", "future")
    git("tag", "v9.0.0")
    previous, base, head = resolve_tags("v1.2.0", "", "v")
    assert previous == "v1.1.0"
    assert base == middle
    assert head == git("rev-parse", "v1.2.0")
    assert not app_changed(base, head, ["**"], ["helm/**", "docs/**"])


def test_different_family_and_invalid_tags_are_not_releases(repo):
    git("tag", "v1.0")
    git("tag", "v1.0.1-beta")
    git("tag", "chart-v1.0.0")
    assert release_tags("v") == ["v1.0.0"]
    assert release_tags("chart-v") == ["chart-v1.0.0"]
    assert release_tags("x.") == []


def test_ambiguous_base_fails_but_explicit_base_resolves(repo):
    git("tag", "v0.9.0")
    commit("docs/guide.md")
    git("tag", "v1.1.0")
    with pytest.raises(
        ValueError, match="^Ambiguous previous release; supply previous-tag explicitly$"
    ):
        resolve_tags("v1.1.0", "", "v")
    assert resolve_tags("v1.1.0", "v1.0.0", "v")[0] == "v1.0.0"


@pytest.mark.parametrize(
    "current,previous",
    [
        ("v1.0.0", ""),
        ("main", ""),
        ("v404.0.0", ""),
        ("v1.0.0", "v1.0.0"),
        ("v1.0.0", "missing"),
    ],
)
def test_missing_invalid_or_identical_tags_fail(repo, current, previous):
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        resolve_tags(current, previous, "v")


def test_same_commit_and_non_ancestor_fail(repo):
    git("tag", "v1.1.0")
    with pytest.raises(
        ValueError, match="^Release tags must point to different commits$"
    ):
        resolve_tags("v1.1.0", "v1.0.0", "v")
    git("checkout", "--orphan", "unrelated")
    git("rm", "-rf", ".")
    commit("other")
    git("tag", "v2.0.0")
    with pytest.raises(subprocess.CalledProcessError):
        resolve_tags("v2.0.0", "v1.0.0", "v")


def test_merge_branch_release_not_selected_as_base(repo):
    git("checkout", "-qb", "side")
    commit("side.txt")
    git("tag", "v9.0.0")
    git("checkout", "-")
    commit("docs/guide.md")
    git("tag", "v1.1.0")
    git("merge", "--no-ff", "side", "-m", "merge")
    git("tag", "v1.2.0")
    assert resolve_tags("v1.2.0", "", "v")[0] == "v1.1.0"


def test_shallow_clone_fails(repo, tmp_path, monkeypatch):
    commit("docs/guide.md")
    git("tag", "v1.1.0")
    destination = tmp_path / "clone"
    git("clone", "-q", "--depth=1", tmp_path.as_uri(), str(destination))
    monkeypatch.chdir(destination)
    with pytest.raises(
        ValueError, match=r"^Full history and tags required \(fetch-depth: 0\)$"
    ):
        resolve_tags("v1.1.0", "v1.0.0", "v")


@pytest.mark.parametrize(
    "pattern", ["/src/**", ":(exclude)**", "!src/**", "src/../secret"]
)
def test_unsafe_pathspecs_rejected(pattern):
    with pytest.raises(
        ValueError,
        match="^Use repository-relative Git glob patterns, without pathspec magic$",
    ):
        paths(pattern)


def test_patterns_strip_blank_lines_and_require_inclusion(repo):
    assert paths("  src/**  \n\n Dockerfile\n") == ["src/**", "Dockerfile"]
    with pytest.raises(ValueError, match="^app-paths must not be empty$"):
        app_changed(repo, repo, [], [])
    with pytest.raises(ValueError, match="^Git comparison failed$"):
        app_changed("missing", repo, ["**"], [])


def run_action(tmp_path, **overrides):
    env = {
        **os.environ,
        "CURRENT_TAG": "v1.1.0",
        "PREVIOUS_TAG": "",
        "TAG_PREFIX": "v",
        "APP_PATHS": "**",
        "NON_IMAGE_PATHS": "helm/**\ndocs/**",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        **overrides,
    }
    return subprocess.run(
        ["python3", str(ROOT / "image_changes.py")],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "name,value", [("docs/guide.md", "false"), ("Dockerfile", "true")]
)
def test_entrypoint_writes_outputs_only_after_success(repo, tmp_path, name, value):
    commit(name)
    git("tag", "v1.1.0")
    result = run_action(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (
        tmp_path / "output"
    ).read_text() == f"app-changed={value}\nprevious-tag=v1.0.0\n"
    (tmp_path / "output").unlink()
    result = run_action(tmp_path, CURRENT_TAG="missing")
    assert result.returncode != 0
    assert not (tmp_path / "output").exists()


def test_action_passes_inputs_as_environment_and_exposes_outputs():
    action = yaml.safe_load(
        (ROOT / ".github/actions/detect-image-changes/action.yml").read_text()
    )
    step = action["runs"]["steps"][0]
    assert step["shell"] == "bash"
    assert "${{ inputs." not in step["run"]
    assert step["env"]["CURRENT_TAG"] == "${{ inputs.current-tag }}"
    assert (
        action["outputs"]["app-changed"]["value"]
        == "${{ steps.detect.outputs.app-changed }}"
    )
    assert action["inputs"]["app-paths"]["default"] == "**"


@pytest.mark.parametrize(
    "name,value", [("docs/guide.md", "false"), ("Dockerfile", "true")]
)
def test_main_directly_exercises_outputs_and_summary(
    repo, tmp_path, monkeypatch, capsys, name, value
):
    from image_changes import main

    commit(name)
    git("tag", "v1.1.0")
    settings = {
        "CURRENT_TAG": "v1.1.0",
        "PREVIOUS_TAG": "",
        "TAG_PREFIX": "v",
        "APP_PATHS": "**",
        "NON_IMAGE_PATHS": "helm/**\ndocs/**",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }
    for key, val in settings.items():
        monkeypatch.setenv(key, val)
    (tmp_path / "output").write_text("existing=kept\n")
    main()
    assert (
        tmp_path / "output"
    ).read_text() == f"existing=kept\napp-changed={value}\nprevious-tag=v1.0.0\n"
    assert capsys.readouterr().out == f"Compared v1.0.0..v1.1.0: app-changed={value}\n"


def test_main_failure_does_not_publish_false(repo, tmp_path, monkeypatch):
    from image_changes import main

    for key, val in {
        "CURRENT_TAG": "missing",
        "PREVIOUS_TAG": "",
        "TAG_PREFIX": "v",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }.items():
        monkeypatch.setenv(key, val)
    with pytest.raises(
        ValueError, match="^Current tag must be an existing stable release tag$"
    ):
        main()
    assert not (tmp_path / "output").exists()


def test_first_release_has_actionable_error(repo):
    with pytest.raises(
        ValueError, match="^No previous release tag; supply previous-tag explicitly$"
    ):
        resolve_tags("v1.0.0", "", "v")


def test_previous_tag_must_be_in_same_release_family(repo):
    git("tag", "chart-v1.0.0")
    commit("docs/guide.md")
    git("tag", "v1.1.0")
    for previous in ("v1.1.0", "chart-v1.0.0", "missing"):
        with pytest.raises(
            ValueError, match="^Previous tag must be a distinct existing release tag$"
        ):
            resolve_tags("v1.1.0", previous, "v")


def test_untagged_parent_and_other_family_cannot_change_resolved_base(repo):
    commit("docs/guide.md")
    git("tag", "v1.1.0")
    commit("docs/second.md")
    git("tag", "-a", "chart-v9.0.0", "-m", "chart")
    commit("docs/third.md")
    git("tag", "v1.2.0")
    assert resolve_tags("v1.2.0", "", "v")[0] == "v1.1.0"


def test_newer_nearby_side_tag_not_used_as_base(repo):
    commit("docs/base.md")
    git("tag", "v1.1.0")
    git("branch", "side")
    for number in range(4):
        commit(f"docs/main-{number}.md")
    git("checkout", "side")
    commit("side.txt")
    git("tag", "v9.0.0")
    git("checkout", "-")
    git("merge", "--no-ff", "side", "-m", "merge")
    git("tag", "v1.2.0")
    # Current's parent is main; add a following release so a side tag is reachable
    # in the searched history, with shorter distance than the first-parent tag.
    commit("docs/followup.md")
    git("tag", "v1.3.0")
    # Exclude v1.2.0 from the candidate family by removing that fixture tag.
    git("tag", "-d", "v1.2.0")
    assert resolve_tags("v1.3.0", "", "v")[0] == "v1.1.0"


def test_main_honors_explicit_base(repo, tmp_path, monkeypatch):
    from image_changes import main

    git("tag", "v0.9.0")
    commit("docs/guide.md")
    git("tag", "v1.1.0")
    for key, val in {
        "CURRENT_TAG": "v1.1.0",
        "PREVIOUS_TAG": "v1.0.0",
        "TAG_PREFIX": "v",
        "APP_PATHS": "**",
        "NON_IMAGE_PATHS": "docs/**",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }.items():
        monkeypatch.setenv(key, val)
    main()
    assert (
        tmp_path / "output"
    ).read_text() == "app-changed=false\nprevious-tag=v1.0.0\n"
