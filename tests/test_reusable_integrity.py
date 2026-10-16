"""The central implementation must survive provisioning of consumer wrappers."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_all_same_repository_actions_and_workflows_exist():
    for folder in (ROOT / ".github/actions", ROOT / ".github/workflows"):
        for source in folder.rglob("*.yml"):
            for relative in re.findall(
                r"^\s*uses:\s*\$/([^\s#]+)", source.read_text(), re.MULTILINE
            ):
                target = ROOT / relative
                assert target.is_file() or (target / "action.yml").is_file(), (
                    source,
                    relative,
                )


def test_central_mutation_helpers_cannot_be_removed_as_consumer_legacy():
    for relative in (
        ".github/workflows/mutation-policy.yml",
        ".github/scripts/mutation_policy.py",
        ".github/scripts/mutation_bootstrap.sh",
    ):
        assert (ROOT / relative).is_file(), relative


def test_execution_and_publisher_use_gitlink_safe_checkout():
    workflow = (ROOT / ".github/workflows/quality-evidence.yml").read_text()
    assert workflow.count("uses: $/.github/actions/checkout-source") == 2
    assert "uses: actions/checkout@" not in workflow
    assert "golangci-lint-version: ${{ inputs.golangci-lint-version }}" in workflow
    assert 'upload-sarif: "false"' in workflow


def test_policy_base_and_candidate_use_only_the_trusted_gitlink_safe_helper():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/policy-integrity.yml").read_text()
    )
    steps = workflow["jobs"]["policy-integrity"]["steps"]
    checkouts = [step for step in steps if step["name"].startswith("Checkout")]
    trusted, base, candidate = checkouts
    assert trusted["with"]["repository"] == "${{ job.workflow_repository }}"
    assert trusted["with"]["ref"] == "${{ job.workflow_sha }}"
    verification = next(
        step for step in steps if step["name"].startswith("Verify trusted workflow")
    )
    assert steps.index(trusted) < steps.index(verification) < steps.index(base)
    for step, destination, repository, ref in (
        (
            base,
            ".policy-base",
            "github.repository",
            "github.event.pull_request.base.sha",
        ),
        (
            candidate,
            ".policy-candidate",
            "github.event.pull_request.head.repo.full_name",
            "github.event.pull_request.head.sha",
        ),
    ):
        assert "uses" not in step
        assert step["env"]["CHECKOUT_REPOSITORY"] == "${{ " + repository + " }}"
        assert step["env"]["CHECKOUT_REF"] == "${{ " + ref + " }}"
        assert step["env"]["CHECKOUT_TOKEN"] == "${{ github.token }}"
        assert f'GITHUB_WORKSPACE="$GITHUB_WORKSPACE/{destination}"' in step["run"]
        assert (
            'bash "$GITHUB_WORKSPACE/.workflow-ci-policy/.github/actions/checkout-source/checkout.sh"'
            in step["run"]
        )
