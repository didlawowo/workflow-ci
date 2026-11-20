"""Behaviour of the Forgejo release engine and of its action contract."""

import importlib
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
release = importlib.import_module("forgejo_release")

ACTION = ROOT / ".forgejo/actions/release/action.yml"
GITHUB_BUMP_ACTION = ROOT / ".github/actions/git-cliff-bump/action.yml"


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def commit(repo, subject, filename):
    (repo / filename).write_text(subject)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    commit(path, "feat: initial", "a.txt")
    git(path, "tag", "v0.5.1")
    return path


@pytest.fixture
def job(repo, tmp_path, monkeypatch):
    """Job environment of the action, with git-cliff replaced by a stub."""
    output = tmp_path / "output.txt"
    output.write_text("")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-temp"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    for name in (
        "RELEASE_VERSION_FILES",
        "RELEASE_BUILD_NUMBER_FILES",
        "RELEASE_TAG_PREFIX",
        "RELEASE_CLIFF_CONFIG",
        "RELEASE_DRY_RUN",
        "RELEASE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    def fake_git_cliff(workspace, config, *args):
        if "--bumped-version" in args:
            return "v0.6.0\n"
        if "--output" in args or "--prepend" in args:
            (workspace / "CHANGELOG.md").write_text("# Changelog\n")
            return ""
        return "notes\n"

    monkeypatch.setattr(release, "git_cliff", fake_git_cliff)

    def outputs():
        return dict(
            line.split("=", 1)
            for line in output.read_text().splitlines()
            if "=" in line
        )

    return outputs


@pytest.mark.parametrize(
    ("bumped", "last", "prefix", "expected"),
    [
        ("v1.2.0\n", "v1.1.0", "v", "v1.2.0"),
        ("v1.2.0", "", "", "1.2.0"),
        ("chart-v1.2.0", "chart-v1.1.0", "chart-v", "chart-v1.2.0"),
        ("v1.2.0", "", "chart-v", "chart-v1.2.0"),
        ("", "v1.1.0", "v", None),
        ("v1.1.0", "v1.1.0", "v", None),
    ],
)
def test_version_gets_the_tag_prefix_exactly_once(bumped, last, prefix, expected):
    assert release.normalise_version(bumped, last, prefix) == expected


def test_file_rules_keep_the_regex_verbatim_and_skip_blank_lines():
    raw = "a.yml:^        KEY: (.+)$\n\n   \nb/c.gradle:^  code (\\d+)$\r\n"
    assert release.parse_file_rules(raw, "version-files") == [
        ("a.yml", "^        KEY: (.+)$"),
        ("b/c.gradle", "^  code (\\d+)$"),
    ]


def test_file_rule_without_separator_is_rejected():
    with pytest.raises(release.ReleaseError):
        release.parse_file_rules("no-separator-here", "version-files")


def test_version_file_only_replaces_the_captured_group(tmp_path):
    target = tmp_path / "project.yml"
    target.write_text("name: app\n        VERSION: 0.5.1\nother: 0.5.1\n")
    changed = release.patch_version_files(
        tmp_path, [("project.yml", "^        VERSION: (.+)$")], "0.6.0"
    )
    assert changed == ["project.yml"]
    assert target.read_text() == "name: app\n        VERSION: 0.6.0\nother: 0.5.1\n"


def test_missing_version_file_is_skipped_like_the_github_workflow(tmp_path):
    present = tmp_path / "a.txt"
    present.write_text("version = 1.0.0\n")
    changed = release.patch_version_files(
        tmp_path,
        [("absent.txt", "^version = (.+)$"), ("a.txt", "^version = (.+)$")],
        "1.1.0",
    )
    assert changed == ["a.txt"]
    assert present.read_text() == "version = 1.1.0\n"


def test_unmatched_version_regex_fails_before_any_file_is_written(tmp_path):
    first = tmp_path / "a.txt"
    first.write_text("version = 1.0.0\n")
    second = tmp_path / "b.txt"
    second.write_text("nothing to see\n")
    with pytest.raises(release.ReleaseError):
        release.patch_version_files(
            tmp_path,
            [("a.txt", "^version = (.+)$"), ("b.txt", "^version = (.+)$")],
            "1.1.0",
        )
    assert first.read_text() == "version = 1.0.0\n"


def test_version_file_outside_the_repository_is_rejected(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (tmp_path / "x.txt").write_text("version = 1.0.0\n")
    with pytest.raises(release.ReleaseError):
        release.patch_version_files(workspace, [("../x.txt", "^version = (.+)$")], "2")


def test_no_build_number_rule_means_no_build_number(tmp_path):
    assert release.bump_build_number(tmp_path, []) is None


def test_build_number_converges_on_the_highest_value_plus_one(tmp_path):
    ios = tmp_path / "project.yml"
    ios.write_text("        BUILD: 8\n")
    android = tmp_path / "build.gradle"
    android.write_text("        versionCode 7\n")
    following = release.bump_build_number(
        tmp_path,
        [
            ("project.yml", "^        BUILD: (\\d+)$"),
            ("build.gradle", "^        versionCode (\\d+)$"),
        ],
    )
    assert following == 9
    assert ios.read_text() == "        BUILD: 9\n"
    assert android.read_text() == "        versionCode 9\n"


@pytest.mark.parametrize(
    ("second_content", "second_regex"),
    [
        (None, "^code (\\d+)$"),
        ("nothing here\n", "^code (\\d+)$"),
        ("code abc\n", "^code (.+)$"),
    ],
    ids=["missing-file", "no-match", "not-an-integer"],
)
def test_invalid_build_number_file_fails_without_writing(
    tmp_path, second_content, second_regex
):
    valid = tmp_path / "a.txt"
    valid.write_text("code 8\n")
    if second_content is not None:
        (tmp_path / "b.txt").write_text(second_content)
    with pytest.raises(release.ReleaseError):
        release.bump_build_number(
            tmp_path, [("a.txt", "^code (\\d+)$"), ("b.txt", second_regex)]
        )
    assert valid.read_text() == "code 8\n"


def test_non_releasing_commits_do_not_require_a_release(repo):
    commit(repo, "docs: readme", "b.txt")
    commit(repo, "chore: tidy", "c.txt")
    assert release.commits_require_release(repo, "v0.5.1") is False
    commit(repo, "fix: real bug", "d.txt")
    assert release.commits_require_release(repo, "v0.5.1") is True


def test_release_commit_at_head_is_detected_for_recovery(repo):
    assert release.pending_release(repo) is None
    commit(repo, "chore(release): v1.2.3 [skip ci]", "b.txt")
    assert release.pending_release(repo) == "v1.2.3"


def stub_api(monkeypatch, responses):
    calls = []

    def fake(method, url, token, payload=None):
        calls.append(method)
        return responses.pop(0), b""

    monkeypatch.setattr(release, "api_request", fake)
    return calls


def test_existing_release_is_never_created_twice(monkeypatch):
    calls = stub_api(monkeypatch, [200])
    release.ensure_release("https://forge/api/v1", "o/r", "v1.0.0", "notes", "t")
    assert calls == ["GET"]


def test_missing_release_is_created_once(monkeypatch):
    calls = stub_api(monkeypatch, [404, 201])
    release.ensure_release("https://forge/api/v1", "o/r", "v1.0.0", "notes", "t")
    assert calls == ["GET", "POST"]


def test_release_created_by_a_concurrent_run_is_accepted(monkeypatch):
    calls = stub_api(monkeypatch, [404, 409, 200])
    release.ensure_release("https://forge/api/v1", "o/r", "v1.0.0", "notes", "t")
    assert calls == ["GET", "POST", "GET"]


def test_failed_release_creation_is_an_error(monkeypatch):
    stub_api(monkeypatch, [404, 500, 404])
    with pytest.raises(release.ReleaseError):
        release.ensure_release("https://forge/api/v1", "o/r", "v1.0.0", "notes", "t")


def test_publish_does_nothing_in_dry_run_even_without_a_token(job, monkeypatch):
    monkeypatch.setenv("RELEASE_DRY_RUN", "true")
    assert release.publish() == 0


@pytest.mark.parametrize(
    ("dry_run", "publish"), [("true", "false"), ("True", "false"), ("false", "true")]
)
def test_prepare_only_asks_for_publication_outside_dry_run(
    repo, job, monkeypatch, dry_run, publish
):
    commit(repo, "feat: queue", "b.txt")
    monkeypatch.setenv("RELEASE_DRY_RUN", dry_run)
    assert release.prepare() == 0
    outputs = job()
    assert outputs["released"] == "true"
    assert outputs["version"] == "v0.6.0"
    assert outputs["publish"] == publish


def test_prepare_writes_version_and_build_number_into_the_files(repo, job, monkeypatch):
    (repo / "project.yml").write_text("        VERSION: 0.5.1\n        BUILD: 8\n")
    commit(repo, "feat: queue", "b.txt")
    monkeypatch.setenv("RELEASE_VERSION_FILES", "project.yml:^        VERSION: (.+)$")
    monkeypatch.setenv(
        "RELEASE_BUILD_NUMBER_FILES", "project.yml:^        BUILD: (\\d+)$"
    )
    assert release.prepare() == 0
    assert (repo / "project.yml").read_text() == (
        "        VERSION: 0.6.0\n        BUILD: 9\n"
    )
    assert job()["build-number"] == "9"


def test_prepare_releases_nothing_for_non_releasing_commits(repo, job):
    commit(repo, "docs: readme", "b.txt")
    assert release.prepare() == 0
    outputs = job()
    assert outputs["released"] == "false"
    assert outputs["publish"] == "false"
    assert outputs["version"] == ""


def test_invalid_dry_run_value_fails_closed(repo, job, monkeypatch):
    commit(repo, "feat: queue", "b.txt")
    monkeypatch.setenv("RELEASE_DRY_RUN", "yes")
    with pytest.raises(release.ReleaseError):
        release.prepare()


def action_steps():
    return yaml.safe_load(ACTION.read_text())["runs"]["steps"]


def test_action_publishes_only_on_the_engine_decision():
    publish = next(step for step in action_steps() if "publish" in step.get("run", ""))
    assert publish["if"] == "steps.prepare.outputs.publish == 'true'"


def test_only_the_publish_step_receives_the_token():
    steps = action_steps()
    holders = [step for step in steps if "RELEASE_TOKEN" in step.get("env", {})]
    assert len(holders) == 1
    assert holders[0].get("id") != "prepare"
    assert holders[0]["run"].rstrip().endswith("publish")
    prepare = next(step for step in steps if step.get("id") == "prepare")
    assert "inputs.token" not in str(prepare)


def test_action_only_uses_fully_qualified_actions():
    uses = [step["uses"] for step in action_steps() if "uses" in step]
    assert uses
    assert all(reference.startswith("https://") for reference in uses)


def test_both_forges_pin_the_same_git_cliff_version():
    steps = yaml.safe_load(GITHUB_BUMP_ACTION.read_text())["runs"]["steps"]
    pinned = {
        step["env"]["GIT_CLIFF_VERSION"]
        for step in steps
        if "GIT_CLIFF_VERSION" in step.get("env", {})
    }
    assert pinned == {release.GIT_CLIFF_VERSION}
