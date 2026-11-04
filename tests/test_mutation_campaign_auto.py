"""Automatic campaign sizing, central entrypoint fallback and the inventory contract."""

import importlib
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
        "    async def xǁCalcǁmul__mutmut_3(self, a, b):\n"
        "        return a - b\n"
        "    def xǁCalcǁmul__mutmut_orig(self, a, b):\n"
        "        return a * b\n"
        "def x_add__mutmut_2(a, b):\n"
        "    return a - b\n"
        "def x_add__mutmut_orig(a, b):\n"
        "    return a + b\n"
    )
    variants = inventory.extract_variants(code)
    assert set(variants) == {
        "x_mǁmul__mutmut_1",
        "x_add__mutmut_2",
        "xǁCalcǁmul__mutmut_3",
    }
    for name in variants:
        assert inventory.variant_name(f"pkg.core.{name}") == name


@pytest.mark.parametrize(
    "configuration", ["", "[tool.mutmut]\n", "[tool.mutmut]\nsource_paths=[]\n"]
)
def test_source_paths_should_fall_back_to_src_when_not_configured(
    tmp_path, configuration
):
    (tmp_path / "src").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="demo"\nversion="0.1.0"\n' + configuration
    )
    assert inventory._source_paths(tmp_path) == [tmp_path / "src"]


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
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.mark.skipif(not HAVE_MUTMUT, reason="mutmut is not provisioned")
@pytest.mark.parametrize("class_method", [False, True])
@pytest.mark.parametrize("source_paths", [None, "src/", "src/pkg/"])
@pytest.mark.parametrize("depth", ["medium", "high"])
def test_inventory_should_plan_and_shard_against_real_mutmut(
    tmp_path, class_method, source_paths, depth
):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname="demo"\nversion="0.1.0"\n'
        + (f'[tool.mutmut]\nsource_paths=["{source_paths}"]\n' if source_paths else "")
    )
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    code = (
        "class Calc:\n    def calc(self, a, b):\n        return a + b\n"
        if class_method
        else "def calc(a, b):\n    return a + b\n"
    )
    (repo / "src" / "pkg" / "core.py").write_text(code)
    (repo / "tests" / "test_core.py").write_text(
        (
            "from pkg.core import Calc\ncalc = Calc().calc\n\n"
            if class_method
            else "from pkg.core import calc\n\n"
        )
        + "def test_calc():\n    assert calc(2, 3) == 5\n"
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "pkg" / "core.py").write_text(code.replace("a + b", "a + b + 0"))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    head = git(repo, "rev-parse", "HEAD")

    plan_json = inventory.plan(repo, base, head, ".", 0, depth)
    keys = set(plan_json["mutants"])
    assert keys, "the planner must enumerate real Mutmut keys"
    assert all(key.startswith("pkg.core.") for key in keys)
    if class_method:
        assert all(".xǁCalcǁcalc__mutmut_" in key for key in keys)
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
    survivors = set(fragment["review"]["reviewed_equivalents"])
    killed = {
        key for key, item in fragment["mutants"].items() if item["status"] == "killed"
    }
    assert survivors, "weakly tested arithmetic must leave surviving mutants"
    assert killed, "tested arithmetic mutants must be killed"
    assert survivors == {
        key for key, item in fragment["mutants"].items() if item["status"] == "survived"
    }
    proofs = fragment["review"]["mutants"]
    assert set(proofs) == survivors
    for key, proof in proofs.items():
        assert proof["source"] == "src/pkg/core.py"
        assert proof["source_sha256"] == plan_json["sources"]["src/pkg/core.py"]
        assert proof["mutant_sha256"] == plan_json["mutants"][key]["mutant_sha256"]
        assert (
            inventory.digest_function(
                proof["mutant_source"], inventory.variant_name(key)
            )
            == proof["mutant_sha256"]
        )


def test_resolve_shard_count_should_clamp_zero_per_shard(monkeypatch):
    monkeypatch.setenv("MUTATION_MUTANTS_PER_SHARD", "0")
    importlib.reload(inventory)
    assert inventory.MUTANTS_PER_SHARD == 1
    assert inventory.resolve_shard_count(0, 0) == 1
    assert inventory.resolve_shard_count(0, 3) == 3
    monkeypatch.undo()
    importlib.reload(inventory)


@pytest.mark.skipif(not HAVE_MUTMUT, reason="mutmut is not provisioned")
@pytest.mark.parametrize("depth", ["medium", "high"])
def test_real_campaign_executes_colocated_tests_without_mutating_them(tmp_path, depth):
    repo = tmp_path / "repo"
    package = repo / "pkg"
    package.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths=["pkg/"]\n'
        'pytest_add_cli_args_test_selection=["pkg/"]\n'
    )
    (package / "__init__.py").write_text("")
    source = package / "core.py"
    test = package / "test_core.py"
    source.write_text("def calc(value):\n    return value + 1\n")
    test.write_text(
        "from pkg.core import calc\ndef test_calc():\n    assert calc(3) == 4\n"
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    source.write_text("def calc(value):\n    return value + 2\n")
    test.write_text(
        "from pkg.core import calc\ndef test_calc():\n    assert calc(3) == 5\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "change implementation and colocated test")
    head = git(repo, "rev-parse", "HEAD")

    plan_json = inventory.plan(repo, base, head, ".", 0, depth)
    assert plan_json["mutants"]
    assert set(plan_json["sources"]) == {"pkg/core.py"}
    assert all(key.startswith("pkg.core.") for key in plan_json["mutants"])
    mutation_campaign.validate_plan(plan_json, repo, base, head, ".", 1)
    fragment = inventory.shard(repo, plan_json, 0)
    assert set(fragment["mutants"]) == set(plan_json["mutants"])
    assert fragment["stats"]["killed"] == len(plan_json["mutants"])
    assert fragment["stats"]["survived"] == 0


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname="demo"\nversion="0.1.0"\n'
        '[tool.mutmut]\nsource_paths=["src/"]\n'
    )
    (repo / "README.md").write_text("base\n")
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    (repo / "src" / "pkg" / "core.py").write_text("def calc(a, b):\n    return a + b\n")
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
def test_plan_should_skip_broken_sources_outside_the_scope(tmp_path):
    repo, base, _ = _git_repo(tmp_path)
    # Keep these files unchanged in the PR: changed unreadable production
    # files must fail scope computation rather than disappear from it.
    (repo / "src" / "pkg" / "broken.py").write_text("def x(:\n    pass\n")
    (repo / "src" / "pkg" / "binary.py").write_bytes(b"\xff\xfe\x00garbage")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "pre-existing unprocessable sources")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "pkg" / "core.py").write_text(
        "def calc(a, b):\n    return a + b\n\ndef extra(a):\n    return a + 1\n"
    )
    # A syntax error and non-UTF-8 bytes in sources that the PR scope never
    # targets must be skipped with a warning, not abort the whole campaign.
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    head = git(repo, "rev-parse", "HEAD")
    plan_json = inventory.plan(repo, base, head, ".", 0, "medium")
    assert set(plan_json["sources"]) == {"src/pkg/core.py"}
    assert plan_json["mutants"]


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


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_variant_proofs_preserve_exact_ast_source(newline):
    import ast

    code = newline.join(
        [
            "class Café:",
            "    @staticmethod",
            "    async def xǁCaféǁméthode__mutmut_1():",
            '        return "été\u2028fin"  # commentaire',
            'def x_été__mutmut_2(): return "é"',
            "def x_nested__mutmut_3():",
            "    def x_inner__mutmut_4():",
            "        return 4",
            "    return x_inner__mutmut_4()",
            "",
        ]
    )
    expected = {
        node.name: ast.get_source_segment(code, node)
        for node in ast.walk(ast.parse(code))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert inventory.extract_variants(code) == expected


def test_survivor_proofs_extract_each_file_once_and_fail_on_missing_variant(
    tmp_path, monkeypatch
):
    staged = tmp_path / "mutants" / "src" / "calc.py"
    staged.parent.mkdir(parents=True)
    staged.write_text(
        "def x_calc__mutmut_1(): return 1\ndef x_calc__mutmut_2(): return 2\n"
    )
    original = inventory.extract_variants
    calls = []

    def measured(code):
        calls.append(code)
        return original(code)

    monkeypatch.setattr(inventory, "extract_variants", measured)
    cache = {}
    for index in (1, 2):
        assert (
            inventory._staged_variant(
                tmp_path, "src/calc.py", f"x_calc__mutmut_{index}", cache
            )
            == f"def x_calc__mutmut_{index}(): return {index}"
        )
    assert len(calls) == 1
    with pytest.raises(inventory.InventoryError, match="missing"):
        inventory._staged_variant(tmp_path, "src/calc.py", "x_missing__mutmut_3", cache)
    # A new campaign must observe new bytes, not reuse cached evidence.
    staged.write_text("def x_calc__mutmut_1(): return 99\n")
    assert inventory._staged_variant(
        tmp_path, "src/calc.py", "x_calc__mutmut_1", {}
    ).endswith("return 99")
