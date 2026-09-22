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
      sonar-project-key: ${{ vars.SONAR_PROJECT_KEY }}
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
      SONAR_ROOT_CERT: ${{ secrets.SONAR_ROOT_CERT }} # optional
```

`SONAR_TOKEN` must be able to analyze the configured project. `SONAR_PROJECT_KEY` is a
repository variable because project keys are not assumed from repository names. Quality Gate
thresholds stay in SonarQube, so tightening coverage/security/duplication rules does not require
another workflow-ci release.

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
