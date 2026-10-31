from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "policy_integrity.py"
)
SPEC = importlib.util.spec_from_file_location("policy_integrity", MODULE_PATH)
assert SPEC and SPEC.loader
policy_integrity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy_integrity)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def wrapper(version: str, extra: str = "") -> str:
    return (
        "jobs:\n"
        "  evidence:\n"
        f"    uses: didlawowo/workflow-ci/.github/workflows/quality-evidence.yml@{version}\n"
        "    with:\n"
        f"      workflow-ci-ref: {version}\n"
        f"{extra}"
    )


def hosted_reasons(candidate: Path, workflow: str) -> list[str]:
    write(candidate, ".github/workflows/ci.yml", workflow)
    result = policy_integrity.evaluate(candidate.parent / "base", candidate)
    return [violation["reason"] for violation in result["violations"]]


@pytest.mark.parametrize(
    "workflow",
    [
        "jobs:\n  a:\n    runs-on: ubuntu-latest\n",
        'jobs:\n  a:\n    runs-on: "macos-15"\n',
        "jobs:\n  a:\n    runs-on: [windows-latest]\n",
        "jobs:\n  a:\n    runs-on: ${{ vars.RUNNER || 'ubuntu-24.04' }}\n",
        "jobs:\n  a:\n    runs-on:\n      - ubuntu-latest\n",
        "jobs:\n  a:\n    runs-on:\n      group: ci\n      labels: ubuntu-latest\n",
        "jobs:\n  a:\n    uses: ./x.yml\n    with:\n      runner: ubuntu-latest\n",
        "jobs:\n  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest]\n",
        "jobs:\n  a:\n    strategy:\n      matrix:\n        include:\n"
        "          - os: macos-latest\n",
        "jobs:\n  a:\n    runs-on: [\n      self-hosted,\n      ubuntu-latest\n    ]\n",
        "jobs:\n  a:\n    runs-on:\n      ubuntu-latest\n",
        "jobs:\n  a:\n    runs-on: >-\n      ubuntu-latest\n",
        'jobs:\n  a:\n    "runs-on": ubuntu-latest\n',
        "jobs:\n  a: {runs-on: ubuntu-latest}\n",
        "env: {R: &r ubuntu-latest}\njobs:\n  a:\n    runs-on: *r\n",
        "jobs:\n  a:\n    strategy:\n      matrix: {platform: [windows-11-arm]}\n",
        "on:\n  workflow_call:\n    inputs:\n      runner:\n"
        "        default: ubuntu-latest\n",
        "jobs:\n  a:\n    runs-on: # c\n      - ubuntu-latest\n",
        """jobs:\n  a:\n    runs-on: "${{ x && ' #' || 'ubuntu-latest' }}"\n""",
        "jobs:\n  a:\n    steps:\n      - run: |\n          echo ok\n"
        "  b:\n    runs-on: ubuntu-latest\n",
        'jobs:\n  a:\n    runs-on: ["a \\" # x", ubuntu-latest]\n',
        "x: {a: don't, b: 'x # y', runs-on: ubuntu-latest}\n",
        """jobs:\n  a:\n    runs-on: [it's, "q", 'a # b', ubuntu-latest]\n""",
        "jobs:\n  a:\n    runs-on: [a\u00a0#, ubuntu-latest]\n",
        "jobs:\n  a:\n    strategy:\n      matrix:\n        include:\n"
        "          - name: >\n              Linux\n"
        "            os: ubuntu-latest\n",
        "jobs:\n  a:\n    steps:\n      - run: |\n          echo ok\n"
        "        with:\n          runner: ubuntu-latest\n",
        "name: &r ubuntu-latest\njobs:\n  a:\n    runs-on: *r\n",
        "jobs:\n  a:\n    strategy:\n      matrix: {name: [ubuntu-latest]}\n",
        "jobs:\n  a:\n    strategy:\n      matrix:\n        include:\n"
        "          - name: ubuntu-latest\n",
        "jobs:\n  a:\n    runs-on: arc-runner-x # ubuntu-latest\n",
        "jobs:\n  a:\n    strategy:\n      matrix:\n        include:\n"
        "          - name: ubuntu-latest # linux\n",
        "description: macos-14 # x\n",
        "jobs:\n  a:\n    strategy:\n      matrix:\n        include:\n"
        "          - name: 'ubuntu-latest' # x\n",
    ],
)
def test_github_hosted_runner_fails_policy_when_a_workflow_selects_it(
    tmp_path: Path, workflow: str
) -> None:
    assert any(
        "GitHub-hosted runner" in reason
        for reason in hosted_reasons(tmp_path / "candidate", workflow)
    )


@pytest.mark.parametrize(
    "workflow",
    [
        "jobs:\n  a:\n    runs-on: arc-runner-keryx\n",
        "jobs:\n  a:\n    runs-on: ${{ vars.RUNNER || 'arc-runner-keryx' }}\n",
        "jobs:\n  a:\n    runs-on: ${{ inputs.runner }}\n",
        "jobs:\n  a:\n    runs-on: [self-hosted, macOS, keryx-ios]\n",
        "jobs:\n  a:\n    runs-on: arc-runner-ubuntu-gpu\n",
        "# runs-on: ubuntu-latest\njobs:\n  a:\n    runs-on: arc-runner-x\n",
        "jobs:\n  a:\n    runs-on: arc-runner-x\n    steps:\n"
        "      - run: echo ubuntu-latest\n",
        "jobs:\n  a:\n    runs-on: arc-runner-x\n    steps:\n      - run: |\n"
        "          cat <<EOF\n          runs-on: ubuntu-latest\n          EOF\n",
        "name: tests on ubuntu-latest\njobs:\n  a:\n    runs-on: arc-runner-x\n",
        "jobs:\n  a:\n    steps:\n      - run: |2-\n          echo ubuntu-latest\n",
        "jobs:\n  a:\n    runs-on: arc-runner-ubuntu-24.04\n",
        "jobs:\n  a:\n    runs-on: arc-runner-x\n"
        "    defaults:\n      run:\n        working-directory: scripts/macos-14\n",
    ],
)
def test_internal_runner_passes_policy_when_no_hosted_label_is_selected(
    tmp_path: Path, workflow: str
) -> None:
    assert hosted_reasons(tmp_path / "candidate", workflow) == []


def test_disabled_workflow_is_ignored_by_hosted_runner_policy(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    write(candidate, ".github/workflows/old.yml.disabled", "runs-on: ubuntu-latest\n")

    assert policy_integrity.evaluate(tmp_path / "base", candidate)["status"] == "pass"


def test_yaml_extension_is_covered_by_hosted_runner_policy(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    write(candidate, ".github/workflows/ci.yaml", "runs-on: ubuntu-latest\n")

    assert policy_integrity.evaluate(tmp_path / "base", candidate)["status"] == "fail"


def test_non_utf8_workflow_is_still_scanned(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    path = candidate / ".github" / "workflows" / "ci.yml"
    path.parent.mkdir(parents=True)
    path.write_bytes(bytes([255]) + b"\nruns-on: ubuntu-latest\n")

    assert policy_integrity.evaluate(tmp_path / "base", candidate)["status"] == "fail"


def test_link_outside_candidate_is_not_read(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    outside = tmp_path / "outside.yml"
    outside.write_text("runs-on: ubuntu-latest\n", encoding="utf-8")
    workflows = candidate / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").symlink_to(outside)

    assert policy_integrity.evaluate(tmp_path / "base", candidate)["status"] == "pass"


def test_annotation_cannot_inject_a_workflow_command() -> None:
    escaped = policy_integrity._annotation_property("a\n::warning::x,y%.yml")

    assert escaped == "a%0A%3A%3Awarning%3A%3Ax%2Cy%25.yml"


def test_hosted_runner_violation_names_file_and_line(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    write(
        candidate,
        ".github/workflows/ci.yml",
        "jobs:\n  a:\n    runs-on: ubuntu-latest\n",
    )

    violation = policy_integrity.evaluate(tmp_path / "base", candidate)["violations"][0]

    assert violation["path"] == ".github/workflows/ci.yml"
    assert "ubuntu-latest at line 3" in violation["reason"]


def test_unmodified_policy_passes(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(base, policy_integrity.WRAPPER, wrapper("v1.10.0"))
    write(candidate, policy_integrity.WRAPPER, wrapper("v1.10.0"))

    result = policy_integrity.evaluate(base, candidate)

    assert result["status"] == "pass"
    assert result["violations"] == []


def test_forward_semver_only_wrapper_upgrade_is_allowed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(base, policy_integrity.WRAPPER, wrapper("v1.10.0"))
    write(candidate, policy_integrity.WRAPPER, wrapper("v1.10.2"))

    result = policy_integrity.evaluate(base, candidate)

    assert result["status"] == "pass"
    assert result["allowed"][0]["path"] == policy_integrity.WRAPPER


def test_wrapper_logic_change_fails_even_with_version_upgrade(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(base, policy_integrity.WRAPPER, wrapper("v1.10.0"))
    write(candidate, policy_integrity.WRAPPER, wrapper("v1.10.2", "    if: false\n"))

    result = policy_integrity.evaluate(base, candidate)

    assert result["status"] == "fail"
    assert result["violations"][0]["path"] == policy_integrity.WRAPPER


def test_guard_deletion_fails_closed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    guard = ".github/workflows/trusted-policy-integrity.yml"
    write(base, guard, "name: guard\n")

    result = policy_integrity.evaluate(base, candidate)

    assert result["status"] == "fail"
    assert result["violations"] == [
        {"path": guard, "reason": "protected policy file was removed by the candidate"}
    ]


def test_mutation_runner_change_fails_closed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(base, ".ci/mutation.sh", "trusted\n")
    write(candidate, ".ci/mutation.sh", "candidate\n")

    result = policy_integrity.evaluate(base, candidate)

    assert result["status"] == "fail"
    assert result["violations"][0]["path"] == ".ci/mutation.sh"


def test_wrapper_downgrade_is_rejected(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(base, policy_integrity.WRAPPER, wrapper("v1.10.2"))
    write(candidate, policy_integrity.WRAPPER, wrapper("v1.10.1"))

    result = policy_integrity.evaluate(base, candidate)

    assert result["status"] == "fail"
    assert "must move forward" in result["violations"][0]["reason"]


def test_policy_integrity_workflow_resolves_annotated_release_tags() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "policy-integrity.yml"
    ).read_text(encoding="utf-8")

    assert (
        'test "$(git -C .workflow-ci-policy rev-parse HEAD)" = '
        "\"$(git -C .workflow-ci-policy rev-parse '${{ job.workflow_sha }}^{commit}')\""
        in workflow
    )


def hidden_wrapper(version: str, evaluator: str, runner: str, extra: str = "") -> str:
    return (
        "jobs:\n"
        "  hidden:\n"
        "    uses: didlawowo/workflow-ci/.github/workflows/"
        f"hidden-evidence.yml@{version}\n"
        "    with:\n"
        f"      evaluator: {evaluator}\n"
        f"      runner: {runner}\n"
        f"{extra}"
    )


def test_registered_hidden_enrollment_can_be_added(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    path = ".github/workflows/hidden-calibration-evidence.yml"
    write(
        candidate,
        path,
        hidden_wrapper(
            "v1.14.1",
            "ioniq-control/calibration",
            "arc-runner-ioniq-control",
        ),
    )

    result = policy_integrity.evaluate(base, candidate, "didlawowo/ioniq-control")

    assert result["status"] == "pass"
    assert result["allowed"][0]["path"] == path


def test_hidden_enrollment_deletion_fails_closed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    path = ".github/workflows/hidden-calibration-evidence.yml"
    write(
        base,
        path,
        hidden_wrapper(
            "v1.14.1",
            "ioniq-control/calibration",
            "arc-runner-ioniq-control",
        ),
    )

    result = policy_integrity.evaluate(base, candidate, "didlawowo/ioniq-control")

    assert result["status"] == "fail"
    assert "removed" in result["violations"][0]["reason"]


def test_hidden_evaluator_substitution_fails_closed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    path = ".github/workflows/hidden-calibration-evidence.yml"
    write(
        base,
        path,
        hidden_wrapper(
            "v1.14.1",
            "ioniq-control/calibration",
            "arc-runner-ioniq-control",
        ),
    )
    write(
        candidate,
        path,
        hidden_wrapper(
            "v1.14.2",
            "keryx/conversation-runtime",
            "arc-runner-ioniq-control",
        ),
    )

    result = policy_integrity.evaluate(base, candidate, "didlawowo/ioniq-control")

    assert result["status"] == "fail"
    assert "not bound" in result["violations"][0]["reason"]


def test_hidden_semver_only_forward_upgrade_is_allowed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    path = ".github/workflows/hidden-conversation-evidence.yml"
    write(
        base,
        path,
        hidden_wrapper(
            "v1.14.0",
            "keryx/conversation-runtime",
            "arc-runner-keryx",
        ),
    )
    write(
        candidate,
        path,
        hidden_wrapper(
            "v1.14.1",
            "keryx/conversation-runtime",
            "arc-runner-keryx",
        ),
    )

    result = policy_integrity.evaluate(base, candidate, "didlawowo/keryx")

    assert result["status"] == "pass"
    assert "allowed hidden workflow-ci migration" in result["allowed"][0]["reason"]


def test_mixed_base_quality_versions_can_converge_forward(tmp_path: Path) -> None:
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    write(
        base,
        policy_integrity.WRAPPER,
        wrapper("v1.18.5").replace(
            "workflow-ci-ref: v1.18.5", "workflow-ci-ref: v1.18.4"
        ),
    )
    write(candidate, policy_integrity.WRAPPER, wrapper("v1.19.0"))
    assert policy_integrity.evaluate(base, candidate)["status"] == "pass"


def test_protected_wrappers_allow_only_forward_ref_changes(tmp_path: Path) -> None:
    for path, workflow in policy_integrity.SEMVER_WRAPPERS.items():
        base, candidate = tmp_path / "base", tmp_path / "candidate"
        old = f"jobs:\n  guard:\n    uses: didlawowo/workflow-ci/.github/workflows/{workflow}@v1.18.8\n    with:\n      runner: arc-runner-keryx\n"
        new = old.replace("v1.18.8", "v1.19.0")
        for root in (base, candidate):
            write(root, path, old)
        write(candidate, path, new)
        assert policy_integrity.evaluate(base, candidate)["status"] == "pass"
        for invalid in (
            new.replace("arc-runner-keryx", "ubuntu-latest"),
            new + "    if: false\n",
            new.replace("v1.19.0", "v1.17.0"),
            new.replace("v1.19.0", "v1.19.0-unsafe"),
            new.replace(workflow, "other.yml"),
            new + "permissions: write-all\n",
        ):
            write(candidate, path, invalid)
            assert policy_integrity.evaluate(base, candidate)["status"] == "fail"
        write(candidate, path, old)


def test_quality_upgrade_cannot_change_other_action_refs(tmp_path: Path) -> None:
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    extra = "      - uses: didlawowo/workflow-ci/.github/actions/other@v1.18.5\n"
    write(base, policy_integrity.WRAPPER, wrapper("v1.18.5", extra))
    write(
        candidate,
        policy_integrity.WRAPPER,
        wrapper("v1.19.0", extra.replace("v1.18.5", "v1.19.0")),
    )
    assert policy_integrity.evaluate(base, candidate)["status"] == "fail"


def test_quality_duplicate_version_input_fails_closed(tmp_path: Path) -> None:
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    write(
        base,
        policy_integrity.WRAPPER,
        wrapper("v1.18.5", "      workflow-ci-ref: v1.18.5\n"),
    )
    write(
        candidate,
        policy_integrity.WRAPPER,
        wrapper("v1.19.0", "      workflow-ci-ref: v1.19.0\n"),
    )
    assert policy_integrity.evaluate(base, candidate)["status"] == "fail"


def test_initial_trust_check_peels_tags_and_rejects_wrong_commit(
    tmp_path: Path,
) -> None:
    import os
    import subprocess

    import yaml

    workflow = yaml.safe_load(
        (MODULE_PATH.parents[1] / "workflows" / "policy-integrity.yml").read_text()
    )
    step = next(
        s
        for s in workflow["jobs"]["policy-integrity"]["steps"]
        if s["name"] == "Verify trusted implementation before using its checkout helper"
    )
    repo = tmp_path / ".workflow-ci-policy"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True
        ).strip()

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "first",
    )
    first = git("rev-parse", "HEAD")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "tag",
        "-a",
        "v1.0.0",
        "-m",
        "release",
    )
    tag_object = git("rev-parse", "v1.0.0")
    assert tag_object != first
    for ref in (first, tag_object):
        assert (
            subprocess.run(
                ["bash", "-c", step["run"]],
                cwd=tmp_path,
                env={**os.environ, "WORKFLOW_SHA": ref},
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "second",
    )
    assert (
        subprocess.run(
            ["bash", "-c", step["run"]],
            cwd=tmp_path,
            env={**os.environ, "WORKFLOW_SHA": tag_object},
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )
