import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "mutation_policy.py"
SPEC = importlib.util.spec_from_file_location("mutation_policy_refresh", MODULE_PATH)
assert SPEC and SPEC.loader
mutation_policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mutation_policy)


def test_refresh_reruns_only_linked_pr_at_current_head(monkeypatch):
    calls = []

    def fake_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path.startswith("pulls?"):
            return [
                {
                    "number": 101,
                    "body": "Fixes #56",
                    "head": {"sha": "head-linked"},
                },
                {
                    "number": 102,
                    "body": "Fixes #999",
                    "head": {"sha": "head-unrelated"},
                },
            ]
        if method == "GET" and path.startswith("actions/runs?"):
            assert "head_sha=head-linked" in path
            return {
                "workflow_runs": [
                    {
                        "id": 700,
                        "status": "completed",
                        "head_sha": "old-head",
                        "path": ".github/workflows/mutation-policy.yml",
                        "pull_requests": [{"number": 101}],
                    },
                    {
                        "id": 701,
                        "status": "completed",
                        "head_sha": "head-linked",
                        "path": ".github/workflows/mutation-policy.yml",
                        "pull_requests": [{"number": 101}],
                    },
                    {
                        "id": 702,
                        "status": "completed",
                        "head_sha": "head-linked",
                        "path": ".github/workflows/other.yml",
                        "pull_requests": [{"number": 101}],
                    },
                ]
            }
        if method == "POST" and path == "actions/runs/701/rerun":
            return {}
        raise AssertionError((method, path, payload))

    monkeypatch.setenv("POLICY_PROVIDER", "github")
    monkeypatch.setattr(mutation_policy, "_api_request", fake_api)

    event = {
        "action": "unlabeled",
        "label": {"name": "complexity:high"},
        "issue": {"number": 56},
    }
    assert mutation_policy.refresh(event) == 0

    posts = [call for call in calls if call[0] == "POST"]
    assert posts == [("POST", "actions/runs/701/rerun", None)]


def test_refresh_ignores_non_policy_labels(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mutation_policy,
        "_api_request",
        lambda method, path, payload=None: calls.append((method, path, payload)),
    )

    event = {
        "action": "labeled",
        "label": {"name": "documentation"},
        "issue": {"number": 56},
    }
    assert mutation_policy.refresh(event) == 0
    assert calls == []


def test_forgejo_run_listing_uses_forgejo_pagination(monkeypatch):
    seen = []

    def fake_api(method, path, payload=None):
        seen.append(path)
        return {"workflow_runs": []}

    monkeypatch.setenv("POLICY_PROVIDER", "forgejo")
    monkeypatch.setattr(mutation_policy, "_api_request", fake_api)

    assert mutation_policy._mutation_runs_for_head("abc123") == []
    assert len(seen) == 1
    assert "head_sha=abc123" in seen[0]
    assert "limit=100" in seen[0]
    assert "per_page=100" not in seen[0]


def test_mutation_run_match_requires_policy_workflow_and_pr_identity():
    base = {
        "id": 1,
        "status": "completed",
        "head_sha": "head",
        "path": ".github/workflows/mutation-policy.yml",
        "pull_requests": [{"number": 42}],
    }
    assert mutation_policy._is_mutation_policy_run(base, 42, "head")

    forgejo = dict(base, path=".forgejo/workflows/mutation-policy.yml")
    assert mutation_policy._is_mutation_policy_run(forgejo, 42, "head")

    wrong_pr = dict(base, pull_requests=[{"number": 99}])
    assert not mutation_policy._is_mutation_policy_run(wrong_pr, 42, "head")

    reusable_caller = dict(
        base,
        path=".github/workflows/trusted-quality-evidence.yml",
        name="Trusted quality evidence",
    )
    assert mutation_policy._is_mutation_policy_run(reusable_caller, 42, "head")

    wrong_path = dict(base, path=".github/workflows/ci.yml")
    assert not mutation_policy._is_mutation_policy_run(wrong_path, 42, "head")

    assert not mutation_policy._is_mutation_policy_run(base, 42, "other-head")


def test_dependency_only_dependabot_pr_skips_mutation(monkeypatch, tmp_path):
    event = {
        "number": 134,
        "pull_request": {
            "number": 134,
            "changed_files": 2,
            "user": {"login": "dependabot[bot]"},
            "labels": [],
            "body": "Bump dependency",
        },
    }
    calls = []

    def fake_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "pulls/134/files?per_page=100":
            return [{"filename": "src/go.mod"}, {"filename": "src/go.sum"}]
        raise AssertionError((method, path, payload))

    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(mutation_policy, "_api_request", fake_api)

    assert mutation_policy.classify(event) == 0
    rendered = output.read_text()
    assert "required=false" in rendered
    assert "labels=" in rendered
    assert calls == [("GET", "pulls/134/files?per_page=100", None)]


def test_dependabot_high_risk_label_still_requires_mutation(monkeypatch, tmp_path):
    event = {
        "number": 135,
        "pull_request": {
            "number": 135,
            "changed_files": 2,
            "user": {"login": "dependabot[bot]"},
            "labels": [{"name": "priority:high"}],
            "body": "",
        },
    }
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        mutation_policy,
        "_api_request",
        lambda method, path, payload=None: (_ for _ in ()).throw(
            AssertionError((method, path, payload))
        ),
    )

    assert mutation_policy.classify(event) == 0
    rendered = output.read_text()
    assert "required=true" in rendered
    assert "labels=priority:high" in rendered


def test_dependabot_with_production_code_is_not_dependency_only(monkeypatch):
    event = {
        "number": 136,
        "pull_request": {
            "number": 136,
            "changed_files": 2,
            "user": {"login": "dependabot[bot]"},
            "labels": [],
        },
    }

    def fake_api(method, path, payload=None):
        if method == "GET" and path == "pulls/136/files?per_page=100":
            return [{"filename": "go.mod"}, {"filename": "internal/service.go"}]
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(mutation_policy, "_api_request", fake_api)
    assert not mutation_policy._dependabot_dependency_only(event)
