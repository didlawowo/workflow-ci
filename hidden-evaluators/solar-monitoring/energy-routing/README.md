# Solar Monitoring energy-routing evaluator

Executable hidden evaluator for `didlawowo/solar-monitoring#256`.

It evaluates the candidate's real `core.optimizer.optimize_charging()` against
randomized surplus/deficit, safety-boundary, Tempo-seasonal and fail-safe scenarios.
Provider, configuration and logging dependencies are stubbed centrally so no live
MQTT, Home Assistant, Zendure, Enphase or Tempo network access is required.

The evaluator is registered in `hidden-evaluators/registry.json` and is intended to
be consumed only through the reusable trusted hidden-evidence workflow.
