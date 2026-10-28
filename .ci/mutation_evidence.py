"""Trusted mutation verification shared by execution and campaign consumers.

Candidate files are data only. Never import a consumer module or execute its
review hook in the verification job. Equivalence approval comes from the base.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from mutation_contract import read_json
from mutation_scope import matches_function, mutation_targets

STATUS_ALIASES = {
    "timeout": "timeouts",
    "timed_out": "timeouts",
    "no_coverage": "not_covered",
}
COUNTERS = (
    "killed",
    "survived",
    "timeouts",
    "suspicious",
    "no_tests",
    "not_covered",
    "skipped",
    "other_bad",
    "segfault",
    "check_was_interrupted_by_user",
)
MAX_BYTES = 20_000_000


def project(root: Path, directory: str) -> Path:
    if Path(directory).is_absolute():
        raise ValueError("Mutation working-directory must be relative")
    resolved = (root / directory).resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_dir():
        raise ValueError("Mutation working-directory escapes the checkout")
    return resolved


def counts(data: dict) -> dict[str, int]:
    raw = data.get("stats", data)
    if not isinstance(raw, dict):
        raise ValueError("Mutation stats must be an object")
    result = dict.fromkeys(COUNTERS, 0)
    seen = set()
    for key, value in raw.items():
        if key in {
            "scope",
            "depth",
            "engine",
            "schema_version",
            "review",
            "diagnostics",
        }:
            continue
        normalized = STATUS_ALIASES.get(key, key)
        if normalized not in {*COUNTERS, "total"}:
            raise ValueError(f"Unknown mutation counter: {key}")
        if normalized in seen or type(value) is not int or value < 0:
            raise ValueError(f"Invalid or duplicate mutation counter: {key}")
        seen.add(normalized)
        result[normalized] = value
    if not {"killed", "survived", "total"} <= seen:
        raise ValueError("Missing killed/survived/total counters")
    if result["total"] != sum(result[key] for key in COUNTERS):
        raise ValueError("Inconsistent mutation counters")
    return result


def statuses(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*(\S+):\s+([a-z_]+)\s*", line)
        if not match or match[1] in result:
            raise ValueError("Malformed or duplicate per-mutant diagnostics")
        status = STATUS_ALIASES.get(match[2], match[2])
        if status not in COUNTERS:
            raise ValueError(f"Unresolved mutant status: {match[2]}")
        result[match[1]] = status
    return result


def identity(data: dict, base: str, head: str) -> dict:
    scope = data.get("scope")
    if (
        not isinstance(scope, dict)
        or scope.get("base_sha") != base
        or scope.get("head_sha") != head
    ):
        raise ValueError("Mutation evidence does not match current base/head SHA")
    if type(scope.get("no_targets")) is not bool:
        raise ValueError("Missing mutation no_targets decision")
    return scope


def digest_function(source: str, name: str) -> str:
    tree = ast.parse(source)
    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(nodes) != 1:
        raise ValueError("Missing or ambiguous mutant function proof")
    node = copy.deepcopy(nodes[0])
    node.name = "reviewed_function"
    return hashlib.sha256(ast.unparse(node).encode()).hexdigest()


def review_equivalents(
    review: dict | None,
    selected: dict[str, str],
    trusted: Path,
    candidate: Path,
    base: str,
    head: str,
) -> list[str]:
    survivors = {key for key, status in selected.items() if status == "survived"}
    if not survivors:
        return []
    if not isinstance(review, dict):
        raise ValueError("Surviving mutants require individual audited proofs")
    if review.get("base_sha") != base or review.get("head_sha") != head:
        raise ValueError("Equivalence proof does not match base/head")
    ledger_path = trusted / ".ci/mutation-equivalents.json"
    ledger = read_json(ledger_path, trusted)
    proofs = review.get("mutants")
    reviewed = review.get("reviewed_equivalents")
    if (
        not isinstance(proofs, dict)
        or not isinstance(reviewed, list)
        or any(not isinstance(key, str) for key in reviewed)
        or len(set(reviewed)) != len(reviewed)
        or set(reviewed) != survivors
        or set(proofs) != survivors
    ):
        raise ValueError("Missing, duplicate or extra equivalence proof")
    from mutation_scope import _module_name

    for key in survivors:
        entry, proof = ledger.get(key), proofs[key]
        if (
            not isinstance(entry, dict)
            or not isinstance(proof, dict)
            or not str(entry.get("reason", "")).strip()
        ):
            raise ValueError(f"Unreviewed survivor: {key}")
        relative = entry.get("source")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ValueError("Unsafe equivalence source")
        source = candidate / relative
        if (
            source.is_symlink()
            or not source.resolve().is_relative_to(candidate.resolve())
            or not source.is_file()
            or source.stat().st_size > MAX_BYTES
        ):
            raise ValueError("Missing or unsafe equivalence source")
        module, _, name = key.rpartition(".")
        if _module_name(relative) != module:
            raise ValueError("Equivalence source does not match mutant module")
        for field in ("source", "source_sha256", "mutant_sha256"):
            if proof.get(field) != entry.get(field):
                raise ValueError(f"Altered equivalence proof: {key}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry.get(
            "source_sha256"
        ):
            raise ValueError(f"Stale equivalence source: {key}")
        mutant_source = proof.get("mutant_source")
        if (
            not isinstance(mutant_source, str)
            or len(mutant_source.encode()) > MAX_BYTES
            or digest_function(mutant_source, name) != entry.get("mutant_sha256")
        ):
            raise ValueError(f"Stale equivalence mutant: {key}")
    return sorted(survivors)


def validate(
    data: dict,
    diagnostics: str,
    repo: Path,
    trusted: Path,
    base: str,
    head: str,
    directory: str = ".",
    depth: str = "medium",
) -> dict:
    candidate_project = project(repo, directory)
    trusted_project = project(trusted, directory)
    scope = identity(data, base, head)
    raw_counts = counts(data)
    from mutation_contract import protect_config

    protect_config(trusted_project, candidate_project, base, head)
    engine = data.get("engine", {}).get("name", "mutmut")
    # Engine identity is derived from protected project metadata, never from
    # the report being verified. Bootstrap projects may add their first manifest.
    metadata_root = trusted_project
    if not any(
        (metadata_root / name).is_file()
        for name in ("go.mod", "pyproject.toml", "setup.cfg", "package.json")
    ):
        metadata_root = candidate_project
    if (metadata_root / "go.mod").is_file():
        expected_engine = "gremlins"
    elif any(
        (metadata_root / name).is_file() for name in ("pyproject.toml", "setup.cfg")
    ):
        expected_engine = "mutmut"
    elif (metadata_root / "package.json").is_file():
        expected_engine = "stryker"
    else:
        expected_engine = "mutmut"
    if engine != expected_engine:
        raise ValueError("Mutation engine differs from protected project metadata")
    python_project = engine == "mutmut"
    if python_project:
        targets = mutation_targets(
            candidate_project, base, head, depth=depth, source_root=trusted_project
        )
        has_targets = bool(targets)
    else:
        has_targets = changed_engine_sources(candidate_project, base, head, engine)
        targets = ()
    if scope["no_targets"] is not (not has_targets):
        raise ValueError(
            "No-target evidence contradicts independently computed mutation scope"
        )
    if not has_targets and any(raw_counts.values()):
        raise ValueError("No-target evidence has nonzero counters")
    if has_targets and raw_counts["total"] <= 0:
        raise ValueError("Mutation evidence contains no measured mutants")
    selected = {}
    if python_project and has_targets:
        parsed = statuses(diagnostics)
        if not parsed:
            raise ValueError("Python mutation evidence requires per-mutant diagnostics")
        for target in targets:
            matching = {
                key: status
                for key, status in parsed.items()
                if matches_function(key, target)
            }
            if not matching:
                raise ValueError(
                    f"Changed function produced no mutation evidence: {target}"
                )
            selected.update(matching)
        effective = dict.fromkeys(COUNTERS, 0)
        for status in selected.values():
            effective[status] += 1
        effective["total"] = len(selected)
        # Raw collection totals may include unselected mutants. They must still
        # account for every supplied status and may never undercount the scope.
        for key in COUNTERS:
            if effective[key] > raw_counts[key]:
                raise ValueError("Per-mutant diagnostics contradict aggregate counters")
    else:
        effective = raw_counts
    if any(effective[key] for key in COUNTERS if key not in {"killed", "survived"}):
        unresolved = [
            f"{key}: {status}"
            for key, status in selected.items()
            if status not in {"killed", "survived"}
        ]
        details = ", ".join(unresolved[:20]) or ", ".join(
            f"{key}={effective[key]}"
            for key in COUNTERS
            if key not in {"killed", "survived"} and effective[key]
        )
        raise ValueError(f"Mutation gate failed: unresolved mutant status: {details}")
    equivalents = review_equivalents(
        data.get("review"), selected, trusted_project, candidate_project, base, head
    )
    if effective["survived"] != len(equivalents):
        raise ValueError("Mutation gate failed: unreviewed surviving mutants")
    return {
        "stats": {
            key: effective[key]
            for key in ("killed", "survived", "timeouts", "suspicious", "total")
        },
        "scope": {
            "base_sha": base,
            "head_sha": head,
            "no_targets": not has_targets,
            "targets": list(targets),
        },
        "reviewed_equivalents": equivalents,
        "validation": {"status": "pass", "unresolved": 0, "unreviewed_survivors": 0},
    }


def changed_engine_sources(repo: Path, base: str, head: str, engine: str) -> bool:
    import subprocess

    if engine not in {"gremlins", "stryker"}:
        raise ValueError(f"Unsupported mutation engine: {engine}")
    listing = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--relative",
            "--name-only",
            "-z",
            "--diff-filter=ACMR",
            f"{base}...{head}",
            "--",
            ".",
        ],
        text=True,
    )
    for path in listing.split("\0"):
        parts = Path(path).parts
        if any(
            part
            in {
                "test",
                "tests",
                "__tests__",
                "node_modules",
                "dist",
                "build",
                "vendor",
                "testdata",
            }
            for part in parts
        ):
            continue
        if (
            engine == "gremlins"
            and path.endswith(".go")
            and not path.endswith("_test.go")
        ):
            return True
        if (
            engine == "stryker"
            and re.search(r"\.(?:js|jsx|ts|tsx)$", path)
            and not re.search(r"\.(?:test|spec)\.(?:js|jsx|ts|tsx)$", path)
        ):
            return True
    return False


def pack(report: Path, diagnostics: Path, review: Path, base: str, head: str) -> dict:
    root = report.parent.resolve()
    data = read_json(report, root)
    # Legacy protected runners publish raw Mutmut counters. Bind the capture to
    # the execution SHA before it leaves the execution job; verification rechecks it.
    if "scope" not in data:
        data["scope"] = {"base_sha": base, "head_sha": head, "no_targets": False}
    if diagnostics.is_file():
        if diagnostics.is_symlink() or diagnostics.stat().st_size > MAX_BYTES:
            raise ValueError("Unsafe mutation diagnostics")
        data["diagnostics"] = diagnostics.read_text()
    if review.is_file():
        data["review"] = read_json(review, review.parent)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--trusted", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--directory", default=".")
    parser.add_argument("--depth", choices=("medium", "high"), default="medium")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = read_json(args.evidence, args.evidence.parent)
    diagnostics_path = args.evidence.parent / "mutmut-results.txt"
    diagnostics = data.get("diagnostics", "")
    if not diagnostics and diagnostics_path.is_file():
        diagnostics = diagnostics_path.read_text()
    result = validate(
        data,
        diagnostics,
        args.repo,
        args.trusted,
        args.base,
        args.head,
        args.directory,
        args.depth,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        f"Mutation gate passed: {result['stats']}; reviewed={len(result['reviewed_equivalents'])}"
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"path={args.output}\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        sys.exit(f"Mutation verification failed: {exc}")
