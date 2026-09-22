# Docker Build & Push: required scan evidence

With `scan: true` (the existing default), a successful action now requires:

1. A successful Trivy process.
2. A non-empty, parseable SARIF 2.1.0 report with at least one run and explicit result arrays. The counter covers all runs, rejects failed invocations, and never substitutes zero for missing or invalid evidence.
3. A successful upload to GitHub Code Scanning.
4. Successful archival of the SARIF report as a workflow artifact.

The previous `continue-on-error: true` on `upload-sarif` is removed. A permission, service, schema or repository-eligibility error remains a job failure. A fresh report is required: the previous file is removed before starting Trivy.

Artifacts are still attempted after scanner, validation or Code Scanning failures, unless the workflow was cancelled. Archival never changes the earlier failure into success. Missing artifacts are errors, not warnings. Artifact names and the seven-day retention period are unchanged. Reports can contain security-sensitive findings; do not publish their raw content in public logs.

## Consumer permissions and repository eligibility

Grant these permissions to each job that calls this composite:

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
