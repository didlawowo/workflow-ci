# Detect image changes

An application release and a Docker image release do not always coincide. A release
that only changes `helm/**` or `docs/**` can keep the existing image. This composite
action centralizes the comparison currently implemented in oci-storage's
`cd-production-orchestrator.yml`.

The action reads Git history only. It does not build, scan, push, sign, change an
image tag or deploy anything. The consumer conditions those jobs on its output.

## Inputs and decision

- `current-tag`: existing stable release tag, defaulting to the release event tag
  or `github.ref_name`. The comparison uses this tag, even if checkout HEAD differs.
- `tag-prefix`: release family, default `v`; tags must be `<prefix>MAJOR.MINOR.PATCH`.
  Prerelease tags are not supported by this initial version.
- `previous-tag`: optional explicit base. Otherwise select the nearest tagged
  ancestor on the current release's **first-parent** history, from the same family.
  Tags from future commits or merged side branches do not become automatic bases.
- `app-paths`: multiline Git glob patterns, default `**` (all files).
- `non-image-paths`: multiline Git glob exclusions, default `helm/**` and `docs/**`.
  Exclusions take precedence. Exclude only files that cannot affect the image;
  include sources, Dockerfiles, copied assets and dependency manifests/lockfiles.
  Unknown paths rebuild under the default configuration. Lists do not accept
  comments, negation, absolute paths or Git pathspec magic.

Outputs: `app-changed` is `true` or `false`; `previous-tag` is the resolved base.
Deletions and both sides of a rename are considered, so moving a source into an
excluded directory still requires rebuilding.

Checkout full history and tags (`fetch-depth: 0`), and provide Git and Python 3
on the calling runner. The action makes no network request and fetches nothing.
Missing/ambiguous bases, missing tags, identical commits, unrelated histories,
shallow clones or Git failures **fail the step**, without publishing a false
`app-changed=false`. A first release has no automatic base: build its image
unconditionally, or provide a distinct ancestor release tag explicitly.

## Migration from oci-storage

Replace the local previous-tag lookup and `git diff` shell step with this action.
The earlier lookup sorted all repository tags and compared against HEAD; this
version resolves the release's own ancestry and compares exact release commits.
Retain the existing image tag in Helm when the build is skipped.

The example below preserves oci-storage's `app_changed` job output for its
existing downstream jobs. Replace `WORKFLOW_CI_REPOSITORY` with `didlawowo/workflow-ci` and
`PUBLISHED_TAG` with the published semantic release containing this action before
adoption; older releases do not contain it.

```yaml
jobs:
  detect-changes:
    needs: extract-version
    runs-on: ${{ vars.RUNNER || 'arc-runner-oci-storage' }}
    permissions:
      contents: read
    outputs:
      app_changed: ${{ steps.changes.outputs.app-changed }}
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
          ref: ${{ needs.extract-version.outputs.tag_name }}
          persist-credentials: false
      - id: changes
        uses: WORKFLOW_CI_REPOSITORY/.github/actions/detect-image-changes@PUBLISHED_TAG
        with:
          current-tag: ${{ needs.extract-version.outputs.tag_name }}
          app-paths: |
            src/**
            views/**
            Dockerfile
            go.mod
            go.sum
          non-image-paths: |
            helm/**
            docs/**
```

Keep the existing downstream wiring:

| Job | Condition / dependency |
| --- | --- |
| Final validation | Needs `detect-changes`; `if: needs.detect-changes.outputs.app_changed == 'true'` |
| Conformance | Needs successful validation and `detect-changes`; same condition |
| Docker build/push/sign | Needs successful validation, conformance and `detect-changes`; same condition |
| Update Helm image tag | Needs Docker build; `if: needs.production-build.result == 'success'` |

Do not use `always()` to bypass a failed detection or failed validation. If your
final summary uses `always()`, explicitly fail it when detection failed, or when
`app_changed=true` but build/tag update did not succeed. A Helm-only release can
publish its chart with the old image tag; it must not point to a new image tag
that was never pushed.

Rollback: remove the action wiring and restore the consumer's prior detection
step. This PR does not change oci-storage or any other consumer automatically.
