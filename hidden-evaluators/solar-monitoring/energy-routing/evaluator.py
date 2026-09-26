from __future__ import annotations

import datetime as real_datetime
import random
from pathlib import Path
from zoneinfo import ZoneInfo

from sandbox import CandidateSandbox

MAX_CHARGE_W = 1600
MAX_DISCHARGE_W = 1200
MIN_POWER_W = 50
BATTERY_MIN_LEVEL = 10
SUMMER_SOLAR_STOP_SOC = 96
GRID_CHARGE_STOP_SOC = 99

BRIDGE = r"""
import asyncio
import datetime as real_datetime
import importlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd() / "src"))


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


loguru = types.ModuleType("loguru")
loguru.logger = _Logger()
sys.modules["loguru"] = loguru

config = types.ModuleType("core.config")
config.settings = SimpleNamespace(battery_min_level=10)
sys.modules["core.config"] = config

providers = types.ModuleType("providers")
providers.__path__ = [str(Path.cwd() / "src" / "providers")]
sys.modules["providers"] = providers

tempo = types.ModuleType("providers.tempo")


class Tariff:
    def __init__(self, hc_price: float, hp_price: float) -> None:
        self.hc_price = hc_price
        self.hp_price = hp_price


class TempoProvider:
    TARIFS_9KVA = {
        "bleu": Tariff(10.0, 20.0),
        "blanc": Tariff(12.0, 30.0),
        "rouge": Tariff(15.0, 75.0),
    }


tempo.TempoProvider = TempoProvider
sys.modules["providers.tempo"] = tempo

optimizer = importlib.import_module("core.optimizer")
request = json.loads(sys.stdin.read())


async def execute(case):
    when = real_datetime.datetime.fromisoformat(case["when"])

    class FrozenDateTime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            if case.get("explode_clock"):
                raise RuntimeError("hidden clock failure")
            return when.astimezone(tz) if tz is not None else when.replace(tzinfo=None)

    old = optimizer.datetime.datetime
    optimizer.datetime.datetime = FrozenDateTime
    try:
        decision = await optimizer.optimize_charging(**case["kwargs"])
    finally:
        optimizer.datetime.datetime = old
    return {
        "should_charge": decision.should_charge,
        "power": decision.power,
        "reason": decision.reason,
    }


async def main():
    return [await execute(case) for case in request["cases"]]


result = asyncio.run(main())
print("__WORKFLOW_CI_RESULT__=" + json.dumps({"result": result}, allow_nan=False))
"""


def _run_cases(sandbox: CandidateSandbox, cases: list[dict]) -> list[dict]:
    value = sandbox.request_python(BRIDGE, {"cases": cases}, timeout=180)
    result = value.get("result")
    if not isinstance(result, list) or len(result) != len(cases):
        raise RuntimeError("candidate optimizer returned invalid batch result")
    return result


def _case(when: real_datetime.datetime, **kwargs) -> dict:
    return {"when": when.isoformat(), "kwargs": kwargs}


def _assert_safe(decision: dict) -> None:
    assert isinstance(decision.get("should_charge"), bool)
    assert isinstance(decision.get("power"), int)
    ceiling = MAX_CHARGE_W if decision["should_charge"] else MAX_DISCHARGE_W
    assert 0 <= decision["power"] <= ceiling
    assert isinstance(decision.get("reason"), str) and decision["reason"]


def _routing_invariants(sandbox: CandidateSandbox, rng: random.Random) -> None:
    summer_peak = real_datetime.datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Europe/Paris"))
    cases = []
    expected = []

    for _ in range(40):
        consumption = rng.randint(0, 700)
        surplus = rng.randint(MIN_POWER_W + 1, 3500)
        production = consumption + surplus
        battery = rng.randint(BATTERY_MIN_LEVEL + 1, SUMMER_SOLAR_STOP_SOC - 1)
        cases.append(
            _case(
                summer_peak,
                is_peak=True,
                today_color="bleu",
                battery_level=battery,
                production=production,
                consumption=consumption,
                production_limit=rng.randint(0, min(200, production - 1)),
                ev_is_charging=False,
            )
        )
        expected.append((True, min(MAX_CHARGE_W, surplus)))

    for _ in range(40):
        production = rng.randint(0, 180)
        deficit = rng.randint(MIN_POWER_W + 1, 3000)
        consumption = production + deficit
        battery = rng.randint(BATTERY_MIN_LEVEL + 1, 95)
        cases.append(
            _case(
                summer_peak,
                is_peak=True,
                today_color="bleu",
                battery_level=battery,
                production=production,
                consumption=consumption,
                production_limit=max(200, production),
                ev_is_charging=False,
            )
        )
        expected.append((False, min(MAX_DISCHARGE_W, deficit)))

    for result, (should_charge, power) in zip(
        _run_cases(sandbox, cases), expected, strict=True
    ):
        _assert_safe(result)
        assert result["should_charge"] is should_charge
        assert result["power"] == power


def _boundary_and_fail_safe(sandbox: CandidateSandbox, rng: random.Random) -> None:
    summer_peak = real_datetime.datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Europe/Paris"))
    cases = [
        _case(
            summer_peak,
            is_peak=True,
            today_color="bleu",
            battery_level=SUMMER_SOLAR_STOP_SOC,
            production=1200,
            consumption=200,
            production_limit=100,
            ev_is_charging=False,
        )
    ]
    expectations = [(False, 0)]

    for battery in (0, BATTERY_MIN_LEVEL):
        cases.append(
            _case(
                summer_peak,
                is_peak=True,
                today_color="bleu",
                battery_level=battery,
                production=0,
                consumption=rng.randint(200, 1000),
                production_limit=50,
                ev_is_charging=False,
            )
        )
        expectations.append((False, 0))

    cases.extend(
        [
            _case(
                summer_peak,
                is_peak=True,
                today_color="bleu",
                battery_level=70,
                production=0,
                consumption=900,
                production_limit=50,
                ev_is_charging=True,
            ),
            _case(
                summer_peak,
                is_peak=True,
                today_color="bleu",
                battery_level=70,
                production=0,
                consumption=MIN_POWER_W - 1,
                production_limit=50,
                ev_is_charging=False,
            ),
            _case(
                summer_peak,
                is_peak=True,
                today_color=None,
                battery_level=-500,
                production=-99999,
                consumption=-99999,
                production_limit=-10,
                ev_is_charging=False,
            ),
            _case(
                summer_peak,
                is_peak=True,
                today_color="bleu",
                battery_level=50,
                production=10**9,
                consumption=0,
                production_limit=50,
                ev_is_charging=False,
            ),
        ]
    )
    expectations.extend([(False, 0), (False, 0), (False, 0), (True, MAX_CHARGE_W)])

    for result, (should_charge, power) in zip(
        _run_cases(sandbox, cases), expectations, strict=True
    ):
        _assert_safe(result)
        assert result["should_charge"] is should_charge
        assert result["power"] == power


def _temporal_invariants(sandbox: CandidateSandbox, rng: random.Random) -> None:
    summer_night = real_datetime.datetime(2026, 7, 15, 4, 0, tzinfo=ZoneInfo("Europe/Paris"))
    winter_red_peak = real_datetime.datetime(2026, 1, 15, 18, 0, tzinfo=ZoneInfo("Europe/Paris"))
    consumption = rng.randint(300, 2600)
    cases = [
        _case(
            summer_night,
            is_peak=False,
            today_color="bleu",
            battery_level=50,
            production=0,
            consumption=300,
            production_limit=50,
            ev_is_charging=False,
            tomorrow_color="bleu",
            yesterday_color="bleu",
            tomorrow_weather="ensoleillé",
        ),
        _case(
            summer_night,
            is_peak=False,
            today_color="bleu",
            battery_level=GRID_CHARGE_STOP_SOC,
            production=0,
            consumption=300,
            production_limit=50,
            ev_is_charging=False,
            tomorrow_color="bleu",
            yesterday_color="bleu",
            tomorrow_weather=None,
        ),
        _case(
            winter_red_peak,
            is_peak=True,
            today_color="rouge",
            battery_level=80,
            production=0,
            consumption=consumption,
            production_limit=50,
            ev_is_charging=False,
            tomorrow_color="bleu",
            yesterday_color="rouge",
        ),
    ]
    expected = [(False, 0), (False, 0), (False, min(MAX_DISCHARGE_W, consumption))]
    for result, (should_charge, power) in zip(
        _run_cases(sandbox, cases), expected, strict=True
    ):
        _assert_safe(result)
        assert result["should_charge"] is should_charge
        assert result["power"] == power


def _exception_is_standby(sandbox: CandidateSandbox) -> None:
    when = real_datetime.datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Europe/Paris"))
    case = _case(
        when,
        is_peak=True,
        today_color="bleu",
        battery_level=50,
        production=500,
        consumption=200,
        production_limit=50,
    )
    case["explode_clock"] = True
    result = _run_cases(sandbox, [case])[0]
    _assert_safe(result)
    assert result["should_charge"] is False and result["power"] == 0


def _check(name: str, callback) -> dict[str, str]:
    try:
        callback()
    except AssertionError as exc:
        return {"name": name, "status": "fail", "detail": str(exc)}
    return {"name": name, "status": "pass"}


def evaluate(candidate: Path, seed: int) -> list[dict[str, str]]:
    if not (candidate / "src" / "core" / "optimizer.py").is_file():
        raise RuntimeError("candidate does not look like solar-monitoring")
    rng = random.Random(seed)
    with CandidateSandbox(candidate) as sandbox:
        return [
            _check("surplus-deficit-routing", lambda: _routing_invariants(sandbox, rng)),
            _check("safety-boundaries", lambda: _boundary_and_fail_safe(sandbox, rng)),
            _check("tempo-seasonal-boundaries", lambda: _temporal_invariants(sandbox, rng)),
            _check("exception-fails-safe", lambda: _exception_is_standby(sandbox)),
        ]
