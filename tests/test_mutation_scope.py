import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / ".ci" / "mutation_scope.py"
SPEC = importlib.util.spec_from_file_location("mutation_scope", MODULE_PATH)
assert SPEC and SPEC.loader
mutation_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mutation_scope)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@example.test")
    _git(repo, "config", "user.name", "CI Test")
    (repo / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["src/"]\n',
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    return repo


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def test_formatting_only_changes_do_not_require_mutants(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src/service.py"
    source.write_text("def compute(value):\n    return dict(value=value)\n")
    base = _commit(repo, "initial")
    source.write_text(
        "def compute(value):\n    return dict(\n        value=value,\n    )\n"
    )
    head = _commit(repo, "format")
    for depth in ("medium", "high"):
        assert mutation_scope.mutation_targets(repo, base, head, depth=depth) == ()
    source.write_text("def compute(value):\n    return dict(value=value + 1)\n")
    head = _commit(repo, "change behavior")
    for depth in ("medium", "high"):
        assert mutation_scope.mutation_targets(repo, base, head, depth=depth)


def test_new_module_still_requires_mutants(tmp_path: Path):
    repo = _init_repo(tmp_path)
    base = _commit(repo, "initial")
    (repo / "src/service.py").write_text("def compute(value):\n    return value + 1\n")
    head = _commit(repo, "add module")
    assert mutation_scope.mutation_targets(repo, base, head)


def test_stacked_pr_scope_only_targets_child_delta(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text(
        "def parent_feature(value):\n"
        "    return value + 1\n\n"
        "def child_feature(value):\n"
        "    return value * 2\n",
        encoding="utf-8",
    )
    _commit(repo, "initial")

    source.write_text(
        "def parent_feature(value):\n"
        "    return value + 2\n\n"
        "def child_feature(value):\n"
        "    return value * 2\n",
        encoding="utf-8",
    )
    parent_sha = _commit(repo, "parent pr")

    source.write_text(
        "def parent_feature(value):\n"
        "    return value + 2\n\n"
        "def child_feature(value):\n"
        "    return value * 3\n",
        encoding="utf-8",
    )
    child_sha = _commit(repo, "child pr")

    targets = mutation_scope.mutation_targets(repo, parent_sha, child_sha)

    assert any("service" in target and "child_feature" in target for target in targets)
    assert all("parent_feature" not in target for target in targets)


def test_scope_targets_function_for_deletion_only_hunk(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text(
        "def compute(value):\n"
        "    if value < 0:\n"
        "        value = 0\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "def compute(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    head = _commit(repo, "delete guard")

    assert mutation_scope.mutation_targets(repo, base, head) == (
        "service.*compute__mutmut_*",
    )


def test_scope_ignores_python_changes_outside_trusted_source_paths(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text("def production():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    test_file = tests / "test_service.py"
    test_file.write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    base = _commit(repo, "initial")

    test_file.write_text(
        "def test_placeholder():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    head = _commit(repo, "tests only")

    assert mutation_scope.mutation_targets(repo, base, head) == ()


def test_bootstrap_scope_ignores_tests_without_mutmut_config(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "bootstrap"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    source = repo / "src" / "service.py"
    source.write_text("def production():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    test_file = tests / "test_service.py"
    test_file.write_text("def test_production():\n    assert True\n", encoding="utf-8")
    base = _commit(repo, "initial")

    source.write_text("def production():\n    return 2\n", encoding="utf-8")
    test_file.write_text(
        "def test_production():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    head = _commit(repo, "bootstrap implementation")

    assert mutation_scope.mutation_targets(repo, base, head) == (
        "service.*production__mutmut_*",
    )


def test_scope_keeps_class_and_method_identity(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text(
        "class Calculator:\n    def compute(self, value):\n        return value + 1\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "class Calculator:\n    def compute(self, value):\n        return value + 2\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change method")

    targets = mutation_scope.mutation_targets(repo, base, head)

    assert targets == ("service.*Calculator*compute__mutmut_*",)


def test_scope_patterns_are_module_anchored(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text(
        "def compute(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "def compute(value):\n    return value + 2\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change function")

    assert mutation_scope.mutation_targets(repo, base, head) == (
        "service.*compute__mutmut_*",
    )


def test_scope_reads_multiline_setup_cfg_source_paths(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").unlink()
    (repo / "setup.cfg").write_text(
        "[mutmut]\nsource_paths =\n    src/\n",
        encoding="utf-8",
    )
    source = repo / "src" / "service.py"
    source.write_text("def compute():\n    return 1\n", encoding="utf-8")
    base = _commit(repo, "initial")

    source.write_text("def compute():\n    return 2\n", encoding="utf-8")
    head = _commit(repo, "change function")

    assert mutation_scope.mutation_targets(repo, base, head) == (
        "service.*compute__mutmut_*",
    )


def test_changed_lines_compares_exact_base_and_head_trees():
    completed = type("Result", (), {"stdout": ""})()

    with patch.object(
        mutation_scope.subprocess,
        "run",
        return_value=completed,
    ) as run:
        assert (
            mutation_scope._changed_lines(
                Path("/repo"),
                "base-sha",
                "head-sha",
            )
            == {}
        )

    run.assert_called_once_with(
        [
            "git",
            "-C",
            "/repo",
            "diff",
            "--unified=0",
            "--no-color",
            "base-sha...head-sha",
            "--",
            "*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_high_depth_mutates_all_functions_in_touched_module(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text(
        "def changed(value):\n"
        "    return value + 1\n\n"
        "def helper(value):\n"
        "    return value * 2\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "def changed(value):\n"
        "    return value + 2\n\n"
        "def helper(value):\n"
        "    return value * 2\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change one function")

    medium = mutation_scope.mutation_targets(repo, base, head, depth="medium")
    high = mutation_scope.mutation_targets(repo, base, head, depth="high")

    assert medium == ("service.*changed__mutmut_*",)
    assert high == ("service.*__mutmut_*",)


def test_scope_rejects_unknown_depth(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text("def compute():\n    return 1\n", encoding="utf-8")
    base = _commit(repo, "initial")
    source.write_text("def compute():\n    return 2\n", encoding="utf-8")
    head = _commit(repo, "change")

    import pytest

    with pytest.raises(ValueError, match="unsupported mutation depth"):
        mutation_scope.mutation_targets(repo, base, head, depth="extreme")


def test_decorated_handler_change_yields_no_unsatisfiable_target(tmp_path: Path):
    """FastAPI-style handlers: mutmut generates no mutants for decorated
    functions, so the scope must not emit a glob that can never match —
    mutmut asserts on such an empty filter match and the mutation gate fails
    without producing evidence (seen on rag-doc PR #140)."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "api.py"
    source.write_text(
        "app = object()\n"
        "\n"
        "\n"
        "@app.post('/ask')\n"
        "async def ask(request=None, question=None):\n"
        "    return question + 'x'\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")
    source.write_text(
        "app = object()\n"
        "\n"
        "\n"
        "@app.post('/ask')\n"
        "async def ask(request=None, question=None):\n"
        "    return question + 'y'\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change handler")

    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == ()
    # high still emits the module-wide glob, which matches the mutable
    # functions of the module — the pattern is satisfiable there.
    assert mutation_scope.mutation_targets(repo, base, head, depth="high") == (
        "api.*__mutmut_*",
    )


def test_decorated_handler_change_targets_only_mutable_neighbours(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "api.py"
    source.write_text(
        "app = object()\n"
        "\n"
        "\n"
        "def compute(value):\n"
        "    return value + 1\n"
        "\n"
        "\n"
        "@app.post('/ask')\n"
        "async def ask(request=None, question=None):\n"
        "    return question + 'x'\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")
    source.write_text(
        "app = object()\n"
        "\n"
        "\n"
        "def compute(value):\n"
        "    return value + 2\n"
        "\n"
        "\n"
        "@app.post('/ask')\n"
        "async def ask(request=None, question=None):\n"
        "    return question + 'y'\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change both")

    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == (
        "api.*compute__mutmut_*",
    )


def test_property_and_stacked_decorators_skipped_but_staticmethod_kept(tmp_path: Path):
    repo = _init_repo(tmp_path)
    source = repo / "src" / "service.py"
    source.write_text(
        "class Service:\n"
        "    @property\n"
        "    def config(self):\n"
        "        return {'a': 1}\n"
        "\n"
        "    @staticmethod\n"
        "    def helper(value):\n"
        "        return value * 2\n"
        "\n"
        "    @staticmethod\n"
        "    @classmethod\n"
        "    def stacked(value):\n"
        "        return value * 3\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")
    source.write_text(
        "class Service:\n"
        "    @property\n"
        "    def config(self):\n"
        "        return {'a': 2}\n"
        "\n"
        "    @staticmethod\n"
        "    def helper(value):\n"
        "        return value * 4\n"
        "\n"
        "    @staticmethod\n"
        "    @classmethod\n"
        "    def stacked(value):\n"
        "        return value * 6\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change all three")

    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == (
        "service.*Service*helper__mutmut_*",
    )


def test_change_inside_decorated_function_body_is_not_a_target(tmp_path: Path):
    """A nested def inside a decorated function: mutmut ignores the whole
    subtree, so no target may reference the nested function either."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "api.py"
    source.write_text(
        "app = object()\n"
        "\n"
        "\n"
        "@app.post('/ask')\n"
        "async def ask(request=None, question=None):\n"
        "    async def inner():\n"
        "        return 1\n"
        "\n"
        "    return inner\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")
    source.write_text(
        "app = object()\n"
        "\n"
        "\n"
        "@app.post('/ask')\n"
        "async def ask(request=None, question=None):\n"
        "    async def inner():\n"
        "        return 2\n"
        "\n"
        "    return inner\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change nested")

    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == ()
