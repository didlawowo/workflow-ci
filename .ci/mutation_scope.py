#!/usr/bin/env python3
"""Compute mutmut target globs for the exact pull-request delta."""

from __future__ import annotations

import argparse
import ast
import configparser
import re
import subprocess
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
                item.strip()
                for item in re.split(r"[\n,]", raw)
                if item.strip()
            )

    return ()


def _is_in_source_path(relative: str, source_paths: tuple[str, ...]) -> bool:
    candidate = Path(relative)
    if not source_paths:
        # Bootstrap repositories can have no protected mutmut scope yet.
        # Default to production Python while excluding test modules.
        parts = candidate.parts
        name = candidate.name
        if "tests" in parts or name.startswith("test_") or name.endswith("_test.py"):
            return False
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
            "--no-color",
            f"{base}...{head}",
            "--",
            "*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
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
            changed[current_path].update(
                {max(start - 1, 1), max(start, 1)}
            )

    return changed


def _module_name(relative: str) -> str:
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _ChangedFunctionVisitor(ast.NodeVisitor):
    def __init__(self, changed_lines: set[int]) -> None:
        self.changed_lines = changed_lines
        self.stack: list[str] = []
        self.functions: set[tuple[str, ...]] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        starts = [node.lineno]
        starts.extend(
            decorator.lineno
            for decorator in node.decorator_list
            if hasattr(decorator, "lineno")
        )
        start = min(starts)
        end = getattr(node, "end_lineno", node.lineno)

        qualified = tuple([*self.stack, node.name])
        if any(start <= line <= end for line in self.changed_lines):
            self.functions.add(qualified)

        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


def mutation_targets(
    repo: Path, base: str, head: str, *, depth: str = "medium"
) -> tuple[str, ...]:
    """Return mutmut target globs for the exact trusted PR scope.

    medium mutates only changed functions. high broadens to every function in
    each changed production Python module so a large/high-risk refactor also
    exercises unchanged helpers in the touched files.
    """
    if depth not in {"medium", "high"}:
        raise ValueError(f"unsupported mutation depth: {depth}")

    source_paths = mutation_source_paths(repo)
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

        if depth == "high":
            targets.add(f"{module}.*__mutmut_*")
            continue

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        visitor = _ChangedFunctionVisitor(lines)
        visitor.visit(tree)
        for qualified in visitor.functions:
            targets.add(f"{module}.*{'*'.join(qualified)}__mutmut_*")

    return tuple(sorted(targets))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--depth", choices=("medium", "high"), default="medium")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for target in mutation_targets(args.repo.resolve(), args.base, args.head, depth=args.depth):
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
