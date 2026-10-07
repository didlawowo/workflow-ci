# Package proxies and runner storage

## Architecture

Package artifacts are cached once, centrally, behind three shared HTTP proxies:

| Ecosystem | Shared proxy | Runner variable |
| --- | --- | --- |
| Python / PyPI | Proxpi | `PIP_INDEX_URL`, `UV_DEFAULT_INDEX` |
| npm / pnpm / Yarn | Verdaccio | `NPM_CONFIG_REGISTRY` |
| Go modules | Athens | `GOPROXY` |

`workflow-ci` consumes the endpoint already supplied by the runner. It does not
select an endpoint from `RUNNER_NAME`, create a second package cache, or switch
to a public registry when a configured proxy is unavailable.

### ARC runners

ARC jobs run inside Kubernetes and use the in-cluster Services directly:

```text
Proxpi     -> proxpi.arc-system.svc.cluster.local
Verdaccio  -> verdaccio.arc-system.svc.cluster.local
Athens     -> athens.arc-system.svc.cluster.local
```

This is the shortest and authoritative path for Kubernetes runners.

### System / out-of-cluster runners

- system/out-of-cluster runners use LAN DNS endpoints to the same proxies.

System runners cannot resolve Kubernetes Service DNS. Their service environment
must instead provide those LAN endpoints. On Phoenix they are configured by
runner infrastructure and route through the existing LAN Gateway routes.

Projects and reusable actions must inherit those variables. They must not
reimplement endpoint selection.

## What local storage is for

Local ephemeral storage is encouraged for high-churn state that does not need to
survive a job:

- checkout/workspace files;
- build intermediates;
- temporary virtual environments where appropriate;
- mutation sandboxes;
- temporary files and generated test data.

This is **scratch**, not a dependency-cache authority. Removing it must never
change which package version or registry is selected.

BuildKit has its own cache mechanism and is outside this policy.

## What not to add

Do not add any of the following to fix a package download failure:

- a runner-specific persistent uv/npm/Go cache;
- `WORKFLOW_CACHE_PROFILE=local` or an equivalent runner-name switch;
- a fallback from Proxpi/Verdaccio/Athens to public registries;
- lockfile export/rewriting solely because an endpoint is unreachable;
- duplicate registry-selection logic inside project repositories or reusable
  language actions.

These patterns hide infrastructure faults and create different dependency
semantics between runners.

## Troubleshooting order

For a dependency-install failure, inspect in this order:

1. verify the shared proxy itself is healthy;
2. verify the runner resolves the endpoint intended for its environment;
3. verify TCP/TLS/HTTP connectivity from that runner;
4. verify the runner environment exposes the expected package-manager variable;
5. only then investigate `workflow-ci` or project dependency configuration.

Do not bypass a failed step by switching to a public registry. A broken proxy
path should fail visibly so the infrastructure problem is repaired.

## Explicit NFS validation

`.ci/nfs-cache.sh` remains for legacy/specialized workflows that intentionally
require runner-provided NFS storage. It is not a package-cache abstraction.
Generic Python, Node and Go setup actions rely on the package proxy configured
by the runner.
