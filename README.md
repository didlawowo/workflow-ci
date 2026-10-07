# workflow-ci

Reusable GitHub Actions composite actions and workflow templates for CI/CD pipelines.

## Composite Actions

### Language-agnostic

| Action                  | Description                                                |
| ----------------------- | ---------------------------------------------------------- |
| [`detect-image-changes`](.github/actions/detect-image-changes/README.md) | Skip image rebuilds for releases containing only declared non-image changes |
| `docker-build-push`     | Build, push, scan (Trivy), sign (Cosign), SBOM, provenance |
| `trivy-filesystem-scan` | Vulnerability, secret, misconfiguration, license scanning  |

### Python

| Action                    | Description                      |
| ------------------------- | -------------------------------- |
| `setup-python-env`        | Python + uv; packages use runner-configured Proxpi |
| `run-python-tests`        | pytest + coverage + Codecov      |
| `python-quality-security` | ruff, bandit, trufflehog, safety |

### Go

| Action                | Description                         |
| --------------------- | ----------------------------------- |
| `setup-go-env`        | Go + module download via runner-configured GOPROXY |
| `run-go-tests`        | go test -race + coverage + Codecov  |
| `go-quality-security` | golangci-lint, go vet, gofmt, gosec |

### Node.js

| Action                  | Description                        |
| ----------------------- | ---------------------------------- |
| `setup-node-env`        | Node.js + package manager via runner-configured registry |
| `run-node-tests`        | test script + Playwright + Codecov |
| `node-quality-security` | eslint + npm/pnpm/yarn audit       |

## Usage

Reference actions from your workflows:

```yaml
steps:
  - uses: actions/checkout@v6

  - uses: didlawowo/workflow-ci/.github/actions/docker-build-push@v1.8.0
    with:
      image-name: myuser/myapp
      image-tag: v1.0.0
      push: "true"
      registry-username: ${{ secrets.DOCKER_USERNAME }}
      registry-password: ${{ secrets.DOCKER_PASSWORD }}
```

## Workflow Templates

Copy templates from `templates/<language>/` into your repo's `.github/workflows/`:

```text
templates/
├── common/          # security-review.yml, dependabot.yaml
├── python/          # ci-branch-pipeline.yml, cd-production.yml, security-orchestrator.yml
├── go/              # ci-branch-pipeline.yml, cd-production.yml, security-orchestrator.yml
└── node/            # ci-branch-pipeline.yml
```

Each template has a `PROJECT CONFIGURATION` section at the top for project-specific values such as `IMAGE_NAME`, language version and working directory.

Runner selection has a single source of truth: the GitHub repository variable `vars.RUNNER`. If it is unset, GitHub templates fall back to `arc-runner-<repo>`; do not add a separate `env.RUNNER`.

### Nested projects and test evidence

Python, Go and Node actions accept `working-directory` so monorepos and projects below the repository root are supported consistently. Python custom test commands have an explicit evidence contract: a successful command must create the configured `junit-report-path` (default `coverage-reports/pytest-report.xml`, relative to `working-directory`). `coverage-report-path` follows the same rule.

Node dependency audits use the selected `package-manager`: `npm audit`, `pnpm audit`, Yarn Classic `yarn audit`, or Yarn Berry `yarn npm audit`.

### Environnements preview (label-gated)

Si un repo build une image preview via un job conditionné au label `preview`
(`if: contains(github.event.pull_request.labels.*.name, 'preview')`), le trigger
`pull_request` **doit** inclure le type `labeled` :

```yaml
on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened, labeled]
```

Sinon, poser le label après le run initial ne déclenche aucun build → l'image
`pr-<n>` n'existe pas → `ImagePullBackOff` sur le pod preview. Les templates
`ci-branch-pipeline.yml` incluent déjà ce type par défaut.

## SonarQube Quality Gate

The reusable `quality-evidence.yml` workflow runs the official SonarQube scanner against
`https://sonarqube.dc-tech.work` and waits for the server-side Quality Gate. A failed gate
fails the trusted quality job; the PR quality report also links to the SonarQube project.

Consumers keep the release pin at the workflow boundary:

```yaml
jobs:
  trusted-quality:
    uses: didlawowo/workflow-ci/.github/workflows/quality-evidence.yml@v1.8.0
    with:
      repo-type: python
      runner: ${{ vars.RUNNER }}
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
      SONAR_ROOT_CERT: ${{ secrets.SONAR_ROOT_CERT }} # optional
```

`SONAR_TOKEN` must be able to analyze the configured project. `SONAR_PROJECT_KEY` is read
directly from the caller repository variable by the reusable workflow; it is deliberately not a
workflow input, so a pull request cannot redirect analysis to another SonarQube project. The
SonarQube host, full-repository scan base and blocking Quality Gate wait are also fixed by
workflow-ci. A pull request that changes `sonar-project.properties` is rejected by trusted
quality evidence; such policy changes must be reviewed separately on the protected base branch.

`sonar-project.properties` is optional: the project key, host and blocking Quality Gate settings
are supplied centrally. If a repository needs custom Sonar properties such as source/exclusion
rules, merge that file to `main` before enabling Sonar. Only then set `SONAR_ENABLED=true`; the
evaluated PR is intentionally not allowed to change its own Sonar policy.

Quality Gate thresholds stay in SonarQube, so tightening coverage/security/duplication rules does
not require another workflow-ci release.

Same-repository action composition uses GitHub's `$/...` self-reference. This resolves internal
actions to the exact commit of the tagged workflow and prevents stale cross-version pins.

## Secrets Required

| Secret                 | Used by           |
| ---------------------- | ----------------- |
| `DOCKER_USERNAME`      | docker-build-push |
| `DOCKER_PASSWORD`      | docker-build-push |
| `CODECOV_TOKEN`        | test actions      |
| `ANTHROPIC_API_KEY`    | security-review   |
| `RELEASE_PLEASE_TOKEN` | cd-production     |
| `SONAR_TOKEN`          | quality-evidence  |
| `SONAR_ROOT_CERT`      | quality-evidence (optional internal CA) |

## Repository variables for quality evidence

| Variable | Value |
| --- | --- |
| `SONAR_PROJECT_KEY` | Exact SonarQube project key imported for the repository |
| `SONAR_ENABLED` | `true` to execute the SonarQube gate; otherwise Sonar is skipped |

### Dependency proxies and runner-local storage

Dependency reuse is centralized behind the package proxies operated by the runner
infrastructure:

- Python / PyPI → **Proxpi**;
- npm / pnpm / Yarn registry traffic → **Verdaccio**;
- Go modules → **Athens** through `GOPROXY`.

`workflow-ci` does not choose different registries based on the runner name and
does not fall back to public registries when an internal proxy is unreachable.
Endpoint selection belongs to infrastructure:

- ARC runners use the Kubernetes Service DNS names (`*.svc.cluster.local`);
- system/out-of-cluster runners use LAN DNS endpoints to the same services.

A proxy resolution or connectivity failure is therefore an infrastructure error,
not a signal to create a new per-runner package cache or rewrite a lockfile.

Local ephemeral storage remains appropriate for checkouts, workspaces, build
intermediates and isolated mutation sandboxes. These directories are scratch:
they must be disposable without changing dependency resolution or reproducibility.

The NFS helper remains available only for workflows that explicitly require
runner-provided persistent NFS storage. Generic Python, Node and Go setup actions
do not require NFS dependency caches.

See [Package proxies and runner storage](docs/cache-profiles.md) for the
architecture and troubleshooting order.

## Forgejo reusable workflows (V1)

Forgejo 15+ consumers can call the workflows in `.forgejo/workflows/` from
this public GitHub repository using a fully qualified URL. The workflow jobs
run on the consumer's internal Forgejo runner, not on GitHub-hosted runners.
See [Forgejo V1 setup](docs/forgejo-ci-v1.md) and the
[moto-tracker example](templates/forgejo/moto-tracker-ci.yaml).

## Release follow-up

After every new workflow-ci release, update the version used by
[github-manager](https://github.com/didlawowo/github-manager). Update
`WORKFLOW_CI_VERSION` in `src/quality_policy.py`, align static workflow/action
references in `.github/workflows/` and `forgejo-content/`, and adjust the associated tests.
Use an actually published tag, run the github-manager checks, open its update
PR and verify its CI. Publishing workflow-ci alone does not update managed
consumer workflows. Any production rollout still requires explicit authorization.


## Shared local pre-commit contract

Workflow CI owns the default local developer gates for managed repositories.

Templates:
- `templates/python/.pre-commit-config.yaml`
- `templates/node/.pre-commit-config.yaml`
- `templates/go/.pre-commit-config.yaml`

The contract separates fast commit-time checks from full pre-push tests:
- **pre-commit**: syntax/configuration, lint, formatting and type/static checks;
- **pre-push**: the repository test suite.

Consumers install both hooks with:

```bash
pre-commit install --hook-type pre-commit --hook-type pre-push --install-hooks
```

CI can verify that the local contract still passes with the shared composite action
`.github/actions/precommit-contract`. Consumer workflow templates must reference
an immutable published tag, never `main`.
