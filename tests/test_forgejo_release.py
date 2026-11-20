"""Behaviour of the Forgejo release engine and of its action contract."""

import http.server
import importlib
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
release = importlib.import_module("forgejo_release")

ACTION = ROOT / ".forgejo/actions/release/action.yml"
GITHUB_BUMP_ACTION = ROOT / ".github/actions/git-cliff-bump/action.yml"
RELEASE_INPUTS = (
    "RELEASE_VERSION_FILES",
    "RELEASE_BUILD_NUMBER_FILES",
    "RELEASE_TAG_PREFIX",
    "RELEASE_CLIFF_CONFIG",
    "RELEASE_DRY_RUN",
    "RELEASE_TOKEN",
)


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def commit(repo, subject, filename, content=None):
    (repo / filename).write_text(subject if content is None else content)
    git(repo, "add", "--", filename)
    git(repo, "commit", "-q", "-m", subject)


def head_of(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def remote_tags(repo):
    """Remote tag name -> commit it points to (annotated tags peeled)."""
    tags = {}
    for line in git(repo, "ls-remote", "--tags", "origin").splitlines():
        sha, _, ref = line.partition("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            tags[name.removesuffix("^{}")] = sha
        else:
            tags.setdefault(name, sha)
    return tags


@pytest.fixture
def repo(tmp_path):
    """A clone tagged v0.5.1 whose origin is a local bare repository."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "remote", "add", "origin", str(origin))
    commit(path, "feat: initial", "a.txt")
    git(path, "tag", "v0.5.1")
    git(path, "push", "-q", "origin", "main", "v0.5.1")
    return path


@pytest.fixture
def job(repo, tmp_path, monkeypatch):
    """Job environment of the action, with git-cliff replaced by a stub.

    ``outputs.calls`` records every stubbed call with the last tag git saw at
    that moment, so a test can check what a computation started from.
    """
    output = tmp_path / "output.txt"
    output.write_text("")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-temp"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    for name in RELEASE_INPUTS:
        monkeypatch.delenv(name, raising=False)
    calls = []

    def fake_git_cliff(workspace, config, *args):
        calls.append((args, release.last_tag(workspace, "v")))
        if "--bumped-version" in args:
            return "v0.6.0\n"
        if "--output" in args or "--prepend" in args:
            (workspace / "CHANGELOG.md").write_text("# Changelog\n")
            return ""
        return "notes for " + args[-1] + "\n"

    monkeypatch.setattr(release, "git_cliff", fake_git_cliff)

    def outputs():
        lines = output.read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    outputs.calls = calls
    return outputs


@pytest.fixture
def forge(job, monkeypatch):
    """Publishing environment: the bare origin and an in-memory release API."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://forge.example")
    monkeypatch.setenv("GITHUB_API_URL", "https://forge.example/api/v1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/app")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("RELEASE_TOKEN", "dummy")
    releases = {}

    def fake_api(method, url, token, payload=None):
        if method == "GET":
            return (200 if url.rsplit("/", 1)[-1] in releases else 404), b""
        releases[payload["tag_name"]] = payload["body"]
        return 201, b""

    monkeypatch.setattr(release, "api_request", fake_api)
    return releases


# --- version -----------------------------------------------------------------


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


def test_non_releasing_commits_do_not_require_a_release(repo):
    commit(repo, "docs: readme", "b.txt")
    commit(repo, "chore: tidy", "c.txt")
    assert release.commits_require_release(repo, "v0.5.1") is False
    commit(repo, "fix: real bug", "d.txt")
    assert release.commits_require_release(repo, "v0.5.1") is True


# --- declared files ----------------------------------------------------------


def test_file_rules_keep_the_regex_verbatim_and_skip_blank_lines():
    raw = "a.yml:^        KEY: (.+)$\n\n   \nb/c.gradle:^  code (\\d+)$\r\n"
    assert release.parse_file_rules(raw, "version-files") == [
        ("a.yml", "^        KEY: (.+)$"),
        ("b/c.gradle", "^  code (\\d+)$"),
    ]


def test_file_rule_without_separator_is_rejected():
    with pytest.raises(release.ReleaseError):
        release.parse_file_rules("no-separator-here", "version-files")


def test_version_file_only_replaces_the_first_captured_group(tmp_path):
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
    (tmp_path / "b.txt").write_text("nothing to see\n")
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


def test_build_number_updates_every_occurrence_in_a_file(tmp_path):
    target = tmp_path / "project.yml"
    target.write_text("app:\n  BUILD: 8\nwidget:\n  BUILD: 5\n")
    rules = [("project.yml", "^  BUILD: (\\d+)$")]
    assert release.bump_build_number(tmp_path, rules) == 9
    assert target.read_text() == "app:\n  BUILD: 9\nwidget:\n  BUILD: 9\n"


def test_crlf_files_match_line_anchors_and_keep_their_line_endings(tmp_path):
    target = tmp_path / "win.properties"
    target.write_bytes(b"name=app\r\nversion=1.0.0\r\nbuild=8\r\n")
    release.patch_version_files(
        tmp_path, [("win.properties", "^version=(.+)$")], "1.1.0"
    )
    rules = [("win.properties", "^build=(\\d+)$")]
    assert release.bump_build_number(tmp_path, rules) == 9
    assert target.read_bytes() == b"name=app\r\nversion=1.1.0\r\nbuild=9\r\n"


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


# --- recovery detection ------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "prefix", "expected"),
    [
        ("chore(release): v1.2.3 [skip ci]", "v", "v1.2.3"),
        ("chore(release): chart-v1.2.3 [skip ci]", "chart-v", "chart-v1.2.3"),
        ("chore(release): 1.2.3 [skip ci]", "", "1.2.3"),
        ("chore(release): main [skip ci]", "v", None),
        ("chore(release): 1.2.3 [skip ci]", "v", None),
        ("chore(release): v1.2.3", "v", None),
        ("feat: v1.2.3 [skip ci]", "v", None),
    ],
)
def test_only_a_prefixed_semantic_version_is_accepted_from_a_commit_subject(
    subject, prefix, expected
):
    assert release.announced_version(subject, prefix) == expected


def test_release_commits_without_tag_are_found_after_the_last_tag(repo):
    assert release.orphan_release_commits(repo, "v0.5.1", "v") == []
    commit(repo, "fix: crash", "b.txt")
    commit(repo, "chore(release): v0.5.2 [skip ci]", "c.txt")
    orphan = head_of(repo)
    commit(repo, "feat: search", "d.txt")
    assert release.orphan_release_commits(repo, "v0.5.1", "v") == [(orphan, "v0.5.2")]


def test_without_any_tag_only_head_can_be_recovered(tmp_path):
    path = tmp_path / "imported"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    commit(path, "chore(release): v0.1.0 [skip ci]", "a.txt")
    commit(path, "feat: more", "b.txt")
    assert release.orphan_release_commits(path, "", "v") == []
    commit(path, "chore(release): v0.2.0 [skip ci]", "c.txt")
    assert release.orphan_release_commits(path, "", "v") == [(head_of(path), "v0.2.0")]


# --- forge release -----------------------------------------------------------


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


# --- prepare -----------------------------------------------------------------


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
    commit(
        repo,
        "chore: project file",
        "project.yml",
        "        VERSION: 0.5.1\n        BUILD: 8\n",
    )
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


def test_invalid_build_number_leaves_the_checkout_untouched(repo, job, monkeypatch):
    commit(repo, "chore: project file", "project.yml", "VERSION: 0.5.1\nBUILD: next\n")
    commit(repo, "feat: queue", "b.txt")
    monkeypatch.setenv("RELEASE_VERSION_FILES", "project.yml:^VERSION: (.+)$")
    monkeypatch.setenv("RELEASE_BUILD_NUMBER_FILES", "project.yml:^BUILD: (.+)$")
    with pytest.raises(release.ReleaseError):
        release.prepare()
    assert git(repo, "status", "--short") == ""


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


# --- publish -----------------------------------------------------------------


def test_publish_does_nothing_in_dry_run_even_without_a_token(job, monkeypatch):
    monkeypatch.setenv("RELEASE_DRY_RUN", "true")
    assert release.publish() == 0


def test_publication_commits_only_declared_files_and_tags_the_release(
    repo, forge, monkeypatch
):
    commit(repo, "chore: project file", "project.yml", "VERSION: 0.5.1\nBUILD: 8\n")
    commit(repo, "feat: queue", "b.txt")
    (repo / "secret.env").write_text("left behind by an earlier step\n")
    monkeypatch.setenv("RELEASE_VERSION_FILES", "project.yml:^VERSION: (.+)$")
    monkeypatch.setenv("RELEASE_BUILD_NUMBER_FILES", "project.yml:^BUILD: (\\d+)$")

    assert release.prepare() == 0
    assert release.publish() == 0

    head = head_of(repo)
    subject = git(repo, "log", "-1", "--pretty=%s").strip()
    assert subject == "chore(release): v0.6.0 [skip ci]"
    committed = git(repo, "show", "--name-only", "--pretty=", "HEAD").split()
    assert sorted(committed) == ["CHANGELOG.md", "project.yml"]
    assert git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == head
    assert remote_tags(repo)["v0.6.0"] == head
    assert forge == {"v0.6.0": "notes for header\n"}


def test_second_run_on_a_published_release_changes_nothing(repo, forge):
    commit(repo, "feat: queue", "b.txt")
    assert release.prepare() == 0
    assert release.publish() == 0
    head = head_of(repo)
    tags = remote_tags(repo)

    assert release.prepare() == 0
    assert release.publish() == 0
    assert head_of(repo) == head
    assert remote_tags(repo) == tags
    assert list(forge) == ["v0.6.0"]


def test_release_commit_pushed_without_tag_is_completed_not_cut_again(repo, forge, job):
    commit(repo, "fix: crash", "b.txt")
    commit(repo, "chore(release): v0.5.2 [skip ci]", "CHANGELOG.md")
    orphan = head_of(repo)
    commit(repo, "feat: search", "c.txt")
    git(repo, "push", "-q", "origin", "main")

    assert release.prepare() == 0
    assert job()["version"] == "v0.6.0"
    assert release.publish() == 0

    tags = remote_tags(repo)
    assert tags["v0.5.2"] == orphan
    assert tags["v0.6.0"] == head_of(repo)
    assert sorted(forge) == ["v0.5.2", "v0.6.0"]
    subjects = git(repo, "log", "--pretty=%s", "v0.5.1..HEAD").splitlines()
    assert subjects.count("chore(release): v0.5.2 [skip ci]") == 1


def test_release_commit_at_head_only_gets_its_missing_tag_and_release(repo, forge, job):
    commit(repo, "fix: crash", "b.txt")
    commit(repo, "chore(release): v0.5.2 [skip ci]", "CHANGELOG.md")
    git(repo, "push", "-q", "origin", "main")
    head = head_of(repo)

    assert release.prepare() == 0
    outputs = job()
    assert (outputs["released"], outputs["version"]) == ("true", "v0.5.2")
    assert release.publish() == 0
    assert head_of(repo) == head
    assert remote_tags(repo)["v0.5.2"] == head
    assert list(forge) == ["v0.5.2"]


def test_a_remote_tag_on_another_commit_is_never_moved(repo, forge):
    commit(repo, "feat: queue", "b.txt")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "push", "-q", "origin", "v0.5.1^{commit}:refs/tags/v0.6.0")
    before = remote_tags(repo)["v0.6.0"]

    assert release.prepare() == 0
    with pytest.raises(release.ReleaseError):
        release.publish()
    assert remote_tags(repo)["v0.6.0"] == before
    assert forge == {}


def test_a_branch_that_moved_since_checkout_publishes_nothing(repo, forge, tmp_path):
    commit(repo, "feat: queue", "b.txt")
    git(repo, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", "-b", "main", str(tmp_path / "origin.git"), str(other)],
        check=True,
    )
    commit(other, "fix: concurrent merge", "z.txt")
    git(other, "push", "-q", "origin", "main")

    assert release.prepare() == 0
    with pytest.raises(release.ReleaseError):
        release.publish()
    assert "v0.6.0" not in remote_tags(repo)
    assert forge == {}


def test_real_git_cliff_computes_the_next_version(repo, tmp_path, monkeypatch):
    real_git_cliff_or_skip()
    output = tmp_path / "real-output.txt"
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "real-temp"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    for name in RELEASE_INPUTS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RELEASE_DRY_RUN", "true")
    commit(repo, "fix: crash", "b.txt")
    commit(repo, "feat: queue", "c.txt")

    assert release.prepare() == 0
    assert "version=v0.6.0" in output.read_text().splitlines()
    assert (repo / "CHANGELOG.md").read_text().count("## [0.6.0]") == 1


def test_two_release_commits_without_tag_are_both_completed_in_order(repo, forge, job):
    commit(repo, "fix: crash", "b.txt")
    commit(repo, "chore(release): v0.5.2 [skip ci]", "c.txt")
    first = head_of(repo)
    commit(repo, "fix: leak", "d.txt")
    commit(repo, "chore(release): v0.5.3 [skip ci]", "e.txt")
    second = head_of(repo)
    commit(repo, "feat: search", "f.txt")
    git(repo, "push", "-q", "origin", "main")

    assert release.prepare() == 0
    assert release.publish() == 0

    tags = remote_tags(repo)
    assert (tags["v0.5.2"], tags["v0.5.3"]) == (first, second)
    assert tags["v0.6.0"] == head_of(repo)
    assert forge["v0.5.2"] == f"notes for v0.5.1..{first}\n"
    assert forge["v0.5.3"] == f"notes for v0.5.2..{second}\n"
    bumped_from = [last for args, last in job.calls if "--bumped-version" in args]
    assert bumped_from == ["v0.5.3"]


def test_prepare_leaves_no_local_tag_behind(repo, job, monkeypatch):
    commit(repo, "fix: crash", "b.txt")
    commit(repo, "chore(release): v0.5.2 [skip ci]", "c.txt")
    commit(repo, "feat: search", "d.txt")
    monkeypatch.setenv("RELEASE_DRY_RUN", "true")
    assert release.prepare() == 0
    assert git(repo, "tag", "--list").split() == ["v0.5.1"]
    assert job()["publish"] == "false"


def test_recovery_behind_head_does_not_announce_a_release_of_head(repo, forge, job):
    commit(repo, "fix: crash", "b.txt")
    commit(repo, "chore(release): v0.5.2 [skip ci]", "c.txt")
    orphan = head_of(repo)
    commit(repo, "docs: readme", "d.txt")
    git(repo, "push", "-q", "origin", "main")

    assert release.prepare() == 0
    outputs = job()
    assert (outputs["released"], outputs["version"]) == ("false", "")
    assert outputs["publish"] == "true"
    assert release.publish() == 0
    assert remote_tags(repo)["v0.5.2"] == orphan
    assert list(forge) == ["v0.5.2"]


@pytest.mark.parametrize(
    "subject",
    [
        "chore(release): v0.5.1 [skip ci]",
        "chore(release): v0.4.0 [skip ci]",
        "chore(release): v0.9.0-a..b [skip ci]",
    ],
    ids=["tag-used-elsewhere", "not-above-last-tag", "invalid-tag-name"],
)
def test_unusable_release_commit_is_ignored_and_never_blocks_releases(
    repo, forge, subject
):
    commit(repo, subject, "b.txt")
    commit(repo, "feat: search", "c.txt")
    git(repo, "push", "-q", "origin", "main")

    assert release.orphan_release_commits(repo, "v0.5.1", "v") == []
    assert release.prepare() == 0
    assert release.publish() == 0
    assert sorted(remote_tags(repo)) == ["v0.5.1", "v0.6.0"]
    assert list(forge) == ["v0.6.0"]


def test_tag_pushed_without_its_forge_release_gets_it_at_the_next_run(repo, forge):
    commit(repo, "feat: queue", "b.txt")
    assert release.prepare() == 0
    assert release.publish() == 0
    forge.clear()
    commit(repo, "docs: readme", "c.txt")
    git(repo, "push", "-q", "origin", "main")

    assert release.prepare() == 0
    assert release.publish() == 0
    assert list(forge) == ["v0.6.0"]
    assert sorted(remote_tags(repo)) == ["v0.5.1", "v0.6.0"]


def test_hand_made_tag_never_gets_a_forge_release(repo, forge, job):
    commit(repo, "docs: readme", "b.txt")
    assert release.prepare() == 0
    assert job()["publish"] == "false"
    assert forge == {}


def test_ignored_declared_file_fails_before_anything_is_written(repo, job, monkeypatch):
    commit(repo, "chore: ignore", ".gitignore", "generated.txt\n")
    (repo / "generated.txt").write_text("version = 0.5.1\n")
    commit(repo, "feat: queue", "b.txt")
    monkeypatch.setenv("RELEASE_VERSION_FILES", "generated.txt:^version = (.+)$")
    with pytest.raises(release.ReleaseError):
        release.prepare()
    assert (repo / "generated.txt").read_text() == "version = 0.5.1\n"
    assert not (repo / "CHANGELOG.md").exists()


def test_plan_computed_for_another_commit_is_refused(repo, forge):
    commit(repo, "feat: queue", "b.txt")
    assert release.prepare() == 0
    commit(repo, "feat: later", "c.txt")
    with pytest.raises(release.ReleaseError):
        release.publish()
    assert forge == {}


def test_plain_http_api_is_refused_when_the_forge_uses_https(repo, forge, monkeypatch):
    commit(repo, "feat: queue", "b.txt")
    monkeypatch.setenv("GITHUB_API_URL", "http://forge.example/api/v1")
    assert release.prepare() == 0
    with pytest.raises(release.ReleaseError):
        release.publish()
    assert forge == {}


def test_api_request_never_follows_a_redirect():
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(302 if self.path == "/start" else 200)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/start"
        status, _ = release.api_request("GET", url, "secret")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 302
    assert seen == [("/start", "token secret")]


def test_mixed_line_endings_are_restored_line_by_line(tmp_path):
    target = tmp_path / "mixed.txt"
    target.write_bytes(b"name=app\nversion=1.0.0\r\nbuild=8\n")
    release.patch_version_files(tmp_path, [("mixed.txt", "^version=(.+)$")], "2.0.0")
    assert target.read_bytes() == b"name=app\nversion=2.0.0\r\nbuild=8\n"


def real_git_cliff_or_skip():
    found = shutil.which("git-cliff")
    if not found:
        pytest.skip("git-cliff is not installed")
    probe = subprocess.run(
        [found, "--version"], capture_output=True, text=True, check=False
    )
    if probe.stdout.split()[-1:] != [release.GIT_CLIFF_VERSION]:
        pytest.skip("git-cliff is not at the pinned version")


def test_real_git_cliff_recovery_cuts_each_version_once(repo, forge, monkeypatch):
    real_git_cliff_or_skip()
    monkeypatch.undo()
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setenv("RUNNER_TEMP", str(repo.parent / "real-recovery-temp"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(repo.parent / "real-recovery-output.txt"))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://forge.example")
    monkeypatch.setenv("GITHUB_API_URL", "https://forge.example/api/v1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/app")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("RELEASE_TOKEN", "dummy")
    for name in RELEASE_INPUTS[:-1]:
        monkeypatch.delenv(name, raising=False)
    created = {}

    def fake_api(method, url, token, payload=None):
        if method == "GET":
            return (200 if url.rsplit("/", 1)[-1] in created else 404), b""
        created[payload["tag_name"]] = payload["body"]
        return 201, b""

    monkeypatch.setattr(release, "api_request", fake_api)

    # First run interrupted after the branch push: release commit, no tag.
    commit(repo, "fix: crash", "b.txt")
    assert release.prepare() == 0
    git(repo, "add", "--", "CHANGELOG.md")
    git(repo, "commit", "-q", "-m", "chore(release): v0.5.2 [skip ci]")
    orphan = head_of(repo)
    commit(repo, "feat: search", "c.txt")
    git(repo, "push", "-q", "origin", "main")

    assert release.prepare() == 0
    assert release.publish() == 0
    tags = remote_tags(repo)
    assert tags["v0.5.2"] == orphan
    assert tags["v0.6.0"] == head_of(repo)
    changelog = (repo / "CHANGELOG.md").read_text()
    assert changelog.count("## [0.5.2]") == 1
    assert changelog.count("## [0.6.0]") == 1
    assert "Crash" in created["v0.5.2"]
    assert "Search" in created["v0.6.0"]


# --- action contract ---------------------------------------------------------


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
