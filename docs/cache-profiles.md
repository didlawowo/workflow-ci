# Package proxies and runner storage

Dependency reuse is provided by shared package proxies, not by per-runner cache
profiles managed by `workflow-ci`.

## Dependency endpoints

- Python / PyPI: Proxpi
- npm: Verdaccio
- Go modules: Athens

Endpoint selection belongs to runner infrastructure:
- ARC runners use Kubernetes service DNS (`*.svc.cluster.local`);
- system/out-of-cluster runners use LAN DNS endpoints to the same services.

`workflow-ci` must not switch registries, rewrite lockfiles, or create a local
persistent dependency-cache profile because a configured proxy is unreachable.
A DNS/routing/configuration failure must be fixed at the runner or proxy layer.

## Local storage that remains valid

Local ephemeral storage is still appropriate for:
- checkouts and workspaces;
- build intermediates;
- isolated mutation sandboxes;
- temporary files whose loss is harmless.

Those paths are scratch, not dependency caches. They must be disposable without
changing dependency resolution or reproducibility.

## Explicit NFS validation

`.ci/nfs-cache.sh` remains only for legacy/specialized workflows that
intentionally require runner-provided NFS storage. Generic Python, Node and Go
setup actions do not require NFS caches and rely on the package proxy configured
by the runner.

BuildKit keeps its own dedicated cache mechanism and is outside this policy.
