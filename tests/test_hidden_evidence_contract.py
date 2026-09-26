from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "hidden-evaluators" / "run.py"
REGISTRY = ROOT / "hidden-evaluators" / "registry.json"
WORKFLOW = ROOT / ".github" / "workflows" / "hidden-evidence.yml"


def test_hidden_registry_binds_evaluator_to_exact_consumer_repository() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert registry["schema_version"] == 1
    calibration = registry["evaluators"]["ioniq-control/calibration"]
    assert calibration["repository"] == "didlawowo/ioniq-control"
    assert calibration["entrypoint"] == (
        "hidden-evaluators/ioniq-control/calibration/evaluator.py"
    )
    assert "src/radar/calibration.py" in calibration["paths"]
    known = set(registry["evaluators"]) | set(registry["planned"])
    assert {
        "ioniq-control/calibration",
        "keryx/conversation-runtime",
        "jet-racer-v2/control-safety",
        "solar-monitoring/energy-routing",
    } <= known


def test_hidden_scope_is_central_and_path_sensitive(tmp_path: Path) -> None:
    changed = tmp_path / "changed.txt"
    changed.write_text(
        "docs/calibration-ui.md\nsrc/radar/calibration.py\nREADME.md\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "scope",
            "--evaluator",
            "ioniq-control/calibration",
            "--repository",
            "didlawowo/ioniq-control",
            "--changed-files",
            str(changed),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result == {"required": True, "matched": ["src/radar/calibration.py"]}


def test_hidden_scope_rejects_evaluator_substitution(tmp_path: Path) -> None:
    changed = tmp_path / "changed.txt"
    changed.write_text("src/radar/calibration.py\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "scope",
            "--evaluator",
            "ioniq-control/calibration",
            "--repository",
            "didlawowo/keryx",
            "--changed-files",
            str(changed),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "bound to didlawowo/ioniq-control" in completed.stderr


def test_hidden_workflow_checks_out_trusted_base_and_exact_candidate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_call:" in workflow
    assert "repository: ${{ job.workflow_repository }}" in workflow
    assert "ref: ${{ job.workflow_sha }}" in workflow
    assert "path: .workflow-ci-hidden" in workflow
    assert "repository: ${{ github.repository }}" in workflow
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "path: base" in workflow
    assert "ref: refs/pull/${{ github.event.pull_request.number }}/head" in workflow
    assert "path: candidate" in workflow
    assert workflow.count("persist-credentials: false") >= 3
    assert (
        'test "$(git -C .workflow-ci-hidden rev-parse HEAD)" = '
        '"$(git -C .workflow-ci-hidden rev-parse \'${{ job.workflow_sha }}^{commit}\')"'
        in workflow
    )
    assert 'test "$(git -C base rev-parse HEAD)" = "${{ github.event.pull_request.base.sha }}"' in workflow
    assert 'test "$(git -C candidate rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"' in workflow
    assert "git -C candidate cat-file -e" not in workflow


def test_hidden_scope_compares_tracked_base_and_candidate_trees() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert '["git", "-C", root, "ls-files", "-s", "-z"]' in workflow
    assert 'base = tracked("base")' in workflow
    assert 'candidate = tracked("candidate")' in workflow
    assert "if base.get(path) != candidate.get(path)" in workflow
    assert 'git -C candidate diff --name-only "$BASE_SHA...$HEAD_SHA"' not in workflow


def test_hidden_workflow_is_read_only_and_reporting_is_best_effort() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    permissions = workflow.split("    permissions:\n", 1)[1].split("    outputs:\n", 1)[0]
    assert "contents: read" in permissions
    assert "write" not in permissions
    assert "pull-requests:" not in permissions
    upload = workflow.split("      - name: Upload trusted replay capsule", 1)[1].split(
        "      - name: Enforce hidden verdict", 1
    )[0]
    assert "continue-on-error: true" in upload
    assert "actions/upload-artifact@v6" in upload
    enforce = workflow.split("      - name: Enforce hidden verdict", 1)[1]
    assert 'if [[ "$STATUS" != "pass" ]]' in enforce


def test_ioniq_hidden_oracle_uses_independent_reference_projection() -> None:
    source = (
        ROOT / "hidden-evaluators" / "ioniq-control" / "calibration" / "evaluator.py"
    ).read_text(encoding="utf-8")
    assert "def _reference_project" in source
    assert "def _reference_errors" in source
    assert "calibration.project(" not in source
    assert "calibration.metrics(" not in source
    assert "calibration.fit_extrinsics(train, intrinsics)" in source
    assert "_reference_errors(holdout, intrinsics, fitted)" in source
    assert 'TRUSTED_AUTO_METHOD = "automatic_unambiguous_radar_yolo_bootstrap"' in source
    assert 'TRUSTED_AUTO_VALIDATION_MODE = "operational_independent_routes"' in source
    assert '"min_frames_with_targets": 40' in source
    assert '"min_frame_match_rate": 0.80' in source
    assert '"min_matches": 60' in source
    assert '"min_inside_box_rate": 0.75' in source
    assert '"max_center_error_median_px": 40.0' in source
    assert "def _assert_trusted_policy_contract" in source
    report_builder = source.split("def _valid_auto_report", 1)[1].split(
        "def _policy_fail_closed", 1
    )[0]
    assert "readiness." not in report_builder
    assert "TRUSTED_AUTO_RUNTIME_POLICY" in report_builder


def test_hidden_public_report_uses_opaque_seed_id_and_separate_replay_capsule() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    report = (ROOT / "hidden-evaluators" / "common" / "report.py").read_text(
        encoding="utf-8"
    )
    assert '"seed_id": seed_id(args.evaluator, seed)' in runner
    assert '"seed": seed' not in runner.split("report = {", 1)[1].split("}", 1)[0]
    assert '"seed": seed' in report
    assert "write_replay_capsule" in runner


def test_solar_routing_evaluator_is_registered_for_energy_paths() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    spec = registry["evaluators"]["solar-monitoring/energy-routing"]
    assert spec["repository"] == "didlawowo/solar-monitoring"
    assert spec["entrypoint"] == (
        "hidden-evaluators/solar-monitoring/energy-routing/evaluator.py"
    )
    assert "src/core/**" in spec["paths"]
    assert "src/controllers/**" in spec["paths"]
    assert "src/zendure.py" in spec["paths"]


def test_solar_routing_evaluator_is_offline_and_owns_safety_limits() -> None:
    source = (
        ROOT
        / "hidden-evaluators"
        / "solar-monitoring"
        / "energy-routing"
        / "evaluator.py"
    ).read_text(encoding="utf-8")
    assert "MAX_CHARGE_W = 1600" in source
    assert "MAX_DISCHARGE_W = 1200" in source
    assert "BATTERY_MIN_LEVEL = 10" in source
    assert "random.Random(seed)" in source
    assert "optimizer.optimize_charging" in source
    assert "providers.tempo" in source
    assert "httpx" not in source
    assert "mqtt" not in source.lower()


def test_keryx_runtime_evaluator_is_registered_with_runtime_paths() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    spec = registry["evaluators"]["keryx/conversation-runtime"]
    assert spec["repository"] == "didlawowo/keryx"
    assert spec["entrypoint"] == (
        "hidden-evaluators/keryx/conversation-runtime/evaluator.py"
    )
    assert "pkg/handlers/**" in spec["paths"]
    assert "pkg/hermes/**" in spec["paths"]
    assert "web/src/hooks/**" in spec["paths"]


def test_keryx_runtime_evaluator_executes_ephemeral_go_tests() -> None:
    source = (
        ROOT
        / "hidden-evaluators"
        / "keryx"
        / "conversation-runtime"
        / "evaluator.py"
    ).read_text(encoding="utf-8")
    assert "workflow_ci_hidden_test.go" in source
    assert '"go",' in source and '"test",' in source
    assert "path.unlink(missing_ok=True)" in source
    assert "TestWorkflowCIHiddenTurnFeedReplay" in source
    assert "TestWorkflowCIHiddenApprovalChoicesFailClosed" in source
    assert "TestWorkflowCIHiddenReasoningClamp" in source
    assert "TestWorkflowCIHiddenRetryIsExactlyOnce" in source
    assert "workflowCITrustedHermes" in source
    assert "workflowCITrustedAPI" in source
    assert "workflowCITrustedCreateThread" in source
    assert "&fakeHermes" not in source
    assert "newTestAPI(" not in source
    assert "&fakeTranscriber" not in source
    assert "createThread(t," not in source
    assert "completedWith(" not in source


def test_hidden_required_skipped_check_cannot_aggregate_to_pass() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "def aggregate_required_checks" in source
    assert 'if "skipped" in statuses:' in source
    assert 'return "error"' in source
    assert 'if statuses == {"pass"}:' in source


def test_hidden_required_check_aggregation_matrix() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("hidden_runner", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cases = [
        (["pass", "pass"], "pass"),
        (["pass", "fail"], "fail"),
        (["pass", "error"], "error"),
        (["pass", "skipped"], "error"),
        (["skipped"], "error"),
    ]
    for statuses, expected in cases:
        checks = [
            {"name": f"check-{index}", "status": status}
            for index, status in enumerate(statuses)
        ]
        assert module.aggregate_required_checks(checks) == expected
