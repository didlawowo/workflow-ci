"""Contract for the plan-derived campaign shard timeout."""

import importlib
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ci"))
shard_budget = importlib.import_module("mutation_shard_budget")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "mutation-campaign.yml"


def test_shard_timeout_grows_with_campaign_size_and_shards():
    # Below the pacing threshold the fixed minimum applies.
    assert shard_budget.shard_timeout_minutes(0, 1, 30, 15) == 45
    assert shard_budget.shard_timeout_minutes(100, 1, 30, 15) == 65
    # The observed failure mode: 455 mutants in one shard at ~14 s per mutant
    # needs far more than the former fixed 45 minutes.
    assert shard_budget.shard_timeout_minutes(455, 1, 30, 15) == 243
    # Sharding divides the execution share, never below the minimum.
    assert shard_budget.shard_timeout_minutes(455, 8, 30, 15) == 45
    assert shard_budget.shard_timeout_minutes(1200, 1, 30, 15) == (
        shard_budget.MAXIMUM_TIMEOUT_MINUTES
    )
    # The ceiling keeps a runaway configuration bounded.
    assert (
        shard_budget.shard_timeout_minutes(100_000, 1, 30, 15)
        == shard_budget.MAXIMUM_TIMEOUT_MINUTES
    )


def test_shard_timeout_rejects_impossible_configuration():
    import pytest

    with pytest.raises(ValueError):
        shard_budget.shard_timeout_minutes(10, 1, 0, 15)
    with pytest.raises(ValueError):
        shard_budget.shard_timeout_minutes(10, 1, 30, -1)


def test_shard_budget_reads_the_plan_and_emits_github_output(tmp_path):
    import json

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"shard_count": 1, "mutants": {f"m{i}": {} for i in range(455)}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(ROOT / ".ci" / "mutation_shard_budget.py"), str(plan)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "shard-timeout=243"


def test_campaign_workflow_sizes_the_shard_job_from_the_plan():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    plan_job = workflow["jobs"]["plan"]
    shard_job = workflow["jobs"]["shard"]
    assert plan_job["outputs"]["shard-timeout"] == (
        "${{ steps.shard-budget.outputs.shard-timeout || '45' }}"
    )
    assert shard_job["timeout-minutes"] == (
        "${{ needs.plan.outputs.shard-timeout || 45 }}"
    )
    budget_step = next(
        s for s in plan_job["steps"] if s.get("id") == "shard-budget"
    )
    assert ".ci/mutation_shard_budget.py" in budget_step["run"]
    assert budget_step["env"]["SHARD_PACE_SECONDS"] == (
        "${{ vars.MUTATION_SHARD_PACE_SECONDS || '30' }}"
    )
