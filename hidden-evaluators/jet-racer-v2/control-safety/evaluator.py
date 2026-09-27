from __future__ import annotations

import json
import math
import random
import sys
import tempfile
from pathlib import Path


def _load(candidate: Path):
    sys.path.insert(0, str(candidate / "src"))
    from jetracer.camera.model import CameraConfig
    from jetracer.collection.session import SessionWriter
    from jetracer.input.dualsense import DualSenseState
    from jetracer.safety import Watchdog
    from jetracer.teleop import TeleopConfig, TeleopSession, command_from_state
    return CameraConfig, SessionWriter, DualSenseState, Watchdog, TeleopConfig, TeleopSession, command_from_state


def _state(DualSenseState, rng: random.Random, seq: int, buttons=frozenset()):
    return DualSenseState(
        lx=rng.randint(0, 255),
        ly=rng.randint(0, 255),
        rx=rng.randint(0, 255),
        ry=rng.randint(0, 255),
        l2=rng.randint(0, 255),
        r2=rng.randint(0, 255),
        seq=seq,
        buttons=frozenset(buttons),
        dpad=rng.randint(0, 15),
    )


def _teleop_invariants(DualSenseState, TeleopConfig, TeleopSession, command_from_state, rng):
    for _ in range(120):
        limit = rng.uniform(0.15, 0.8)
        start = rng.uniform(0.0, limit)
        config = TeleopConfig(
            steer_deadzone=rng.uniform(0.0, 0.25),
            throttle_deadzone=rng.uniform(0.0, 0.20),
            start=start,
            limit=limit,
            invert_throttle=bool(rng.getrandbits(1)),
            invert_steering=bool(rng.getrandbits(1)),
            stale_after_s=rng.uniform(0.05, 0.24),
        )
        state = _state(DualSenseState, rng, rng.randint(1, 2**31 - 1))
        steering, throttle = command_from_state(state, config)
        assert math.isfinite(steering) and -1.0 <= steering <= 1.0
        assert math.isfinite(throttle) and abs(throttle) <= config.limit + 1e-12

    now = [100.0]
    timeout = rng.uniform(0.08, 0.22)
    config = TeleopConfig(start=0.2, limit=0.7, stale_after_s=timeout)
    session = TeleopSession(config, clock=lambda: now[0])
    base = rng.randint(1000, 9000)

    # Options must first be observed released, then a rising edge arms.
    session.update(_state(DualSenseState, rng, base, frozenset()))
    armed = session.update(_state(DualSenseState, rng, base + 1, {"options"}))
    assert armed.armed is True

    # A stale input always removes propulsion and disarms.
    now[0] += timeout + 0.001
    stale = session.current()
    assert stale.armed is False and stale.throttle == 0.0

    # Keeping options pressed after stale cannot immediately rearm.
    still_pressed = session.update(_state(DualSenseState, rng, base + 2, {"options"}), now=now[0])
    assert still_pressed.armed is False and still_pressed.throttle == 0.0

    session.update(_state(DualSenseState, rng, base + 3, frozenset()), now=now[0] + 0.001)
    rearmed = session.update(_state(DualSenseState, rng, base + 4, {"options"}), now=now[0] + 0.002)
    assert rearmed.armed is True
    stopped = session.update(_state(DualSenseState, rng, base + 5, {"cross"}), now=now[0] + 0.003)
    assert stopped.armed is False and stopped.throttle == 0.0


def _watchdog_invariants(Watchdog, rng):
    now = [rng.uniform(10.0, 1000.0)]
    timeout = rng.uniform(0.05, 1.0)
    calls: list[float] = []
    watchdog = Watchdog(timeout, on_timeout=lambda: calls.append(now[0]), clock=lambda: now[0])

    assert watchdog.check() is False
    watchdog.feed()
    now[0] += timeout
    assert watchdog.check() is False, "timeout is strict, not >="
    now[0] += max(1e-6, timeout / 1000)
    assert watchdog.check() is True
    assert len(calls) == 1
    assert watchdog.check() is False
    assert len(calls) == 1

    watchdog.feed()
    now[0] += timeout + max(1e-6, timeout / 1000)
    assert watchdog.check() is True
    assert len(calls) == 2, "feed must rearm exactly once"


def _session_crash_invariants(CameraConfig, SessionWriter, TeleopConfig, rng):
    session_id = f"hidden-{rng.getrandbits(64):016x}"
    with tempfile.TemporaryDirectory(prefix="jetracer-hidden-") as directory:
        root = Path(directory)
        try:
            with SessionWriter(
                root,
                camera=CameraConfig(),
                teleop=TeleopConfig(),
                session_id=session_id,
                min_free_bytes=0,
            ) as writer:
                writer.write_event("hidden-start")
                raise RuntimeError("hidden synthetic interruption")
        except RuntimeError as exc:
            assert "hidden synthetic interruption" in str(exc)

        manifest = json.loads((root / session_id / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "error"
        assert manifest["stats"]["events"] == 1
        assert "hidden synthetic interruption" in manifest.get("error", "")
        telemetry = (root / session_id / "telemetry.jsonl").read_text(encoding="utf-8")
        rows = [json.loads(line) for line in telemetry.splitlines() if line.strip()]
        assert len(rows) == 1 and rows[0]["name"] == "hidden-start"

        # close() after context cleanup must remain idempotent.
        writer.close(status="completed")
        manifest_after = json.loads((root / session_id / "manifest.json").read_text(encoding="utf-8"))
        assert manifest_after["status"] == "error"


def _check(name: str, callback) -> dict[str, str]:
    try:
        callback()
    except AssertionError as exc:
        return {"name": name, "status": "fail", "detail": str(exc)}
    return {"name": name, "status": "pass"}


def evaluate(candidate: Path, seed: int) -> list[dict[str, str]]:
    if not (candidate / "src" / "jetracer").is_dir():
        raise RuntimeError("candidate does not look like jet-racer-v2")
    CameraConfig, SessionWriter, DualSenseState, Watchdog, TeleopConfig, TeleopSession, command_from_state = _load(candidate)
    rng = random.Random(seed)
    return [
        _check(
            "teleop-bounds-and-stale-failsafe",
            lambda: _teleop_invariants(DualSenseState, TeleopConfig, TeleopSession, command_from_state, rng),
        ),
        _check("watchdog-exactly-once-rearm", lambda: _watchdog_invariants(Watchdog, rng)),
        _check(
            "collection-crash-manifest",
            lambda: _session_crash_invariants(CameraConfig, SessionWriter, TeleopConfig, rng),
        ),
    ]
