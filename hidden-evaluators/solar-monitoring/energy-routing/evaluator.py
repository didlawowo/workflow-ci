from __future__ import annotations

import asyncio
import datetime as real_datetime
import importlib
import math
import random
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

MAX_CHARGE_W = 1600
MAX_DISCHARGE_W = 1200
MIN_POWER_W = 50
BATTERY_MIN_LEVEL = 10
SUMMER_SOLAR_STOP_SOC = 96
GRID_CHARGE_STOP_SOC = 99


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _load_optimizer(candidate: Path):
    sys.path.insert(0, str(candidate / "src"))

    loguru = types.ModuleType("loguru")
    loguru.logger = _Logger()
    sys.modules["loguru"] = loguru

    config = types.ModuleType("core.config")
    config.settings = SimpleNamespace(battery_min_level=BATTERY_MIN_LEVEL)
    sys.modules["core.config"] = config

    providers = types.ModuleType("providers")
    providers.__path__ = [str(candidate / "src" / "providers")]
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

    return importlib.import_module("core.optimizer")


class _FrozenDateTime(real_datetime.datetime):
    current = real_datetime.datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Europe/Paris"))

    @classmethod
    def now(cls, tz=None):
        value = cls.current
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


async def _decision(optimizer, when: real_datetime.datetime, **kwargs):
    old = optimizer.datetime.datetime
    _FrozenDateTime.current = when
    optimizer.datetime.datetime = _FrozenDateTime
    try:
        return await optimizer.optimize_charging(**kwargs)
    finally:
        optimizer.datetime.datetime = old


def _assert_safe(decision) -> None:
    assert isinstance(decision.should_charge, bool)
    assert isinstance(decision.power, int)
    assert 0 <= decision.power <= (MAX_CHARGE_W if decision.should_charge else MAX_DISCHARGE_W)
    assert isinstance(decision.reason, str) and decision.reason


async def _routing_invariants(optimizer, rng: random.Random) -> None:
    summer_peak = real_datetime.datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Europe/Paris"))

    for _ in range(40):
        consumption = rng.randint(0, 700)
        surplus = rng.randint(MIN_POWER_W + 1, 3500)
        production = consumption + surplus
        battery = rng.randint(BATTERY_MIN_LEVEL + 1, SUMMER_SOLAR_STOP_SOC - 1)
        result = await _decision(
            optimizer, summer_peak, is_peak=True, today_color="bleu",
            battery_level=battery, production=production, consumption=consumption,
            production_limit=rng.randint(0, min(200, production - 1)), ev_is_charging=False,
        )
        _assert_safe(result)
        assert result.should_charge is True
        assert result.power == min(MAX_CHARGE_W, surplus)

    for _ in range(40):
        production = rng.randint(0, 180)
        deficit = rng.randint(MIN_POWER_W + 1, 3000)
        consumption = production + deficit
        battery = rng.randint(BATTERY_MIN_LEVEL + 1, 95)
        result = await _decision(
            optimizer, summer_peak, is_peak=True, today_color="bleu",
            battery_level=battery, production=production, consumption=consumption,
            production_limit=max(200, production), ev_is_charging=False,
        )
        _assert_safe(result)
        assert result.should_charge is False
        assert result.power == min(MAX_DISCHARGE_W, deficit)


async def _boundary_and_fail_safe(optimizer, rng: random.Random) -> None:
    summer_peak = real_datetime.datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Europe/Paris"))

    # Solar stop boundary: a real surplus is released once the summer SOC cap is reached.
    result = await _decision(
        optimizer, summer_peak, is_peak=True, today_color="bleu",
        battery_level=SUMMER_SOLAR_STOP_SOC, production=1200, consumption=200,
        production_limit=100, ev_is_charging=False,
    )
    _assert_safe(result)
    assert result.should_charge is False and result.power == 0

    # Battery floor: peak deficit cannot discharge at or below the configured reserve.
    for battery in (0, BATTERY_MIN_LEVEL):
        result = await _decision(
            optimizer, summer_peak, is_peak=True, today_color="bleu",
            battery_level=battery, production=0, consumption=rng.randint(200, 1000),
            production_limit=50, ev_is_charging=False,
        )
        _assert_safe(result)
        assert result.should_charge is False and result.power == 0

    # EV fast charge protects the network from simultaneous battery discharge.
    result = await _decision(
        optimizer, summer_peak, is_peak=True, today_color="bleu",
        battery_level=70, production=0, consumption=900, production_limit=50,
        ev_is_charging=True,
    )
    _assert_safe(result)
    assert result.should_charge is False and result.power == 0

    # Marginal deficit below the BMS application threshold stays in standby.
    result = await _decision(
        optimizer, summer_peak, is_peak=True, today_color="bleu",
        battery_level=70, production=0, consumption=MIN_POWER_W - 1, production_limit=50,
        ev_is_charging=False,
    )
    _assert_safe(result)
    assert result.power == 0

    # Malformed negative sensor values are clamped and cannot create an aggressive order.
    result = await _decision(
        optimizer, summer_peak, is_peak=True, today_color=None,
        battery_level=-500, production=-99999, consumption=-99999, production_limit=-10,
        ev_is_charging=False,
    )
    _assert_safe(result)
    assert result.power == 0

    # Extreme PV is still capped by the software charge ceiling.
    result = await _decision(
        optimizer, summer_peak, is_peak=True, today_color="bleu",
        battery_level=50, production=10**9, consumption=0, production_limit=50,
        ev_is_charging=False,
    )
    _assert_safe(result)
    assert result.should_charge is True and result.power == MAX_CHARGE_W


async def _temporal_invariants(optimizer, rng: random.Random) -> None:
    summer_night = real_datetime.datetime(2026, 7, 15, 4, 0, tzinfo=ZoneInfo("Europe/Paris"))
    winter_red_peak = real_datetime.datetime(2026, 1, 15, 18, 0, tzinfo=ZoneInfo("Europe/Paris"))

    # Sunny summer morning keeps headroom for PV at/above the documented reserve.
    result = await _decision(
        optimizer, summer_night, is_peak=False, today_color="bleu",
        battery_level=50, production=0, consumption=300, production_limit=50,
        ev_is_charging=False, tomorrow_color="bleu", yesterday_color="bleu",
        tomorrow_weather="ensoleillé",
    )
    _assert_safe(result)
    assert result.should_charge is False and result.power == 0

    # Grid charging always stops at the central 99% cap.
    result = await _decision(
        optimizer, summer_night, is_peak=False, today_color="bleu",
        battery_level=GRID_CHARGE_STOP_SOC, production=0, consumption=300, production_limit=50,
        ev_is_charging=False, tomorrow_color="bleu", yesterday_color="bleu",
        tomorrow_weather=None,
    )
    _assert_safe(result)
    assert result.should_charge is False and result.power == 0

    # Red winter peak uses the battery for a meaningful deficit but remains capped.
    consumption = rng.randint(300, 2600)
    result = await _decision(
        optimizer, winter_red_peak, is_peak=True, today_color="rouge",
        battery_level=80, production=0, consumption=consumption, production_limit=50,
        ev_is_charging=False, tomorrow_color="bleu", yesterday_color="rouge",
    )
    _assert_safe(result)
    assert result.should_charge is False
    assert result.power == min(MAX_DISCHARGE_W, consumption)


async def _exception_is_standby(optimizer) -> None:
    class ExplodingDateTime(real_datetime.datetime):
        @classmethod
        def now(cls, _tz=None):
            raise RuntimeError("hidden clock failure")

    old = optimizer.datetime.datetime
    optimizer.datetime.datetime = ExplodingDateTime
    try:
        result = await optimizer.optimize_charging(
            is_peak=True, today_color="bleu", battery_level=50,
            production=500, consumption=200, production_limit=50,
        )
    finally:
        optimizer.datetime.datetime = old
    _assert_safe(result)
    assert result.should_charge is False and result.power == 0


def _check(name: str, coroutine_factory) -> dict[str, str]:
    try:
        asyncio.run(coroutine_factory())
    except AssertionError as exc:
        return {"name": name, "status": "fail", "detail": str(exc)}
    return {"name": name, "status": "pass"}


def evaluate(candidate: Path, seed: int) -> list[dict[str, str]]:
    if not (candidate / "src" / "core" / "optimizer.py").is_file():
        raise RuntimeError("candidate does not look like solar-monitoring")
    optimizer = _load_optimizer(candidate)
    rng = random.Random(seed)
    return [
        _check("surplus-deficit-routing", lambda: _routing_invariants(optimizer, rng)),
        _check("safety-boundaries", lambda: _boundary_and_fail_safe(optimizer, rng)),
        _check("tempo-seasonal-boundaries", lambda: _temporal_invariants(optimizer, rng)),
        _check("exception-fails-safe", lambda: _exception_is_standby(optimizer)),
    ]
