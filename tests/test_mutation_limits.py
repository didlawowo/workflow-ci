"""Central Mutmut runs are bounded in memory per process and in concurrent mutants."""

import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
limits = importlib.import_module("mutation_limits")
SCRIPT = ROOT / ".ci" / "mutation_limits.py"
MIB = 1024**2
linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="only Linux enforces the data limit",
)


def cgroup(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n")
    return tmp_path


@pytest.mark.parametrize(
    ("cpu_max", "usable", "expected"),
    [
        ("200000 100000", 32, 2),
        ("max 100000", 32, limits.MAX_CHILDREN),
        ("max 100000", 3, 3),
        ("150000 100000", 32, 2),
        ("50000 100000", 32, 1),
        ("1600000 100000", 2, 2),
        ("abc def", 32, limits.MAX_CHILDREN),
        ("200000", 32, limits.MAX_CHILDREN),
    ],
)
def test_children_follow_the_cgroup_v2_cpu_budget(tmp_path, cpu_max, usable, expected):
    root = cgroup(tmp_path, {"cpu.max": cpu_max})
    assert limits.max_children(root, usable) == expected


def test_children_follow_the_cgroup_v1_cpu_budget(tmp_path):
    root = cgroup(
        tmp_path,
        {"cpu/cpu.cfs_quota_us": "300000", "cpu/cpu.cfs_period_us": "100000"},
    )
    assert limits.max_children(root, 32) == 3
    (root / "cpu" / "cpu.cfs_quota_us").write_text("-1\n")
    assert limits.max_children(root, 32) == limits.MAX_CHILDREN


def test_children_without_any_cgroup_file_use_the_ceiling(tmp_path):
    assert limits.max_children(tmp_path, 32) == limits.MAX_CHILDREN
    assert limits.max_children(tmp_path, 2) == 2


def test_there_is_always_at_least_one_child(tmp_path):
    assert limits.max_children(tmp_path, 0) == 1


def test_mutmut_run_receives_the_children_bound(monkeypatch):
    monkeypatch.setattr(limits, "max_children", lambda: 3)
    assert limits.bounded_command(["mutmut", "run", "a", "b"]) == [
        "mutmut",
        "run",
        "--max-children",
        "3",
        "a",
        "b",
    ]
    assert limits.bounded_command(["mutmut", "run"])[2:] == ["--max-children", "3"]


@pytest.mark.parametrize(
    "command",
    [[], ["pytest"], ["mutmut", "results"], ["mutmut", "run", "--max-children", "64"]],
)
def test_only_a_plain_mutmut_run_is_accepted(command):
    with pytest.raises(ValueError):
        limits.bounded_command(command)


def limited(body):
    """Run body in a throwaway interpreter: the limit cannot be undone."""
    code = (
        f"import resource, sys; sys.path.insert(0, {str(ROOT / '.ci')!r}); "
        f"import mutation_limits as limits\n{body}"
    )
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )


@linux_only
def test_limit_is_hard_and_refuses_an_allocation_above_it():
    result = limited(
        f"limits.limit_memory({256 * MIB})\n"
        "print(resource.getrlimit(resource.RLIMIT_DATA))\n"
        f"bytearray({MIB})\n"
        "try:\n"
        f"    bytearray({512 * MIB})\n"
        "except MemoryError:\n"
        "    print('refused')\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"({256 * MIB}, {256 * MIB})",
        "refused",
    ]


@linux_only
def test_a_stricter_runner_limit_is_never_loosened():
    result = limited(
        f"resource.setrlimit(resource.RLIMIT_DATA, ({128 * MIB}, {128 * MIB}))\n"
        f"limits.limit_memory({256 * MIB})\n"
        "print(resource.getrlimit(resource.RLIMIT_DATA))\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"({128 * MIB}, {128 * MIB})"


def test_command_line_refuses_anything_but_mutmut_run():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "echo", "bonjour"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert result.stdout == ""


@linux_only
def test_command_line_starts_mutmut_bounded(tmp_path):
    fake = tmp_path / "bin" / "mutmut"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho "$@"\nulimit -d\n')
    fake.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "mutmut", "run", "target_a", "target_b"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    arguments, data_limit = result.stdout.splitlines()
    command, flag, children, *targets = arguments.split()
    assert (command, flag, targets) == (
        "run",
        "--max-children",
        ["target_a", "target_b"],
    )
    assert 1 <= int(children) <= limits.MAX_CHILDREN
    assert int(data_limit) * 1024 <= limits.MEMORY_LIMIT_BYTES


def test_mutmut_environment_loads_the_memory_verdict_plugin(monkeypatch):
    monkeypatch.setattr(limits, "max_children", lambda: 2)
    environment = limits.bounded_environment(
        {"PATH": "/bin", "PYTEST_ADDOPTS": "-q", "PYTHONPATH": "/consumer"}
    )
    assert environment["PATH"] == "/bin"
    assert environment["PYTEST_ADDOPTS"] == "-q -p mutation_memory_guard"
    assert environment["PYTHONPATH"] == f"{limits.PLUGINS}{os.pathsep}/consumer"
    assert (limits.PLUGINS / "mutation_memory_guard.py").is_file()
    assert environment["OMP_NUM_THREADS"] == environment["OPENBLAS_NUM_THREADS"] == "2"
    kept = limits.bounded_environment({"OMP_NUM_THREADS": "8"})
    assert kept["OMP_NUM_THREADS"] == "8"
    assert kept["PYTEST_ADDOPTS"] == "-p mutation_memory_guard"


VERDICT_TESTS = """
import pytest

def test_out_of_memory():
    raise MemoryError("limit reached")

def test_chained_out_of_memory():
    try:
        raise MemoryError("limit reached")
    except MemoryError as error:
        raise RuntimeError("wrapped") from error

def test_expected_out_of_memory():
    with pytest.raises(MemoryError):
        raise MemoryError("handled by the test")

def test_ordinary_failure():
    assert 1 == 2

def test_success():
    assert True
"""


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ("test_out_of_memory", 35),
        ("test_chained_out_of_memory", 35),
        ("test_out_of_memory or test_ordinary_failure", 35),
        ("test_ordinary_failure", 1),
        ("test_expected_out_of_memory or test_success", 0),
    ],
)
def test_memory_error_is_never_reported_as_a_killed_mutant(
    tmp_path, selected, expected
):
    (tmp_path / "test_verdict.py").write_text(VERDICT_TESTS)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-k",
            selected,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        env=limits.bounded_environment({"PATH": os.environ["PATH"]}),
    )
    assert result.returncode == expected, result.stdout + result.stderr
