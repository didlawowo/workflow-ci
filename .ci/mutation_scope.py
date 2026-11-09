#!/usr/bin/env python3
"""Compute mutmut target globs for the exact pull-request delta."""

from __future__ import annotations

import argparse
import ast
import configparser
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path


def mutation_source_paths(root: Path) -> tuple[str, ...]:
    """Read trusted mutmut source paths from pyproject.toml or setup.cfg."""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        import tomllib

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        configured = data.get("tool", {}).get("mutmut", {}).get("source_paths", ())
        if isinstance(configured, str):
            return (configured,)
        if isinstance(configured, list):
            return tuple(str(value) for value in configured)

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        parser = configparser.ConfigParser()
        parser.read(setup_cfg, encoding="utf-8")
        if parser.has_option("mutmut", "source_paths"):
            raw = parser.get("mutmut", "source_paths")
            return tuple(
                item.strip() for item in re.split(r"[\n,]", raw) if item.strip()
            )

    return ()


def _is_in_source_path(relative: str, source_paths: tuple[str, ...]) -> bool:
    candidate = Path(relative)
    # Test code executes against mutants; it is never a production mutation
    # target, even when source_paths includes a package with colocated tests.
    parts = candidate.parts
    name = candidate.name
    if "tests" in parts or name.startswith("test_") or name.endswith("_test.py"):
        return False
    if not source_paths:
        # Bootstrap repositories can have no protected mutmut scope yet.
        # Default to production Python.
        return True

    for raw in source_paths:
        configured = Path(raw.rstrip("/"))
        if candidate == configured:
            return True
        if configured.suffix == "" and (
            candidate == configured or configured in candidate.parents
        ):
            return True
    return False


def _changed_lines(repo: Path, base: str, head: str) -> dict[str, set[int]]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--unified=0",
            "--no-ext-diff",
            "--no-textconv",
            "--relative",
            "--no-color",
            f"{base}...{head}",
            "--",
            "*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )

    changed: dict[str, set[int]] = {}
    current_path: str | None = None

    for raw in completed.stdout.splitlines():
        if raw.startswith("+++ b/"):
            current_path = raw[6:]
            changed.setdefault(current_path, set())
            continue
        if raw.startswith("+++ /dev/null"):
            current_path = None
            continue
        if not raw.startswith("@@") or current_path is None:
            continue

        match = re.search(r"\+(\d+)(?:,(\d+))?", raw)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count:
            changed[current_path].update(range(start, start + count))
        else:
            # A deletion-only hunk has no lines on the head side. Anchor both
            # adjacent surviving lines so a deletion inside an existing
            # function cannot be misclassified as "no mutation targets".
            changed[current_path].update({max(start - 1, 1), max(start, 1)})

    return changed


def _module_name(relative: str) -> str:
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# Mirror mutmut's trampoline rules (mutmut/mutation/file_mutation.py): mutmut
# never generates mutants for a function carrying any decorator except a single
# bare @staticmethod/@classmethod, and never for a small set of dunder names.
_MUTMUT_NEVER_MUTATE_FUNCTION_NAMES = frozenset(
    {"__getattribute__", "__setattr__", "__new__"}
)
_MUTMUT_TRAMPOLINE_SAFE_DECORATORS = frozenset({"staticmethod", "classmethod"})


def _mutmut_generates_mutants(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether mutmut actually produces mutants for this function.

    A target glob for a function mutmut skips can never match a generated
    mutant: the mutation run asserts "Filtered for specific mutants, but
    nothing matches" and the gate fails without producing any evidence, even
    though the PR is genuinely untestable at that scope.
    """
    if node.name in _MUTMUT_NEVER_MUTATE_FUNCTION_NAMES:
        return False
    decorators = node.decorator_list
    if not decorators:
        return True
    if len(decorators) == 1:
        single = decorators[0]
        return (
            isinstance(single, ast.Name)
            and single.id in _MUTMUT_TRAMPOLINE_SAFE_DECORATORS
        )
    return False


_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

# Names mutmut's operator_name rewrites; any other bare name is left intact.
_MUTMUT_MUTATED_NAMES = frozenset({"True", "False", "deepcopy"})
# Statements no mutmut operator can rewrite.
_MUTMUT_INERT_STATEMENTS = (ast.Pass, ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)


def _is_inert_value(value: ast.expr | None) -> bool:
    """A value mutmut never mutates: nothing, None, ``...`` or a plain name."""
    if value is None:
        return True
    if isinstance(value, ast.Constant):
        return value.value is None or value.value is Ellipsis
    return isinstance(value, ast.Name) and value.id not in _MUTMUT_MUTATED_NAMES


_TYPE_PARAM_NODES = tuple(
    getattr(ast, name)
    for name in ("TypeVar", "ParamSpec", "TypeVarTuple")
    if hasattr(ast, name)
)


def _identifiers(node: ast.AST) -> list[str | None]:
    """Identifiers libcst models as ``cst.Name`` on this single node."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.alias):
        return [*node.name.split("."), node.asname]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, (ast.Attribute,)):
        return [node.attr]
    if isinstance(node, ast.keyword):
        return [node.arg]
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return list(node.names)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.ExceptHandler):
        return [node.name]
    if isinstance(node, _TYPE_PARAM_NODES):
        return [node.name]
    if isinstance(node, (ast.MatchAs, ast.MatchStar)):
        return [node.name]
    if isinstance(node, ast.MatchMapping):
        return [node.rest]
    return []


def _mentions_mutated_name(node: ast.AST, *, root: bool = True) -> bool:
    """Whether a mutable identifier under ``node`` is one operator_name rewrites.

    libcst models every identifier as ``cst.Name`` (import aliases, ``global``
    names, parameters, type parameters, attribute names, def names), so mutmut
    can rewrite a ``deepcopy`` wherever it appears. Annotations, decorators
    and nested defs mutmut skips are never mutated, so they are not searched.
    """
    if not root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if not _mutmut_generates_mutants(node):
            return False
    if any(name in _MUTMUT_MUTATED_NAMES for name in _identifiers(node)):
        return True
    skipped: set[int] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        skipped.update(id(decorator) for decorator in node.decorator_list)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
        skipped.add(id(node.returns))
    if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation:
        skipped.add(id(node.annotation))
    return any(
        _mentions_mutated_name(child, root=False)
        for child in ast.iter_child_nodes(node)
        if id(child) not in skipped
    )


def _is_triple_quoted(value: ast.Constant, source: str) -> bool:
    """Whether the docstring is one triple-quoted literal operator_string skips.

    Any other spelling, including an implicit concatenation that starts with a
    triple-quoted part, contains a part mutmut mutates.
    """
    segment = ast.get_source_segment(source, value) or ""
    try:
        tokens = [
            token
            for token in tokenize.generate_tokens(io.StringIO(segment).readline)
            if token.type
            not in {tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER, tokenize.COMMENT}
        ]
    except (tokenize.TokenError, SyntaxError):
        return False
    if len(tokens) != 1 or tokens[0].type != tokenize.STRING:
        return False
    return tokens[0].string.lstrip("rRbBuU").startswith(('"""', "'''"))


def _has_mutable_code(node: _FunctionNode, source: str) -> bool:
    """Whether mutmut can produce at least one mutant inside ``node``.

    Conservative: only bodies made exclusively of statements no operator can
    rewrite answer False; anything else (compound statements, calls,
    literals, assignments, defaults, a ``deepcopy`` identifier) keeps the
    function in scope.
    """
    if _mentions_mutated_name(node):
        return True
    arguments = node.args
    if arguments.defaults or any(arguments.kw_defaults):
        return True
    for index, statement in enumerate(node.body):
        if isinstance(statement, _MUTMUT_INERT_STATEMENTS):
            continue
        if isinstance(statement, ast.Expr):
            value = statement.value
            is_docstring = (
                index == 0
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and _is_triple_quoted(value, source)
            )
            if is_docstring or _is_inert_value(value):
                continue
            return True
        if isinstance(statement, ast.Return):
            if _is_inert_value(statement.value):
                continue
            return True
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A skipped nested def (decorated, reserved name) is ignored by
            # mutmut together with its decorators and whole subtree.
            if _mutmut_generates_mutants(statement) and _has_mutable_code(
                statement, source
            ):
                return True
            continue
        return True
    return False


def _changed_mutmut_functions(
    tree: ast.Module, changed_lines: set[int], source: str
) -> set[tuple[str, ...]]:
    """Qualified functions mutmut can mutate whose source range was changed.

    Mirrors mutmut's OuterFunctionProvider / combine_mutations_to_source:
    only module-level functions and the direct methods of a module-level
    class get a trampoline. A def nested anywhere else (closure, nested
    class, def under ``if``/``try``) is either folded into its enclosing
    top-level function or method (x_outer__mutmut_N) or never mutated.
    """
    candidates: list[tuple[tuple[str, ...], _FunctionNode]] = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates.append(((statement.name,), statement))
        elif isinstance(statement, ast.ClassDef):
            candidates.extend(
                ((statement.name, method.name), method)
                for method in statement.body
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            )

    functions: set[tuple[str, ...]] = set()
    for qualified, node in candidates:
        if not _mutmut_generates_mutants(node) or not _has_mutable_code(node, source):
            continue
        start = min(
            [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
        )
        end = node.end_lineno or node.lineno
        if any(start <= line <= end for line in changed_lines):
            functions.add(qualified)
    return functions


def syntax_unchanged(repo: Path, base: str, relative: str, tree: ast.AST) -> bool:
    """Ignore formatting-only edits; unavailable base source remains in scope."""
    prefix = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--show-prefix"], text=True
    ).strip()
    previous = subprocess.run(
        ["git", "show", f"{base}:{prefix}{relative}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if previous.returncode:
        return False
    try:
        return ast.dump(ast.parse(previous.stdout)) == ast.dump(tree)
    except SyntaxError:
        return False


def mutation_targets(
    repo: Path,
    base: str,
    head: str,
    *,
    depth: str = "medium",
    source_root: Path | None = None,
) -> tuple[str, ...]:
    """Return mutmut target globs for the exact trusted PR scope.

    medium mutates only changed functions. high broadens to every function in
    each changed production Python module so a large/high-risk refactor also
    exercises unchanged helpers in the touched files.
    """
    if depth not in {"medium", "high"}:
        raise ValueError(f"unsupported mutation depth: {depth}")

    source_paths = mutation_source_paths(source_root or repo)
    changed = _changed_lines(repo, base, head)
    targets: set[str] = set()

    for relative, lines in changed.items():
        if not lines or not _is_in_source_path(relative, source_paths):
            continue

        path = repo / relative
        if not path.is_file() or path.suffix != ".py":
            continue

        module = _module_name(relative)
        if not module:
            continue

        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError) as error:
            raise ValueError(
                f"Cannot compute mutation scope for {relative} with Python "
                f"{sys.version.split()[0]}: {error}. "
                "Use the repository's target Python version and valid UTF-8 source."
            ) from error

        if syntax_unchanged(repo, base, relative, tree):
            continue

        if depth == "high":
            targets.add(f"{module}.*__mutmut_*")
            continue

        for qualified in _changed_mutmut_functions(tree, lines, source):
            targets.add(f"{module}.*{'*'.join(qualified)}__mutmut_*")

    return tuple(sorted(targets))


def matches_function(mutant_id: str, target: str) -> bool:
    """Match native IDs by module and qualified identity, not loose wildcards.

    Engine globs tolerate Mutmut's Unicode trampoline separators, but an
    independent verifier must not accept RulesExtra.check for Rules.check.
    """
    module, separator, generated = mutant_id.rpartition(".")
    target_module, target_separator, pattern = target.partition(".*")
    if not separator or not target_separator or module != target_module:
        return False
    match = re.fullmatch(r"(x_.+|xǁ.+)__mutmut_[0-9]+", generated)
    if not match or not pattern.endswith("__mutmut_*"):
        return False
    name = match[1]
    qualified = tuple(name[2:].split("ǁ")) if name.startswith("xǁ") else (name[2:],)
    expected = tuple(pattern.removesuffix("__mutmut_*").split("*"))
    if expected == ("",):
        return True
    return qualified == expected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--depth", choices=("medium", "high"), default="medium")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for target in mutation_targets(
        args.repo.resolve(), args.base, args.head, depth=args.depth
    ):
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
