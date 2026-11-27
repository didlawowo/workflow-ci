"""Run a mutation command under an explicit time budget.

A job-level ``timeout-minutes`` cannot use a value computed inside the job, and
when it fires the job ends ``cancelled``, which reads exactly like a run
superseded by a newer revision. This wrapper enforces the budget computed from
the PR scope around the mutation command only, stops the whole process group,
and says that the run stopped on its duration limit.

Exit status 124 means the budget expired, and only that: a command that itself
exits 124 is reported as a plain failure (1), so the caller can rely on it.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys

BUDGET_EXPIRED = 124
GRACE_SECONDS = 30


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def run(
    command: list[str], minutes: int, grace: float = GRACE_SECONDS, unit: float = 60
) -> int:
    if minutes <= 0:
        raise ValueError("Mutation time budget must be positive")
    process = subprocess.Popen(command, start_new_session=True)

    def forward(signum, _frame):
        # Runner cancellation still reaches the detached mutation processes.
        _signal_group(process, signum)

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, forward)
    try:
        status = process.wait(timeout=minutes * unit)
    except subprocess.TimeoutExpired:
        print(
            f"::error::Mutation execution exceeded its time budget of {minutes} "
            "minutes and was stopped. This is a duration limit, not a mutation "
            "verdict: no mutant result is reported for this run.",
            flush=True,
        )
        _signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        # The leader may exit on SIGTERM while another member of the group
        # ignores it: always finish the whole group, not only a live leader.
        _signal_group(process, signal.SIGKILL)
        process.wait()
        return BUDGET_EXPIRED
    if status == BUDGET_EXPIRED:
        return 1
    return status if status >= 0 else 128 - status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("::error::Mutation deadline requires a command.", file=sys.stderr)
        return 1
    try:
        return run(command, args.minutes)
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
