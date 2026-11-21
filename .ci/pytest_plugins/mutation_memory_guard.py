"""Pytest plugin loaded by the central Mutmut run.

A test that fails on MemoryError says its process reached the memory limit,
not that the tests caught the mutation. Reporting it as an ordinary failure
(exit 1) would count the mutant as killed. The session then exits with
Mutmut's "suspicious" code instead, which every central gate reads as not
killed: same verdict as a mutant stopped by its time limit.

What it cannot see stays an ordinary failure: code under test that catches
the MemoryError and then fails differently, or a subprocess of the tests that
runs out of memory. A nested pytest started by the tests inherits this plugin
through the environment.
"""

from __future__ import annotations

import pytest

SUSPICIOUS = 35
_PROPERTY = "mutation_memory_guard"
_FLAG = "_mutation_memory_guard_triggered"
_config = None


def _is_memory_error(error: BaseException | None) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, MemoryError):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


def pytest_configure(config):
    global _config
    _config = config


def pytest_unconfigure(config):
    global _config
    if _config is config:
        _config = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if call.excinfo is not None and _is_memory_error(call.excinfo.value):
        # A report property also reaches the main session of a distributed run.
        outcome.get_result().user_properties.append((_PROPERTY, True))


def pytest_runtest_logreport(report):
    if _config is not None and any(
        name == _PROPERTY for name, _ in getattr(report, "user_properties", ())
    ):
        setattr(_config, _FLAG, True)


def pytest_collectreport(report):
    if _config is not None and report.failed and "MemoryError" in str(report.longrepr):
        setattr(_config, _FLAG, True)


def pytest_sessionfinish(session, exitstatus):
    if getattr(session.config, _FLAG, False) and session.exitstatus == 1:
        session.exitstatus = SUSPICIOUS
