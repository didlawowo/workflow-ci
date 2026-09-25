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


def test_hidden_workflow_checks_out_trusted_oracle_and_exact_candidate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_call:" in workflow
    assert "repository: ${{ job.workflow_repository }}" in workflow
    assert "ref: ${{ job.workflow_sha }}" in workflow
    assert "path: .workflow-ci-hidden" in workflow
    assert "repository: ${{ github.repository }}" in workflow
    assert "ref: refs/pull/${{ github.event.pull_request.number }}/head" in workflow
    assert "path: candidate" in workflow
    assert workflow.count("persist-credentials: false") >= 2
    assert 'test "$(git -C .workflow-ci-hidden rev-parse HEAD)" = "${{ job.workflow_sha }}"' in workflow
    assert 'test "$(git -C candidate rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"' in workflow


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


def test_hidden_public_report_uses_opaque_seed_id_and_separate_replay_capsule() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    report = (ROOT / "hidden-evaluators" / "common" / "report.py").read_text(
        encoding="utf-8"
    )
    assert '"seed_id": seed_id(args.evaluator, seed)' in runner
    assert '"seed": seed' not in runner.split("report = {", 1)[1].split("}", 1)[0]
    assert '"seed": seed' in report
    assert "write_replay_capsule" in runner


def test_jetracer_safety_evaluator_is_registered_for_control_paths() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    spec = registry["evaluators"]["jet-racer-v2/control-safety"]
    assert spec["repository"] == "didlawowo/jet-racer-v2"
    assert spec["entrypoint"] == "hidden-evaluators/jet-racer-v2/control-safety/evaluator.py"
    assert "src/jetracer/safety.py" in spec["paths"]
    assert "src/jetracer/teleop.py" in spec["paths"]
    assert "src/jetracer/collection/**" in spec["paths"]


def test_jetracer_safety_evaluator_is_hardware_free_and_randomized() -> None:
    source = (
        ROOT / "hidden-evaluators" / "jet-racer-v2" / "control-safety" / "evaluator.py"
    ).read_text(encoding="utf-8")
    assert "random.Random(seed)" in source
    assert "command_from_state" in source
    assert "TeleopSession" in source
    assert "Watchdog" in source
    assert "SessionWriter" in source
    assert "TemporaryDirectory" in source
    assert "/dev/" not in source
