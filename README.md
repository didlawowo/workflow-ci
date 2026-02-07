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
| `node-quality-security` | eslint, npm audit                  |

## Usage

Reference actions from your workflows:

```yaml
steps:
  - uses: actions/checkout@v6

  - uses: didlawowo/workflow-ci/.github/actions/docker-build-push@main
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

Each template has a `PROJECT CONFIGURATION` section at the top — edit `RUNNER`, `IMAGE_NAME`, version, etc.

## Secrets Required

| Secret                 | Used by           |
| ---------------------- | ----------------- |
| `DOCKER_USERNAME`      | docker-build-push |
| `DOCKER_PASSWORD`      | docker-build-push |
| `CODECOV_TOKEN`        | test actions      |
| `ANTHROPIC_API_KEY`    | security-review   |
| `RELEASE_PLEASE_TOKEN` | cd-production     |
