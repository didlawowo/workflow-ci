"""Pytest plugin loaded by the central Mutmut run.

A test that fails on MemoryError says its process reached the memory limit,
not that the tests caught the mutation. Reporting it as an ordinary failure
(exit 1) would count the mutant as killed. The session then exits with
Mutmut's "suspicious" code instead, which every central gate reads as not
killed: same verdict as a mutant stopped by its time limit.
"""

from __future__ import annotations

SUSPICIOUS = 35
_FLAG = "_mutation_memory_guard_triggered"


def _is_memory_error(error: BaseException | None) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, MemoryError):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


def pytest_runtest_makereport(item, call):
    if call.excinfo is not None and _is_memory_error(call.excinfo.value):
        setattr(item.config, _FLAG, True)


def pytest_sessionfinish(session, exitstatus):
    if getattr(session.config, _FLAG, False) and session.exitstatus == 1:
        session.exitstatus = SUSPICIOUS
