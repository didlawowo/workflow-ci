from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "trusted_mutation_sample.py"
SPEC = importlib.util.spec_from_file_location("trusted_mutation_sample", MODULE_PATH)
assert SPEC and SPEC.loader
sample = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sample)


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_select_samples_killed_mutants_with_min_one_and_maximum(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    ids = tmp_path / "ids.txt"
    state = tmp_path / "state.json"
    write(
        report,
        {
            "stats": {"killed": 20, "survived": 0, "total": 20},
            "mutants": {f"m{i}": "killed" for i in range(20)},
        },
    )

    count = sample.select(report, ids, state, percent=10, maximum=5)

    selected = [line for line in ids.read_text().splitlines() if line]
    assert count == 2
    assert len(selected) == 2
    assert set(selected) <= {f"m{i}" for i in range(20)}
    payload = json.loads(state.read_text())
    assert payload["sample_count"] == 2
    assert payload["sample_percent"] == 10
    assert payload["sample_id"] != "none"
    assert set(payload["selected"]) == set(selected)


def test_select_zero_target_report_is_not_applicable(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    ids = tmp_path / "ids.txt"
    state = tmp_path / "state.json"
    write(report, {"stats": {"killed": 0, "survived": 0, "total": 0}})

    assert sample.select(report, ids, state, percent=10, maximum=5) == 0
    payload = json.loads(state.read_text())
    assert payload["status"] == "not_applicable"
    assert payload["sample_id"] == "none"


def test_measured_report_without_per_mutant_map_fails_closed(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    write(report, {"stats": {"killed": 1, "survived": 0, "total": 1}})

    with pytest.raises(ValueError, match="no per-mutant map"):
        sample.select(
            report,
            tmp_path / "ids.txt",
            tmp_path / "state.json",
            percent=10,
            maximum=5,
        )


def test_replay_divergence_rejects_all_evidence(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    replay = tmp_path / "replay.json"
    write(report, {"stats": {"killed": 2, "survived": 0, "total": 2}})
    write(
        state,
        {
            "status": "selected",
            "sample_count": 1,
            "sample_percent": 10,
            "sample_id": "opaque",
            "selected": {"module.x_f__mutmut_1": "killed"},
        },
    )
    write(
        replay,
        {
            "mutants": {"module.x_f__mutmut_1": "survived"},
            "duration_ms": 123,
        },
    )

    with pytest.raises(ValueError, match="replay diverged"):
        sample.verify(report, state, replay)


def test_successful_replay_adds_public_attestation_without_seed(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    replay = tmp_path / "replay.json"
    write(report, {"stats": {"killed": 2, "survived": 0, "total": 2}})
    write(
        state,
        {
            "status": "selected",
            "sample_count": 1,
            "sample_percent": 10,
            "sample_id": "opaque-id",
            "selected": {"module.x_f__mutmut_1": "killed"},
        },
    )
    write(
        replay,
        {
            "mutants": {"module.x_f__mutmut_1": "killed"},
            "duration_ms": 456,
        },
    )

    sample.verify(report, state, replay)

    payload = json.loads(report.read_text())
    assert payload["attestation"] == {
        "status": "pass",
        "sample_count": 1,
        "sample_percent": 10,
        "sample_id": "opaque-id",
        "duration_ms": 456,
    }
    assert "seed" not in json.dumps(payload["attestation"]).lower()


def test_workflow_replays_after_validation_and_before_publication() -> None:
    workflow = (ROOT / ".github" / "workflows" / "mutation-policy.yml").read_text()

    validate = workflow.index("- name: Validate mutation evidence")
    select = workflow.index("- name: Select trusted random mutant sample")
    replay = workflow.index("- name: Replay trusted random mutant sample")
    verify = workflow.index("- name: Verify trusted mutation attestation")
    publish = workflow.index("- name: Upload scoped mutation evidence")

    assert validate < select < replay < verify < publish
    assert "secrets.SystemRandom" in MODULE_PATH.read_text()
    assert "MUTATION_REPLAY_IDS_FILE" in workflow


@pytest.mark.parametrize(
    "relative",
    [".ci/mutation.sh", ".ci/mutation-go.sh", ".ci/mutation-node.sh"],
)
def test_central_mutation_engines_support_trusted_replay(relative: str) -> None:
    script = (ROOT / relative).read_text(encoding="utf-8")
    assert "MUTATION_REPLAY_IDS_FILE" in script
    assert ".quality/mutation-replay.json" in script
