# Migration Guidelines — release-please → git-cliff

Brief unifié pour aligner les 4 repos didlawowo qui migrent vers la stack
release centralisée (`workflow-ci`) : `dc-finance`, `solar-monitoring`,
`code-search`, `genai-benchmark-tool`.

**Objectif :** pipelines structurellement **identiques** post-migration. Une
seule façon de faire, pas de divergence. Tout écart doit être justifié
explicitement dans la description de la PR.

---

## ⛔ Ne pas faire

1. **Pas de `docker-release.yml`** ni autre workflow Docker autonome. Le build
   Docker reste dans le **CD orchestrator** (déclenché sur push de tag).
2. **Pas de `docker/login-action` ou `docker/setup-qemu-action` manuel** dans
   les workflows. Le composite `docker-build-push@v1` gère tout.
3. **Pas de `workflow-ci@main`** dans les `uses:`. Toujours `@v1` (tag
   flottant majeur) ou un tag exact (`@v1.3.0`).
4. **Pas de build local Mac qui pousse en oci-storage** avec un tag de prod
   (cf. section Architecture multi-arch plus bas).
5. **Pas de bump manuel** des fichiers de version dans la PR de migration —
   git-cliff doit calculer depuis le prochain commit conventional.
6. **Pas de PAT Docker Hub** ni autre PAT à provisionner. La stack tourne
   uniquement avec `GITHUB_TOKEN` (perms de repo) + secrets oci-storage déjà
   en place.
7. **Pas de décision unilatérale** sur un drift release-please / tag git :
   signaler à Chris, attendre.

---

## ✅ Structure canonique

### A. `.github/workflows/release.yml`

```yaml
name: release

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      dry-run:
        description: 'Dry run (no commit/tag/push)'
        required: false
        default: false
        type: boolean

permissions:
  contents: write

jobs:
  release:
    uses: didlawowo/workflow-ci/.github/workflows/release.yml@v1
    with:
      version-files: |
        <SPÉCIFIQUE_REPO — voir tableau plus bas>
      tag-prefix: "v"
      dry-run: ${{ inputs.dry-run || false }}
```

### B. CD orchestrator (refactor in-place du fichier existant)

**Ne pas** renommer en `.old` et créer un nouveau fichier. Refactor dans le
même fichier (généralement `cd-production-orchestrator.yml`).

Changements :

1. Trigger **uniquement sur tag** :
   ```yaml
   on:
     push:
       tags: ['v*']
   ```
2. Remplacer le job `release-please` par un job `extract-version` :
   ```yaml
   extract-version:
     runs-on: ${{ vars.RUNNER || 'arc-runner-<repo>' }}
     outputs:
       tag_name: ${{ steps.info.outputs.tag_name }}
       version:  ${{ steps.info.outputs.version }}
     steps:
       - id: info
         run: |
           TAG_NAME="${{ github.ref_name }}"
           VERSION="${TAG_NAME#v}"
           echo "tag_name=${TAG_NAME}" >> "$GITHUB_OUTPUT"
           echo "version=${VERSION}"  >> "$GITHUB_OUTPUT"
   ```
3. Tous les `needs: release-please` → `needs: extract-version`, idem pour les
   références aux outputs (`needs.release-please.outputs.X` →
   `needs.extract-version.outputs.X`).
4. Tous les appels à `docker-build-push` doivent pinner sur **`@v1`** (ou
   tag exact compatible). **Pas besoin de secret Docker Hub** — le composite
   utilise par défaut le miroir oci-storage pour l'image QEMU binfmt :
   ```yaml
   - uses: didlawowo/workflow-ci/.github/actions/docker-build-push@v1
     with:
       image-name: oci-storage.dc-tech.work/<repo>/<image>
       image-tag:  ${{ needs.extract-version.outputs.tag_name }}
       registry:   oci-storage.dc-tech.work
       registry-username: ${{ secrets.REGISTRY_USERNAME }}
       registry-password: ${{ secrets.REGISTRY_PASSWORD }}
       platforms: linux/amd64,linux/arm64
       push: "true"
       sign: "true"
       sbom: "true"
   ```

### C. Fichiers à supprimer

- `release-please-config.json`
- `.release-please-manifest.json`
- Workflow `release-please.yml` standalone, s'il existe

### D. Tag de baseline

Avant de merger, vérifier que `git describe --tags --abbrev=0` retourne un
tag valide (ex: `v1.2.0`). Si absent ou désaligné avec le manifest
release-please, **escalader à Chris** — ne pas créer le tag depuis l'agent.

---

## 🔧 Docker Hub rate limit — résolution (déjà appliquée)

### Problème historique

`docker/setup-qemu-action@v3` exécute en interne :
```
docker run tonistiigi/binfmt:latest --install all
```
Le pull de `tonistiigi/binfmt` est **anonyme** depuis Docker Hub →
rate limit 100 pulls / 6h / IP. Les runners ARC du cluster partagent une IP
de sortie NAT → le quota est consommé rapidement → 429 → multi-arch impossible.

### Solution adoptée

`docker-build-push@v1` (depuis v1.3.0) accepte un input `qemu-image` avec
**default = miroir oci-storage** :
```
oci-storage.dc-tech.work/mirror/tonistiigi/binfmt:latest
```
Le pull se fait depuis oci-storage (cluster-local, pas de rate limit), pas de
secret nécessaire, transparent pour les appelants.

### Pré-requis côté infra (à faire une fois par Chris)

```bash
skopeo copy --multi-arch all \
  docker://docker.io/tonistiigi/binfmt:latest \
  docker://oci-storage.dc-tech.work/mirror/tonistiigi/binfmt:latest
```

Si oci-storage est inaccessible / l'image absente lors du build, le composite
échoue : passer un override explicite via l'input `qemu-image` (ex:
`tonistiigi/binfmt:latest` pour retomber sur Docker Hub anonyme).

---

## 🏗️ Multi-arch — règles

Le cluster RKE2 tourne sur **amd64** (node Ryzen). Risque : image arm64-only
(typiquement buildée localement sur Mac) → pods amd64 ne peuvent pas pull,
échec `exec format error` ou `no matching manifest`.

### Règles

1. **Toujours builder en CI**. Le runner ARC est amd64 et QEMU émule arm64
   via le miroir oci-storage.
2. Le composite `docker-build-push@v1` build par défaut
   `linux/amd64,linux/arm64`. Ne pas restreindre à `linux/arm64` seul.
3. Si un build local Mac est nécessaire pour debug :
   `docker buildx build --platform linux/amd64 ...` **et ne pas push** vers
   oci-storage avec un tag de prod.
4. Après push, vérifier le manifest list :
   ```bash
   docker manifest inspect oci-storage.dc-tech.work/<repo>/<image>:<tag> \
     | jq '.manifests[].platform'
   ```
   → doit contenir amd64 ET arm64.

---

## 📋 Spécifiques par repo

### dc-finance — PR de cleanup (#145 déjà mergée, dettes à reprendre)

| Item | Action |
|---|---|
| `release.yml` pin | re-pin `@main` → `@v1` |
| `cd-production-orchestrator.yml.old` | supprimer (dead code) |
| `cd-production-simple.yml` | renommer → `cd-production-orchestrator.yml` |
| `docker-build-push` calls | pin `@v1`, pas besoin d'inputs Docker Hub |

`version-files` :
```
pyproject.toml:^version = "(.+)"$
helm/dc-finance/Chart.yaml:^version: (.+)$
helm/dc-finance/Chart.yaml:^appVersion: (.+)$
```

### solar-monitoring — PR de migration à créer

Suivre la structure A + B + C. **Ne pas créer** `docker-release.yml`
(changement de plan).

`version-files` (à confirmer en lisant le repo, regex `appVersion` selon
guillemets ou non dans `Chart.yaml`) :
```
pyproject.toml:^version = "(.+)"$
helm/solar-monitoring/Chart.yaml:^version: (.+)$
helm/solar-monitoring/Chart.yaml:^appVersion: (.+)$
```

### code-search — PR #14 ouverte, ajustements avant merge

| Item | Action |
|---|---|
| Pin `@v1` | déjà OK ✅ |
| `workflow_dispatch` + `dry-run` input | ➕ ajouter au `release.yml` |
| `docker-build-push` calls dans CD orchestrator | pin `@v1` |
| Bump VERSION 0.9.8 → 1.1.1 | justifier dans la PR comme resync de drift release-please, sinon retirer |

`version-files` (déjà correct) :
```
VERSION:^(.+)$
helm/code-search/Chart.yaml:^version: (.+)$
helm/code-search/Chart.yaml:^appVersion: "(.+)"$
```

### genai-benchmark-tool — PR de migration à créer

Suivre la structure A + B + C.

`version-files` (à confirmer en lisant le repo — vérifier présence d'un chart
Helm) :
```
pyproject.toml:^version = "(.+)"$
```

---

## Workflow attendu par agent

1. **Lire ce brief intégralement** et confirmer la compréhension à Chris.
2. **Auditer le repo cible** :
   ```bash
   gh pr list --state all --limit 10
   git describe --tags --abbrev=0
   cat helm/*/Chart.yaml 2>/dev/null
   ls .github/workflows/
   ```
3. **Lister explicitement** les fichiers à créer / modifier / supprimer
   avant d'éditer. Attendre validation Chris.
4. **PR unique** par repo :
   - `chore/migrate-to-git-cliff` (solar-monitoring, genai-benchmark-tool)
   - `chore/cleanup-release-migration` (dc-finance)
   - Itération sur la PR existante (code-search #14)
5. **Description PR** : matche la structure canon. Toute déviation =
   justifiée explicitement.
6. **Pas de merge auto** : attendre Chris.

---

## Critères de succès

Après les 4 PRs mergées :

- ✅ 4 repos avec **exactement** la même structure `release.yml`
- ✅ 4 CDs avec le pattern `extract-version` job
- ✅ 0 workflow `docker-release.yml`
- ✅ Tous les `docker-build-push` pinés sur `@v1` (ou tag exact ≥ v1.3.0)
- ✅ Plus de rate limit Docker Hub (miroir oci-storage)
- ✅ Plus d'image arm64-only en prod
- ✅ Plus de drift entre `Chart.yaml`, `VERSION`, manifest, tag git
