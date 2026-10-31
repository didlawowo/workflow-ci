"""Automatic campaign sizing, central entrypoint fallback and the inventory contract."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ci"))
inventory = importlib.import_module("mutation_campaign_inventory")
campaign_run = importlib.import_module("mutation_campaign_run")
mutation_campaign = importlib.import_module("mutation_campaign")

REPO = Path(__file__).resolve().parents[1]

try:
    importlib.import_module("mutmut.mutation.file_mutation")
    import shutil

    HAVE_MUTMUT = shutil.which("mutmut") is not None
except ImportError:
    HAVE_MUTMUT = False


def test_resolve_shard_count_should_size_plan_when_auto():
    assert inventory.resolve_shard_count(0, 1) == 1
    assert inventory.resolve_shard_count(0, 1200) == 1
    assert inventory.resolve_shard_count(0, 1201) == 2
    assert inventory.resolve_shard_count(0, 7681) == 7
    assert inventory.resolve_shard_count(0, 100_000) == 16
    assert inventory.resolve_shard_count(5, 100) == 5


def test_extract_variants_should_find_trampoline_function_sources():
    code = (
        "from mutmut.mutation.trampoline import wrap_in_trampoline\n"
        "class Calc:\n"
        "    def x_mǁmul__mutmut_1(self, a, b):\n"
        "        return a / b\n"
        "def x_add__mutmut_2(a, b):\n"
        "    return a - b\n"
        "def x_add__mutmut_orig(a, b):\n"
        "    return a + b\n"
    )
    variants = inventory.extract_variants(code)
    assert set(variants) == {"x_mǁmul__mutmut_1", "x_add__mutmut_2"}


def test_plan_mode_should_publish_resolved_count_from_central_entrypoint(tmp_path):
    root = tmp_path
    (root / ".workflow-ci" / ".ci").mkdir(parents=True)
    (root / ".policy" / ".ci").mkdir(parents=True)
    pr = root / "pr"
    pr.mkdir()
    fake = root / ".workflow-ci" / ".ci" / "mutation-plan.sh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'cat > "$1" <<JSON\n'
        '{"schema_version":1,"shard_count":7}\n'
        "JSON\n"
    )
    fake.chmod(0o755)
    output = root / "plan.json"
    github_output = root / "github_output"
    github_output.write_text("")
    environment = dict(os.environ, GITHUB_OUTPUT=str(github_output))
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO / ".ci" / "mutation_campaign_run.py"),
            "plan",
            "--root",
            str(root),
            "--plan",
            str(output),
            "--output",
            str(output),
            "--shards",
            "0",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    assert "shard-count=7" in github_output.read_text()


def test_run_should_prefer_consumer_entrypoint_over_central(tmp_path):
    for base in (".policy", ".workflow-ci"):
        script = tmp_path / base / ".ci" / "mutation-plan.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env bash\ntrue\n")
        script.chmod(0o755)
    selected = campaign_run.resolve_entrypoint(tmp_path.resolve(), "plan")
    assert ".policy" in str(selected)


def test_run_should_fail_closed_on_symlinked_consumer_entrypoint(tmp_path):
    script = tmp_path / ".policy" / ".ci" / "mutation-shard.sh"
    script.parent.mkdir(parents=True)
    escape = tmp_path / "escape.sh"
    escape.write_text("#!/usr/bin/env bash\ntrue\n")
    script.symlink_to(escape)
    central = tmp_path / ".workflow-ci" / ".ci" / "mutation-shard.sh"
    central.parent.mkdir(parents=True)
    central.write_text("#!/usr/bin/env bash\ntrue\n")
    central.chmod(0o755)
    with pytest.raises(SystemExit):
        campaign_run.resolve_entrypoint(tmp_path.resolve(), "shard")


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True
    ).strip()


@pytest.mark.skipif(not HAVE_MUTMUT, reason="mutmut is not provisioned")
def test_inventory_should_plan_and_shard_against_real_mutmut(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname="demo"\nversion="0.1.0"\n'
        '[tool.mutmut]\nsource_paths=["src/"]\n'
    )
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    (repo / "src" / "pkg" / "core.py").write_text(
        "def calc(a, b):\n    return a + b\n"
    )
    (repo / "tests" / "test_core.py").write_text(
        "from pkg.core import calc\n\n"
        "def test_calc():\n    assert calc(2, 3) == 5\n"
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "pkg" / "core.py").write_text(
        "def calc(a, b):\n    return a + b\n"
        "\n"
        "def extra(a):\n    return a + 1\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    head = git(repo, "rev-parse", "HEAD")

    plan_json = inventory.plan(repo, base, head, ".", 0, "medium")
    keys = set(plan_json["mutants"])
    assert keys, "the planner must enumerate real Mutmut keys"
    assert all(key.startswith("pkg.core.") for key in keys)
    assert plan_json["shard_count"] == 1
    assert set(plan_json["sources"]) == {"src/pkg/core.py"}
    mutation_campaign.validate_plan(
        plan_json, repo, base, head, ".", plan_json["shard_count"]
    )

    fragment = inventory.shard(repo, plan_json, 0)
    assert set(fragment["mutants"]) == keys
    assert fragment["stats"]["total"] == len(keys)
    for key, item in fragment["mutants"].items():
        assert item["mutant_sha256"] == plan_json["mutants"][key]["mutant_sha256"]
        assert item["status"] in inventory.COUNTERS


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname="demo"\nversion="0.1.0"\n'
        '[tool.mutmut]\nsource_paths=["src/"]\n'
    )
    (repo / "README.md").write_text("base\n")
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    (repo / "src" / "pkg" / "core.py").write_text(
        "def calc(a, b):\n    return a + b\n"
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("documentation only\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "docs")
    head = git(repo, "rev-parse", "HEAD")
    return repo, base, head


@pytest.mark.skipif(not HAVE_MUTMUT, reason="mutmut is not provisioned")
def test_plan_should_return_empty_plan_when_auto_and_no_targets(tmp_path):
    repo, base, head = _git_repo(tmp_path)
    plan_json = inventory.plan(repo, base, head, ".", 0, "medium")
    assert plan_json["mutants"] == {}
    assert plan_json["sources"] == {}
    assert plan_json["shard_count"] == 1
    mutation_campaign.validate_plan(plan_json, repo, base, head, ".", 1)


@pytest.mark.skipif(not HAVE_MUTMUT, reason="mutmut is not provisioned")
def test_plan_should_fail_when_explicit_shards_and_no_targets(tmp_path, monkeypatch):
    repo, base, head = _git_repo(tmp_path)
    monkeypatch.setattr(inventory, "mutation_targets", lambda *a, **k: iter(()))
    with pytest.raises(inventory.InventoryError):
        inventory.plan(repo, base, head, ".", 2, "medium")


@pytest.mark.skipif(not HAVE_MUTMUT, reason="mutmut is not provisioned")
def test_shard_should_emit_zero_fragment_when_partition_empty(tmp_path):
    repo, base, head = _git_repo(tmp_path)
    plan_json = inventory.plan(repo, base, head, ".", 0, "medium")
    fragment = inventory.shard(repo, plan_json, 0)
    assert fragment["mutants"] == {}
    assert fragment["shard_index"] == 0
    assert fragment["shard_count"] == 1
    assert fragment["exit_code"] == 0
    assert fragment["stats"]["total"] == 0
    verdict = mutation_campaign.aggregate(
        plan_json, [fragment], repo, repo, base, head, ".", "medium", 1
    )
    assert verdict["stats"]["total"] == 0
