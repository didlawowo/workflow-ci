# Solar Monitoring energy-routing evaluator

Executable hidden evaluator for `didlawowo/solar-monitoring#256`.

It evaluates the candidate's real `core.optimizer.optimize_charging()` together
with `core.storage_coordinator.coordinate_storage()`. The optimizer is checked as
a site-level objective generator; per-device SOC reserves and power ceilings are
checked after allocation to Hyper and SolarFlow. Randomized surplus/deficit,
safety-boundary, Tempo-seasonal and fail-safe scenarios are exercised offline.
Provider, configuration and logging dependencies are stubbed centrally so no live
MQTT, Home Assistant, Zendure, Enphase or Tempo network access is required.

The evaluator is registered in `hidden-evaluators/registry.json` and is intended to
be consumed only through the reusable trusted hidden-evidence workflow.
