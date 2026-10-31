from pathlib import Path

import re

ROOT = Path(__file__).resolve().parents[1]


def content(path):
    return (ROOT / path).read_text()


def version_default(text):
    block = re.search(r"(?m)^ +go-version:\n(?: +[^\n]*\n)*? +default: ([^\n]*)", text)
    assert block is not None
    return block[1].strip().strip("\"'")


def test_go_setup_uses_the_consumer_module_and_preserves_explicit_overrides():
    setup = content(".github/actions/setup-go-env/action.yml")
    assert version_default(setup) == ""
    assert "uses: actions/setup-go@" in setup
    assert "go-version: ${{ inputs.go-version }}" in setup
    assert "go-version-file: ${{ inputs.working-directory }}/go.mod" in setup
    assert "cache: false" in setup


def test_no_default_version_masks_the_module_in_go_callers():
    for name in ("run-go-tests", "go-quality-security"):
        action = content(f".github/actions/{name}/action.yml")
        assert version_default(action) == ""
        assert "uses: $/.github/actions/setup-go-env" in action
        assert "go-version: ${{ inputs.go-version }}" in action
        assert "working-directory: ${{ inputs.working-directory }}" in action
    for name in ("ci", "quality-evidence"):
        workflow = content(f".github/workflows/{name}.yml")
        assert version_default(workflow) == ""
