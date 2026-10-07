# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and follows the [Conventional Commits](https://www.conventionalcommits.org) spec.

## [1.18.0] - 2026-10-07

### Bug Fixes

- *(test)* Avoid sudo in Forgejo mutation smoke

- *(mutation)* Isolate candidate caches in runner-local scratch

- *(test)* Keep no-project Forgejo smoke independent of package proxy

- *(release)* Do not require language caches for post-bump commands


### Documentation

- *(agent)* Enforce shared package proxy architecture

- *(cache)* Replace local cache profiles with proxy-first architecture

- Align README with proxy-first dependency architecture

- Document proxy-first package and scratch storage model

- *(forgejo)* Align dependency storage with shared proxies

- *(bench)* Mark NFS uv-cache experiment as historical

- *(precommit)* Document shared local quality contract

- *(cache)* Restore out-of-cluster LAN DNS statement


### Features

- *(precommit)* Centralize local developer quality gates

- *(precommit)* Centralize local developer quality gates

- *(precommit)* Centralize local developer quality gates

- *(precommit)* Centralize local developer quality gates

- *(precommit)* Centralize local developer quality gates


### Other

- *(quality)* Remove Phoenix local dependency cache profile

- *(forgejo)* Remove public PyPI fallback


### Refactor

- *(cache)* Remove local profile and public registry fallback

- *(python)* Use runner proxy configuration without managed uv cache

- *(python)* Stop requiring runner dependency caches

- *(go)* Use GOPROXY without mandatory NFS caches

- *(node)* Rely on Verdaccio instead of runner cache profiles

- *(mutation)* Stop selecting dependency caches by runner type

- *(quality)* Stop requiring NFS uv cache

- *(mutation)* Remove NFS cache preflight from policy helpers

- *(forgejo)* Remove NFS dependency cache requirement

- *(forgejo)* Inherit package proxy without NFS cache gate

- *(node)* Remove dead lockfile cache resolver


### Tests

- *(quality)* Stop depending on runner cache profiles

- *(mutation)* Require runner proxy config instead of public fallback

- *(forgejo)* Remove proxy fallback expectation

- *(cache)* Enforce proxy-first runner contract

- *(quality)* Reject runner-specific dependency cache selection

- *(mutation)* Enforce runner-local scratch isolation

- *(mutation)* Align cache tests with local scratch

- *(go)* Align cache contract with GOPROXY architecture

- *(quality)* Align reporter with proxy-first dependency model

- *(forgejo)* Drop obsolete NFS cache contract

- *(ci)* Remove stale NFS cache expectations

- *(release)* Make post-bump independent of language caches

- *(node)* Reject removed GitHub cache contract

- *(cache)* Validate concrete ARC proxy endpoints

- *(forgejo)* Skip root boundary smoke when runner cannot elevate


