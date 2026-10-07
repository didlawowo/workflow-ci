from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_precommit_template_blocks_static_errors_and_tests_before_push() -> None:
    text = (ROOT / "templates/python/.pre-commit-config.yaml").read_text()
    assert "fail_fast: true" in text
    assert "uv run python -m py_compile" in text
    assert "uv run ruff check --force-exclude" in text
    assert "uv run ruff format --check --force-exclude" in text
    assert "uv run mypy ." in text
    assert "types: [python]" in text
    assert "src scripts tests" not in text
    assert "mypy src/" not in text
    assert "stages: [pre-push]" in text
    assert "uv run pytest -q --maxfail=1" in text


def test_node_and_go_precommit_templates_have_commit_and_push_gates() -> None:
    node = (ROOT / "templates/node/.pre-commit-config.yaml").read_text()
    go = (ROOT / "templates/go/.pre-commit-config.yaml").read_text()
    assert "npm run lint --if-present" in node
    assert "npm run format:check --if-present" in node
    assert "npm test --if-present" in node
    assert "gofmt -l ." in go
    assert "go vet ./..." in go
    assert "go test ./..." in go


def test_precommit_contract_action_runs_repository_configuration() -> None:
    text = (ROOT / ".github/actions/precommit-contract/action.yml").read_text()
    assert "pre-commit==4.3.0" in text
    assert ".pre-commit-config.yaml is missing" in text
    assert '--hook-stage "$HOOK_STAGE"' in text
    assert "args+=(--all-files)" in text
