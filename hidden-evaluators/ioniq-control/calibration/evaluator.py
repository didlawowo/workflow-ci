from __future__ import annotations

import copy
import math
import random
import sys
from pathlib import Path
from statistics import median


TRUSTED_AUTO_METHOD = "automatic_unambiguous_radar_yolo_bootstrap"
TRUSTED_AUTO_VALIDATION_MODE = "operational_independent_routes"
TRUSTED_AUTO_RUNTIME_POLICY = {
    "min_frames_with_targets": 40,
    "min_frame_match_rate": 0.80,
    "min_matches": 60,
    "min_inside_box_rate": 0.75,
    "max_center_error_median_px": 40.0,
}


def _load_candidate(candidate: Path):
    sys.path.insert(0, str(candidate))
    sys.path.insert(0, str(candidate / "src"))
    from scripts import auto_geometric_finalize as finalize
    from src.dataset import calibration_readiness as readiness
    from src.radar import calibration
    return calibration, readiness, finalize


def _signed(rng: random.Random, low: float, high: float) -> float:
    return rng.choice((-1.0, 1.0)) * rng.uniform(low, high)


def _reference_project(point, intrinsics, pose):
    """Independent SE(3) oracle; never calls candidate.project()."""
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
    return (
        intrinsics.fx * rx / rz + intrinsics.cx,
        intrinsics.fy * ry / rz + intrinsics.cy,
    )


def _reference_errors(samples, intrinsics, pose):
    errors = []
    for sample in samples:
        projected = _reference_project(sample.radar_xyz_m, intrinsics, pose)
        if projected is None:
            errors.append(1_000_000.0)
            continue
        errors.append(
            math.hypot(
                projected[0] - sample.image_uv_px[0],
                projected[1] - sample.image_uv_px[1],
            )
        )
    return sorted(errors)


def _p95(values):
    return values[max(0, math.ceil(0.95 * len(values)) - 1)]


def _synthetic_generalization(calibration, rng: random.Random) -> None:
    intrinsics = calibration.Intrinsics(
        fx=rng.uniform(1350.0, 1850.0),
        fy=rng.uniform(1325.0, 1825.0),
        cx=rng.uniform(930.0, 990.0),
        cy=rng.uniform(515.0, 565.0),
    )

    # Avoid a weak random world where the null pose is accidentally close to truth.
    for _ in range(32):
        truth = calibration.Extrinsics(
            tx_m=_signed(rng, 0.08, 0.22),
            ty_m=_signed(rng, 0.06, 0.20),
            tz_m=_signed(rng, 0.05, 0.18),
            roll_rad=_signed(rng, 0.008, 0.028),
            pitch_rad=_signed(rng, 0.010, 0.032),
            yaw_rad=_signed(rng, 0.014, 0.040),
        )
        probe = [
            (rng.uniform(12.0, 52.0), rng.uniform(-2.2, 2.2), rng.uniform(-0.45, 0.45))
            for _ in range(16)
        ]
        probe_errors = []
        for point in probe:
            uv = _reference_project(point, intrinsics, truth)
            null_uv = _reference_project(point, intrinsics, calibration.Extrinsics())
            if uv is not None and null_uv is not None:
                probe_errors.append(math.hypot(uv[0] - null_uv[0], uv[1] - null_uv[1]))
        if probe_errors and median(probe_errors) >= 12.0:
            break
    else:
        raise AssertionError("could not construct a discriminating synthetic geometry")

    route_token = f"r{rng.getrandbits(48):012x}"
    train_count = rng.randint(44, 60)
    holdout_count = rng.randint(20, 30)

    def rows(count: int, suffix: str, sigma: float):
        result = []
        for index in range(count):
            point = (
                rng.uniform(12.0, 52.0),
                rng.uniform(-2.2, 2.2),
                rng.uniform(-0.45, 0.45),
            )
            projected = _reference_project(point, intrinsics, truth)
            if projected is None:
                raise AssertionError("trusted reference point projected behind camera")
            result.append(
                calibration.Correspondence(
                    observation_id=f"{rng.getrandbits(64):016x}",
                    session_id=f"{route_token}-{suffix}",
                    route_id=f"{route_token}-{suffix}",
                    radar_xyz_m=point,
                    image_uv_px=(
                        projected[0] + rng.gauss(0.0, sigma),
                        projected[1] + rng.gauss(0.0, sigma),
                    ),
                )
            )
        return result

    train = rows(train_count, "a", 0.35)
    holdout = rows(holdout_count, "b", 0.15)

    # Robustness challenge: corrupt a small unknown subset of training labels only.
    for index in rng.sample(range(len(train)), max(2, len(train) // 13)):
        row = train[index]
        train[index] = calibration.Correspondence(
            observation_id=row.observation_id,
            session_id=row.session_id,
            route_id=row.route_id,
            radar_xyz_m=row.radar_xyz_m,
            image_uv_px=(
                row.image_uv_px[0] + _signed(rng, 35.0, 90.0),
                row.image_uv_px[1] + _signed(rng, 25.0, 70.0),
            ),
        )

    baseline = _reference_errors(holdout, intrinsics, calibration.Extrinsics())
    fitted = calibration.fit_extrinsics(train, intrinsics)
    after = _reference_errors(holdout, intrinsics, fitted)

    baseline_median = median(baseline)
    fitted_median = median(after)
    baseline_p95 = _p95(baseline)
    fitted_p95 = _p95(after)
    if fitted_median > min(6.0, baseline_median * 0.45):
        raise AssertionError(
            "fit does not generalize on independent holdout "
            f"(median ratio={fitted_median / baseline_median:.3f})"
        )
    if fitted_p95 > min(12.0, baseline_p95 * 0.55):
        raise AssertionError(
            "fit p95 does not generalize on independent holdout "
            f"(p95 ratio={fitted_p95 / baseline_p95:.3f})"
        )


def _runtime_metrics(rng: random.Random) -> dict:
    frames_with_targets = rng.randint(80, 140)
    frame_match_rate = rng.uniform(0.86, 0.96)
    return {
        "frames_with_targets": frames_with_targets,
        "frames_with_matches": int(frames_with_targets * frame_match_rate),
        "frame_match_rate": frame_match_rate,
        "matches": rng.randint(90, 180),
        "inside_box_rate": rng.uniform(0.84, 0.97),
        "center_error_median_px": rng.uniform(12.0, 25.0),
        "negative_controls": {
            "null_pose": {
                "frame_match_rate": rng.uniform(0.05, 0.20),
                "inside_box_rate": rng.uniform(0.05, 0.25),
            },
            "shuffled_frames": {
                "frame_match_rate": rng.uniform(0.04, 0.18),
                "inside_box_rate": rng.uniform(0.05, 0.22),
            },
            "margin_ok": True,
        },
    }


def _valid_auto_report(rng: random.Random, *, pixel_status: str = "accepted") -> dict:
    token = f"{rng.getrandbits(48):012x}"
    train_a, train_b = f"train-{token}-a", f"train-{token}-b"
    holdout_route = f"holdout-{token}"
    target_route = f"target-{token}"
    return {
        "schema_version": 1,
        "status": pixel_status,
        "method": TRUSTED_AUTO_METHOD,
        "target_route_excluded_from_calibration": target_route,
        "intrinsics": {"fx": 1600.0, "fy": 1595.0, "cx": 960.0, "cy": 540.0},
        "extrinsics": {
            "tx_m": 0.11, "ty_m": -0.08, "tz_m": 0.07,
            "roll_rad": 0.011, "pitch_rad": -0.017, "yaw_rad": 0.023,
        },
        "split": {
            "train_routes": [train_a, train_b],
            "holdout_routes": [holdout_route],
            "train_observations": {"count": 24, "ids_sha256": "a" * 64},
            "holdout_observations": {"count": 12, "ids_sha256": "b" * 64},
        },
        "input_sha256": "c" * 64,
        "metrics": {
            "train": {"median_px": 3.0, "p95_px": 9.0},
            "holdout": {"median_px": 5.0, "p95_px": 15.0},
        },
        "thresholds": {"median_px": 8.0, "p95_px": 20.0},
        "validation": {
            "mode": TRUSTED_AUTO_VALIDATION_MODE,
            "thresholds": dict(TRUSTED_AUTO_RUNTIME_POLICY),
            "holdout": {"route": holdout_route, **_runtime_metrics(rng)},
            "target": {"segment": target_route + "--23", **_runtime_metrics(rng)},
        },
    }


def _policy_fail_closed(readiness, rng: random.Random) -> None:
    report = _valid_auto_report(rng)
    errors = readiness.accepted_report_errors(report)
    if errors:
        raise AssertionError(f"baseline accepted report rejected: {errors[0]}")

    attacks = []
    relaxed = copy.deepcopy(report)
    key = rng.choice(list(TRUSTED_AUTO_RUNTIME_POLICY))
    policy = float(TRUSTED_AUTO_RUNTIME_POLICY[key])
    relaxed["validation"]["thresholds"][key] = (
        policy * rng.uniform(0.50, 0.90)
        if key.startswith("min_")
        else policy * rng.uniform(1.10, 1.80)
    )
    attacks.append(("threshold-relaxation", relaxed))

    leaked = copy.deepcopy(report)
    leaked["split"]["train_routes"].append(leaked["target_route_excluded_from_calibration"])
    attacks.append(("target-train-leakage", leaked))

    overlap = copy.deepcopy(report)
    overlap["split"]["train_routes"].append(overlap["split"]["holdout_routes"][0])
    attacks.append(("train-holdout-overlap", overlap))

    forged = copy.deepcopy(report)
    fitted = forged["validation"]["holdout"]
    fitted["negative_controls"]["null_pose"] = {
        "frame_match_rate": fitted["frame_match_rate"],
        "inside_box_rate": fitted["inside_box_rate"],
    }
    fitted["negative_controls"]["margin_ok"] = True
    attacks.append(("forged-negative-control", forged))

    weak = copy.deepcopy(report)
    metric = rng.choice(list(TRUSTED_AUTO_RUNTIME_POLICY))
    boundary = float(TRUSTED_AUTO_RUNTIME_POLICY[metric])
    if metric == "min_frames_with_targets":
        weak["validation"]["target"]["frames_with_targets"] = int(boundary) - 1
    elif metric == "min_matches":
        weak["validation"]["target"]["matches"] = int(boundary) - 1
    elif metric.startswith("min_"):
        weak["validation"]["target"][metric.removeprefix("min_")] = boundary - 0.01
    else:
        weak["validation"]["target"]["center_error_median_px"] = boundary + 0.01
    attacks.append((f"runtime-boundary-{metric}", weak))

    non_finite = copy.deepcopy(report)
    non_finite["extrinsics"]["yaw_rad"] = math.inf
    attacks.append(("non-finite-extrinsics", non_finite))

    for name, candidate in rng.sample(attacks, len(attacks)):
        if not readiness.accepted_report_errors(candidate):
            raise AssertionError(f"fail-open policy accepted {name}")


def _rejected_pixel_gate_never_upgrades(finalize, rng: random.Random) -> None:
    report = _valid_auto_report(rng, pixel_status="rejected")
    holdout = copy.deepcopy(report["validation"]["holdout"])
    target = copy.deepcopy(report["validation"]["target"])
    result = finalize.finalize(report, holdout, target)
    if result.get("status") != "rejected":
        raise AssertionError("rejected pixel gate was upgraded by runtime evidence")


def _assert_trusted_policy_contract(readiness) -> None:
    observed_policy = dict(readiness.AUTO_RUNTIME_POLICY)
    if readiness.AUTO_METHOD != TRUSTED_AUTO_METHOD:
        raise AssertionError(
            f"candidate AUTO_METHOD drifted: {readiness.AUTO_METHOD!r} != {TRUSTED_AUTO_METHOD!r}"
        )
    if readiness.AUTO_VALIDATION_MODE != TRUSTED_AUTO_VALIDATION_MODE:
        raise AssertionError(
            "candidate AUTO_VALIDATION_MODE drifted: "
            f"{readiness.AUTO_VALIDATION_MODE!r} != {TRUSTED_AUTO_VALIDATION_MODE!r}"
        )
    if observed_policy != TRUSTED_AUTO_RUNTIME_POLICY:
        raise AssertionError(
            "candidate AUTO_RUNTIME_POLICY drifted from trusted publication policy: "
            f"{observed_policy!r}"
        )


def _check(name: str, callback) -> dict[str, str]:
    try:
        callback()
    except AssertionError as exc:
        return {"name": name, "status": "fail", "detail": str(exc)}
    return {"name": name, "status": "pass"}


def evaluate(candidate: Path, seed: int) -> list[dict[str, str]]:
    if not (candidate / "src").is_dir():
        raise RuntimeError("candidate does not look like ioniq-control")
    calibration, readiness, finalize = _load_candidate(candidate)
    rng = random.Random(seed)

    def policy_contract():
        _assert_trusted_policy_contract(readiness)

    def geometry():
        for _ in range(3):
            _synthetic_generalization(calibration, rng)

    def policy():
        for _ in range(4):
            _policy_fail_closed(readiness, rng)

    def downgrade():
        for _ in range(2):
            _rejected_pixel_gate_never_upgrades(finalize, rng)

    return [
        _check("trusted-publication-policy-contract", policy_contract),
        _check("independent-geometry-generalization", geometry),
        _check("fail-closed-publication-policy", policy),
        _check("rejected-gate-never-upgrades", downgrade),
    ]
