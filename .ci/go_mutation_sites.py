"""Prove that a Go diff touches no line Gremlins can mutate.

Gremlins lists every mutation site of the module, with ``SKIPPED`` for the
sites outside ``--diff``. A run with zero mutants is accepted as "no target"
only when no reported site, whatever its status, falls on a line added or
modified between base and head, and no changed file belongs to a nested module
Gremlins does not analyse. Anything else stays a failure (fail closed).

Shared by the execution runner (``mutation-go.sh``) and the trusted verifier
(``mutation_evidence.py``); both recompute the changed lines from git.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

REASON = "no-mutable-sites-on-changed-lines"
EXCLUDED_PARTS = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "node_modules",
        "dist",
        "build",
        "vendor",
        "testdata",
    }
)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def is_production_go(path: str) -> bool:
    parts = Path(path).parts
    return (
        path.endswith(".go")
        and not path.endswith("_test.go")
        and not any(part in EXCLUDED_PARTS for part in parts)
    )


def _git(project: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.quotePath=false", "-C", str(project), *args],
        text=True,
    )


def changed_lines(project: Path, base: str, head: str) -> dict[str, set[int]]:
    """Head-side line numbers added or modified, per production Go file."""
    # Raw entries carry modes and blobs. A symlink or a submodule anywhere in
    # the diff can change compiled Go code without a reviewable .go hunk, and a
    # tracked .go symlink compiles its target: none of them can prove "empty".
    raw = _git(
        project,
        "diff",
        "--relative",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "-z",
        f"{base}...{head}",
        "--",
        ".",
    ).split("\0")
    entries = []
    for header, path in zip(raw[0::2], raw[1::2]):
        old_mode, new_mode, old_blob, new_blob, status = header.lstrip(":").split()
        if {old_mode, new_mode} & {"120000", "160000"}:
            raise ValueError(f"Symlink or submodule changed: {path}")
        if status != "D":
            entries.append((path, old_blob == new_blob))
    tree = _git(project, "ls-tree", "-r", "-z", head, "--", ".").split("\0")
    for row in filter(None, tree):
        mode, _, rest = row.partition(" ")
        if mode == "120000" and rest.split("\t", 1)[1].endswith(".go"):
            raise ValueError("Go file tracked as a symlink: " + rest.split("\t", 1)[1])
    result: dict[str, set[int]] = {}
    for path, same_content in entries:
        if not is_production_go(path):
            continue
        if same_content:
            # Mode-only change (chmod): no line changed.
            result[path] = set()
            continue
        lines: set[int] = set()
        hunks = 0
        diff = _git(
            project,
            "diff",
            "--relative",
            "--no-color",
            "--no-ext-diff",
            # A PR-controlled .gitattributes (-diff, binary, textconv) must not
            # hide its hunks: force a textual diff of the raw content.
            "--text",
            "--no-textconv",
            "-U0",
            f"{base}...{head}",
            "--",
            path,
        )
        for row in diff.splitlines():
            match = _HUNK.match(row)
            if match:
                hunks += 1
                start = int(match[1])
                count = 1 if match[2] is None else int(match[2])
                lines.update(range(start, start + count))
        if not hunks:
            raise ValueError(f"Changed Go file without a textual diff: {path}")
        result[path] = lines
    return result


def in_nested_module(project: Path, path: str) -> bool:
    parent = Path(path).parent
    while parent != Path("."):
        if (project / parent / "go.mod").is_file():
            return True
        parent = parent.parent
    return False


def reported_sites(raw: dict, files: set[str]) -> list[dict]:
    """Sites Gremlins reported in the changed files, any status."""
    sites = []
    for file_result in raw.get("files", []):
        name = file_result.get("file_name")
        if name not in files:
            continue
        for mutation in file_result.get("mutations", []):
            line = mutation.get("line")
            if type(line) is not int or line <= 0:
                raise ValueError(f"Gremlins site without a valid line in {name}")
            sites.append({"file": name, "line": line})
    return sorted(sites, key=lambda site: (site["file"], site["line"]))


def check_no_mutable_sites(
    project: Path, base: str, head: str, sites: object
) -> dict[str, set[int]]:
    """Raise unless no site lies on a changed line of a changed file."""
    if not isinstance(sites, list) or not all(
        isinstance(site, dict)
        and set(site) == {"file", "line"}
        and type(site["file"]) is str
        and type(site["line"]) is int
        for site in sites
    ):
        raise ValueError("Malformed Gremlins site list")
    changed = changed_lines(project, base, head)
    if not changed:
        raise ValueError("No changed production Go file to prove empty")
    nested = sorted(path for path in changed if in_nested_module(project, path))
    if nested:
        raise ValueError(
            "Changed Go files belong to a nested module: " + ", ".join(nested[:5])
        )
    hits = [
        f"{site['file']}:{site['line']}"
        for site in sites
        if site["line"] in changed.get(site["file"], set())
    ]
    if hits:
        raise ValueError(
            "Gremlins reported mutation sites on changed lines without measuring "
            "them: " + ", ".join(hits[:10])
        )
    return changed
