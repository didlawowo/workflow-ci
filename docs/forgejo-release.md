# Release sur Forgejo

L'action `.forgejo/actions/release` applique sur Forgejo la politique du workflow
GitHub `release.yml` : à chaque exécution sur la branche publiée, elle calcule la
version suivante à partir des commits conventionnels, réécrit `CHANGELOG.md` et
les fichiers de version déclarés, puis publie le commit de release, le tag et la
release Forgejo.

C'est une action et non un workflow réutilisable : un workflow Forgejo distant ne
connaît pas sa propre référence et devrait donc recloner `workflow-ci` pendant le
job. L'action est livrée par le runner avec ses scripts, à la version épinglée
par l'appelant, sans téléchargement supplémentaire.

## Utilisation

```yaml
name: Release

on:
  push:
    branches: [main]

jobs:
  release:
    runs-on: <label du runner Forgejo interne>
    steps:
      - uses: https://data.forgejo.org/actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: https://github.com/didlawowo/workflow-ci/.forgejo/actions/release@<tag publié>
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          version-files: |
            pyproject.toml:^version = "(.+)"$
          build-number-files: |
            app/build.properties:^buildNumber=(\d+)$
```

Le checkout complet (`fetch-depth: 0`) est obligatoire : l'action refuse un
historique tronqué, qui fausserait le calcul depuis le dernier tag. L'image du
job doit fournir `git`, `bash` et `curl`.

## Entrées

| Entrée | Défaut | Rôle |
| --- | --- | --- |
| `token` | obligatoire | Jeton du job, autorisé à pousser sur la branche et à créer une release. Seule l'étape de publication le reçoit. |
| `version-files` | vide | Lignes `chemin:regex` ; le groupe capturé reçoit la version sans préfixe. Fichier absent : avertissement. Regex sans correspondance : échec. |
| `build-number-files` | vide | Lignes `chemin:regex` ; le groupe capturé est un entier, augmenté de 1 à chaque release. |
| `tag-prefix` | `v` | Préfixe des tags. |
| `cliff-config` | vide | `cliff.toml` du dépôt appelant ; sinon la configuration commune de `workflow-ci`. |
| `dry-run` | `false` | `true` : calcule et affiche tout, sans commit, tag, push ni release. |

Sorties : `released`, `version`, `build-number`.

La regex est lue telle quelle, espaces compris : une ligne indentée s'écrit avec
son indentation après l'ancre (`^        clé: (.+)$`).

## Numéro de build

Chaque release augmente le numéro de 1. Si les fichiers déclarés ne portent pas
la même valeur, tous reçoivent la plus haute plus 1 : le numéro ne recule dans
aucun fichier. Un fichier absent, une regex sans correspondance ou une valeur
non entière font échouer la release avant toute écriture.

## Règles de version

- `feat` : version mineure ; `fix` et les autres types publiables : correctif ;
  changement cassant : majeure.
- `chore`, `ci`, `docs`, `style` et `test` seuls ne publient rien
  (`release_commit_gate.py`, comme sur GitHub).
- Sans tag existant, git-cliff part du début de l'historique : poser un premier
  tag sur la branche avant d'activer l'action évite une première version et un
  changelog calculés depuis l'origine du dépôt.

## Boucle et reprise

Le commit de release est de type `chore` : il ne déclenche jamais lui-même une
nouvelle version, quelle que soit la façon dont Forgejo traite les pushs faits
avec le jeton du job. Son message porte aussi la balise de saut de CI, comme sur
GitHub.

La publication est idempotente. Une exécution qui trouve le commit de release
en tête de branche reprend là où la précédente s'est arrêtée : elle pose le tag
et la release s'ils manquent, et ne fait rien s'ils existent déjà au bon commit.
Si la branche a avancé depuis le checkout, le push est refusé et rien n'est
publié ; l'exécution suivante couvre tous les commits depuis le dernier tag.

## Outils

Le calcul utilise git-cliff à la même version que l'action GitHub
`git-cliff-bump`. S'il n'est pas déjà sur le runner à cette version, il est
fourni par `uv` depuis l'index de paquets configuré sur le runner.

## Vérification

`forgejo-runner validate --action` valide le schéma de l'action. Les tests du
dépôt couvrent le calcul, les fichiers de version, le numéro de build, la
reprise et le mode `dry-run`. Ils ne remplacent pas une exécution réelle sur
Forgejo : les droits du jeton du job (push sur la branche, création de release)
et le comportement du runner se vérifient par un `dry-run`, puis par une première
release sur un dépôt Forgejo.
