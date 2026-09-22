# Patch rollout after v1.8.0

This change is a patch candidate; it does not move or recreate the existing v1.8.0 tag.

## Issues #76 and #77: diagnosis and contract

The Keryx #413 run 35715799181 / job 106708231168 shows two separate events:

- golangci-lint v1.64.8 rejects the repository's v2 configuration at 10:34:03 UTC;
- SARIF upload reports Code Scanning unavailable, but already has a successful conclusion through continue-on-error.

The linter failure, not the upload outcome, sets quality-passed=false. Auto selection now chooses pinned v1/v2 releases from the configuration version, and the reusable workflow forwards an optional exact golangci-lint-version override. The v2 invocation omits the removed v1 --out-format flag.

GoSec must install, execute and produce valid local SARIF. Missing or invalid evidence is an error, never zero findings. Findings are counted across all SARIF runs. The optional Code Scanning upload defaults to false and cannot change the local gate; local SARIF remains an Actions artifact. Explicit direct-action upload users must enable Code Scanning and grant security-events: write. The reusable verifier deliberately remains read-only.

## Issue #75: exact source checkout

The central checkout-source action fetches full ancestry and checks out the exact supplied SHA (the prospective merge commit on pull_request). It does not call git submodule, execute repository hooks, or persist a token. An orphan gitlink is retained and diagnosed, not silently removed from version control. The read-only execution and publisher both use this action.

GitHub Manager's generated semantic-ref-policy job must also use this checkout action. Its rollout PR targets v1.8.1 and must not be applied before that tag exists. Existing consumer wrappers need regeneration or an equivalent reviewed update; changing only workflow-ci does not replace the caller's first checkout.

## Restore central policy ownership

The central mutation workflow and helpers were removed from main after v1.8.0. They are restored from the published release blobs. GitHub Manager must detach ownership of these RepositoryFile resources without deleting their GitHub files, and must not generate consumer wrappers over workflow-ci's implementation.

## Order

1. Merge the reviewed corrections, including GitHub Manager's ownership safeguard.
2. Publish an immutable patch tag (v1.8.1); do not rewrite v1.8.0.
3. Apply GitHub Manager's staged v1.8.1 wrapper rollout only after the tag exists.
4. Re-run consumer CI with their regenerated wrappers. Neither PR performs a live deployment or edits consumer branches automatically.
