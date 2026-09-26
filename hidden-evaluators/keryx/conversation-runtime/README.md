# Keryx conversation runtime evaluator

Executable hidden evaluator for `didlawowo/keryx#469`.

It injects short-lived Go tests into the candidate checkout and executes the real
Keryx runtime package against randomized values derived from the trusted hidden seed.

The evaluator checks turn-feed replay/idempotency, approval fail-closed behavior,
reasoning-effort normalization and exactly-once user-message semantics on retry.
The injected test source is removed after execution and does not become part of Keryx.

The evaluator is registered in `hidden-evaluators/registry.json` and is intended to
be consumed only through the reusable trusted hidden-evidence workflow.
