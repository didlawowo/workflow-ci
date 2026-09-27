# Docker Build & Push: required scan evidence

With `scan: true` (the existing default), a successful action requires:

1. A successful Trivy process. A layer-extraction `unexpected EOF` is treated as a recognized transient failure and gets exactly one retry; other scanner failures are not retried.
2. A non-empty, parseable SARIF 2.1.0 report with at least one run and explicit result arrays. The counter covers all runs, rejects failed invocations, and never substitutes zero for missing or invalid evidence.
3. GitHub Code Scanning publication only when `upload-sarif: true`.
4. Raw SARIF artifact archival only when `upload-scan-artifacts: true`.

Both publication paths are **disabled by default**. Trivy execution and local SARIF validation remain hard gates.

On ARC runners, `TRIVY_SHARED_DB_DIR` may expose both `db/` and `java-db/`. Each database is detected independently. A complete shared Java DB enables `TRIVY_SKIP_JAVA_DB_UPDATE=true`; an absent or partial Java DB deliberately keeps the normal network fallback instead of pretending the cache is usable.

Raw SARIF artifacts are diagnostic only and may contain security-sensitive findings. Enable `upload-scan-artifacts` only when the retained file is actually needed.

## Optional Code Scanning publication

Code Scanning is off by default. Only jobs that explicitly set `upload-sarif: true` need these permissions:

```yaml
permissions:
  contents: read
  actions: read
  security-events: write
```

The composite cannot grant itself permissions. Keep unrelated resolution and promotion jobs read-only. Do not add a broad PAT to bypass an insufficient `GITHUB_TOKEN`.

**Permissions are necessary but not sufficient.** GitHub Code Scanning must also be available and enabled for the repository. GitHub documents availability for public repositories and eligible organization-owned repositories with GitHub Code Security enabled. A private personal repository is not made eligible merely by adding `security-events: write`. Verify this prerequisite before migrating; an unavailable Code Scanning backend will now correctly block the action rather than silently fail.

References:

- https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file
- https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#steps-context

## Scope of the fix

This patch addresses **execution and evidence integrity**, not vulnerability acceptance policy. It does not introduce or change a CVE severity/count threshold, Trivy's vulnerability exit-code setting, ignore lists, signing options, or the existing `scan` input. A successful report with findings remains a report with findings, not proof of a vulnerability-free image.

The Docker Hub authentication fallback and best-effort BuildKit cache export are unchanged: these are independent optimizations, not mandatory scan evidence.

The existing build/push happens before the scan. Therefore an error may leave the versioned image in the registry. Consumers must keep production promotion dependent on the successful image jobs. In Ioniq Control, the separate promotion job updates `latest` and the GitOps digests only after both image jobs succeed. This patch does not add cluster rollout validation.

## Release and validation

Related incident: https://github.com/didlawowo/ioniq-control/actions/runs/35753262141

Merge and validate this change before creating a new version tag containing it. The companion Ioniq Control PR targets `v1.8.1`; no tag is created or moved by this change. Other consumers with scanning enabled also need the permissions and repository eligibility above when upgrading.

Run the isolated regressions (requires Bash, jq and pytest):

```sh
python -m pytest tests/test_docker_scan_contract.py -q
```

These tests execute the actual shell extracted from `action.yml` and verify the workflow contract. They do not simulate a real GitHub Code Scanning upload or an OCI production publish. Complete that integration validation with an eligible repository and the released action before declaring the migration operational.


## Build bootstrap performance

`native-multiarch` defaults to `auto`.

- On ARC runners (`runner.name` starts with `arc-runner-`), published
  linux/amd64 and linux/arm64 builds use the persistent native remote BuildKit
  workers. No QEMU emulation and no per-job binfmt image pull are used. Local
  non-push validation keeps the local builder and does not need multi-arch QEMU.
- `native-multiarch: true` still forces the native remote workers.
- `native-multiarch: false` keeps the portable docker-container/QEMU fallback
  for runners that cannot reach the in-cluster BuildKit services.
- Non-ARC runners in `auto` keep the portable fallback.

The repository controls which architectures are built through `platforms`
(`linux/amd64`, `linux/arm64`, or both).
