"""Resource bounds of a central Mutmut run.

Mutmut only bounds a mutant by CPU time. A mutant that allocates in an endless
loop can exhaust the memory of the node within seconds, and Mutmut starts one
child per core of the node, whatever CPU budget the job really has.

The central engine therefore starts Mutmut under a hard data limit, inherited
by every mutant process, and with a number of children derived from the CPU
budget of the job. A mutant stopped by the limit is never counted as killed
(see pytest_plugins/mutation_memory_guard.py). There is deliberately no option
to lift these bounds.

They guard against accidents, not against an adversary: shared mappings,
temporary filesystems and privileged helpers are outside a data limit, and
each subprocess gets its own allowance. The memory limit of the job container
remains the real bound of a run.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import resource
import sys

# Private writable memory (heap, anonymous mappings, thread stacks) of ONE
# process. Reserved address space and file mappings of large libraries do not
# count, unlike with an address-space limit.
MEMORY_LIMIT_BYTES = 4 * 1024**3
MAX_CHILDREN = 4
CGROUP_ROOT = Path("/sys/fs/cgroup")
PLUGINS = Path(__file__).resolve().parent / "pytest_plugins"
# Numeric libraries start one thread per core of the node, each with its own
# stack and buffers, which a data limit counts.
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")


def _cgroup_cpu_quota(root: Path) -> float | None:
    """CPU quota of the job in cores, None when the cgroup sets none."""
    try:
        quota, period = (root / "cpu.max").read_text().split()  # cgroup v2
    except (OSError, ValueError):
        try:
            quota = (root / "cpu" / "cpu.cfs_quota_us").read_text().strip()
            period = (root / "cpu" / "cpu.cfs_period_us").read_text().strip()
        except OSError:
            return None
    try:
        quota_us, period_us = int(quota), int(period)
    except ValueError:
        return None  # "max": no quota
    if quota_us <= 0 or period_us <= 0:
        return None
    return quota_us / period_us


def max_children(root: Path = CGROUP_ROOT, usable_cpus: int | None = None) -> int:
    """Concurrent mutants: CPU budget of the job, never above MAX_CHILDREN."""
    if usable_cpus is None:
        if hasattr(os, "sched_getaffinity"):
            usable_cpus = len(os.sched_getaffinity(0))
        else:
            usable_cpus = os.cpu_count() or 1
    budget = usable_cpus
    quota = _cgroup_cpu_quota(root)
    if quota is not None:
        budget = min(budget, math.ceil(quota))
    return max(1, min(MAX_CHILDREN, budget))


def limit_memory(limit: int = MEMORY_LIMIT_BYTES) -> None:
    """Cap the data size of this process and of every process it starts."""
    if not sys.platform.startswith("linux"):
        # Only Linux enforces a data limit on anonymous mappings; CI runners
        # are Linux. A local run elsewhere keeps its previous behaviour.
        print("WARN: no per-mutant memory limit outside Linux", file=sys.stderr)
        return
    for existing in resource.getrlimit(resource.RLIMIT_DATA):
        if existing != resource.RLIM_INFINITY:
            limit = min(limit, existing)  # never loosen a limit of the runner
    resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))


def bounded_environment(environ: dict[str, str]) -> dict[str, str]:
    """Environment of the Mutmut run: memory verdict plugin, bounded threads."""
    if not (PLUGINS / "mutation_memory_guard.py").is_file():
        raise RuntimeError(
            "Central mutation engine is incomplete: pytest_plugins/"
            "mutation_memory_guard.py is missing next to mutation_limits.py"
        )
    result = dict(environ)
    result["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(PLUGINS), environ.get("PYTHONPATH")) if part
    )
    result["PYTEST_ADDOPTS"] = " ".join(
        part
        for part in (environ.get("PYTEST_ADDOPTS"), "-p mutation_memory_guard")
        if part
    )
    for name in THREAD_VARIABLES:
        result.setdefault(name, "1")  # each mutant already is a process
    return result


def bounded_command(command: list[str]) -> list[str]:
    """Insert the children bound into a `mutmut run [mutants...]` command."""
    if command[:2] != ["mutmut", "run"] or "--max-children" in command:
        raise ValueError("Expected a plain `mutmut run [mutants...]` command")
    return [*command[:2], "--max-children", str(max_children()), *command[2:]]


# Messages of a test process stopped by its memory limit. Go and Node report
# an ordinary test failure in that case, which their engines count as killed.
MEMORY_SIGNATURES = (
    "fatal error: runtime: out of memory",
    "fatal error: out of memory",
    "JavaScript heap out of memory",
    "Reached heap limit",
    "Fatal process out of memory",
    # Node outside the JavaScript heap (node_errors.cc), worker threads
    # (ERR_WORKER_OUT_OF_MEMORY) and buffers refused by the data limit.
    "Allocation failed - process out of memory",
    "JS heap out of memory",
    "Array buffer allocation failed",
    # npm, yarn and pnpm report a test process killed by the kernel.
    "signal SIGKILL",
    'signal "SIGKILL"',
    "signal=SIGKILL",
)
# Whole lines only: go test prints this summary for a test binary killed by the
# kernel, while the same words inside test output can be ordinary text.
MEMORY_LINES = ("signal: killed",)
# A process killed by SIGKILL is most often the OOM killer: never killed.
_SIGKILL_STATUSES = (-9, 137)


def guard(marker: Path, memory_exit: int, command: list[str]) -> int:
    """Run one test command under the data limit, never as killed on exhaustion.

    Standard output and error are streamed unchanged, each to its own stream.
    When either shows a memory exhaustion, the marker file is written and
    ``memory_exit`` is returned instead of the command status: the engine then
    records the mutant as not killed, and the central script fails on the
    marker.
    """
    import subprocess
    import threading

    if not command:
        raise ValueError("guard requires a command after --")
    limit_memory()
    exhausted = threading.Event()

    def relay(source, target) -> None:
        for line in source:
            target.write(line)
            target.flush()
            if not exhausted.is_set():
                text = line.decode("utf-8", "replace")
                if any(signature in text for signature in MEMORY_SIGNATURES) or (
                    text.strip() in MEMORY_LINES
                ):
                    exhausted.set()

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    relays = [
        threading.Thread(target=relay, args=(process.stdout, sys.stdout.buffer)),
        threading.Thread(target=relay, args=(process.stderr, sys.stderr.buffer)),
    ]
    for thread in relays:
        thread.start()
    status = process.wait()
    for thread in relays:
        thread.join()
    if status in _SIGKILL_STATUSES:
        exhausted.set()
    if not exhausted.is_set():
        return status if status >= 0 else 128 - status
    with marker.open("a", encoding="utf-8") as handle:
        handle.write(" ".join(command)[:200] + "\n")
    return memory_exit


def _guard_main(arguments: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="mutation_limits.py guard")
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--memory-exit", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(arguments)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return guard(args.marker, args.memory_exit, command)


if __name__ == "__main__":
    if sys.argv[1:2] == ["children"]:
        print(max_children())
        sys.exit(0)
    if sys.argv[1:2] == ["guard"]:
        sys.exit(_guard_main(sys.argv[2:]))
    try:
        bounded = bounded_command(sys.argv[1:])
    except ValueError as error:
        sys.exit(str(error))
    try:
        environment = bounded_environment(dict(os.environ))
    except RuntimeError as error:
        sys.exit(f"::error::{error}")
    print(
        f"Mutation bounds: {bounded[3]} concurrent mutants, "
        f"{MEMORY_LIMIT_BYTES // 1024**2} MiB of data per process",
        file=sys.stderr,
    )
    limit_memory()
    os.execvpe(bounded[0], bounded, environment)
