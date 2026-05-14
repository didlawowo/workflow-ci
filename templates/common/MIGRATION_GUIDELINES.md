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
6. **Pas de décision unilatérale** sur un drift release-please / tag git :
   signaler à Chris, attendre.

---

## 🔴 Obligatoires sur tous les appels `docker-build-push`

Tout appel à `didlawowo/workflow-ci/.github/actions/docker-build-push@v1`
**DOIT** passer ces 4 secrets, sinon les builds échoueront aléatoirement avec
`429 Too Many Requests` sur les pulls Docker Hub (rate limit 100/6h partagé
sur l'IP egress cluster — observé sur code-search v1.1.0 CD).

```yaml
- uses: didlawowo/workflow-ci/.github/actions/docker-build-push@v1
  with:
    # image-tag DOIT utiliser `version` (sans `v`), PAS `tag_name` (avec `v`).
    # Convention SemVer Docker/Helm. Cohérent avec `appVersion` dans Chart.yaml.
    # Sinon mismatch avec values.yaml.image.tag → ImagePullBackOff.
    image-tag: ${{ needs.extract-version.outputs.version }}
    # ... autres inputs ...
    registry-username: ${{ secrets.OCI_USERNAME }}      # push vers oci-storage
    registry-password: ${{ secrets.OCI_PASSWORD }}
    dockerhub-username: ${{ secrets.DOCKERHUB_USERNAME }}  # pull base images FROM
    dockerhub-token:    ${{ secrets.DOCKERHUB_TOKEN }}
```

### Convention de tag : `version` sans `v`

| Source | Format | Exemple |
|---|---|---|
| Tag git (créé par git-cliff) | `v` + SemVer | `v1.1.3` |
| `extract-version.outputs.tag_name` | identique au tag git | `v1.1.3` |
| `extract-version.outputs.version` | SemVer pur (sans `v`) | `1.1.3` |
| **Tag image Docker** | **`version` (sans `v`)** | `oci-storage.dc-tech.work/foo:1.1.3` |
| **`appVersion` dans Chart.yaml** | SemVer pur | `1.1.3` |
| **`image.tag` dans values.yaml** | SemVer pur | `1.1.3` |

→ Toujours utiliser `version` pour `image-tag` et pour bumper `values.yaml`.
Réserver `tag_name` (avec `v`) au git tag, GH Release, et nom de fichier
chart `<name>-${VERSION}.tgz`. Symptôme typique du mismatch : `ImagePullBackOff
... not found` sur le pod, observé sur code-search v1.1.2/v1.1.3.

Les 4 secrets sont **déjà provisionnés** sur les 7 repos (vérifié 2026-05-14).
Si un repo est ajouté à la stack, ces secrets doivent être provisionnés
**avant** la première PR de migration. Cf. section "Convention de naming
des secrets" pour les détails.

### Permissions du job appelant

Le job qui appelle `docker-build-push@v1` avec `sign: "true"` **DOIT**
déclarer ces permissions, sinon Cosign keyless échoue avec
`expired_token` lors de l'obtention du certificat Fulcio (le token OIDC
n'est jamais émis par GitHub si la permission n'est pas accordée) :

```yaml
publish-image:           # ou le nom de votre job de build
  permissions:
    contents: read       # checkout
    packages: write      # push registry (selon le registry)
    id-token: write      # ← OBLIGATOIRE pour Cosign keyless (Fulcio OIDC)
  steps:
    - uses: didlawowo/workflow-ci/.github/actions/docker-build-push@v1
      with:
        sign: "true"
        sbom: "true"
        ...
```

Symptôme observé sur genai-benchmark-tool v0.6.4 :
`signing [...]: getting signer: getting key from Fulcio: retrieving cert: error obtaining token: expired_token`
→ image buildée et pushée OK, mais l'étape sign keyless casse.

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
  actions: write   # required for trigger-cd-workflow → gh workflow run

jobs:
  release:
    uses: didlawowo/workflow-ci/.github/workflows/release.yml@v1
    with:
      version-files: |
        <SPÉCIFIQUE_REPO — voir tableau plus bas>
      tag-prefix: "v"
      # Guard for typed boolean: when triggered by `push:`, `inputs` is null
      # and `null || false` may leak a non-boolean to the strictly-typed input.
      dry-run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry-run || false }}
      # GitHub anti-loop: tags pushed by GITHUB_TOKEN do not trigger the CD
      # orchestrator's `push: tags: v*`. The reusable workflow dispatches it
      # explicitly after pushing the tag. The CD orchestrator MUST declare
      # `workflow_dispatch:` with `inputs.tag` (see section B).
      trigger-cd-workflow: cd-production-orchestrator.yml
```

### B. CD orchestrator (refactor in-place du fichier existant)

**Ne pas** renommer en `.old` et créer un nouveau fichier. Refactor dans le
même fichier (généralement `cd-production-orchestrator.yml`).

Changements :

1. Trigger sur tag **et** `workflow_dispatch` (le second est requis — c'est
   ce trigger qui sera fired par `release.yml` après le push de tag, et
   permet aussi la recovery manuelle si un release échoue mi-parcours) :
   ```yaml
   on:
     push:
       tags: ['v*']
     workflow_dispatch:
       inputs:
         tag:
           description: 'Tag to deploy (e.g. v1.2.0). Required for manual recovery.'
           required: true
           type: string
   ```
2. Remplacer le job `release-please` par un job `extract-version` qui gère
   les deux events :
   ```yaml
   extract-version:
     runs-on: ${{ vars.RUNNER || 'arc-runner-<repo>' }}
     outputs:
       tag_name: ${{ steps.info.outputs.tag_name }}
       version:  ${{ steps.info.outputs.version }}
     steps:
       - id: info
         run: |
           if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
             TAG_NAME="${{ inputs.tag }}"
           else
             TAG_NAME="${{ github.ref_name }}"
           fi
           VERSION="${TAG_NAME#v}"
           echo "tag_name=${TAG_NAME}" >> "$GITHUB_OUTPUT"
           echo "version=${VERSION}"  >> "$GITHUB_OUTPUT"
   ```
   ⚠️ Les `actions/checkout` suivants doivent pinner explicitement le tag :
   ```yaml
   - uses: actions/checkout@v4
     with:
       ref: ${{ needs.extract-version.outputs.tag_name }}
   ```
   Sinon le checkout part de la branche default et tu builds le code du
   prochain commit, pas du tag.
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
       # SemVer pur (1.1.3) sans le préfixe `v`. Cohérent avec Chart.yaml
       # `appVersion` et `values.yaml` `image.tag`. Utiliser `tag_name` ici
       # (v1.1.3) produit un mismatch → ImagePullBackOff. Cf. section
       # "Convention de tag : `version` sans `v`" plus haut.
       image-tag:  ${{ needs.extract-version.outputs.version }}
       registry:   oci-storage.dc-tech.work
       registry-username: ${{ secrets.OCI_USERNAME }}
       registry-password: ${{ secrets.OCI_PASSWORD }}
       # Docker Hub auth: avoids 100/6h-per-IP rate limit on the FROM
       # directives of the Dockerfile (python:*, oven/bun:*, etc.). The
       # secrets are provisioned on every repo (DOCKERHUB_USERNAME +
       # DOCKERHUB_TOKEN). Without these, multi-arch builds randomly fail
       # with 429 Too Many Requests once the cluster's IP quota is reached.
       dockerhub-username: ${{ secrets.DOCKERHUB_USERNAME }}
       dockerhub-token:    ${{ secrets.DOCKERHUB_TOKEN }}
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

## 🔐 Convention de naming des secrets

| Préfixe | Cible | Provisionné sur les 5 repos ? |
|---|---|---|
| `OCI_USERNAME` / `OCI_PASSWORD` | `oci-storage.dc-tech.work` (registry interne, push & pull) | ✅ oui (8 repos) |
| `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` | Docker Hub (`docker.io`) — auth pour pull des FROM dans les Dockerfiles, évite le rate limit 100/6h anonyme partagé par IP cluster | ✅ oui (7 repos: les 8 sauf invoice-automation 404) |
| `DOCKER_USERNAME` / `DOCKER_PASSWORD` | **Legacy** — historiquement utilisés pour oci-storage avec un nom trompeur. À supprimer post-migration. | legacy sur certains repos |

**Règle :** dans les workflows, toujours utiliser `secrets.OCI_*` pour les
pushes/pulls oci-storage. Ne pas créer ni consommer `DOCKER_*` sauf si
authentification Docker Hub réellement nécessaire (cas futur).

Post-migration, supprimer les anciens secrets `DOCKER_*` sur les repos qui en
ont (dc-finance, solar-monitoring, code-search, oci-storage) une fois que
plus aucun workflow ne les référence.

---

## 📦 Push des charts Helm dans oci-storage

Les charts Helm vivent dans le même registry que les images Docker, sous
le namespace `charts/` :

```
oci://oci-storage.dc-tech.work/charts/<chart-name>
```

### Commandes Helm

```bash
# Login (une fois par session)
helm registry login oci-storage.dc-tech.work -u "$OCI_USERNAME" -p "$OCI_PASSWORD"

# Package + push
helm package helm/<chart-name>
helm push <chart-name>-<version>.tgz oci://oci-storage.dc-tech.work/charts
```

### Convention dans le CD orchestrator

Le push du chart est typiquement une étape **après** le build/push de
l'image, dans le même job ou un job suivant. Pattern attendu :

```yaml
package-and-push-chart:
  needs: [extract-version, update-deployments]
  runs-on: ${{ vars.RUNNER || 'arc-runner-<repo>' }}
  steps:
    - uses: actions/checkout@v4
      with:
        ref: main  # pour récupérer le commit qui bump values.yaml/Chart.yaml
        fetch-depth: 0

    - name: Helm registry login
      run: |
        helm registry login oci-storage.dc-tech.work \
          -u "${{ secrets.OCI_USERNAME }}" \
          -p "${{ secrets.OCI_PASSWORD }}"

    - name: Package + push chart
      env:
        VERSION: ${{ needs.extract-version.outputs.version }}
      run: |
        helm package helm/<chart-name> --version "$VERSION" --app-version "$VERSION"
        helm push <chart-name>-${VERSION}.tgz oci://oci-storage.dc-tech.work/charts
```

### Règles

1. **Toujours en CI**, jamais en local depuis le Mac.
2. **Tag chart = tag image** (même version sémantique).
3. **Pas de chart en `0.0.0-dev`** poussé en prod — uniquement les versions
   issues d'un tag git.
4. Vérification post-push :
   ```bash
   curl -s -u "$OCI_USERNAME:$OCI_PASSWORD" \
     https://oci-storage.dc-tech.work/v2/charts/<chart-name>/tags/list
   ```

### Hygiène du chart pour les repos publics

Si le repo est public (ou peut le devenir), **le `helm package` doit être
propre** — pas de fuite d'infos perso/infra. Référence : portal-checker #61.

Checklist avant le premier push d'un chart depuis un repo public :

1. **`helm/values.yaml`** :
   - ❌ Pas de domaines réels (`*.dc-tech.work`, `infisical.dc-tech.work`,
     `grafana.dc-tech.work`, etc.) en valeur par défaut.
   - ✅ Liste vide ou exemples génériques (`example.com`) commentés.
   - ❌ Pas de credentials, tokens, ou IPs internes.
2. **`helm/README.md` / chart `README.md`** :
   - ❌ Pas de référence à des hostnames internes spécifiques.
   - ✅ Utiliser `example.com` ou `<your-domain>` dans la doc.
3. **`helm/.helmignore`** (à créer si absent) :
   ```
   # Project-specific: exclude development-only values overrides
   values/

   # Never package local TLS material (real certs are mounted at runtime)
   certs/
   *.crt
   *.pem
   *.key
   ```
4. **Avant push, render le chart en local** et grep les leak potentiels :
   ```bash
   helm template helm/<chart-name> | grep -iE "dc-tech\.work|192\.168|10\.0\.|api[_-]?key|password.*=.*[a-zA-Z0-9]{12,}"
   ```
   Doit retourner vide. Sinon → fix avant push.

### Repos concernés

| Repo | Public ? | Chart hygiene requise ? |
|---|---|---|
| **dc-finance** | privé | non (bonne pratique tout de même) |
| **solar-monitoring** | privé | non |
| **code-search** | privé | non |
| **genai-benchmark-tool** | privé | non |

Les 4 repos sont actuellement privés (vérifié le 2026-05-14). Si un repo
passe public plus tard, **appliquer la checklist au moment du passage** —
avant de re-push le chart. Vérifier avec
`gh repo view didlawowo/<repo> --json visibility`.

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
oci-storage.dc-tech.work/mirror-binfmt:latest
```
Le pull se fait depuis oci-storage (cluster-local, pas de rate limit), pas de
secret nécessaire, transparent pour les appelants.

### Pré-requis côté infra (à faire une fois par Chris)

```bash
skopeo copy --multi-arch all docker://docker.io/tonistiigi/binfmt:latest docker://oci-storage.dc-tech.work/mirror-binfmt:latest
```

Note: oci-storage n'accepte que des paths à 2 segments max (`<ns>/<repo>` ou
`<repo>`), d'où le nom plat `mirror-binfmt` plutôt qu'une arborescence
`mirror/tonistiigi/binfmt`.

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

## 🚧 Dérogations légitimes

Le brief impose un pattern unique pour éviter la divergence. Mais quelques
cas ont une cause technique solide qui justifie une dérogation. **Toute
dérogation doit être explicitement justifiée dans la description de la PR**,
avec la cause technique réelle (pas "ça me semble plus simple").

### Cas connus

| Repo | Dérogation | Raison technique |
|---|---|---|
| **oci-storage** | image **reste sur Docker Hub** (`didlawowo/oci-storage`), pas migrée vers `oci-storage.dc-tech.work`. CD push uniquement vers Docker Hub. `values.yaml` non modifié. | Paradoxe bootstrap : si le registry est down, on ne peut plus pull sa propre image pour le redéployer. Circular dependency rejetée. |

### Ce qui n'est PAS une dérogation acceptable

- ❌ "Le repo a déjà un workflow différent, je le garde" — non, refactor.
- ❌ "Builder dans `ci.yml` au lieu d'un CD orchestrator dédié" — non, sépare.
- ❌ "Pinner `@main` parce que `@v1` n'a pas X" — non, demande un bump de tag.
- ❌ "Garder les `DOCKER_*` parce qu'ils sont là" — non, switch vers `OCI_*`.
- ❌ "Pas besoin de signer / SBOM, c'est un petit repo" — non, pattern unique.

Si tu hésites : **demande à Chris** avant d'ouvrir la PR. Pas d'interprétation
créative du brief.

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
