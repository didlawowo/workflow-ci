"""Both forges consume the same classifier; provisioners have no copy."""
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
import forgejo_mutation as adapter

spec = importlib.util.spec_from_file_location(
    "canonical_mutation_policy", ROOT / ".github/scripts/mutation_policy.py"
)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def test_linked_issue_numbers_are_deduplicated_and_ordered():
    assert policy.linked_issue_numbers("Closes #42, fixes: #7 and refs #42. Unrelated #99.") == (42, 7)


def test_forgejo_adapter_calls_canonical_classifier_not_its_own_rules(monkeypatch):
    monkeypatch.setitem(sys.modules, "mutation_policy", policy)
    event = {"pull_request": {"labels": [], "body": ""}}
    seen = []

    def classify(supplied):
        assert supplied is event
        seen.append(supplied)
        policy._write_output("required", "true")
        policy._write_output("labels", "priority:high")
        return 0

    monkeypatch.setattr(policy, "classify", classify)
    assert adapter.classify(event) is True
    assert seen == [event]


def test_forgejo_issue_events_use_central_notification_and_refresh(monkeypatch):
    monkeypatch.setitem(sys.modules, "mutation_policy", policy)
    calls = []
    monkeypatch.setattr(policy, "notify", lambda event: calls.append(("notify", event)))
    monkeypatch.setattr(policy, "refresh", lambda event: calls.append(("refresh", event)))
    labeled = {"issue": {"number": 1}, "action": "labeled"}
    unlabeled = {"issue": {"number": 1}, "action": "unlabeled"}
    assert adapter.classify(labeled) is False
    assert adapter.classify(unlabeled) is False
    assert calls == [("notify", labeled), ("refresh", labeled), ("refresh", unlabeled)]


def test_canonical_policy_retains_linked_risk_and_normal_pr_behavior():
    event = {"pull_request": {"labels": [{"name": "complexity:high"}], "body": "Fixes #12"}}
    assert policy.mutation_reasons(event, lambda _: {"labels": [{"name": "priority:high"}]}) == ("complexity:high", "priority:high")
    event = {"pull_request": {"labels": [{"name": "complexity:medium"}], "body": "Refs #3"}}
    assert policy.mutation_reasons(event, lambda _: {"labels": [{"name": "priority:medium"}]}) == ()


def test_workflow_output_uses_real_newlines(tmp_path, monkeypatch):
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("FORGEJO_OUTPUT", raising=False)
    policy._write_output("required", "true")
    policy._write_output("labels", "complexity:high")
    assert output.read_text() == "required=true\nlabels=complexity:high\n"
