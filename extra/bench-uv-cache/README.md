# Bench: uv cache strategies on ARC runners

A/B/C comparison of three uv-cache patterns to settle which is fastest and most predictable on our self-hosted ARC runners (PVC-NFS-backed `UV_CACHE_DIR`).

## Variants

| | `astral-sh/setup-uv` | `actions/cache@v4` on `~/.cache/uv` | Notes |
|---|---|---|---|
| **A — pre-fix baseline** | default (enable-cache=true) | yes, unconditional | what most repos did before |
| **B — central post-PR#1 (v1.0.0)** | default | yes, but `if: env.UV_CACHE_DIR == ''` (skipped on ARC) | minimal fix |
| **C — dc-finance pattern** | `enable-cache: false` + `cache-local-path: /home/runner/.cache/uv` | none | most robust |

## How to run

This workflow is `workflow_call`-only. Trigger it from a repo that has its own ARC runner-set via a tiny wrapper:

```yaml
# .github/workflows/uv-bench-call.yml in dc-finance (or any repo with a runner)
name: uv-cache-bench
on:
  workflow_dispatch:
jobs:
  bench:
    uses: didlawowo/workflow-ci/.github/workflows/bench-uv-cache.yml@bench/uv-cache-ab
```

Then `gh workflow run uv-cache-bench --repo didlawowo/dc-finance` and inspect the summary.

## Methodology

- Same `pyproject.toml` (~15 heavy deps) for all variants
- Each variant uses an **isolated** `UV_CACHE_DIR` subdir (`/home/runner/.cache/uv/bench-<X>`) to avoid cross-contamination
- Each variant runs **cold** (subdir wiped before) then **warm** (NFS subdir kept)
- `uv sync --group dev` is timed via `time` builtin; bytes downloaded captured from `du` deltas
- A summary job aggregates results into `$GITHUB_STEP_SUMMARY`

## What we expect

- A warm = fast (GHA cache hit), A cold = very slow (WAN download + restore)
- B warm = fast (NFS hit), B cold = slow first time then warm
- C warm = fast (NFS hit), C cold = slow first time then warm
- B vs C should be near-identical on warm; C should be more deterministic because `setup-uv` doesn't try to override `UV_CACHE_DIR`
