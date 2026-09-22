# Mutation runners

Mutation execution uses `UNTRUSTED_RUNNER` when explicitly configured,
otherwise the repository's existing `RUNNER`, then the conventional `arc-runner-<repository>` scale set.
Verification keeps its separate `trusted-runner` override and otherwise
uses `RUNNER`, then the conventional `arc-runner-<repository>` scale set. Issue-policy jobs follow the same
self-hosted fallback while preserving their explicit `runner` override.

The default follows the existing ARC naming convention, for example
`arc-runner-photo-analyser`. An existing `RUNNER` takes precedence;
set it for a nonstandard scale-set name. No extra variable or GitHub
Manager deployment is required for the existing conventional runners.

This changes scheduling, not trust: PR execution remains read-only with
a sanitized environment. Untrusted contributions require an appropriately
isolated, ephemeral execution runner; environment cleanup is not a VM
or network security boundary. Use `UNTRUSTED_RUNNER` for that dedicated
runner when needed. No mutation threshold or evidence check is relaxed.

Existing published tags are unchanged. Consumers pinned to an older tag
only receive the fix through a maintainer-published release containing it.
