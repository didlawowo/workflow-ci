"""Derive the campaign shard job timeout from the validated plan size.

The auto-sizer targets ``MUTATION_MUTANTS_PER_SHARD`` mutants per shard, but
the per-mutant cost varies by orders of magnitude between repositories (the
per-mutant test selection dominates). A fixed job timeout silently cancels
long campaigns, and a cancelled shard means missing mandatory evidence, so
the gate fails on every PR that touches a sizeable function. The plan knows
the exact mutant count and resolved shard count, so the shard timeout is
derived from the plan itself: a conservative per-mutant pace plus the fixed
per-shard setup cost (mutant generation, stats collection, clean tests).
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

DEFAULT_PACE_SECONDS = 30
DEFAULT_SETUP_MINUTES = 15
MINIMUM_TIMEOUT_MINUTES = 45
MAXIMUM_TIMEOUT_MINUTES = 360


def shard_timeout_minutes(
    total_mutants: int, shard_count: int, pace_seconds: int, setup_minutes: int
) -> int:
    """Return the shard job timeout in minutes for a planned campaign."""
    if pace_seconds <= 0 or setup_minutes < 0:
        raise ValueError("Shard pace must be positive and setup non-negative")
    shards = max(int(shard_count or 1), 1)
    minutes = MINIMUM_TIMEOUT_MINUTES
    if total_mutants:
        minutes = max(
            minutes,
            setup_minutes + math.ceil(total_mutants * pace_seconds / shards / 60),
        )
    return min(minutes, MAXIMUM_TIMEOUT_MINUTES)


def main() -> int:
    if len(sys.argv) != 2:
        print("::error::mutation_shard_budget.py requires one plan path.", file=sys.stderr)
        return 1
    try:
        plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        pace = int(os.environ.get("MUTATION_SHARD_PACE_SECONDS") or DEFAULT_PACE_SECONDS)
        setup = int(os.environ.get("MUTATION_SHARD_SETUP_MINUTES") or DEFAULT_SETUP_MINUTES)
    except (OSError, ValueError) as error:
        print(f"::error::Invalid shard budget inputs: {error}", file=sys.stderr)
        return 1
    total = len(plan.get("mutants") or {})
    timeout = shard_timeout_minutes(total, plan.get("shard_count"), pace, setup)
    print(f"shard-timeout={timeout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
