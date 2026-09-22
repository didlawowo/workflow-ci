"""The central implementation must survive provisioning of consumer wrappers."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_same_repository_actions_and_workflows_exist():
    for folder in (ROOT / '.github/actions', ROOT / '.github/workflows'):
        for source in folder.rglob('*.yml'):
            for relative in re.findall(r'^\s*uses:\s*\$/([^\s#]+)', source.read_text(), re.M):
                target = ROOT / relative
                assert target.is_file() or (target / 'action.yml').is_file(), (source, relative)


def test_central_mutation_helpers_cannot_be_removed_as_consumer_legacy():
    for relative in ('.github/workflows/mutation-policy.yml',
                     '.github/scripts/mutation_policy.py',
                     '.github/scripts/mutation_bootstrap.sh'):
        assert (ROOT / relative).is_file(), relative


def test_execution_and_publisher_use_gitlink_safe_checkout():
    workflow = (ROOT / '.github/workflows/quality-evidence.yml').read_text()
    assert workflow.count('uses: $/.github/actions/checkout-source') == 2
    assert 'uses: actions/checkout@' not in workflow
    assert 'golangci-lint-version: ${{ inputs.golangci-lint-version }}' in workflow
    assert 'upload-sarif: "false"' in workflow
