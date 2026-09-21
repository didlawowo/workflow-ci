import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "ci@example.test")
    _git(path, "config", "user.name", "CI Test")


def test_arc_like_runner_needs_no_system_python3_or_python_venv(tmp_path: Path):
    consumer = tmp_path / "consumer"
    _init_repo(consumer)
    (consumer / ".ci").mkdir()
    shutil.copy2(ROOT / ".ci" / "mutation.sh", consumer / ".ci" / "mutation.sh")
    shutil.copy2(
        ROOT / ".ci" / "mutation_scope.py",
        consumer / ".ci" / "mutation_scope.py",
    )
    (consumer / "src").mkdir()
    (consumer / "src" / "service.py").write_text(
        "def compute(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    (consumer / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["src/"]\n',
        encoding="utf-8",
    )
    _git(consumer, "add", ".")
    _git(consumer, "commit", "-qm", "initial")
    sha = _git(consumer, "rev-parse", "HEAD")

    poison = tmp_path / "poison"
    poison.mkdir()
    python3 = poison / "python3"
    python3.write_text(
        "#!/usr/bin/env sh\necho system-python3-must-not-run >&2\nexit 97\n",
        encoding="utf-8",
    )
    python3.chmod(0o755)

    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["PATH"] = f"{poison}:{env['PATH']}"
    env["RUNNER_TEMP"] = str(tmp_path / "runner-temp")
    env["MUTATION_BASE_SHA"] = sha
    env["MUTATION_HEAD_SHA"] = sha

    completed = subprocess.run(
        ["bash", ".ci/mutation.sh"],
        cwd=consumer,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "system-python3-must-not-run" not in completed.stderr
    assert (consumer / ".quality" / "mutation-no-targets.json").is_file()


def test_plain_git_fetch_checkout_tolerates_orphan_gitlink(tmp_path: Path):
    source = tmp_path / "source"
    _init_repo(source)
    (source / "README.md").write_text("consumer\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-qm", "initial")
    target = _git(source, "rev-parse", "HEAD")

    _git(
        source,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{target},.workflow-ci",
    )
    _git(source, "commit", "-qm", "orphan gitlink")
    head = _git(source, "rev-parse", "HEAD")
    assert not (source / ".gitmodules").exists()

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    source_url = source.resolve().as_uri()
    _git(checkout, "fetch", "--no-tags", source_url, head)
    _git(checkout, "checkout", "-q", "--detach", head)

    staged = _git(checkout, "ls-files", "--stage")
    assert "160000 " in staged
    assert ".workflow-ci" in staged

    workflow = (ROOT / ".github" / "workflows" / "mutation-policy.yml").read_text()
    mutation_jobs = workflow.split("  mutation-run:", 1)[1]
    assert "actions/checkout@" not in mutation_jobs
    assert "Mutation checkout diagnostic: gitlink" in mutation_jobs
