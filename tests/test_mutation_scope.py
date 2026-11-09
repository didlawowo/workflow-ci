import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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


@pytest.mark.parametrize("depth", ["medium", "high"])
@pytest.mark.parametrize("contents", [b"def broken(:\n", b"# invalid utf8: \xff\n"])
def test_scope_should_fail_with_filename_when_changed_source_is_unreadable(
    tmp_path, depth, contents
):
    repo = _init_repo(tmp_path)
    base = _commit(repo, "initial")
    (repo / "src/broken.py").write_bytes(contents)
    head = _commit(repo, "unreadable source")
    with pytest.raises(
        ValueError, match=r"Cannot compute mutation scope for src/broken.py with Python"
    ) as caught:
        mutation_scope.mutation_targets(repo, base, head, depth=depth)
    assert sys.version.split()[0] in str(caught.value)
    assert caught.value.__cause__ is not None


def test_scope_should_report_unsupported_python_syntax(tmp_path):
    if sys.version_info >= (3, 14):
        pytest.skip("PEP 758 is supported by this interpreter")
    repo = _init_repo(tmp_path)
    base = _commit(repo, "initial")
    (repo / "src/service.py").write_text(
        "def compute():\n    try:\n        return 1\n"
        "    except TypeError, ValueError:\n        return 0\n"
    )
    head = _commit(repo, "Python 3.14 syntax")
    with pytest.raises(ValueError, match="src/service.py.*Python"):
        mutation_scope.mutation_targets(repo, base, head)


def test_scope_should_ignore_unreadable_files_outside_trusted_sources(tmp_path):
    repo = _init_repo(tmp_path)
    base = _commit(repo, "initial")
    (repo / "test_broken.py").write_text("def broken(:\n")
    head = _commit(repo, "broken test outside scope")
    assert mutation_scope.mutation_targets(repo, base, head) == ()


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


@pytest.mark.parametrize("depth", ["medium", "high"])
@pytest.mark.parametrize("configured", ["src/", "src/pkg/", "."])
@pytest.mark.parametrize(
    "test_path", ["pkg/test_core.py", "pkg/core_test.py", "pkg/tests/helper.py"]
)
def test_configured_packages_keep_colocated_tests_out_of_targets(
    tmp_path: Path, depth: str, configured: str, test_path: str
):
    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        f'[tool.mutmut]\nsource_paths = ["{configured}"]\n'
    )
    production = repo / "src/pkg/core.py"
    test = repo / "src" / test_path
    test.parent.mkdir(parents=True, exist_ok=True)
    production.write_text("def compute(value):\n    return value + 1\n")
    test.write_text("def check():\n    assert True\n")
    base = _commit(repo, "base")
    test.write_text("def check():\n    assert 1 + 1 == 2\n")
    tests_only = _commit(repo, "change test")
    assert mutation_scope.mutation_targets(repo, base, tests_only, depth=depth) == ()
    production.write_text("def compute(value):\n    return value + 2\n")
    head = _commit(repo, "change production")
    expected = (
        "pkg.core.*__mutmut_*" if depth == "high" else "pkg.core.*compute__mutmut_*"
    )
    assert mutation_scope.mutation_targets(repo, base, head, depth=depth) == (expected,)


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
            "--no-ext-diff",
            "--no-textconv",
            "--relative",
            "--no-color",
            "base-sha...head-sha",
            "--",
            "*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
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


def test_verification_preserves_exact_module_class_and_method_identity():
    target = "service.*Rules*check__mutmut_*"
    assert mutation_scope.matches_function("service.xǁRulesǁcheck__mutmut_1", target)
    assert not mutation_scope.matches_function(
        "service.xǁRulesExtraǁcheck__mutmut_1", target
    )
    assert not mutation_scope.matches_function(
        "service.sub.xǁRulesǁcheck__mutmut_1", target
    )
    assert not mutation_scope.matches_function("service.x_check__mutmut_1", target)
    assert not mutation_scope.matches_function(
        "service.xǁRulesǁothercheck__mutmut_1", target
    )
    assert mutation_scope.matches_function(
        "service.x_x_function__mutmut_1", "service.*x_function__mutmut_*"
    )
    assert mutation_scope.matches_function(
        "service.x_func__mutmut_1", "service.*__mutmut_*"
    )
    assert not mutation_scope.matches_function(
        "service.sub.x_func__mutmut_1", "service.*__mutmut_*"
    )


def test_changed_closure_targets_its_enclosing_function(tmp_path: Path):
    """#259: mutmut stores a nested def's mutants under the enclosing function
    (x_create_app__mutmut_N), so targeting the closure itself can never be
    satisfied and the evidence gate fails on a fully killed campaign."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "observer_app.py"
    body = (
        "def create_app():\n    def plan(x):\n        return x + {}\n    return plan\n"
    )
    source.write_text(body.format(1), encoding="utf-8")
    base = _commit(repo, "initial")
    source.write_text(body.format(2), encoding="utf-8")
    head = _commit(repo, "change closure")
    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == (
        "observer_app.*create_app__mutmut_*",
    )


def test_nested_class_method_is_not_a_target(tmp_path: Path):
    """#266: mutmut ne génère pas de mutant pour une méthode d'une classe imbriquée."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "nested_classes.py"
    source.write_text(
        "class A:\n"
        "    class B:\n"
        "        def m(self):\n"
        "            return 1 + N\n"
        "\n"
        "    def direct(self):\n"
        "        return 3 + 4\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "class A:\n"
        "    class B:\n"
        "        def m(self):\n"
        "            return 1 + N + 1\n"
        "\n"
        "    def direct(self):\n"
        "        return 3 + 4\n",
        encoding="utf-8",
    )
    intermediate = _commit(repo, "change nested method")
    assert mutation_scope.mutation_targets(repo, base, intermediate, depth="medium") == ()

    source.write_text(
        "class A:\n"
        "    class B:\n"
        "        def m(self):\n"
        "            return 1 + N + 1\n"
        "\n"
        "    def direct(self):\n"
        "        return 3 + 5\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change direct method")
    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == (
        "nested_classes.*A*direct__mutmut_*",
    )


def test_defs_under_if_or_try_are_not_targets(tmp_path: Path):
    """#266: mutmut ne cible pas les defs nichés sous if ou try au niveau module."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "conditional_defs.py"
    source.write_text(
        "import sys\n"
        "\n"
        "if sys.version_info >= (3, 0):\n"
        "    def under_if():\n"
        "        return 1 + N\n"
        "\n"
        "try:\n"
        "    def under_try():\n"
        "        return 3 + N\n"
        "except ImportError:\n"
        "    pass\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "import sys\n"
        "\n"
        "if sys.version_info >= (3, 0):\n"
        "    def under_if():\n"
        "        return 1 + N + 1\n"
        "\n"
        "try:\n"
        "    def under_try():\n"
        "        return 3 + N + 1\n"
        "except ImportError:\n"
        "    pass\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change guarded defs")
    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == ()


def test_enclosing_function_with_only_decorated_closure_is_not_a_target(tmp_path: Path):
    """#266: mutmut ignore la closure décorée et l'englobante n'a pas d'autre code mutable."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "decorated_closure.py"
    source.write_text(
        "import functools\n"
        "\n"
        "def outer():\n"
        "    @functools.cache\n"
        "    def inner():\n"
        "        return 1 + N\n"
        "    return inner\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "import functools\n"
        "\n"
        "def outer():\n"
        "    @functools.cache\n"
        "    def inner():\n"
        "        return 1 + N + 1\n"
        "    return inner\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change decorated closure")
    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == ()


def test_enclosing_function_with_mutable_code_stays_a_target(tmp_path: Path):
    """#266: contrôle, outer_mixed a du code mutable hors de la closure décorée."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "decorated_closure_mixed.py"
    source.write_text(
        "import functools\n"
        "\n"
        "def outer_mixed():\n"
        "    @functools.cache\n"
        "    def inner():\n"
        "        return 1 + N\n"
        "    return inner() + 3\n",
        encoding="utf-8",
    )
    base = _commit(repo, "initial")

    source.write_text(
        "import functools\n"
        "\n"
        "def outer_mixed():\n"
        "    @functools.cache\n"
        "    def inner():\n"
        "        return 1 + N + 1\n"
        "    return inner() + 3\n",
        encoding="utf-8",
    )
    head = _commit(repo, "change mixed closure")
    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == (
        "decorated_closure_mixed.*outer_mixed__mutmut_*",
    )


@pytest.mark.parametrize(
    "body",
    ["def f(x):\n    'doc{}'\n    return x\n", "def f(x{}):\n    from copy import deepcopy\n    return x{}\n"],
)
def test_inert_looking_code_mutmut_rewrites_stays_a_target(tmp_path: Path, body: str):
    """#266 review: mutmut mutates single-quoted docstrings and any deepcopy name."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "m.py"
    source.write_text(body.format("", ""), encoding="utf-8")
    base = _commit(repo, "initial")
    source.write_text(body.format("2", "2"), encoding="utf-8")
    head = _commit(repo, "change")
    assert mutation_scope.mutation_targets(repo, base, head, depth="medium") == ("m.*f__mutmut_*",)
