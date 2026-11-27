from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "policy_integrity.py"
)
WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "policy-integrity.yml"
)

SPEC = importlib.util.spec_from_file_location("policy_integrity", MODULE_PATH)
assert SPEC and SPEC.loader
policy_integrity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy_integrity)

FORK_PATH = ".ci/mutation-plan.sh"
INTEGRITY_STEP_NAME = "Enforce protected policy integrity"


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def violation_paths(result: dict) -> set[str]:
    return {violation["path"] for violation in result["violations"]}


def allowed_paths(result: dict) -> set[str]:
    return {allowed["path"] for allowed in result["allowed"]}


def integrity_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["policy-integrity"]["steps"]
    return next(step for step in steps if step.get("name") == INTEGRITY_STEP_NAME)


def test_fork_introducible_path_table_is_exact() -> None:
    assert policy_integrity.FORK_INTRODUCIBLE_PATHS == (FORK_PATH,)


def test_fork_can_add_mutation_plan_once(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(candidate, FORK_PATH, "#!/usr/bin/env bash\n")

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=True,
    )

    assert result["status"] == "pass"
    assert FORK_PATH in allowed_paths(result)
    assert result["violations"] == []


def test_non_fork_cannot_add_mutation_plan(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(candidate, FORK_PATH, "#!/usr/bin/env bash\n")

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=False,
    )

    assert result["status"] == "fail"
    assert FORK_PATH in violation_paths(result)


def test_missing_fork_argument_cannot_add_mutation_plan(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(candidate, FORK_PATH, "#!/usr/bin/env bash\n")

    result = policy_integrity.evaluate(base, candidate, "owner/repo")

    assert result["status"] == "fail"
    assert FORK_PATH in violation_paths(result)


def test_fork_cannot_modify_existing_mutation_plan(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(base, FORK_PATH, "base\n")
    write(candidate, FORK_PATH, "candidate\n")

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=True,
    )

    assert result["status"] == "fail"
    assert FORK_PATH in violation_paths(result)


def test_fork_cannot_remove_existing_mutation_plan(tmp_path: Path) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(base, FORK_PATH, "base\n")

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=True,
    )

    assert result["status"] == "fail"
    assert FORK_PATH in violation_paths(result)


@pytest.mark.parametrize("base_kind", ["folder", "broken-link"])
def test_fork_cannot_replace_existing_non_file_at_mutation_plan(
    tmp_path: Path,
    base_kind: str,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    base_path = base / FORK_PATH
    base_path.parent.mkdir(parents=True, exist_ok=True)

    if base_kind == "folder":
        base_path.mkdir()
    else:
        base_path.symlink_to(base_path.parent / "missing-target")

    write(candidate, FORK_PATH, "candidate\n")

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=True,
    )

    assert result["status"] == "fail"
    assert FORK_PATH in violation_paths(result)


def test_fork_mutation_plan_symlink_to_regular_inside_candidate_is_refused(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(candidate, ".ci/regular.txt", "regular\n")

    link = candidate / FORK_PATH
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("regular.txt")

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=True,
    )

    assert result["status"] == "fail"
    assert FORK_PATH in violation_paths(result)


def test_fork_mutation_plan_under_symlinked_ci_outside_candidate_is_refused(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    outside_ci = tmp_path / "outside-ci"
    outside_ci.mkdir()
    (outside_ci / "mutation-plan.sh").write_text("outside\n", encoding="utf-8")

    candidate.mkdir(parents=True, exist_ok=True)
    (candidate / ".ci").symlink_to(outside_ci)

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=True,
    )

    assert result["status"] == "fail"
    assert FORK_PATH in violation_paths(result)


@pytest.mark.parametrize(
    "protected_path",
    [
        ".ci/mutation-shard.sh",
        ".ci/mutation.sh",
    ],
)
def test_fork_cannot_add_other_protected_ci_scripts(
    tmp_path: Path,
    protected_path: str,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    write(candidate, protected_path, "candidate\n")

    result = policy_integrity.evaluate(
        base,
        candidate,
        "owner/repo",
        repository_is_fork=True,
    )

    assert result["status"] == "fail"
    assert protected_path in violation_paths(result)


def test_main_repository_fork_argument_controls_mutation_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    report = tmp_path / "report.json"
    base.mkdir()
    write(candidate, FORK_PATH, "#!/usr/bin/env bash\n")

    argv = [
        "policy_integrity.py",
        "--base",
        str(base),
        "--candidate",
        str(candidate),
        "--report",
        str(report),
        "--repository",
        "owner/repo",
    ]

    monkeypatch.setattr(sys, "argv", argv)
    assert policy_integrity.main() == 2

    monkeypatch.setattr(sys, "argv", argv + ["--repository-fork", "false"])
    assert policy_integrity.main() == 2

    monkeypatch.setattr(sys, "argv", argv + ["--repository-fork", "true"])
    assert policy_integrity.main() == 0


def test_main_rejects_invalid_repository_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    report = tmp_path / "report.json"
    base.mkdir()
    write(candidate, FORK_PATH, "#!/usr/bin/env bash\n")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "policy_integrity.py",
            "--base",
            str(base),
            "--candidate",
            str(candidate),
            "--report",
            str(report),
            "--repository",
            "owner/repo",
            "--repository-fork",
            "maybe",
        ],
    )

    with pytest.raises(SystemExit):
        policy_integrity.main()


def test_integrity_step_declares_fork_env() -> None:
    step = integrity_step()

    assert step["env"]["REPOSITORY_IS_FORK"] == "${{ github.event.repository.fork }}"


@pytest.mark.parametrize(
    ("fork_value", "expected_argument"),
    [
        ("true", "true"),
        ("false", "false"),
        ("", "false"),
        ("True", "false"),
        ("1", "false"),
    ],
)
def test_integrity_step_normalizes_fork_env_before_calling_script(
    tmp_path: Path,
    fork_value: str,
    expected_argument: str,
) -> None:
    step = integrity_step()
    script = step["run"].replace("${{ github.repository }}", "owner/repo")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_PYTHON_ARGS\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    args_file = tmp_path / "python-args.txt"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["RUNNER_TEMP"] = str(tmp_path)
    env["REPOSITORY_IS_FORK"] = fork_value
    env["FAKE_PYTHON_ARGS"] = str(args_file)

    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[args.index("--repository-fork") + 1] == expected_argument
