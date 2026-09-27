# Jet Racer control/safety evaluator

Central hidden evaluator for `didlawowo/jet-racer-v2`.

It executes only pure candidate Python code and requires no Jetson, camera, motor,
Bluetooth controller or other physical hardware.

Covered invariants:

- randomized steering/throttle commands remain finite and within configured bounds;
- arming requires an Options rising edge;
- stale controller input disarms and forces throttle to zero;
- keeping Options pressed after a stale gap cannot immediately re-arm;
- Cross disarms;
- the watchdog fires strictly after its timeout, only once per lapse, and `feed()`
  rearms it;
- an interrupted collection session persists an error manifest and telemetry;
- closing an already-finalized collection session remains idempotent.

The evaluator is registered for the Jet Racer safety, teleop/input, hardware,
collection, inference, camera, training and native-camera paths.
