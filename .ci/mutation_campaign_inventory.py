"""Central campaign planner and worker producing content-bound plans and fragments.

Plan mode enumerates the exact Mutmut 3.x mutant inventory for the PR scope and
writes the content-bound plan JSON. Shard mode executes one plan partition and
writes the campaign fragment. Both modes implement the contract validated by
mutation_campaign.validate_plan/aggregate so that consumers do not need their
own mutation-plan.sh / mutation-shard.sh entrypoints.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from mutation_evidence import COUNTERS, digest_function
from mutation_campaign import partition, plan_digest
from mutation_scope import mutation_targets

MUTANTS_PER_SHARD = int(os.environ.get("MUTATION_MUTANTS_PER_SHARD", "1200"))
MAX_SHARDS = 16
VARIANT_MARKER = "__mutmut_"


class InventoryError(ValueError):
    """The mutant inventory cannot be produced or executed safely."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_shard_count(requested: int, total: int) -> int:
    if requested:
        return requested
    return max(1, min(MAX_SHARDS, math.ceil(total / MUTANTS_PER_SHARD)))


def variant_name(key: str) -> str:
    module, _, name = key.rpartition(".")
    if not name.startswith("x_") or VARIANT_MARKER not in name:
        raise InventoryError(f"Malformed mutant key: {key}")
    return name


def extract_variants(code: str) -> dict[str, str]:
    """Return every ``x_<fn>__mutmut_N`` function source inside a staged file."""
    tree = ast.parse(code)
    variants: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if not name.startswith("x_") or not re.search(
            rf"{VARIANT_MARKER}\d+$", name
        ):
            continue
        segment = ast.get_source_segment(code, node)
        if segment is not None:
            variants[name] = segment
    return variants


def _source_paths(project_root: Path) -> list[Path]:
    """Read mutmut source_paths from pyproject/setup.cfg the same way Mutmut does."""
    import configparser

    config = configparser.ConfigParser()
    setup_cfg = project_root / "setup.cfg"
    if setup_cfg.is_file():
        config.read(setup_cfg, encoding="utf-8")
        raw = config.get("mutmut", "source_paths", fallback="")
        paths = [part for part in raw.replace(",", " ").split() if part]
        if paths:
            return [project_root / part for part in paths]
    pyproject = project_root / "pyproject.toml"
    if pyproject.is_file():
        import tomllib

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        raw = data.get("tool", {}).get("mutmut", {}).get("source_paths", [])
        if isinstance(raw, str):
            raw = [raw]
        return [project_root / part for part in raw]
    for candidate in ("src",):
        if (project_root / candidate).is_dir():
            return [project_root / candidate]
    if (project_root / "tests").is_dir():
        return [project_root]
    raise InventoryError("Cannot determine mutation source paths")


def _empty_plan(base: str, head: str, directory: str) -> dict:
    return {
        "schema_version": 1,
        "base_sha": base,
        "head_sha": head,
        "working_directory": directory,
        "shard_count": 1,
        "sources": {},
        "mutants": {},
    }


def plan(project_root: Path, base: str, head: str, directory: str, shards: int, depth: str) -> dict:
    from mutmut.mutation.file_mutation import mutate_file_contents

    targets = list(
        mutation_targets(
            project_root, base, head, depth=depth, source_root=project_root
        )
    )
    if not targets:
        if shards:
            raise InventoryError("Mutation plan requires at least one changed function")
        # Automatic sizing tolerates an empty scope: a PR touching no mutable
        # function produces an empty plan instead of failing the campaign.
        return _empty_plan(base, head, directory)
    from mutation_scope import matches_function

    sources: dict[str, str] = {}
    mutants: dict[str, dict[str, str]] = {}
    for root in _source_paths(project_root):
        if not root.is_dir():
            continue
        for source in sorted(root.rglob("*.py")):
            if source.is_symlink() or not source.is_file():
                continue
            relative = source.relative_to(project_root).as_posix()
            mutmut_relative = source.relative_to(root).as_posix()
            try:
                code = source.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            module = mutmut_relative.removesuffix(".py").replace("/", ".")
            if module.endswith(".__init__"):
                module = module.removesuffix(".__init__")
            mutated = mutate_file_contents(mutmut_relative, code)
            if not mutated.mutant_names:
                continue
            staged = mutated.code
            variants: dict[str, str] | None = None
            file_mutants = 0
            for name in mutated.mutant_names:
                key = f"{module}.{name}"
                if not any(matches_function(key, target) for target in targets):
                    continue
                if variants is None:
                    variants = extract_variants(staged)
                if name not in variants:
                    raise InventoryError(
                        f"Mutmut staging did not expose mutant source: {key}"
                    )
                mutants[key] = {
                    "source": relative,
                    "mutant_sha256": digest_function(variants[name], name),
                }
                file_mutants += 1
            if file_mutants:
                sources[relative] = sha256(source.read_bytes())
    if not mutants:
        raise InventoryError(
            "Mutation plan found no mutants for the changed functions"
        )
    shard_count = resolve_shard_count(shards, len(mutants))
    return {
        "schema_version": 1,
        "base_sha": base,
        "head_sha": head,
        "working_directory": directory,
        "shard_count": shard_count,
        "sources": sources,
        "mutants": mutants,
    }


def _staged_variant(project_root: Path, relative: str, name: str) -> str:
    staged = project_root / "mutants" / relative
    if not staged.is_file():
        raise InventoryError(f"Staged mutant source is missing: {relative}")
    variants = extract_variants(staged.read_text(encoding="utf-8"))
    if name not in variants:
        raise InventoryError(f"Staged mutant source is missing: {name}")
    return variants[name]


def _key_statuses(project_root: Path, keys: set[str]) -> dict[str, str]:
    """Read native per-mutant metadata, same status table as the trusted
    diagnostics exporter, but keyed individually for the fragment."""
    statuses: dict[str, str] = {}
    for path in (project_root / "mutants").rglob("*.meta"):
        entries = json.loads(path.read_text(encoding="utf-8")).get(
            "exit_code_by_key"
        )
        if not isinstance(entries, dict):
            raise InventoryError("Malformed per-mutant metadata")
        for key, code in entries.items():
            if key in statuses:
                raise InventoryError(f"Duplicate mutant metadata: {key}")
            if type(code) is not int:
                status = "skipped" if code is None else "suspicious"
            else:
                status = {
                    0: "survived",
                    1: "killed",
                    -24: "timeouts",
                    5: "no_tests",
                    33: "no_tests",
                    -11: "segfault",
                }.get(code, "suspicious")
            statuses[key] = status
    missing = keys - set(statuses)
    if missing:
        raise InventoryError(
            f"Mutmut did not report a status for {sorted(missing)[0]}"
        )
    return statuses


def shard(project_root: Path, plan_json: dict, index: int) -> dict:
    shard_count = plan_json["shard_count"]
    keys = sorted(
        key
        for key in plan_json["mutants"]
        if partition(key, shard_count) == index
    )
    if not keys:
        # Automatic campaigns can legitimately schedule an empty partition
        # (no mutable function changed); it reports a zero fragment instead
        # of running Mutmut with no keys.
        return {
            "shard_index": index,
            "shard_count": shard_count,
            "plan_sha256": plan_digest(plan_json),
            "base_sha": plan_json["base_sha"],
            "head_sha": plan_json["head_sha"],
            "exit_code": 0,
            "mutants": {},
            "stats": {**dict.fromkeys(COUNTERS, 0), "total": 0},
            "review": {
                "base_sha": plan_json["base_sha"],
                "head_sha": plan_json["head_sha"],
                "reviewed_equivalents": [],
                "mutants": {},
            },
        }
    completed = subprocess.run(
        ["mutmut", "run", *keys], cwd=project_root, check=False
    )
    if completed.returncode:
        raise InventoryError(f"mutmut run failed with status {completed.returncode}")
    all_statuses = _key_statuses(project_root, set(keys))
    observed: dict[str, dict[str, str]] = {}
    stats = dict.fromkeys(COUNTERS, 0)
    for key in keys:
        status = all_statuses[key]
        if status not in COUNTERS:
            raise InventoryError(f"Mutmut did not report a status for {key}")
        observed[key] = {
            "mutant_sha256": plan_json["mutants"][key]["mutant_sha256"],
            "status": status,
        }
        stats[status] += 1
    stats["total"] = len(observed)
    survivors = sorted(
        key for key, item in observed.items() if item["status"] == "survived"
    )
    review: dict[str, object] = {
        "base_sha": plan_json["base_sha"],
        "head_sha": plan_json["head_sha"],
        "reviewed_equivalents": survivors,
        "mutants": {},
    }
    proofs: dict[str, object] = review["mutants"]  # type: ignore[index]
    for key in survivors:
        entry = plan_json["mutants"][key]
        name = variant_name(key)
        relative = entry["source"]
        proofs[key] = {
            "source": relative,
            "source_sha256": plan_json["sources"][relative],
            "mutant_sha256": entry["mutant_sha256"],
            "mutant_source": _staged_variant(project_root, relative, name),
        }
    return {
        "shard_index": index,
        "shard_count": shard_count,
        "plan_sha256": plan_digest(plan_json),
        "base_sha": plan_json["base_sha"],
        "head_sha": plan_json["head_sha"],
        "exit_code": 0,
        "mutants": observed,
        "stats": stats,
        "review": review,
    }


def main() -> int:
    mode = sys.argv[1]
    output = Path(sys.argv[2])
    directory = os.environ.get("MUTATION_WORKING_DIRECTORY", ".")
    project_root = Path.cwd()
    if mode == "plan":
        base = os.environ["MUTATION_BASE_SHA"]
        head = os.environ["MUTATION_HEAD_SHA"]
        depth = os.environ.get("MUTATION_DEPTH", "medium")
        shards = int(os.environ.get("MUTATION_SHARD_COUNT", "0"))
        payload = plan(project_root, base, head, directory, shards, depth)
    elif mode == "shard":
        plan_path = Path(os.environ["MUTATION_PLAN"])
        plan_json = json.loads(plan_path.read_text(encoding="utf-8"))
        index = int(os.environ["MUTATION_SHARD_INDEX"])
        declared = int(os.environ["MUTATION_SHARD_COUNT"])
        if declared != plan_json.get("shard_count"):
            raise InventoryError(
                "Declared shard count differs from the validated plan"
            )
        payload = shard(project_root, plan_json, index)
    else:
        raise InventoryError(f"Unsupported inventory mode: {mode}")
    output.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        sys.exit(f"Campaign inventory failure: {error}")
