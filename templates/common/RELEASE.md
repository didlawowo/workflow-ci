# Release workflow (centralisé)

Workflow réutilisable qui remplace `release-please`. Flow en **une étape** :
*merge sur `main` → CHANGELOG + tag + GitHub Release*. Pas de PR intermédiaire.

## Adopter dans un repo

1. Copier `templates/common/release-example.yml` vers
   `.github/workflows/release.yml` dans ton repo.
2. Dé-commenter les lignes `version-files` qui s'appliquent à ton projet.
3. (Optionnel) ajouter un `cliff.toml` à la racine et passer
   `cliff-config: cliff.toml`. Sinon, la config par défaut de workflow-ci
   est utilisée.

## Conventional commits

Le bump est calculé depuis le type de commit :

| Préfixe                  | Bump   |
|--------------------------|--------|
| `feat:`                  | minor  |
| `fix:`, `perf:`          | patch  |
| `feat!:` / `BREAKING CHANGE:` dans le body | major  |
| `chore:`, `docs:`, `style:`, `test:` | **aucun** (release skippée si rien d'autre) |

Pas de commits releasables depuis le dernier tag → le workflow log "Nothing
to release" et exit 0. C'est normal, ce n'est pas une erreur.

## Forcer / skipper

- **Forcer major** : `feat!: ...` ou ajouter `BREAKING CHANGE: explication`
  dans le footer.
- **Skipper le run** : ajouter `[skip ci]` au message de merge.

## Migrer depuis release-please

1. Merger ou fermer toute release PR pending (sinon le tag de baseline est
   incohérent).
2. Vérifier le dernier tag git :
   ```bash
   git describe --tags --abbrev=0
   ```
   S'il manque, le créer manuellement à la version courante depuis
   `.release-please-manifest.json` :
   ```bash
   git tag v1.2.0 <sha-du-merge-de-la-derniere-release-please-pr>
   git push origin v1.2.0
   ```
3. Supprimer les artefacts release-please :
   - `release-please-config.json`
   - `.release-please-manifest.json`
   - `.github/workflows/release-please.yml` (ou la section dans un orchestrator)
4. Ajouter le workflow d'appel (cf. `release-example.yml`).
5. Premier merge `feat:` sur `main` → release auto.

## Permissions

Le job appelant **doit** déclarer :

```yaml
permissions:
  contents: write
```

`GITHUB_TOKEN` suffit pour tag + push + release.

### Déclencher un workflow CD sur le tag créé

Limitation GitHub bien connue : un tag poussé par `GITHUB_TOKEN` **ne
déclenche aucun workflow en aval**. Un `on: push: tags: ['v*']` ne partira
donc jamais tout seul après une release automatique.

Pas besoin de PAT ni de GitHub App pour autant : passer `trigger-cd-workflow`
au workflow réutilisable, qui dispatchera explicitement la cible.

```yaml
jobs:
  release:
    uses: didlawowo/workflow-ci/.github/workflows/release.yml@v1
    permissions:
      contents: write
      actions: write          # requis pour le dispatch
    with:
      trigger-cd-workflow: cd-production-orchestrator.yml
```

La cible **doit** déclarer `workflow_dispatch` avec un `inputs.tag` :

```yaml
on:
  push:
    tags: ['v*']              # tags poussés à la main
  workflow_dispatch:
    inputs:
      tag:
        required: true
        type: string
```

…et lire la version depuis `inputs.tag` en dispatch, car `GITHUB_REF_NAME`
vaut alors `main` et non le tag :

```yaml
- id: version
  run: |
    if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
      VERSION="${{ inputs.tag }}"
    else
      VERSION="${GITHUB_REF_NAME}"
    fi
    echo "version=${VERSION#v}" >> "$GITHUB_OUTPUT"
```

`trigger-cd-workflow` n'accepte **qu'un** nom de workflow. Si la release doit
en lancer plusieurs (image *et* chart, par exemple), viser un orchestrateur qui
les dispatche à son tour.

### `version-files` : ne pas oublier le values.yaml

Quand ArgoCD déploie le chart **depuis git** (et non depuis un registre OCI
épinglé), il lit `image.tag` dans `values.yaml`. Ce fichier doit donc figurer
dans `version-files`, sinon le merge fait déployer une image qui n'existe pas
encore — `ImagePullBackOff` en production le temps que le build sorte. Cas réel.

```yaml
version-files: |
  pyproject.toml:^version = "(.+)"$
  helm/<app>/Chart.yaml:^version: (.+)$
  helm/<app>/Chart.yaml:^appVersion: "(.+)"$
  helm/<app>/values.yaml:^  tag: "(.+)"$
```

## Outputs

Pour chaîner un downstream (Docker build, ArgoCD bump, …) :

```yaml
jobs:
  release:
    uses: didlawowo/workflow-ci/.github/workflows/release.yml@main
    permissions:
      contents: write
  deploy:
    needs: release
    if: needs.release.outputs.released == 'true'
    runs-on: ubuntu-latest
    steps:
      - run: echo "Deploying ${{ needs.release.outputs.version }}"
```

## Dry-run

Pour tester sans rien pusher :

```yaml
with:
  dry-run: true
```

Le workflow calcule le bump, écrit CHANGELOG en local sur le runner, log
les notes de release, et exit sans commit/tag/push.
