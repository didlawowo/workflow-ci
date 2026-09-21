# workflow-ci

Reusable GitHub Actions composite actions and workflow templates for CI/CD pipelines.

## Composite Actions

### Language-agnostic

| Action                  | Description                                                |
| ----------------------- | ---------------------------------------------------------- |
| `docker-build-push`     | Build, push, scan (Trivy), sign (Cosign), SBOM, provenance |
| `trivy-filesystem-scan` | Vulnerability, secret, misconfiguration, license scanning  |

### Python

| Action                    | Description                      |
| ------------------------- | -------------------------------- |
| `setup-python-env`        | Python + uv + cache              |
| `run-python-tests`        | pytest + coverage + Codecov      |
| `python-quality-security` | ruff, bandit, trufflehog, safety |

### Go

| Action                | Description                         |
| --------------------- | ----------------------------------- |
| `setup-go-env`        | Go + cache + mod download           |
| `run-go-tests`        | go test -race + coverage + Codecov  |
| `go-quality-security` | golangci-lint, go vet, gofmt, gosec |

### Node.js

| Action                  | Description                        |
| ----------------------- | ---------------------------------- |
| `setup-node-env`        | Node.js + npm/pnpm/yarn cache      |
| `run-node-tests`        | test script + Playwright + Codecov |
| `node-quality-security` | eslint + npm/pnpm/yarn audit       |

## Usage

Reference actions from your workflows:

```yaml
steps:
  - uses: actions/checkout@v6

  - uses: didlawowo/workflow-ci/.github/actions/docker-build-push@3147f59553546407f94163368f21e2d4d9f8775e
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

Runner selection has a single source of truth: the GitHub repository variable `vars.RUNNER`. If it is unset, templates fall back to `ubuntu-latest`; do not add a separate `env.RUNNER`.

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

## Secrets Required

| Secret                 | Used by           |
| ---------------------- | ----------------- |
| `DOCKER_USERNAME`      | docker-build-push |
| `DOCKER_PASSWORD`      | docker-build-push |
| `CODECOV_TOKEN`        | test actions      |
| `ANTHROPIC_API_KEY`    | security-review   |
| `RELEASE_PLEASE_TOKEN` | cd-production     |
