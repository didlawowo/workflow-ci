"""Run only protected campaign entrypoints with a sanitized environment."""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from mutation_environment import execution_environment
from mutation_evidence import project


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
    if not 1 <= args.shards <= 16 or not 0 <= args.index < args.shards:
        parser.error("Invalid shard partition")
    root = args.root.resolve()
    trusted = project(root / ".policy", args.directory)
    candidate = project(root / "pr", args.directory)
    script = trusted / ".ci" / f"mutation-{args.mode}.sh"
    if (
        script.is_symlink()
        or not script.is_file()
        or not script.resolve().is_relative_to(trusted)
    ):
        parser.error(f"Missing protected campaign entrypoint: {script.name}")
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
