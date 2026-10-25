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

## ARC runner image contract

The workflow fast paths target the ARC worker image declared by `.arc-runner-version`.
For this release the contract is **v1.3.3**. The image deployment itself is owned by
`continuous-delivery`; workflow-ci only declares the expected toolchain and keeps
portable fallbacks for tools such as Trivy, Cosign and TruffleHog.

Do not update the cluster runner image implicitly from this repository. Validate the
workflow-ci release first, then update or roll back the ARC image in continuous-delivery.

## Protected project and evidence contract

The central resolver supports Python/Mutmut, Go/Gremlins and Node/Stryker.
`working-directory` is resolved inside the candidate checkout and applied to
both candidate configuration and protected-base configuration. Absolute paths
and symlink escapes are rejected. Python stages standalone pytest configuration
and the root conftest in the disposable `mutants/` tree; diagnostics come from
native `.meta` records, including killed mutants and Unicode method identifiers.

Execution depth and verdict scope retain their distinct existing meanings:
`high` broadens engine execution to touched modules; the ordinary PR verifier
checks the changed functions using the base project's source paths. A complete
sharded campaign, described below, additionally checks every expected mutant,
including mutants outside those changed functions. A missing diagnostic, stale
identity, inconsistent counter or unresolved selected mutant is always blocking.

Runner proxy settings are inherited through an explicit credential-free allowlist.
The central executor drops forge tokens, action command files and persistent HOME.
It does not choose an endpoint or add a fallback registry. Scratch is disposable.
An authenticated proxy URL cannot be passed to candidate code; infrastructure
must provide an endpoint usable by the isolated runner without exposing a secret.

### Individually reviewed equivalent survivors

A survivor remains `survived` in raw counts and in the published score. Acceptance
requires an entry in the **protected base** `.ci/mutation-equivalents.json`, keyed
by the native mutant ID, with `source`, `source_sha256`, `mutant_sha256` and a
nonempty `reason`. A candidate cannot approve its own ledger entry.

The execution runner must export `.quality/mutation-v2-review.json`:

```json
{
  "base_sha": "exact-base-sha",
  "head_sha": "exact-head-sha",
  "reviewed_equivalents": ["package.module.x_function__mutmut_1"],
  "mutants": {
    "package.module.x_function__mutmut_1": {
      "source": "src/package/module.py",
      "source_sha256": "sha256-of-exact-candidate-source-bytes",
      "mutant_sha256": "sha256-of-normalized-mutant-function",
      "mutant_source": "def x_function__mutmut_1(value):\n    return value\n"
    }
  }
}
```

Source paths are relative to the selected project. The verifier recomputes the
source hash and parses the supplied mutant as data. For the mutant digest it
renames the one identified function to `reviewed_function`, serializes it with
`ast.unparse`, and hashes the UTF-8 result. It compares both digests and the source
path to the protected ledger. Missing, extra, duplicate, stale or altered proofs
fail. No consumer review hook runs in the verification job. An older report that
only lists reviewed IDs is intentionally insufficient; the consumer runner must
first export the auditable function evidence and exact head SHA.

### Complete campaigns with bounded workers

Set `mutation-shards` to a number from 2 to 16 on `quality-evidence.yml`, `ci.yml`
or `mutation-policy.yml` to use `mutation-campaign.yml`. The default remains one
ordinary mutation job. The campaign uses at most four workers concurrently;
planning and each worker are bounded to 45 minutes, verification to ten minutes.

This is a protected-runner protocol, not an automatic port of a consumer's native
mutation engine. Install two reviewed entrypoints on the protected base before
enabling the campaign:

- `.ci/mutation-plan.sh <output-json>` generates the **entire expected native
  mutant inventory**, without rewriting candidate sources or adding suppression
  pragmas. It receives the current base/head SHAs, depth and shard count.
- `.ci/mutation-shard.sh <output-json>` reads `MUTATION_PLAN`, executes exactly the
  partition selected by `MUTATION_SHARD_INDEX` and `MUTATION_SHARD_COUNT`, and exports
  all individual statuses and raw counts. Nonzero execution exits fail the job.

Entrypoints run from the candidate project, but their executable and supporting
code must be resolved from the protected script's directory. No forge credentials
or action output files are passed to them. They write only their ordinary scratch
output argument. Preparation and verification use separate exact SHA checkouts.

The plan schema is:

```json
{
  "schema_version": 1,
  "base_sha": "exact-base-sha",
  "head_sha": "exact-head-sha",
  "working_directory": ".",
  "shard_count": 2,
  "sources": {"src/package/module.py": "sha256-of-source-bytes"},
  "mutants": {
    "package.module.x_function__mutmut_1": {
      "source": "src/package/module.py",
      "mutant_sha256": "sha256-of-normalized-mutant-function"
    }
  }
}
```

The planner must include every generated mutant in the selected campaign. The
central verifier validates source identities against a fresh exact head checkout.
The plan digest is SHA256 of `json.dumps(plan, sort_keys=True,
separators=(",", ":"), ensure_ascii=True).encode()`. Mutant ownership is
`int(sha256(mutant_id.encode()).hexdigest(), 16) % shard_count`.

Each worker exports `shard_index`, `shard_count`, `plan_sha256`, `base_sha`,
`head_sha`, integer `exit_code: 0`, `stats`, and `mutants`. Each mutant record has
its expected `mutant_sha256` and a status: `killed`, `survived`, `timeouts`,
`no_tests`, `suspicious`, `not_covered`, `skipped`, `other_bad`, `segfault`, or
`check_was_interrupted_by_user`. `stats` includes at least `killed`, `survived`,
`total`, and every nonzero status counter. Survivors additionally require the
individual `review` object described above, limited to that worker's survivors.

Aggregation requires every worker and every planned mutant exactly once, with
matching content hashes, identities, counters and partition ownership. It rejects
failed, partial or duplicate campaigns; all unresolved states block. Equivalents
are checked separately across the entire campaign, including unchanged functions,
and keep their raw survived count. The PR scope receives only its corresponding
proofs after campaign-wide verification; outside-scope proofs cannot mask an
unreviewed survivor or invalidate an otherwise complete audited campaign. The final report includes
`campaign.complete`, `expected_mutants`, the plan digest, and `raw_stats`.

Campaign artifacts are required execution transport, unlike best-effort diagnostic
uploads. An unavailable or incomplete artifact transfer fails the campaign; it
cannot certify a partial run. Artifact names include the run attempt to prevent a
retry from mixing fragments from different executions. Single-job evidence keeps
its bounded compressed job-output transport and remains independent of diagnostic
artifact availability.

Consumer runner adoption and a real full native campaign remain consumer-side
validation steps after a published central release. Central protocol tests alone
do not certify an Ioniq campaign of approximately 55,000 mutants.
