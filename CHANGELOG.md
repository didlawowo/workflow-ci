# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and follows the [Conventional Commits](https://www.conventionalcommits.org) spec.

## [1.9.1] - 2026-09-24

### Bug Fixes

- *(ci)* Execute BuildKit retry failure guard (#97)


## [1.8.3] - 2026-09-24

### Bug Fixes

- *(ci)* Survive exhausted Actions artifact storage (#94)

- *(release)* Publier automatiquement workflow-ci depuis main (#95)


## [1.8.2] - 2026-09-23

### Bug Fixes

- *(ci)* Make SARIF publication non-blocking (#85)

- *(trusted)* Support non-src Python layouts and complete PR history (#88)

- *(mutation)* Avoid read-only uv cache on ARC runners (#86)

- *(mutation)* Exécuter le runner trusted depuis le worktree PR (#84)

- *(mutation)* Auto-run on production source changes (#89)

- *(mutation)* Accept exported stats after ephemeral mutmut execution (#90)

- *(python)* Harden uv cache and Bandit trusted gate (#91)

- *(mutation)* Import configparser in scoped validator (#93)


### Performance

- *(docker)* Disable CodeQL by default and reuse native BuildKit (#87)

- Reuse preinstalled CI tools on ARC runners (#92)


## [1.8.1] - 2026-09-22

### Bug Fixes

- *(ci)* Centralize mutation ownership, safe checkout and Go quality compatibility (#78)

- *(ci)* Utiliser le runner self-hosted existant pour les mutations (#81)

- *(ci)* Corriger les faux PASS mutation et fiabiliser TruffleHog (#79, #80) (#82)

- *(docker)* Bloquer les échecs SARIF et les faux compteurs zéro (#83)


## [1.8.0] - 2026-09-22

### Features

- *(ci)* V1.8.0 SonarQube quality gate (#74)


## [1.7.1] - 2026-09-21

### Bug Fixes

- *(release)* Ne jamais versionner le checkout .workflow-ci

- *(quality)* Isolate trusted evidence and pin mutable actions (#65)

- *(python)* Support uv dependency-groups in shared test actions (#67)

- *(quality)* Publish the first PR quality report (#68)

- *(ci)* Cancel stale quality and mutation runs (#70)


## [1.7.0] - 2026-09-21

### Bug Fixes

- *(release)* Ne plus committer le checkout .workflow-ci

- Restore reusable quality evidence workflow (#50)

- *(ci)* Harden mutation and quality gates (#51)

- *(ci)* Lock mutation config to protected base (#57)

- *(quality)* Run Python evidence helpers through uv

- *(python)* Use uv interpreter for test evidence

- *(ci)* Fiabiliser le runner mutation sur ARC et les PR empilées (#61)

- *(ci)* Finish issue 55 portability contracts (#62)

- *(ci)* Keep deletion-only changes in mutation scope (#63)

- *(ci)* Close remaining workflow-ci audit issues (#64)


### Features

- Add trusted PR quality evidence reporting (#49)


## [1.6.0] - 2026-09-11

### Features

- *(release)* Entrée runs-on pour choisir le runner du job de release (#46)


## [1.5.0] - 2026-09-08

### Bug Fixes

- *(docker-build-push)* Registry-plain-http + native-multiarch — registry.insecure=true sur l'export image (#38)

- *(docker-build-push)* Limiter registry.insecure=true au chemin native-multiarch (#41)

- *(release)* Sérialiser les releases et repartir de la pointe de branche (#42)

- *(ci)* Utiliser les chemins effectifs des caches Trivy et Python (#43)


### Features

- *(docker-build-push)* Opt-in registry-plain-http — push in-cluster direct sur le Service (oci-storage#99) (#36)


### Performance

- *(go-ci)* Réparer le cache Go (chemin go.sum) + cacher le binaire golangci-lint (#39)


## [1.4.0] - 2026-08-27

### Bug Fixes

- *(cliff)* Tag_pattern tolérant v? — corrige le premier release (#25)

- *(docker-build-push)* Nom d'artifact unique par image (évite le 409) (#26)

- *(docker-build-push)* Rendre le login Docker Hub non-fatal (#33)


### CI

- *(llm-pr-review)* Clarifier l'alias modèle + bump github-script v7->v9 (#23)

- *(templates)* Supprimer le doublon push/PR et documenter le dispatch CD (#31)


### Documentation

- *(guidelines)* Preview/CD — éviter le pod stale (tag mutable) (#30)


### Features

- *(templates)* Trigger 'labeled' pour les builds preview label-gated (#27)

- *(docker-build)* Option native-multiarch via BuildKit remote (sans QEMU) (#29)

- *(release)* Ajouter un hook post-bump avant le commit de release (#34)


### Performance

- *(docker-build)* Cache buildx registry oci-storage + cache DB Trivy (#28)


## [1.3.2] - 2026-05-31

### Bug Fixes

- *(release)* Escape "true" in output description (YAML parse error) (#10)

- *(release-example)* Guard dry-run boolean cast for push events (#11)

- *(git-cliff-bump)* Escape "true"/"false" in description (YAML parse error) (#12)

- *(go-quality)* Build golangci-lint from source & guard gosec install (#22)


### Documentation

- *(guidelines)* OCI_* secrets, chart push, public repo hygiene (#8)

- *(guidelines)* Add 'Dérogations légitimes' section (#9)

- *(guidelines)* Mandatory dockerhub-* inputs on every docker-build-push (#15)

- Caller release.yml needs actions:write for trigger-cd-workflow (#16)

- *(guidelines)* Require id-token:write on jobs that sign images (#17)

- *(guidelines)* Mandate image-tag without v prefix (fixes ImagePullBackOff) (#18)

- Fix image-tag in section B example (use version, not tag_name) (#19)

- *(migration)* Généralise les guidelines à tous les repos workflow-ci (#20)

- *(migration)* Clarifications + bootstrap repo neuf + conventional commits (#21)


### Features

- *(release)* Auto-trigger CD orchestrator after tag push (#13)

- *(docker-build-push)* Authenticate Docker Hub for base image pulls (#14)


## [1.3.1] - 2026-05-14

### Bug Fixes

- *(docker-build-push)* Flat mirror path for oci-storage (#7)


## [1.3.0] - 2026-05-14

### Bug Fixes

- *(docker-build-push)* Switch to oci-storage binfmt mirror + add migration guidelines (#6)


## [1.2.0] - 2026-05-14

### Bug Fixes

- *(docker-build-push)* Authenticate Docker Hub before setup-qemu-action (#5)


### Features

- *(release)* Add reusable git-cliff release workflow (#4)


## [1.1.0] - 2026-05-14

### Features

- *(python)* Align with dc-finance pattern, drop actions/cache (#3)


### Other

- A/B/C uv cache strategies on ARC runners (#2)

- Target arc-runner-workflow-ci (not GH-hosted)


## [1.0.0] - 2026-05-14

### Bug Fixes

- *(llm-pr-review)* Use rawfile for prompt to fix bash escaping

- *(llm-pr-review)* Sanitize diff and fix user prompt interpolation

- *(llm-pr-review)* Write payload to file and use curl -d @file

- *(llm-pr-review)* Replace json_schema with json_object for llama.cpp compat

- *(llm-pr-review)* Add /no_think for Qwen3 thinking models

- *(llm-pr-review)* Increase default timeout to 600s for large diffs

- *(llm-pr-review)* Move /no_think to start of user prompt, increase max_tokens to 16k

- *(llm-pr-review)* Fix YAML syntax error in user prompt block

- *(llm-pr-review)* Remove /no_think hack, reset max_tokens to 4000

- *(run-python-tests)* Corriger syntax error quand test-command est vide

- *(python)* Skip actions/cache quand UV_CACHE_DIR pré-défini (#1)


### Features

- Add new workflow ci

- *(llm-pr-review)* Improve review quality with confidence filtering



