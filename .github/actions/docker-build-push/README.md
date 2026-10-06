# Docker Build & Push

This composite builds and optionally pushes OCI images. **OCI vulnerability scanning is intentionally not part of this build action.**

The goal is to keep image publication fast and deterministic: build, push, signing, SBOM/provenance and registry-cache handling stay in the critical path; Trivy image scanning does not.

## OCI vulnerability scanning

workflow-ci currently performs **no OCI image vulnerability gate**. Registry-side scanning can be enabled independently later, but builds and releases must not claim that such a gate exists while it is disabled operationally.

Filesystem/security scanning remains separate and unchanged.

## Build bootstrap performance

`native-multiarch` defaults to `auto`.

- On ARC runners (`runner.name` starts with `arc-runner-`), published linux/amd64 and linux/arm64 builds use the persistent native remote BuildKit workers. No QEMU emulation and no per-job binfmt image pull are used. Local non-push validation keeps the local builder and does not need multi-arch QEMU.
- `native-multiarch: true` forces the native remote workers.
- `native-multiarch: false` keeps the portable docker-container/QEMU fallback for runners that cannot reach the in-cluster BuildKit services.
- Non-ARC runners in `auto` keep the portable fallback.

The repository controls which architectures are built through `platforms` (`linux/amd64`, `linux/arm64`, or both).
