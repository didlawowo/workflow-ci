# How the mutation campaign is split into shards

This page explains why a mutation run takes the time it takes and which settings
change it. The protocol itself (plan schema, evidence, equivalence proofs) is in
[mutation-runners.md](mutation-runners.md); this page covers scheduling only.

## What happens during a run

For a pull request that requires mutation (`complexity:medium` or
`complexity:high`, see below), `mutation-campaign.yml` runs four stages:

```text
plan  ->  shard 0 ... shard N-1 (in parallel, at most 4 at a time)  ->  verify  ->  gate
```

1. **plan** (job `plan`, timeout 45 min). Classifies the policy, lists every
   mutant the PR is responsible for, validates the list against the exact base
   and head SHAs, then publishes it as the `mutation-plan-<run id>` artifact. It
   also fixes the number of shards `N` and the per-shard timeout.
2. **shards** (job `shard`, a matrix of `N` jobs). Each shard runs only the
   mutants it owns with `mutmut run <ids>` and uploads one fragment with the
   status of each mutant. A shard that fails or is cancelled leaves its fragment
   missing, which the verify step rejects.
3. **verify** (job `verify`, timeout 10 min). Downloads the plan and all
   fragments and checks that every shard and every planned mutant is present
   exactly once, with matching hashes. It fails when any mutant is unresolved
   (see "Verify errors"), checks the survivors against the reviewed equivalence
   proofs, and publishes the campaign report.
4. **gate**. The policy gate reads that report. A shard that was never run is
   missing evidence, not a pass.

### What is in the plan

The scope depends on the depth, which comes from the PR label:

| Label | Depth | Mutants planned |
| --- | --- | --- |
| `complexity:medium` (also the default for supported production code) | medium | the functions whose syntax changed in the PR |
| `complexity:high` (also set automatically for large changes) | high | every function of each Python module whose syntax changed |
| `complexity:low` | none | no mutation required, unless the change is promoted to high automatically |

So `complexity:high` is the main driver of the number of mutants, hence of `N`
and of the duration.

## How `N` is chosen

`mutation-shards` is an input of `quality-evidence.yml`, `ci.yml` and
`mutation-policy.yml`:

| Value | Behaviour |
| --- | --- |
| `0` (default) | Auto, for Python repositories: `N = ceil(mutants / 1200)`, clamped to 1..16. 1,201 mutants give 2 shards, 6,000 give 5. |
| `1` | No campaign: one ordinary mutation job. |
| `2` to `16` | Campaign with exactly that many shards. A change with no mutable function then fails ("Mutation plan requires at least one changed function"), whereas auto mode produces an empty one-shard campaign. |

The 1,200 mutants per shard is a code constant
(`MUTANTS_PER_SHARD` in `.ci/mutation_campaign_inventory.py`); a consumer cannot
change it. A non-Python repository with `0` runs the single-job path.

## How mutants are assigned to shards

Each mutant belongs to the shard `int(sha256(mutant_id.encode()).hexdigest(), 16) % N`. This is
deterministic, so the plan, the shards and the verifier all agree without
exchanging a list. It is also blind to cost: shards receive about the same
number of mutants, but not the same amount of work. A shard that happens to
hold slow mutants (long test selection, timeouts) finishes much later than the
others. Sizes are statistically even, durations are not.

## Concurrency and time limits

- **At most 4 shards run at the same time** (`max-parallel: 4` in the shard
  job matrix; `fail-fast` is off, so one failing shard does not cancel the
  others). With `N > 4`, the remaining shards start as earlier ones finish.
- **Plan**: 45 min. **Verify**: 10 min.
- **Each shard**: not a fixed 45 min. The plan step derives it from the
  campaign size with `.ci/mutation_shard_budget.py`:
  `setup + ceil(mutants x pace / N / 60)` minutes, never below 45 and capped at
  360. Defaults: 30 s of pace per mutant and 15 min of setup. A consumer can
  change them with the repository variables `MUTATION_SHARD_PACE_SECONDS` and
  `MUTATION_SHARD_SETUP_MINUTES`. These variables only move the timeout; they
  do not speed up anything.
- Plan and shards run on `UNTRUSTED_RUNNER` if set, otherwise on the runner
  given by the caller (`RUNNER`, then `arc-runner-<repo>`). Verify always runs on the runner given by
  the caller (`inputs.runner`), never on `UNTRUSTED_RUNNER`. All jobs queue for a free runner of that scale set.

## Estimating the wall-clock duration

```text
duration ~ plan
         + ceil(N / min(N, 4, free runners))  x  duration of the slowest shard
         + verify
         + time spent waiting for a free runner
```

The middle term counts "waves": with 5 shards and a cap of 4, shards 0 to 3
start together and shard 4 starts when the first of them ends. In practice the
shard that starts late fills the gap left by the fastest one, so the total is
close to `max(slowest of the first 4, fastest of the first 4 + the late shard)`.

Consequences:

- Doubling `N` does not halve the duration when concurrency is capped at 4 or
  when the scale set has fewer free runners than shards. Extra shards then only
  queue.
- Doubling `N` also adds the fixed setup of each shard (checkout, environment,
  mutant generation, clean test run) and one more plan/verify fragment.
- The duration is set by the slowest shard, not by the average, because the
  hash split is cost-blind.
- Runner availability can dominate: a shard waiting for a runner is time in
  which nothing runs.

## Real example

Repository `solar-monitoring`, PR #439, label `complexity:high`, run of
26 Nov 2026 at 21:46 UTC (5 shards):

| Step | Duration |
| --- | --- |
| plan | 4 min 19 s |
| shard 1 | 18 min |
| shard 3 | 21 min |
| shard 2 | 35 min 30 s |
| shard 0 | 35 min 48 s |
| shard 4 | 14 min (started only when shard 1 ended: concurrency cap of 4) |
| verify | 30 s |
| total | about 42 min |

Shards 0 to 3 started together. Shard 4 waited for the end of shard 1, then ran
14 min. The wall-clock time is therefore the slowest shard (shard 0, 35 min 48 s)
plus plan and verify, not the sum of the shards.

Run of 27 Nov 2026 at 06:16 UTC (4 shards): shard 0 waited 17 min for a free
runner before starting; total about 53 min, longer than the 5-shard run above
although it had fewer shards.

Reading: the slowest shard and the wait for a runner weigh more than the number
of shards. Adding a shard would not have shortened either run.

## Levers

What a consumer repository controls:

| Lever | Effect |
| --- | --- |
| `mutation-shards` | Fixes `N` instead of auto sizing. Useful to cap the number of runners a PR uses (`1` disables the campaign path) or to raise `N` above the auto value, within the cap of 4 concurrent shards. |
| `complexity:*` label | Sets the depth. `complexity:high` mutates whole modules, `medium` only changed functions. The largest effect on the number of mutants. |
| PR size | Fewer changed functions or modules, fewer mutants. Splitting a large PR into smaller ones shortens each campaign. |
| `MUTATION_SHARD_PACE_SECONDS`, `MUTATION_SHARD_SETUP_MINUTES` | Per-shard timeout only. Raise them if a shard is cancelled by its timeout. |
| Tests | Faster or better targeted tests shorten every mutant. |

What belongs to workflow-ci or to the infrastructure, not to the consumer:

- the concurrency cap (`max-parallel: 4` in `mutation-campaign.yml`);
- the 1,200 mutants per shard and the cap of 16 shards;
- the hash assignment, which does not balance the cost of shards;
- the number of runners in the ARC scale set (`continuous-delivery`), which
  decides how long a shard waits.

Ideas, not implemented here: a configurable concurrency cap, a cost-aware
assignment that balances shards by past duration, warming runners before the
first shard.

## Verify errors

Errors come from `.ci/mutation_campaign.py` and `.ci/mutation_evidence.py`
and appear in the log as `Mutation campaign failed: <message>`.

| Message | Meaning |
| --- | --- |
| `Campaign contains unresolved mutants` | At least one mutant ended in a status other than `killed` or `survived` (timeout, no tests, suspicious, not covered, skipped, segfault...). A campaign must resolve every mutant. Look at the per-mutant diagnostics for the status. |
| `Missing campaign shards`, `Duplicate or invalid campaign shard`, `Failed campaign shard` | A fragment is absent or malformed, typically because a shard was cancelled, hit its timeout, or its artifact upload failed. Re-run the failed jobs. |
| `Campaign fragment identity changed` | A fragment does not match the plan (different plan hash, SHAs or shard count), for example fragments from another run. |
| `Missing, duplicated or unexpected campaign mutants` | A shard did not report exactly the mutants it owns. |
| `Incomplete campaign inventory` | The fragments together do not cover the whole plan. |
| `Changed function produced no mutation evidence: <target>` | The PR scope contains a changed function (the target, a glob such as `module.*function__mutmut_*`) but no mutant in the results matches it, so nothing proves that function was tested. |

About the last message: the scope computation (`mutation_targets` in
`.ci/mutation_scope.py`) mirrors the rules of mutmut and drops functions mutmut
does not mutate: those with any decorator other than a single bare
`@staticmethod` or `@classmethod`, the dunder methods `__getattribute__`,
`__setattr__` and `__new__`, and functions without mutable code. A decorated
function (other than a bare `@staticmethod` or `@classmethod`) is therefore
not a target at medium depth. At high depth the target is the whole module
(`module.*__mutmut_*`): it is only emitted when at least one function of the
module is mutable, so a module made only of decorated handlers yields no target
either (before workflow-ci#316 it produced this error). When every changed
function is of that kind, the scope is empty (empty one-shard
campaign in auto mode, an error with a fixed `mutation-shards`). The error
appears when a target exists but mutmut produced no matching mutant, which means
the scope computation and mutmut disagree on that function. The code does not
list all such cases; report one with the target printed in the message.
