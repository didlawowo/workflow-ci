from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "policy_integrity.py"
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
        '"$(git -C .workflow-ci-policy rev-parse \'${{ job.workflow_sha }}^{commit}\')"'
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
