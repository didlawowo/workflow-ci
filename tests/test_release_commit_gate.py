"""Regression tests for release-only conventional commit classification."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/release_commit_gate.py"
ACTION = ROOT / ".github/actions/git-cliff-bump/action.yml"

_spec = importlib.util.spec_from_file_location("release_commit_gate", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


@pytest.mark.parametrize(
    "message",
    [
        "test: ajouter une recette prod",
        "test(prod): ajouter une recette Office",
        "docs: corriger le README",
        "ci: ajuster un workflow",
        "chore: nettoyer les fixtures",
        "style: reformater le code",
        "message non conventionnel",
    ],
)
def test_non_releasing_commits_do_not_trigger_release(message):
    assert gate.message_requires_release(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "feat: ajouter un endpoint",
        "fix: corriger une régression",
        "perf: réduire la latence",
        "refactor: isoler le registry",
        "build: changer l'image de base",
        "revert: revenir sur une régression",
        "test!: casser volontairement le contrat\n\nBREAKING CHANGE: nouveau format",
        "docs: documenter le nouveau format\n\nBREAKING CHANGE: format incompatible",
    ],
)
def test_releasing_commits_still_trigger_release(message):
    assert gate.message_requires_release(message) is True


def test_only_test_commits_since_tag_do_not_trigger_release():
    messages = [
        "test(prod): ajouter la recette Office post-déploiement (#94)",
        "test(prod): déclencher la recette Office à sa promotion (#95)",
    ]
    assert gate.any_message_requires_release(messages) is False


def test_mixed_test_and_fix_commits_still_trigger_patch_release():
    messages = [
        "test(prod): ajouter une recette",
        "fix(release): corriger la publication",
    ]
    assert gate.any_message_requires_release(messages) is True


def test_multiline_record_separator_preserves_breaking_footer():
    raw = (
        "test: scénario sans release\x1e"
        "docs: migration\n\nBREAKING CHANGE: format incompatible\x1e"
    )
    messages = gate._messages_from_stdin(raw)
    assert len(messages) == 2
    assert gate.any_message_requires_release(messages) is True


def test_action_gates_git_cliff_bump_before_computing_version():
    steps = yaml.safe_load(ACTION.read_text())["runs"]["steps"]
    scope = next(step for step in steps if step.get("id") == "release-scope")
    compute = next(step for step in steps if step.get("name") == "Compute next version")
    decide = next(step for step in steps if step.get("id") == "decide")

    assert "git log" in scope["run"]
    assert "release_commit_gate.py" in scope["run"]
    assert compute["if"] == "steps.release-scope.outputs.releasable == 'true'"
    assert "-f /tmp/next-version.txt" in decide["run"]
