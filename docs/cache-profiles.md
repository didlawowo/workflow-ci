# Profils de stockage des caches runners

Les helpers de cache (`UV_CACHE_DIR`, `GOCACHE`, `GOMODCACHE`,
`NPM_CONFIG_CACHE`) sont résolus par `.ci/nfs-cache.sh`. Le profil est
**explicite** : en l'absence de configuration, le comportement historique NFS
est conservé, sans repli silencieux.

## Sélection

| Variable | Valeurs | Défaut |
| --- | --- | --- |
| `WORKFLOW_CACHE_PROFILE` | `nfs`, `local` | `nfs` |
| `WORKFLOW_LOCAL_ROOT` | chemin absolu local (obligatoire en `local`) | — |

- `nfs` : les variables doivent pointer vers un répertoire de runner fourni,
  monté en `nfs`/`nfs4`. Toute autre configuration échoue avec `::error::`.
- `local` : un répertoire de travail isolé est créé sous `WORKFLOW_LOCAL_ROOT`
  (`job.XXXXXX`), et `UV_CACHE_DIR`, `GOCACHE`, `GOMODCACHE`,
  `NPM_CONFIG_CACHE`, `UV_PROJECT_ENVIRONMENT`, `TMPDIR` et
  `UV_PYTHON_INSTALL_DIR` y sont placés en `0700`.
- Toute autre valeur de profil échoue explicitement (`Unknown
  WORKFLOW_CACHE_PROFILE`).

Le profil `local` refuse un `WORKFLOW_LOCAL_ROOT` relatif, absent, non possédé
par l'utilisateur, atteint via un lien symbolique, ou situé sur un système de
fichiers réseau. Il n'existe **aucun repli** vers `/tmp` ou vers un cache NFS
implicite.

## Points d'entrée

- `require_runner_cache <VARIABLE>` : nom canonique, gère les deux profils.
- `require_nfs_cache <VARIABLE>` : alias conservé pour les appelants de
  politique *trusted* ; il honore également `WORKFLOW_CACHE_PROFILE`.

Les actions composites `setup-python-env` et `setup-go-env` utilisent ce helper.
`setup-node-env` configure `NPM_CONFIG_CACHE` explicitement avant
`actions/setup-node` lorsque le profil `local` est demandé.

## Quand ne pas mettre de cache

- Cache absent, invalide ou corrompu : la reconstruction à froid locale doit
  toujours rester possible et ne jamais bloquer le job.
- Dépendances peu coûteuses à reconstruire : le coût de restauration et de
  publication d'une archive peut dépasser le gain.
- Le cache BuildKit dispose de son propre mécanisme et reste hors de ce
  chemin (voir #165).

L'adoption en production reste conditionnée à une mesure : profil local et
NFS comparés sur un même commit, à concurrence égale, avec au moins trois
répétitions à froid/à chaud (voir #167/#168).
