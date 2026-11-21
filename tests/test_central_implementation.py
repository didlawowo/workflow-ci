"""Central implementation loaded from the runner copy, never fetched again."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ".github/actions/central-implementation"
USES = "$/.github/actions/central-implementation"
SCRIPT = ROOT / ACTION / "materialize.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
WORKFLOWS = (
    ".github/workflows/mutation-policy.yml",
    ".github/workflows/mutation-issue-policy.yml",
    ".github/workflows/node-mutation-evidence.yml",
    ".github/workflows/policy-integrity.yml",
    ".github/workflows/hidden-evidence.yml",
)
CAMPAIGN_CHECKOUT = ".github/actions/mutation-campaign-checkout/action.yml"


def loaded(tmp_path: Path, owner: str = "owner", reference: str = SHA) -> Path:
    """Repository as the runner stores it once an action of it is loaded."""
    root = tmp_path / "_actions" / owner / "repo" / reference
    (root / ACTION).mkdir(parents=True)
    shutil.copy2(SCRIPT, root / ACTION / "materialize.sh")
    (root / "top.py").write_text("print('central')\n")
    (root / ".hidden").write_text("hidden\n")
    (root / ".ci").mkdir()
    (root / ".ci" / "helper.sh").write_text("#!/usr/bin/env bash\necho helper\n")
    return root


def materialize(
    tmp_path: Path,
    root: Path,
    *,
    repository: str = "owner/repo",
    sha: str = SHA,
    destination: str = ".workflow-ci",
    action_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(root / ACTION / "materialize.sh")],
        env={
            **os.environ,
            "GITHUB_ACTION_PATH": str(action_path or root / ACTION),
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "CENTRAL_WORKFLOW_REPOSITORY": repository,
            "CENTRAL_WORKFLOW_SHA": sha,
            "CENTRAL_DESTINATION": destination,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_copies_the_loaded_repository_and_reports_its_location(tmp_path: Path) -> None:
    root = loaded(tmp_path)

    completed = materialize(tmp_path, root)

    assert completed.returncode == 0, completed.stderr
    copy = tmp_path / "workspace" / ".workflow-ci"
    assert files(copy) == files(root)
    assert {".hidden", ".ci/helper.sh", "top.py"} <= set(files(copy))
    assert f"source={root.resolve()}" in (tmp_path / "output").read_text().splitlines()


def test_reference_is_compared_verbatim_whatever_object_it_names(
    tmp_path: Path,
) -> None:
    # An annotated tag yields the identifier of the tag object, not of the
    # commit: the runner loads the repository under that same identifier.
    root = loaded(tmp_path, reference=OTHER_SHA)

    completed = materialize(tmp_path, root, sha=OTHER_SHA)

    assert completed.returncode == 0, completed.stderr
    assert files(tmp_path / "workspace" / ".workflow-ci") == files(root)


def test_refuses_an_implementation_loaded_for_another_reference(
    tmp_path: Path,
) -> None:
    root = loaded(tmp_path)

    completed = materialize(tmp_path, root, sha=OTHER_SHA)

    assert completed.returncode != 0
    assert "::error::" in completed.stderr
    assert not (tmp_path / "workspace" / ".workflow-ci").exists()


def test_refuses_an_implementation_loaded_from_another_repository(
    tmp_path: Path,
) -> None:
    root = loaded(tmp_path)

    completed = materialize(tmp_path, root, repository="owner/other")

    assert completed.returncode != 0
    assert not (tmp_path / "workspace" / ".workflow-ci").exists()


def test_repository_name_is_compared_without_case(tmp_path: Path) -> None:
    root = loaded(tmp_path, owner="Owner")

    completed = materialize(tmp_path, root, repository="owner/repo")

    assert completed.returncode == 0, completed.stderr


def test_refuses_a_reference_that_is_not_a_full_identifier(tmp_path: Path) -> None:
    root = loaded(tmp_path, reference="v1.2.3")

    completed = materialize(tmp_path, root, sha="v1.2.3")

    assert completed.returncode != 0
    assert not (tmp_path / "workspace" / ".workflow-ci").exists()


def test_refuses_an_action_read_from_a_checkout(tmp_path: Path) -> None:
    root = loaded(tmp_path)
    checkout = tmp_path / "checkout" / ACTION
    checkout.mkdir(parents=True)

    completed = materialize(tmp_path, root, action_path=checkout)

    assert completed.returncode != 0
    assert not (tmp_path / "workspace" / ".workflow-ci").exists()


@pytest.mark.parametrize("destination", ["", "ABSOLUTE", "..", "../outside", "a/../b", "."])
def test_refuses_a_destination_outside_a_plain_relative_path(
    tmp_path: Path, destination: str
) -> None:
    root = loaded(tmp_path)
    outside = tmp_path / "elsewhere"
    if destination == "ABSOLUTE":
        destination = str(outside)

    completed = materialize(tmp_path, root, destination=destination)

    assert completed.returncode != 0
    assert "destination" in completed.stderr
    assert not outside.exists()
    assert not (tmp_path / "outside").exists()
    assert list((tmp_path / "workspace").iterdir()) == []


def test_refuses_a_destination_that_leaves_the_workspace_through_a_link(
    tmp_path: Path,
) -> None:
    root = loaded(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)

    completed = materialize(tmp_path, root, destination="link/copy")

    assert completed.returncode != 0
    assert list(outside.iterdir()) == []


def test_replaces_a_destination_that_already_exists(tmp_path: Path) -> None:
    root = loaded(tmp_path)
    stale = tmp_path / "workspace" / ".workflow-ci"
    stale.mkdir(parents=True)
    (stale / "stale.txt").write_text("left by an earlier step\n")

    completed = materialize(tmp_path, root)

    assert completed.returncode == 0, completed.stderr
    assert files(stale) == files(root)


def test_creates_the_parents_of_a_nested_destination(tmp_path: Path) -> None:
    root = loaded(tmp_path)

    completed = materialize(tmp_path, root, destination="a/b")

    assert completed.returncode == 0, completed.stderr
    assert files(tmp_path / "workspace" / "a" / "b") == files(root)


@pytest.mark.parametrize("path", [*WORKFLOWS, CAMPAIGN_CHECKOUT])
def test_central_scripts_are_never_fetched_again(path: str) -> None:
    content = (ROOT / path).read_text(encoding="utf-8")

    assert f"uses: {USES}" in content
    lines = [line.strip() for line in content.splitlines()]
    for download in (
        "repository: ${{ job.workflow_repository }}",
        "repository: ${{ inputs.workflow-repository }}",
    ):
        assert download not in lines, f"{path} checks the central repository out"
    assert "${WORKFLOW_REPOSITORY}.git" not in content, f"{path} fetches the central repository"


@pytest.mark.parametrize(
    "path",
    [".github/workflows/mutation-policy.yml", ".github/workflows/mutation-issue-policy.yml"],
)
def test_both_jobs_of_a_two_job_workflow_load_the_implementation(path: str) -> None:
    assert (ROOT / path).read_text(encoding="utf-8").count(f"uses: {USES}") == 2


def loading_steps(path: str) -> list[dict]:
    document = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    if "jobs" in document:
        steps = [
            step for job in document["jobs"].values() for step in job.get("steps", [])
        ]
    else:
        steps = document["runs"]["steps"]
    return [step for step in steps if step.get("uses") == USES]


@pytest.mark.parametrize("path", WORKFLOWS)
def test_workflows_bind_the_implementation_to_their_own_identity(path: str) -> None:
    steps = loading_steps(path)

    assert steps
    for step in steps:
        assert step["with"]["workflow-repository"] == "${{ job.workflow_repository }}"
        assert step["with"]["workflow-sha"] == "${{ job.workflow_sha }}"


def test_campaign_checkout_forwards_the_identity_of_the_called_workflow() -> None:
    (step,) = loading_steps(CAMPAIGN_CHECKOUT)

    assert step["with"]["workflow-repository"] == "${{ inputs.workflow-repository }}"
    assert step["with"]["workflow-sha"] == "${{ inputs.workflow-sha }}"


@pytest.mark.parametrize(
    ("path", "verification", "copy"),
    [
        (
            ".github/workflows/policy-integrity.yml",
            "Verify checkout identities",
            ".workflow-ci-policy",
        ),
        (
            ".github/workflows/hidden-evidence.yml",
            "Verify trusted, base and candidate identities",
            ".workflow-ci-hidden",
        ),
    ],
)
def test_trusted_copy_is_compared_again_before_it_is_used(
    path: str, verification: str, copy: str
) -> None:
    document = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    (steps,) = [
        job["steps"]
        for job in document["jobs"].values()
        if any(step.get("name") == verification for step in job["steps"])
    ]
    names = [step.get("name") for step in steps]
    central = next(i for i, step in enumerate(steps) if step.get("id") == "central")
    verify = steps[names.index(verification)]

    assert steps[central]["uses"] == USES
    assert steps[central]["with"]["path"] == copy
    assert central < names.index(verification)
    assert verify["env"]["CENTRAL_SOURCE"] == "${{ steps.central.outputs.source }}"
    assert f'diff -r "$CENTRAL_SOURCE" {copy}' in verify["run"]


def test_trusted_policy_is_loaded_before_the_protected_base_checkout() -> None:
    document = yaml.safe_load(
        (ROOT / ".github/workflows/policy-integrity.yml").read_text(encoding="utf-8")
    )
    steps = document["jobs"]["policy-integrity"]["steps"]
    order = [step.get("id") or step.get("name") for step in steps]

    assert order.index("central") < order.index("Checkout protected base")
