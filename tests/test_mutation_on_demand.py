import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mutation_on_demand", ROOT / ".github/scripts/mutation_policy.py"
)
assert SPEC and SPEC.loader
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def classify(monkeypatch, tmp_path, mode, labels):
    if mode is None:
        monkeypatch.delenv("MUTATION_EXECUTION_MODE", raising=False)
    else:
        monkeypatch.setenv("MUTATION_EXECUTION_MODE", mode)
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        policy, "_api_request", lambda *args: [{"filename": "src/app.py", "changes": 2}]
    )
    policy.classify(
        {"pull_request": {"number": 7, "changed_files": 1, "labels": labels}}
    )
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


@pytest.mark.parametrize("mode", [None, "automatic"])
def test_other_repositories_keep_automatic_high_risk_enforcement(
    monkeypatch, tmp_path, mode
):
    result = classify(monkeypatch, tmp_path, mode, ["complexity:high"])
    assert result["required"] == "true" and result["depth"] == "high"


@pytest.mark.parametrize("labels", [[], ["complexity:high"], ["complexity:medium"]])
def test_on_demand_never_starts_from_complexity_alone(monkeypatch, tmp_path, labels):
    result = classify(monkeypatch, tmp_path, "on-demand", labels)
    assert result == {"required": "false", "labels": "", "depth": "none"}


@pytest.mark.parametrize(
    "complexity,depth", [("complexity:high", "high"), ("complexity:low", "medium")]
)
def test_explicit_pr_request_requires_real_evidence(
    monkeypatch, tmp_path, complexity, depth
):
    result = classify(
        monkeypatch, tmp_path, "on-demand", [complexity, "mutation:requested"]
    )
    assert result["required"] == "true" and result["depth"] == depth
    assert "mutation:requested" in result["labels"]


def test_issue_or_body_cannot_implicitly_request_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("MUTATION_EXECUTION_MODE", "on-demand")
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        policy,
        "_api_request",
        lambda *args: pytest.fail("no request should fetch issue complexity"),
    )
    policy.classify(
        {"pull_request": {"labels": [], "body": "Fixes #42 mutation:requested"}}
    )
    assert "required=false" in output.read_text()


@pytest.mark.parametrize("mode", ["", "disabled", "on_demand", "never"])
def test_invalid_repository_policy_fails_closed(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("MUTATION_EXECUTION_MODE", mode)
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    with pytest.raises(ValueError, match="invalid_mutation_execution_mode"):
        policy.classify({"pull_request": {"labels": []}})
    assert not output.exists()


def test_on_demand_does_not_post_mandatory_guidance(monkeypatch):
    monkeypatch.setenv("MUTATION_EXECUTION_MODE", "on-demand")
    monkeypatch.setattr(
        policy,
        "_api_request",
        lambda *args: pytest.fail("must not write mandatory comments"),
    )
    assert (
        policy.notify({"label": {"name": "complexity:high"}, "issue": {"number": 42}})
        == 0
    )


def test_issue_refresh_only_reruns_explicitly_requested_pr(monkeypatch):
    monkeypatch.setenv("MUTATION_EXECUTION_MODE", "on-demand")
    prs = [
        {"number": 1, "head": {"sha": "a"}, "body": "Fixes #42", "labels": []},
        {
            "number": 2,
            "head": {"sha": "b"},
            "body": "Fixes #42",
            "labels": ["mutation:requested"],
        },
    ]
    monkeypatch.setattr(policy, "_open_pull_requests", lambda: prs)
    observed = []

    def runs(head):
        observed.append(head)
        return [{"id": 17, "status": "completed", "head_sha": head}]

    monkeypatch.setattr(policy, "_mutation_runs_for_head", runs)
    monkeypatch.setattr(policy, "_is_mutation_policy_run", lambda *args: True)
    writes = []
    monkeypatch.setattr(policy, "_api_request", lambda *args: writes.append(args))
    policy.refresh({"label": {"name": "complexity:high"}, "issue": {"number": 42}})
    assert observed == ["b"]
    assert writes == [("POST", "actions/runs/17/rerun")]


@pytest.mark.parametrize(
    "filename,count",
    [
        ("mutation-policy.yml", 2),
        ("mutation-campaign.yml", 1),
        ("mutation-issue-policy.yml", 2),
    ],
)
def test_execution_and_verification_read_same_repository_owned_mode(filename, count):
    workflow = yaml.load(
        (ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader
    )
    steps = [
        s
        for job in workflow["jobs"].values()
        for s in job.get("steps", [])
        if "POLICY_EVENT_PATH" in s.get("env", {})
    ]
    assert len(steps) == count
    assert all(
        s["env"]["MUTATION_EXECUTION_MODE"]
        == "${{ vars.MUTATION_EXECUTION_MODE || 'automatic' }}"
        for s in steps
    )
