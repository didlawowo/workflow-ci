"""Exercise the trusted oracle against conforming and faulty boundary behavior."""

import asyncio
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "solar_soc_oracle",
    ROOT / "hidden-evaluators/solar-monitoring/energy-routing/evaluator.py",
)
assert SPEC and SPEC.loader
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


@dataclass
class Plan:
    should_charge: bool
    requested_power_w: int
    hyper_power_w: int
    solarflow_power_w: int

    @property
    def total_power_w(self):
        return self.hyper_power_w + self.solarflow_power_w


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "cutoff_100",
        "cutoff_98",
        "charge_at_99",
        "reserve_14",
        "reserve_16",
        "wrong_sign",
    ],
)
def test_oracle_accepts_only_independent_99_15_boundary_contract(monkeypatch, fault):
    async def decision(_optimizer, _when, **kwargs):
        charging = not kwargs["is_peak"] or kwargs["production"] > kwargs["consumption"]
        return SimpleNamespace(
            should_charge=charging if fault != "wrong_sign" else not charging,
            power=1000 if charging else 500,
            reason="boundary fixture",
            charge_stop_soc={"cutoff_100": 100, "cutoff_98": 98}.get(fault, 99),
        )

    def coordinate(objective, *, hyper, solarflow, minimum_power_w):
        assert minimum_power_w == 50
        powers = []
        for unit in (hyper, solarflow):
            if objective.should_charge:
                cutoff = 100 if fault == "charge_at_99" else unit.charge_stop_soc
                available = unit.soc < cutoff
            else:
                reserve = unit.min_soc
                if unit.device_id == "solarflow":
                    reserve = {"reserve_14": 14, "reserve_16": 16}.get(fault, reserve)
                available = unit.soc > reserve
            powers.append(min(objective.power, unit.max_charge_w) if available else 0)
        return Plan(objective.should_charge, objective.power, *powers)

    monkeypatch.setattr(oracle, "_decision", decision)
    coordinator = SimpleNamespace(
        StorageUnit=SimpleNamespace, coordinate_storage=coordinate
    )
    if fault is None:
        asyncio.run(oracle._soc_boundary_invariants(None, coordinator))
    else:
        with pytest.raises(AssertionError):
            asyncio.run(oracle._soc_boundary_invariants(None, coordinator))
