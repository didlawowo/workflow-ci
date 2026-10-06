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


def run_step(
    name: str,
    tmp_path: Path,
    content: str | None,
    extra_env: dict[str, str] | None = None,
):
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
        env={
            **os.environ,
            "SARIF_FILE": str(report),
            "GITHUB_OUTPUT": str(output),
            **(extra_env or {}),
        },
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



def test_docker_actions_use_node24_capable_majors():
    assert "docker/login-action@v4" in TEXT
    assert "docker/setup-qemu-action@v4" in TEXT
    assert "docker/setup-buildx-action@v4" in TEXT
    assert "docker/build-push-action@v7" in TEXT
    assert "docker/login-action@v3" not in TEXT
    assert "docker/setup-qemu-action@v3" not in TEXT
    assert "docker/setup-buildx-action@v3" not in TEXT
    assert "docker/build-push-action@v6" not in TEXT


def test_reusable_ci_propagates_native_auto_default():
    reusable = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert (
        'native-multiarch:\n        required: false\n        type: string\n        default: "auto"'
        in reusable
    )
    assert "native-multiarch: ${{ inputs.native-multiarch }}" in reusable


def test_arc_auto_prefers_native_buildkit_and_skips_qemu():
    resolve = STEPS["Resolve Docker build execution mode"]
    qemu = STEPS["Set up QEMU"]
    local = STEPS["Set up Docker Buildx"]
    remote = STEPS["Set up native multi-arch Buildx (remote BuildKit)"]

    assert "NATIVE_MULTIARCH: ${{ inputs.native-multiarch }}" in resolve
    assert "RUNNER_NAME: ${{ runner.name }}" in resolve
    assert '[ "$PUSH_IMAGE" = "true" ]' in resolve
    assert '[[ "$RUNNER_NAME" == arc-runner-* ]]' in resolve
    assert 'echo "use-native=$use_native"' in resolve
    assert "steps.execution-mode.outputs.use-native != 'true'" in qemu
    assert "steps.execution-mode.outputs.use-native != 'true'" in local
    assert "steps.execution-mode.outputs.use-native == 'true'" in remote
    assert 'default: "auto"' in TEXT


def test_explicit_false_keeps_portable_qemu_fallback():
    resolve = STEPS["Resolve Docker build execution mode"]
    qemu = STEPS["Set up QEMU"]

    assert 'case "$NATIVE_MULTIARCH" in' in resolve
    assert "auto|true|false" in resolve
    assert 'handler="qemu-aarch64"' in resolve
    assert 'handler="qemu-x86_64"' in resolve
    assert 'handler="qemu-arm"' in resolve
    assert 'handler_path="/proc/sys/fs/binfmt_misc/$handler"' in resolve
    assert "steps.execution-mode.outputs.needs-qemu == 'true'" in qemu
    assert "steps.execution-mode.outputs.qemu-preinstalled != 'true'" in qemu


def test_unknown_foreign_platform_keeps_qemu_fallback():
    resolve = STEPS["Resolve Docker build execution mode"]
    assert '*) handler="" ;;' in resolve
    assert '[ -z "$handler" ]' in resolve


def test_remote_buildkit_only_adds_requested_architectures():
    remote = STEPS["Set up native multi-arch Buildx (remote BuildKit)"]
    assert 'case "$target" in' in remote
    assert "linux/amd64)" in remote
    assert "linux/arm64)" in remote
    assert "BUILDKIT_AMD64_ENDPOINT" in remote
    assert "BUILDKIT_ARM64_ENDPOINT" in remote
    assert "Unsupported remote BuildKit platform" in remote




@pytest.mark.parametrize(
    "java_files,expected",
    [
        (("trivy-java.db", "metadata.json"), True),
        (("trivy-java.db",), False),
        (("metadata.json",), False),
        ((), False),
    ],
)
def test_shared_java_db_is_used_only_when_complete(tmp_path, java_files, expected):
    shared = tmp_path / "shared"
    (shared / "db").mkdir(parents=True)
    (shared / "db" / "trivy.db").write_text("db", encoding="utf-8")
    (shared / "db" / "metadata.json").write_text("{}", encoding="utf-8")
    (shared / "java-db").mkdir()
    for filename in java_files:
        (shared / "java-db" / filename).write_text("java", encoding="utf-8")

    runner_temp = tmp_path / "runner-temp"
    result, output, _ = run_step(
        "Resolve Trivy cache",
        tmp_path,
        None,
        extra_env={
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_WORKSPACE": str(tmp_path),
            "TRIVY_SHARED_DB_DIR": str(shared),
        },
    )

    assert result.returncode == 0, result.stderr
    local_java = runner_temp / "trivy-cache" / "java-db"
    assert ("shared-java-db=true" in output) is expected
    assert ("shared-java-db=false" in output) is (not expected)
    assert local_java.is_symlink() is expected



def test_filesystem_scan_java_db_and_artifact_contract():
    filesystem = (
        ROOT / ".github" / "actions" / "trivy-filesystem-scan" / "action.yml"
    ).read_text(encoding="utf-8")
    assert (
        'default: "false"'
        in filesystem.split("  upload-scan-artifacts:\n", 1)[1].split("\noutputs:", 1)[
            0
        ]
    )
    assert 'ln -s "$SHARED_DB/java-db" "$LOCAL_CACHE/java-db"' in filesystem
    assert '"$SHARED_DB/java-db/trivy-java.db"' in filesystem
    assert '"$SHARED_DB/java-db/metadata.json"' in filesystem
    assert "TRIVY_SKIP_JAVA_DB_UPDATE:" in filesystem
    assert "inputs.upload-scan-artifacts == 'true' && always()" in filesystem


def test_cosign_prefers_runner_binary_and_fallback_is_same_version():
    detect = STEPS["Detect preinstalled Cosign"]
    install = STEPS["Install Cosign when absent"]
    assert "v3.0.6" in detect
    assert "steps.cosign-runtime.outputs.preinstalled != 'true'" in install
    assert 'cosign-release: "v3.0.6"' in install

def test_build_action_has_no_nested_scan_or_publication_actions():
    assert "aquasecurity/setup-trivy@" not in TEXT
    assert "actions/github-script@" not in TEXT
    assert "github/codeql-action/" not in TEXT
    assert "actions/upload-artifact@" not in TEXT
    assert "scan=true requires a preinstalled Trivy" in TEXT
