"""Trusted independent Ioniq V2 calibration oracle.

V2 deliberately has *no* pose fitting/finalization service. This oracle probes
its actual projection and pair-selection contracts, not retired V1 endpoints.
The hidden randomized world remains outside the consumer repository.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path


def _load_candidate(candidate: Path):
    sys.path.insert(0, str(candidate / "src"))
    from ioniq.pipeline.calibration import projection, selection

    return projection, selection


def _reference_project(point, intrinsics, pose):
    """Independent SE(3) oracle, never calling candidate.project()."""
    x, y, z = point
    vx, vy, vz = -y, -z, x
    cr, sr = math.cos(pose.roll_rad), math.sin(pose.roll_rad)
    cp, sp = math.cos(pose.pitch_rad), math.sin(pose.pitch_rad)
    cy, sy = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    rx = cy * cp * vx + (cy * sp * sr - sy * cr) * vy + (cy * sp * cr + sy * sr) * vz
    ry = sy * cp * vx + (sy * sp * sr + cy * cr) * vy + (sy * sp * cr - cy * sr) * vz
    rz = -sp * vx + cp * sr * vy + cp * cr * vz
    rx += pose.tx_m
    ry += pose.ty_m
    rz += pose.tz_m
    if rz <= 1e-6:
        return None
    return (intrinsics.fx * rx / rz + intrinsics.cx, intrinsics.fy * ry / rz + intrinsics.cy)


def _projection_generalization(projection, rng: random.Random):
    intrinsics = projection.Intrinsics(1600, 1600, 964, 604)
    for _ in range(100):
        pose = projection.Extrinsics(
            tx_m=rng.uniform(-0.3, 0.3),
            ty_m=rng.uniform(-0.3, 0.3),
            tz_m=rng.uniform(-0.3, 0.3),
            roll_rad=rng.uniform(-0.07, 0.07),
            pitch_rad=rng.uniform(-0.07, 0.07),
            yaw_rad=rng.uniform(-0.07, 0.07),
        )
        point = (
            rng.uniform(5, 90),
            rng.uniform(-3.0, 3.0),
            rng.uniform(-1.0, 1.0),
        )
        expected = _reference_project(point, intrinsics, pose)
        actual = projection.project(point, intrinsics, pose)
        if actual is None or expected is None:
            raise AssertionError("valid forward projection unexpectedly missing")
        if max(abs(a - b) for a, b in zip(actual, expected, strict=True)) > 1e-9:
            raise AssertionError("projection differs from independent SE(3) oracle")
        # Mutation of the caller's data must never occur.
        if pose.values() != (
            pose.tx_m, pose.ty_m, pose.tz_m, pose.roll_rad, pose.pitch_rad, pose.yaw_rad
        ):
            raise AssertionError("calibration values contract drifted")
    if projection.project((-1, 0, 0), intrinsics, projection.Extrinsics()) is not None:
        raise AssertionError("behind-camera target was projected")


def _target_selection_fail_closed(selection, rng: random.Random):
    # Unique targets in the trusted radar window are eligible, ambiguity is not.
    for _ in range(80):
        x = rng.uniform(12, 35)
        y = rng.uniform(-1.5, 1.5)
        target = {"x_m": x, "y_m": y}
        chosen, reason = selection.target_decision([target])
        if reason != "eligible" or chosen is None or chosen["target_index"] != 0:
            raise AssertionError("unique radar target not retained")
        if chosen["radar_xyz_m"] != [x, y, 0.0]:
            raise AssertionError("radar coordinates changed")
        ambiguous = [{"x_m": x, "y_m": y}, {"x_m": x + rng.uniform(-1, 1), "y_m": 0}]
        if selection.target_decision(ambiguous)[0] is not None:
            raise AssertionError("ambiguous radar candidates promoted")
        for rejected in (
            {"x_m": -2, "y_m": y},
            {"x_m": 100, "y_m": y},
            {"x_m": x, "y_m": 5},
            {"x_m": math.inf, "y_m": y},
        ):
            if selection.target_decision([rejected])[0] is not None:
                raise AssertionError("out-of-domain radar target promoted")


def _vision_selection_fail_closed(selection, rng: random.Random):
    for _ in range(80):
        y1 = rng.uniform(100.0, 400.0)
        box = [300, y1, 600, y1 + 150]
        candidate = {"cls": "car", "conf": 0.95, "box": box}
        eligible, reason = selection.detection_decision([candidate], 1208)
        if reason != "eligible" or eligible is None or eligible["box"] != box:
            raise AssertionError("single qualifying vehicle was discarded")
        for invalid in (
            {"cls": "person", "conf": 0.99, "box": box},
            {"cls": "car", "conf": 0.01, "box": box},
            {"cls": "car", "conf": 0.99, "box": [300, y1, 301, y1 + 1]},
            {"cls": "car", "conf": math.nan, "box": box},
        ):
            if selection.detection_decision([invalid], 1208)[0] is not None:
                raise AssertionError("ineligible detection promoted")
        if selection.detection_decision([candidate, candidate], 1208)[0] is not None:
            raise AssertionError("ambiguous camera candidates promoted")
        if selection.select_pseudo_label(
            [{"x_m": 15, "y_m": 0}], [candidate, candidate], image_height_px=1208
        ) is not None:
            raise AssertionError("ambiguous observations produced a pseudo-label")


def _check(name, callback):
    try:
        callback()
    except AssertionError as exc:
        return {"name": name, "status": "fail", "detail": str(exc)}
    return {"name": name, "status": "pass"}


def evaluate(candidate: Path, seed: int) -> list[dict[str, str]]:
    if not (candidate / "src/ioniq/pipeline/calibration/projection.py").is_file():
        raise RuntimeError("V2 calibration sources missing")
    projection, selection = _load_candidate(candidate)
    rng = random.Random(seed)
    return [
        _check("independent-v2-se3-projection", lambda: _projection_generalization(projection, rng)),
        _check("v2-radar-selection-fail-closed", lambda: _target_selection_fail_closed(selection, rng)),
        _check("v2-yolo-selection-fail-closed", lambda: _vision_selection_fail_closed(selection, rng)),
    ]
