"""Docker build contracts.

OCI image vulnerability scanning is deliberately outside the build critical path.
The registry-side scanner may be enabled independently; workflow-ci must not pretend
that an OCI vulnerability gate exists when it is disabled operationally.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/docker-build-push/action.yml"
TEXT = ACTION.read_text(encoding="utf-8")
STEPS = {
    block.splitlines()[0].removeprefix("    - name: "): block
    for block in re.split(r"(?=^    - name: )", TEXT, flags=re.MULTILINE)[1:]
}

def test_native_remote_buildkit_retries_once_and_then_fails_closed():
    primary = STEPS["Build and push Docker image"]
    retry = STEPS["Retry native remote BuildKit once"]
    enforce = STEPS["Enforce native Docker build result"]

    assert (
        "continue-on-error: ${{ steps.execution-mode.outputs.use-native == 'true' }}"
        in primary
    )
    assert "steps.build.outcome == 'failure'" in retry
    assert "builder: native" in retry
    assert "steps.build-retry.outcome != 'success'" in enforce
    assert "failure()" in enforce
    assert "failed twice" in enforce
    assert "steps.build.outputs.digest || steps.build-retry.outputs.digest" in TEXT



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

def test_build_action_has_no_oci_scan_surface():
    forbidden = (
        "Trivy",
        "trivy",
        "inputs.scan",
        "scan-severity",
        "scan-results",
        "vulnerabilities-found",
        "sarif-file",
        "docker-scan-",
    )
    for token in forbidden:
        assert token not in TEXT

