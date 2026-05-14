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

`GITHUB_TOKEN` suffit pour tag + push + release. Si tu veux qu'un autre
workflow se déclenche **sur** le tag créé (limitation GitHub bien connue :
les events poussés par `GITHUB_TOKEN` ne re-déclenchent rien), il faut un
PAT ou une GitHub App. Documenté mais hors scope par défaut.

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
