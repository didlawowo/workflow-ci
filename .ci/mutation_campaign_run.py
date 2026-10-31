"""Run only protected campaign entrypoints with a sanitized environment."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from mutation_environment import execution_environment
from mutation_evidence import project


def resolve_entrypoint(root: Path, mode: str) -> Path:
    """Return the consumer entrypoint, falling back to the central one. The
    fallback comes from the pinned workflow-ci checkout, the same commit whose
    code performs this validation, so it carries the same trust as a declared
    entrypoint and consumers do not need to vendor campaign scripts. An unsafe
    (symlinked or escaping) consumer entrypoint is a hard failure, never a
    reason to silently switch implementation."""
    policy = root / ".policy"
    declared = policy / ".ci" / f"mutation-{mode}.sh"
    if declared.is_symlink() or (
        declared.exists()
        and not declared.resolve().is_relative_to(policy.resolve())
    ):
        raise SystemExit(
            f"Missing protected campaign entrypoint: {declared.name}"
        )
    if declared.is_file():
        return declared
    workflow_ci = root / ".workflow-ci"
    central = workflow_ci / ".ci" / f"mutation-{mode}.sh"
    if (
        central.is_symlink()
        or not central.is_file()
        or not central.resolve().is_relative_to(workflow_ci.resolve())
    ):
        raise SystemExit(
            f"Missing protected campaign entrypoint: {central.name}"
        )
    return central


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "shard"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--directory", default=".")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--index", type=int, default=0)
    args = parser.parse_args()
    # shards == 0 asks the planner to size the campaign automatically; the
    # resolved count is read back from the generated plan and published.
    auto = args.mode == "plan" and args.shards == 0
    if not 0 <= args.shards <= 16:
        parser.error("Invalid shard partition")
    if not auto and not 1 <= args.shards <= 16:
        parser.error("Invalid shard partition")
    if not 0 <= args.index < max(args.shards, 1):
        parser.error("Invalid shard partition")
    root = args.root.resolve()
    candidate = project(root / "pr", args.directory)
    script = resolve_entrypoint(root, args.mode)
    with tempfile.TemporaryDirectory(prefix="mutation-campaign-") as scratch:
        environment = execution_environment(dict(os.environ))
        environment.update(
            {
                "HOME": scratch,
                "RUNNER_TEMP": scratch,
                "UV_CACHE_DIR": f"{scratch}/uv",
                "GOCACHE": f"{scratch}/go-build",
                "GOMODCACHE": f"{scratch}/go-mod",
                "MUTATION_PLAN": str(args.plan.resolve()),
                "MUTATION_WORKING_DIRECTORY": args.directory,
                "MUTATION_SHARD_COUNT": str(args.shards),
                "MUTATION_SHARD_INDEX": str(args.index),
            }
        )
        # No action output files are exposed to the consumer runner. It receives
        # one ordinary scratch output path, copied by trusted code afterwards.
        result = Path(scratch) / "result.json"
        completed = subprocess.run(
            ["bash", str(script), str(result)],
            cwd=candidate,
            env=environment,
            check=False,
        )
        if completed.returncode:
            raise SystemExit(
                f"Protected campaign {args.mode} failed: {completed.returncode}"
            )
        if (
            result.is_symlink()
            or not result.is_file()
            or result.stat().st_size > 20_000_000
        ):
            raise SystemExit("Missing, symlinked or oversized campaign output")
        args.output.write_bytes(result.read_bytes())
    if args.mode == "plan":
        plan = json.loads(args.output.read_text(encoding="utf-8"))
        shard_count = plan.get("shard_count")
        if type(shard_count) is not int or not 1 <= shard_count <= 16:
            raise SystemExit("Campaign plan declares an invalid shard count")
        output_path = os.environ.get("GITHUB_OUTPUT")
        # Only an already-existing action output file is written; the wrapper
        # never creates one, so a spoofed path cannot materialize.
        if output_path and os.path.isfile(output_path):
            with open(output_path, "a", encoding="utf-8") as handle:
                handle.write(f"shard-count={shard_count}\n")
