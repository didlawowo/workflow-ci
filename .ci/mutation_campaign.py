"""Content-bound planning and complete fan-in for protected mutation runners."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

from mutation_contract import read_json
from mutation_evidence import (
    COUNTERS,
    MAX_BYTES,
    counts,
    project,
    review_equivalents,
    validate,
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_digest(plan: dict) -> str:
    return sha256(
        json.dumps(
            plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    )


def partition(mutant_id: str, shard_count: int) -> int:
    return int(sha256(mutant_id.encode()), 16) % shard_count


def validate_plan(
    plan: dict, repo: Path, base: str, head: str, directory: str, shard_count: int
) -> None:
    if type(shard_count) is not int or not 1 <= shard_count <= 16:
        raise ValueError("Campaign requires between 1 and 16 shards")
    if (
        plan.get("schema_version") != 1
        or plan.get("base_sha") != base
        or plan.get("head_sha") != head
        or plan.get("working_directory") != directory
        or plan.get("shard_count") != shard_count
    ):
        raise ValueError("Campaign plan identity does not match execution")
    project_root = project(repo, directory)
    sources, mutants = plan.get("sources"), plan.get("mutants")
    if (
        not isinstance(sources, dict)
        or not sources
        or not isinstance(mutants, dict)
        or not mutants
        or len(mutants) > 100_000
    ):
        raise ValueError("Campaign requires a nonempty bounded expected inventory")
    for relative, expected_hash in sources.items():
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ValueError("Unsafe campaign source path")
        source = project_root / relative
        if (
            source.is_symlink()
            or not source.resolve().is_relative_to(project_root)
            or not source.is_file()
            or source.stat().st_size > MAX_BYTES
            or sha256(source.read_bytes()) != expected_hash
        ):
            raise ValueError(f"Campaign source content changed: {relative}")
    for mutant_id, item in mutants.items():
        if (
            not isinstance(mutant_id, str)
            or not isinstance(item, dict)
            or item.get("source") not in sources
            or not isinstance(item.get("mutant_sha256"), str)
            or len(item["mutant_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in item["mutant_sha256"])
        ):
            raise ValueError("Malformed expected mutant identity")


def aggregate(
    plan: dict,
    fragments: list[dict],
    repo: Path,
    trusted: Path,
    base: str,
    head: str,
    directory: str,
    depth: str,
    shard_count: int,
) -> dict:
    validate_plan(plan, repo, base, head, directory, shard_count)
    if len(fragments) != shard_count:
        raise ValueError("Missing campaign shards")
    digest = plan_digest(plan)
    seen_shards = set()
    statuses = {}
    reviewed = {}
    for fragment in fragments:
        index = fragment.get("shard_index")
        if (
            type(index) is not int
            or not 0 <= index < shard_count
            or index in seen_shards
        ):
            raise ValueError("Duplicate or invalid campaign shard")
        seen_shards.add(index)
        if (
            fragment.get("plan_sha256") != digest
            or fragment.get("base_sha") != base
            or fragment.get("head_sha") != head
            or fragment.get("shard_count") != shard_count
        ):
            raise ValueError("Campaign fragment identity changed")
        if type(fragment.get("exit_code")) is not int or fragment["exit_code"] != 0:
            raise ValueError("Failed campaign shard")
        observed = fragment.get("mutants")
        expected = {
            key for key in plan["mutants"] if partition(key, shard_count) == index
        }
        if not isinstance(observed, dict) or set(observed) != expected:
            raise ValueError("Missing, duplicated or unexpected campaign mutants")
        shard_stats = dict.fromkeys(COUNTERS, 0)
        for key, item in observed.items():
            if (
                key in statuses
                or not isinstance(item, dict)
                or item.get("mutant_sha256") != plan["mutants"][key]["mutant_sha256"]
                or item.get("status") not in COUNTERS
            ):
                raise ValueError("Duplicate, altered or unresolved campaign mutant")
            statuses[key] = item["status"]
            shard_stats[item["status"]] += 1
        shard_stats["total"] = len(observed)
        if counts({"stats": fragment.get("stats")}) != shard_stats:
            raise ValueError("Campaign fragment counters contradict individual results")
        review = fragment.get("review")
        survivors = {
            key for key, item in observed.items() if item["status"] == "survived"
        }
        if survivors:
            if (
                not isinstance(review, dict)
                or review.get("base_sha") != base
                or review.get("head_sha") != head
                or not isinstance(review.get("reviewed_equivalents"), list)
                or len(review["reviewed_equivalents"]) != len(survivors)
                or set(review["reviewed_equivalents"]) != survivors
                or not isinstance(review.get("mutants"), dict)
                or set(review["mutants"]) != survivors
            ):
                raise ValueError(
                    "Campaign equivalents require complete individual proofs"
                )
            for key in survivors:
                proof = review["mutants"][key]
                expected_mutant = plan["mutants"][key]
                if (
                    not isinstance(proof, dict)
                    or proof.get("source") != expected_mutant["source"]
                    or proof.get("source_sha256")
                    != plan["sources"][expected_mutant["source"]]
                    or proof.get("mutant_sha256") != expected_mutant["mutant_sha256"]
                ):
                    raise ValueError("Equivalence proof differs from planned mutant")
            reviewed.update(review["mutants"])
    if set(statuses) != set(plan["mutants"]):
        raise ValueError("Incomplete campaign inventory")
    stats = dict.fromkeys(COUNTERS, 0)
    stats.update(Counter(statuses.values()))
    stats["total"] = len(statuses)
    payload = {
        "stats": stats,
        "scope": {"base_sha": base, "head_sha": head, "no_targets": False},
        "diagnostics": "".join(f"{key}: {statuses[key]}\n" for key in sorted(statuses)),
        "review": {
            "base_sha": base,
            "head_sha": head,
            "reviewed_equivalents": sorted(reviewed),
            "mutants": reviewed,
        },
    }
    if any(stats[key] for key in COUNTERS if key not in {"killed", "survived"}):
        raise ValueError("Campaign contains unresolved mutants")
    equivalents = review_equivalents(
        payload["review"],
        statuses,
        project(trusted, directory),
        project(repo, directory),
        base,
        head,
    )
    result = validate(
        payload, payload["diagnostics"], repo, trusted, base, head, directory, depth
    )
    result["stats"] = {
        key: stats[key]
        for key in ("killed", "survived", "timeouts", "suspicious", "total")
    }
    result["reviewed_equivalents"] = equivalents
    result["campaign"] = {
        "complete": True,
        "plan_sha256": digest,
        "shards": shard_count,
        "expected_mutants": len(statuses),
    }
    result["raw_stats"] = stats
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "aggregate"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--trusted", type=Path)
    parser.add_argument("--fragments", type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--directory", default=".")
    parser.add_argument("--depth", choices=("medium", "high"), default="medium")
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = read_json(args.plan, args.plan.parent)
    validate_plan(plan, args.repo, args.base, args.head, args.directory, args.shards)
    if args.mode == "plan":
        value = {"include": [{"index": index} for index in range(args.shards)]}
        with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
            handle.write(f"matrix={json.dumps(value)}\n")
        args.output.write_text(json.dumps(plan, sort_keys=True) + "\n")
    else:
        if not args.fragments or not args.trusted:
            raise ValueError(
                "Campaign aggregation requires fragments and protected base"
            )
        paths = list(args.fragments.rglob("fragment.json"))
        fragments = [read_json(path, args.fragments) for path in paths]
        result = aggregate(
            plan,
            fragments,
            args.repo,
            args.trusted,
            args.base,
            args.head,
            args.directory,
            args.depth,
            args.shards,
        )
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        sys.exit(f"Mutation campaign failed: {exc}")
