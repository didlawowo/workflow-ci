# Mutation belongs to Workflow CI

GitHub Manager owns repository resources, versioned wrappers and credentials/runner configuration, not mutation engines, classification, scope selection or result validation. Consumer project metadata (`tool.mutmut`, test dependencies) stays with the consumer. Tests must not weaken those settings to pass a migration.

## GitHub

`quality-evidence.yml` delegates to the central `mutation-policy.yml`; the issue-maintenance wrapper delegates to `mutation-issue-policy.yml`. Remove GitHub Manager's local runner and scope/result helpers before reconciling its new consumer wrappers. Existing protected-base custom scripts in other repositories remain supported by the existing compatibility path; they are not generated or distributed by the provisioner.

The central Python runner now installs the consumer's locked dependencies and declared development dependencies inside its isolated environment. It does not require a provisioner-specific `uv sync` script. Mutmut and Gremlins runners and the canonical classifier are owned here.

## Forgejo

The small consumer wrapper uses `https://github.com/didlawowo/workflow-ci/.forgejo/actions/mutation-policy@v1.8.1`. The composite calls the same canonical `mutation_policy.py`, `mutation_scope.py` and Python/Go engine scripts; there is no copied Forgejo policy implementation in GitHub Manager.

The central Forgejo adapter fetches exact base/head commits, verifies protected mutation configuration, computes a root-owned scope, and executes tests under UID/GID 65532 with an empty allowlisted environment and dropped capabilities. It verifies evidence separately as data, requires observations for every selected Python function (including methods), rejects unknown/non-killed outcomes and checks SHA-bound no-target evidence. It fails closed for unsupported languages rather than claiming an analysis ran.

Forgejo must run this action in a **disposable isolated root job container** with `setpriv`, no privileged mode, no Docker socket and no host filesystem/credential mounts. UID separation is not a replacement for container or network isolation. Provisioner-managed wrappers preserve that existing container model. Real Forgejo service integration remains a required rollout smoke test; unit tests do not claim to establish server compatibility.

## Rollout

Review Workflow CI #78 and GitHub Manager #80 together. Publish v1.8.1 from the reviewed engine commit, then merge/reconcile GitHub Manager. Do not move v1.8.0. Legacy Forgejo scripts already present in remote consumer repositories become unused when the wrapper is replaced; this change does not claim they were physically deleted by content synchronization.

The tests formerly covering GitHub Manager's mutation bootstrap move here. New behavioral evidence/sandbox tests and a real Mutmut consumer smoke test guard the replacement rather than lowering coverage or skipping a required gate.
