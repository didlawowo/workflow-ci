"""A campaign is green only when its exact expected partition is complete."""

import copy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ci"))
campaign = importlib.import_module("mutation_campaign")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def complete(tmp_path):
    repo, trusted = tmp_path / "repo", tmp_path / "trusted"
    repo.mkdir()
    trusted.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Campaign Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    (repo / "src").mkdir()
    config = '[tool.mutmut]\nsource_paths=["src/"]\n'
    (repo / "pyproject.toml").write_text(config)
    (trusted / "pyproject.toml").write_text(config)
    source = repo / "src/core.py"
    source.write_text("def run(value):\n    return value + 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    source.write_text("def run(value):\n    return value + 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    head = git(repo, "rev-parse", "HEAD")
    plan = {
        "schema_version": 1,
        "base_sha": base,
        "head_sha": head,
        "working_directory": ".",
        "shard_count": 3,
        "sources": {"src/core.py": hashlib.sha256(source.read_bytes()).hexdigest()},
        "mutants": {
            f"core.x_run__mutmut_{index}": {
                "source": "src/core.py",
                "mutant_sha256": hashlib.sha256(str(index).encode()).hexdigest(),
            }
            for index in range(30)
        },
    }
    digest = campaign.plan_digest(plan)
    fragments = []
    for index in range(3):
        mutants = {
            key: {"status": "killed", "mutant_sha256": value["mutant_sha256"]}
            for key, value in plan["mutants"].items()
            if campaign.partition(key, 3) == index
        }
        fragments.append(
            {
                "shard_index": index,
                "shard_count": 3,
                "plan_sha256": digest,
                "base_sha": base,
                "head_sha": head,
                "exit_code": 0,
                "stats": {"killed": len(mutants), "survived": 0, "total": len(mutants)},
                "mutants": mutants,
            }
        )
    return plan, fragments, repo, trusted, base, head


def aggregate(complete):
    plan, fragments, repo, trusted, base, head = complete
    return campaign.aggregate(
        plan, fragments, repo, trusted, base, head, ".", "medium", 3
    )


def test_complete_partition_passes_without_reclassifying_counts(complete):
    result = aggregate(complete)
    assert result["campaign"]["complete"] is True
    assert result["campaign"]["expected_mutants"] == 30
    assert result["stats"]["killed"] == 30
    assert result["raw_stats"]["survived"] == 0


@pytest.mark.parametrize(
    "change",
    [
        "missing_shard",
        "duplicate_shard",
        "missing_mutant",
        "wrong_partition",
        "unexpected_mutant",
        "stale_head",
        "altered_plan",
        "altered_mutant",
        "source_changed",
        "failed_worker",
        "fractional_exit",
        "bad_counts",
        "no_tests",
        "survived",
    ],
)
def test_partial_or_altered_campaign_never_passes(complete, change):
    plan, fragments, repo, _, _, _ = complete
    first = fragments[0]
    key = next(iter(first["mutants"]))
    if change == "missing_shard":
        fragments.pop()
    elif change == "duplicate_shard":
        fragments[1] = copy.deepcopy(first)
    elif change == "missing_mutant":
        first["mutants"].pop(key)
    elif change == "wrong_partition":
        fragments[1]["mutants"][key] = first["mutants"].pop(key)
    elif change == "unexpected_mutant":
        first["mutants"]["other.x_run__mutmut_1"] = first["mutants"].pop(key)
    elif change == "stale_head":
        first["head_sha"] = "stale"
    elif change == "altered_plan":
        first["plan_sha256"] = "0" * 64
    elif change == "altered_mutant":
        first["mutants"][key]["mutant_sha256"] = "0" * 64
    elif change == "source_changed":
        (repo / "src/core.py").write_text("def run(value):\n    return 99\n")
    elif change == "failed_worker":
        first["exit_code"] = 1
    elif change == "fractional_exit":
        first["exit_code"] = 0.0
    elif change == "bad_counts":
        first["stats"]["killed"] += 1
    elif change in {"no_tests", "survived"}:
        first["mutants"][key]["status"] = change
        first["stats"]["killed"] -= 1
        first["stats"][change] = 1
    with pytest.raises(ValueError):
        aggregate(complete)


def test_symlinked_source_outside_candidate_is_rejected(complete, tmp_path):
    plan, _, repo, _, base, head = complete
    source = repo / "src/core.py"
    external = tmp_path / "external.py"
    external.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(external)
    with pytest.raises(ValueError, match="source content"):
        campaign.validate_plan(plan, repo, base, head, ".", 3)


def test_campaign_entrypoint_must_be_from_protected_base(tmp_path):
    root = tmp_path / "workspace"
    (root / ".policy/.ci").mkdir(parents=True)
    (root / "pr/.ci").mkdir(parents=True)
    (root / "pr/.ci/mutation-plan.sh").write_text("exit 0\n")
    script = Path(__file__).resolve().parents[1] / ".ci/mutation_campaign_run.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "plan",
            "--root",
            str(root),
            "--plan",
            str(tmp_path / "plan.json"),
            "--output",
            str(tmp_path / "result.json"),
            "--shards",
            "2",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "Missing protected campaign entrypoint" in completed.stderr


def test_protected_planner_workers_and_aggregation_run_end_to_end(complete, tmp_path):
    import os
    import shutil

    plan, fragments, repo, trusted, base, head = complete
    root = tmp_path / "execution"
    shutil.copytree(repo, root / "pr")
    shutil.copytree(trusted, root / ".policy")
    scripts = root / ".policy/.ci"
    scripts.mkdir()
    (scripts / "plan-template.json").write_text(json.dumps(plan))
    for index, fragment in enumerate(fragments):
        (scripts / f"fragment-{index}.json").write_text(json.dumps(fragment))
    (scripts / "mutation-plan.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\ncp "$(dirname "$0")/plan-template.json" "$1"\n'
    )
    (scripts / "mutation-shard.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\ntest -z "${GITHUB_TOKEN:-}"\ntest -z "${GITHUB_OUTPUT:-}"\ntest -s "$MUTATION_PLAN"\ncp "$(dirname "$0")/fragment-${MUTATION_SHARD_INDEX}.json" "$1"\n'
    )
    entrypoint = Path(__file__).resolve().parents[1] / ".ci/mutation_campaign_run.py"
    plan_path = tmp_path / "plan.json"
    env = {
        **os.environ,
        "GITHUB_TOKEN": "secret-sentinel",
        "GITHUB_OUTPUT": "/must-not-be-exposed",
        "MUTATION_BASE_SHA": base,
        "MUTATION_HEAD_SHA": head,
    }
    command = [
        sys.executable,
        str(entrypoint),
        "plan",
        "--root",
        str(root),
        "--plan",
        str(plan_path),
        "--output",
        str(plan_path),
        "--shards",
        "3",
    ]
    subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    collected = []
    for index in range(3):
        output = tmp_path / f"fragment-{index}.json"
        command = [
            sys.executable,
            str(entrypoint),
            "shard",
            "--root",
            str(root),
            "--plan",
            str(plan_path),
            "--output",
            str(output),
            "--shards",
            "3",
            "--index",
            str(index),
        ]
        subprocess.run(command, env=env, check=True, capture_output=True, text=True)
        collected.append(json.loads(output.read_text()))
    verdict = campaign.aggregate(
        json.loads(plan_path.read_text()),
        collected,
        root / "pr",
        root / ".policy",
        base,
        head,
        ".",
        "medium",
        3,
    )
    assert verdict["stats"]["killed"] == 30
    assert verdict["campaign"]["complete"] is True


def test_equivalence_proof_must_describe_the_exact_planned_mutant(complete):
    plan, fragments, repo, trusted, base, head = complete
    key = next(iter(fragments[0]["mutants"]))
    name = key.rsplit(".", 1)[1]
    mutant = f"def {name}(value):\n    return value + 3\n"
    evidence = importlib.import_module("mutation_evidence")
    entry = {
        "source": "src/core.py",
        "source_sha256": plan["sources"]["src/core.py"],
        "mutant_sha256": evidence.digest_function(mutant, name),
        "reason": "Reviewed alternative B",
    }
    (trusted / ".ci").mkdir()
    (trusted / ".ci/mutation-equivalents.json").write_text(json.dumps({key: entry}))
    fragments[0]["mutants"][key]["status"] = "survived"
    fragments[0]["stats"]["killed"] -= 1
    fragments[0]["stats"]["survived"] += 1
    fragments[0]["review"] = {
        "base_sha": base,
        "head_sha": head,
        "reviewed_equivalents": [key],
        "mutants": {key: {**entry, "mutant_source": mutant}},
    }
    # The plan/fragment describe mutant A; the ledger and supplied AST approve B.
    assert entry["mutant_sha256"] != plan["mutants"][key]["mutant_sha256"]
    with pytest.raises(ValueError, match="planned mutant"):
        aggregate(complete)
