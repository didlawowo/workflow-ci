from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


BEST_EFFORT_ARTIFACT_FILES = (
    ".github/actions/go-quality-security/action.yml",
    ".github/actions/run-go-tests/action.yml",
    ".github/actions/run-node-tests/action.yml",
    ".github/actions/run-python-tests/action.yml",
    ".github/actions/python-quality-security/action.yml",
    ".github/actions/trivy-filesystem-scan/action.yml",
    ".github/actions/docker-build-push/action.yml",
    ".github/actions/quality-report/action.yml",
    ".github/workflows/mutation-policy.yml",
    "templates/forgejo/mutation-policy.yml",
)


def _upload_blocks(text: str) -> list[str]:
    return re.findall(
        r"(?ms)^\s*- name: .*?\n(?:(?!^\s*- name: ).)*?uses: actions/upload-artifact@v6.*?(?=^\s*- name: |\Z)",
        text,
    )


def test_diagnostic_artifact_uploads_are_best_effort():
    for relative in BEST_EFFORT_ARTIFACT_FILES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        blocks = _upload_blocks(text)
        assert blocks, f"{relative}: expected at least one upload-artifact block"
        for block in blocks:
            assert "continue-on-error: true" in block, (
                f"{relative}: diagnostic artifact upload must not make CI red when "
                "Actions artifact storage is exhausted"
            )
