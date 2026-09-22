"""Real engine + UID sandbox smoke; only the network fetch is replaced locally."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
import forgejo_mutation as adapter


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def main():
    assert os.geteuid() == 0, "This test exercises the real root-to-65532 boundary"
    with tempfile.TemporaryDirectory(prefix="forgejo-fixture-") as temp:
        source = Path(temp) / "consumer"
        source.mkdir()
        (source / "src").mkdir()
        (source / "tests").mkdir()
        (source / "pyproject.toml").write_text('''[project]
name = "forgejo-central-smoke"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = ["packaging>=24"]
[dependency-groups]
dev = ["pytest>=8,<9", "pytest-cov>=6"]
[tool.uv]
package = false
[tool.mutmut]
source_paths = ["src/"]
pytest_add_cli_args_test_selection = ["tests/"]
''')
        (source / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")
        (source / "tests/test_calc.py").write_text('''import os
from pathlib import Path
import sys
import pytest
from packaging.version import Version
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from calc import add


def test_real_engine_and_sandbox():
    assert add(1, 1) == 3
    assert add(0, 0) == 1
    assert add(-1, 2) == 2
    assert Version("1.0") < Version("2.0")
    assert os.geteuid() == 65532
    assert os.getegid() == 65532
    for name in ("POLICY_TOKEN", "FORGEJO_TOKEN", "GITHUB_TOKEN", "GITHUB_OUTPUT"):
        assert name not in os.environ
    root = next(parent for parent in Path(__file__).resolve().parents
                if parent.name.startswith("workflow-ci-forgejo-"))
    with pytest.raises(PermissionError):
        (root / "state.json").read_text()
    with pytest.raises(PermissionError):
        (root / "engine/mutation.sh").write_text("exit 0")
''')
        subprocess.run(["uv", "lock", "--python", "3.12"], cwd=source, check=True)
        git(source, "init", "-q")
        git(source, "config", "user.email", "ci@example.test")
        git(source, "config", "user.name", "Sandbox test")
        git(source, "add", ".")
        git(source, "commit", "-qm", "base")
        base = git(source, "rev-parse", "HEAD")
        (source / "src/calc.py").write_text("def add(a, b):\n    return a + b + 1\n")
        git(source, "add", "src/calc.py")
        git(source, "commit", "-qm", "head")
        head = git(source, "rev-parse", "HEAD")

        # Avoid a live forge dependency; classification, config, scope, privilege
        # drop, Mutmut, evidence verification and cleanup are all real code.
        def fetch_local(destination, server, repository, sha, token):
            assert server == "https://forgejo.example"
            assert repository == "test/consumer"
            assert token == "credential-canary"
            shutil.copytree(source, destination, dirs_exist_ok=True)
            git(destination, "checkout", "--detach", "--force", sha)

        adapter.fetch = fetch_local
        output = Path(temp) / "outputs"
        os.environ.update(POLICY_PROVIDER="forgejo",
                          POLICY_API_URL="https://forgejo.example/api/v1",
                          MUTATION_SERVER_URL="https://forgejo.example",
                          POLICY_REPOSITORY="test/consumer",
                          POLICY_TOKEN="credential-canary", FORGEJO_TOKEN="credential-canary",
                          GITHUB_TOKEN="credential-canary", GITHUB_OUTPUT=str(output))
        event = {"pull_request": {"number": 1, "user": {"login": "tester"},
                 "labels": [{"name": "priority:high"}], "body": "",
                 "base": {"sha": base}, "head": {"sha": head, "repo": {"full_name": "test/consumer"}}}}
        adapter.prepare(event)
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert values["required"] == "true"
        state = Path(values["state"])
        sandbox = state.parent
        try:
            plan = json.loads(state.read_text())
            assert plan["engine"] == "python" and plan["targets"]
            bindir = sandbox / "engine/bin"
            bindir.mkdir(mode=0o755)
            shutil.copy2(shutil.which("uv"), bindir / "uv")
            (bindir / "uv").chmod(0o755)
            os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
            adapter.execute(state)
            assert not sandbox.exists(), "The disposable sandbox must be cleaned"
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
    print("Forgejo adapter smoke passed with real Mutmut, UID 65532 and protected policy")


if __name__ == "__main__":
    main()
