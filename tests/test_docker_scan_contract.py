"""Exercise the real composite shell, without Docker or network access."""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/docker-build-push/action.yml"
TEXT = ACTION.read_text(encoding="utf-8")
# Read only the composite's uniformly indented named steps. No YAML dependency
# is needed in the repository's existing pytest-only self-test environment.
STEPS = {
    block.splitlines()[0].removeprefix("    - name: "): block
    for block in re.split(r"(?=^    - name: )", TEXT, flags=re.MULTILINE)[1:]
}


def shell_for(name: str) -> str:
    block = STEPS[name]
    match = re.search(r"^      run: \|\n((?:        .*\n|\n)+)", block, re.MULTILINE)
    assert match is not None, f"No shell found in {name}"
    return textwrap.dedent(match.group(1))


def run_step(name: str, tmp_path: Path, content: str | None):
    report = tmp_path / "scan report.sarif"
    output = tmp_path / "outputs"
    output.write_text("", encoding="utf-8")
    if content is not None:
        report.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-e",
            "-o",
            "pipefail",
            "-c",
            shell_for(name),
        ],
        cwd=tmp_path,
        env={**os.environ, "SARIF_FILE": str(report), "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result, output.read_text(encoding="utf-8"), report


def sarif(*counts: int) -> dict:
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "Trivy"}},
                "results": [
                    {
                        "ruleId": f"TEST-{index}",
                        "message": {"text": "Synthetic finding"},
                    }
                    for index in range(count)
                ],
            }
            for count in counts
        ],
    }


@pytest.mark.parametrize("counts,expected", [((0,), 0), ((2,), 2), ((0, 3, 2), 5)])
def test_counts_only_valid_reports(tmp_path, counts, expected):
    result, output, _ = run_step(
        "Analyze scan results", tmp_path, json.dumps(sarif(*counts))
    )
    assert result.returncode == 0, result.stderr
    assert output == f"vuln-count={expected}\n"


@pytest.mark.parametrize(
    "content",
    [None, "", " ", "{", "null", "[]", "{}", "{}\n{}", "false", "42"],
    ids=[
        "missing",
        "empty",
        "whitespace",
        "truncated",
        "null",
        "array",
        "object",
        "stream",
        "bool",
        "number",
    ],
)
def test_unavailable_report_never_means_zero(tmp_path, content):
    result, output, _ = run_step("Analyze scan results", tmp_path, content)
    assert result.returncode != 0
    assert "::error" in result.stdout
    assert output == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"version": "2.0.0", "runs": sarif(0)["runs"]},
        {"version": "2.1.0", "runs": []},
        {"version": "2.1.0", "runs": None},
        {"version": "2.1.0", "runs": {}},
        {"version": "2.1.0", "runs": [None]},
        {"version": "2.1.0", "runs": [{"results": []}]},
        {
            "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": ""}}, "results": []}],
        },
        {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Trivy"}}}]},
        {"version": "2.1.0", "runs": [{**sarif(0)["runs"][0], "results": None}]},
        {"version": "2.1.0", "runs": [{**sarif(0)["runs"][0], "results": {}}]},
        {"version": "2.1.0", "runs": [{**sarif(0)["runs"][0], "results": [None]}]},
        {"version": "2.1.0", "runs": [sarif(0)["runs"][0], {}]},
        {
            "version": "2.1.0",
            "runs": [
                {
                    **sarif(0)["runs"][0],
                    "invocations": [{"executionSuccessful": False}],
                }
            ],
        },
        {
            "version": "2.1.0",
            "runs": [{**sarif(0)["runs"][0], "invocations": [{}]}],
        },
        {
            "version": "2.1.0",
            "runs": [{**sarif(0)["runs"][0], "invocations": None}],
        },
    ],
)
def test_incomplete_analysis_never_means_zero(tmp_path, payload):
    result, output, _ = run_step("Analyze scan results", tmp_path, json.dumps(payload))
    assert result.returncode != 0
    assert "::error" in result.stdout
    assert output == ""


def test_valid_successful_invocation(tmp_path):
    payload = sarif(0)
    payload["runs"][0]["invocations"] = [{"executionSuccessful": True}]
    result, output, _ = run_step("Analyze scan results", tmp_path, json.dumps(payload))
    assert result.returncode == 0, result.stderr
    assert output == "vuln-count=0\n"


@pytest.mark.parametrize("content", [None, json.dumps(sarif(0))])
def test_prepare_removes_stale_report_without_error_on_first_use(tmp_path, content):
    result, output, report = run_step("Prepare Trivy report", tmp_path, content)
    assert result.returncode == 0, result.stderr
    assert not report.exists()
    assert output == ""


@pytest.mark.parametrize(
    "name",
    [
        "Prepare Trivy report",
        "Run Trivy vulnerability scanner",
        "Analyze scan results",
    ],
)
def test_required_scan_steps_cannot_swallow_failures(name):
    assert "continue-on-error:" not in STEPS[name]


def test_diagnostic_scan_artifact_is_best_effort():
    assert "continue-on-error: true" in STEPS["Upload scan artifacts"]


def test_native_remote_buildkit_retries_once_and_then_fails_closed():
    primary = STEPS["Build and push Docker image"]
    retry = STEPS["Retry native remote BuildKit once"]
    enforce = STEPS["Enforce native Docker build result"]

    assert "continue-on-error: ${{ inputs.native-multiarch == 'true' }}" in primary
    assert "steps.build.outcome == 'failure'" in retry
    assert "builder: native" in retry
    assert "steps.build-retry.outcome != 'success'" in enforce
    assert "failure()" in enforce
    assert "failed twice" in enforce
    assert "steps.build.outputs.digest || steps.build-retry.outputs.digest" in TEXT


@pytest.mark.parametrize(
    "name",
    [
        "Prepare Trivy report",
        "Run Trivy vulnerability scanner",
        "Analyze scan results",
    ],
)
def test_scan_steps_are_gated_but_not_always_successful(name):
    assert "      if: inputs.scan == 'true'\n" in STEPS[name]
    assert "always()" not in STEPS[name]


def test_code_scanning_publication_is_disabled_by_default_and_opt_in():
    upload_input = TEXT.split("  upload-sarif:\n", 1)[1].split("  scan-severity:\n", 1)[0]
    assert '    default: "false"' in upload_input

    upload = STEPS["Upload Trivy scan results"]
    assert "      id: upload-sarif\n" in upload
    assert "inputs.scan == 'true' && inputs.upload-sarif == 'true'" in upload
    assert "      continue-on-error: true\n" in upload

    warning = STEPS["Warn when SARIF publication failed"]
    assert "inputs.upload-sarif == 'true'" in warning
    assert "steps.upload-sarif.outcome == 'failure'" in warning
    assert "::warning title=Code Scanning upload failed::" in warning


def test_validate_before_upload_and_keep_evidence_after_failure():
    order = list(STEPS)
    expected = [
        "Prepare Trivy report",
        "Run Trivy vulnerability scanner",
        "Analyze scan results",
        "Upload Trivy scan results",
        "Upload scan artifacts",
    ]
    assert [name for name in order if name in expected] == expected
    assert "      id: trivy\n" in STEPS["Run Trivy vulnerability scanner"]
    archive = STEPS["Upload scan artifacts"]
    condition = (
        "if: ${{ !cancelled() && inputs.scan == 'true' && "
        "(steps.trivy.outcome == 'success' || steps.trivy.outcome == 'failure') }}"
    )
    assert condition in archive
    assert "        if-no-files-found: error\n" in archive
    assert "        retention-days: 7\n" in archive


def test_vulnerability_policy_and_non_security_fallbacks_are_unchanged():
    scan_input = TEXT.split("  scan:\n", 1)[1].split("  scan-severity:\n", 1)[0]
    assert '    default: "true"' in scan_input
    assert "exit-code:" not in STEPS["Run Trivy vulnerability scanner"]
    hub_login = STEPS["Login to Docker Hub (authenticated base image pulls)"]
    assert "continue-on-error: true" in hub_login
    assert "ignore-error=true" in STEPS["Build and push Docker image"]


@pytest.mark.parametrize(
    "template",
    [
        "templates/go/ci-branch-pipeline.yml",
        "templates/go/cd-production.yml",
        "templates/node/ci-branch-pipeline.yml",
        "templates/python/ci-branch-pipeline.yml",
        "templates/python/cd-production.yml",
    ],
)
def test_templates_grant_actions_read_when_they_publish_sarif(template):
    text = (ROOT / template).read_text(encoding="utf-8")
    assert "docker-build-push@" in text
    assert "actions: read" in text


def test_trivy_summary_is_written_to_step_summary_and_pr_comment_is_best_effort():
    summary = STEPS["Build Trivy Markdown summary"]
    assert "GITHUB_STEP_SUMMARY" in summary
    assert "Top findings" in summary
    assert "Showing 20 of" in summary
    assert "workflow-ci:trivy-report:" in summary

    comment = STEPS["Publish Trivy summary on pull request"]
    assert "continue-on-error: true" in comment
    assert "github.event_name == 'pull_request'" in comment
    assert "issues.listComments" in comment
    assert "issues.updateComment" in comment
    assert "issues.createComment" in comment

    warning = STEPS["Warn when PR scan comment failed"]
    assert "steps.trivy-pr-comment.outcome == 'failure'" in warning
    assert "::warning title=Trivy PR comment failed::" in warning


@pytest.mark.parametrize(
    "template",
    [
        "templates/go/ci-branch-pipeline.yml",
        "templates/go/cd-production.yml",
        "templates/node/ci-branch-pipeline.yml",
        "templates/python/ci-branch-pipeline.yml",
        "templates/python/cd-production.yml",
    ],
)
def test_templates_allow_best_effort_trivy_pr_comment(template):
    text = (ROOT / template).read_text(encoding="utf-8")
    assert "pull-requests: write" in text


def test_docker_actions_use_node24_capable_majors():
    assert "docker/login-action@v4" in TEXT
    assert "docker/setup-qemu-action@v4" in TEXT
    assert "docker/setup-buildx-action@v4" in TEXT
    assert "docker/build-push-action@v7" in TEXT
    assert "docker/login-action@v3" not in TEXT
    assert "docker/setup-qemu-action@v3" not in TEXT
    assert "docker/setup-buildx-action@v3" not in TEXT
    assert "docker/build-push-action@v6" not in TEXT


def test_qemu_is_skipped_for_native_single_arch_builds():
    detect = STEPS["Detect whether QEMU is required"]
    assert 'native_platform="linux/amd64"' in detect
    assert 'native_platform="linux/arm64"' in detect
    assert 'needs_qemu=false' in detect

    qemu = STEPS["Set up QEMU"]
    assert "steps.execution-mode.outputs.needs-qemu == 'true'" in qemu


def test_remote_buildkit_only_adds_requested_architectures():
    remote = STEPS["Set up native multi-arch Buildx (remote BuildKit)"]
    assert 'case "$target" in' in remote
    assert "linux/amd64)" in remote
    assert "linux/arm64)" in remote
    assert "BUILDKIT_AMD64_ENDPOINT" in remote
    assert "BUILDKIT_ARM64_ENDPOINT" in remote
    assert "Unsupported remote BuildKit platform" in remote


def test_trivy_prefers_preinstalled_binary_with_portable_fallback():
    detect = STEPS["Detect preinstalled Trivy"]
    scan = STEPS["Run Trivy vulnerability scanner"]
    assert "Version: 0.70.0" in detect
    assert "aquasecurity/trivy-action@v0.36.0" in scan
    assert "skip-setup-trivy:" in scan
    assert "aquasecurity/trivy-action@master" not in TEXT

    filesystem = (
        ROOT / ".github" / "actions" / "trivy-filesystem-scan" / "action.yml"
    ).read_text(encoding="utf-8")
    assert filesystem.count("aquasecurity/trivy-action@v0.36.0") == 4
    assert filesystem.count("skip-setup-trivy:") == 4
    assert filesystem.count('timeout: "15m"') == 4
    assert filesystem.count('TRIVY_SKIP_VERSION_CHECK: "true"') == 4
    assert 'scanners: "secret"' not in filesystem
    assert 'scanners: "vuln,secret"' not in filesystem
    assert 'TRIVY_CACHE_BACKEND: "memory"' in filesystem
    assert "TRIVY_SKIP_DB_UPDATE:" in filesystem
    assert "TRIVY_SHARED_DB_DIR" in filesystem
    assert "TRIVY_SHARED_CACHE" not in filesystem
    assert "aquasecurity/trivy-action@master" not in filesystem


def test_trivy_image_scan_uses_shared_db_with_memory_scan_cache():
    scan = STEPS["Run Trivy vulnerability scanner"]
    cache = STEPS["Resolve Trivy cache"]

    assert 'timeout: "15m"' in scan
    assert 'scanners: "vuln"' in scan
    assert 'TRIVY_SKIP_VERSION_CHECK: "true"' in scan
    assert 'TRIVY_CACHE_BACKEND: "memory"' in scan
    assert "TRIVY_SKIP_DB_UPDATE:" in scan
    assert 'TRIVY_SHARED_DB_DIR' in cache
    assert 'ln -s "$SHARED_DB/db" "$LOCAL_CACHE/db"' in cache
    assert 'action-cache=false' in cache
    assert 'action-cache=true' in cache
    assert 'TRIVY_SHARED_CACHE' not in cache
    assert 'test -w "$TRIVY_CACHE_DIR"' not in cache



def test_cosign_prefers_runner_binary_and_fallback_is_same_version():
    detect = STEPS["Detect preinstalled Cosign"]
    install = STEPS["Install Cosign when absent"]
    assert "v3.0.6" in detect
    assert "steps.cosign-runtime.outputs.preinstalled != 'true'" in install
    assert 'cosign-release: "v3.0.6"' in install
