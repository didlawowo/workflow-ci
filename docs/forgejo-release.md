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
  workflow_dispatch:

concurrency:
  group: release-${{ github.repository }}
  cancel-in-progress: false

jobs:
  release:
    if: github.ref == 'refs/heads/main'
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
historique tronqué, qui fausserait le calcul depuis le dernier tag.

Le groupe `concurrency` fait attendre deux releases rapprochées au lieu de les
laisser se doubler ; une action ne peut pas le déclarer elle-même, il appartient
au workflow appelant. `workflow_dispatch` sert à relancer depuis la tête de
branche (voir « Reprise »). La condition `if` réserve la release à la branche
publiée : l'action accepte toute branche, c'est au workflow appelant de
restreindre laquelle.

L'image du job doit fournir `git`, `bash` et Node (l'action `setup-uv` est en
JavaScript). `uv` télécharge Python 3.12 s'il n'est pas déjà présent.

Seuls `CHANGELOG.md` et les fichiers déclarés entrent dans le commit de
release : ce que les étapes précédentes du job ont laissé dans le dépôt n'est
jamais commité. Un de ces fichiers ignoré par `.gitignore` fait échouer la
release avant toute écriture.

## Entrées

| Entrée | Défaut | Rôle |
| --- | --- | --- |
| `token` | obligatoire | Jeton du job, autorisé à pousser sur la branche et à créer une release. Seule l'étape de publication le reçoit. |
| `version-files` | vide | Lignes `chemin:regex` ; le groupe capturé reçoit la version sans préfixe. Seule la première correspondance du fichier est réécrite, comme sur GitHub. Fichier absent : avertissement. Regex sans correspondance : échec. |
| `build-number-files` | vide | Lignes `chemin:regex` ; le groupe capturé est un entier, augmenté de 1 à chaque release. Toutes les correspondances du fichier sont réécrites. |
| `tag-prefix` | `v` | Préfixe des tags. |
| `cliff-config` | vide | `cliff.toml` du dépôt appelant ; sinon la configuration commune de `workflow-ci`. |
| `dry-run` | `false` | `true` : calcule et affiche tout, sans commit, tag, push ni release. |

Sorties : `released`, `version`, `build-number`. Elles décrivent la tête de
branche : `released` vaut `true` quand une nouvelle version vient d'y être
publiée, ou quand le commit de release qui s'y trouve vient d'être complété
(`build-number` est alors vide). Une reprise sur un commit plus ancien est
signalée dans le journal, pas dans les sorties, de même que la création
après coup de la release d'un tag déjà poussé.

La regex est lue telle quelle, espaces compris : une ligne indentée s'écrit avec
son indentation après l'ancre (`^        clé: (.+)$`). Les fichiers aux fins de
ligne Windows, même mélangées, sont lus comme les autres et chaque ligne garde
sa fin de ligne. Pour
viser une occurrence précise de la version dans un fichier qui en compte
plusieurs, donner une regex plus précise (une regex peut s'étendre sur
plusieurs lignes avec `[\s\S]*?`).

## Numéro de build

Chaque release augmente le numéro de 1. Toutes les correspondances de tous les
fichiers déclarés sont lues : si elles ne portent pas la même valeur, toutes
reçoivent la plus haute plus 1, et le numéro ne recule nulle part. Un fichier
absent, une regex sans correspondance ou une valeur non entière font échouer la
release avant toute écriture dans le dépôt.

## Règles de version

- `feat` : version mineure ; `fix` et les autres types publiables : correctif ;
  changement cassant : majeure.
- `chore`, `ci`, `docs`, `style` et `test` seuls ne publient rien
  (`release_commit_gate.py`, comme sur GitHub).
- Sans tag existant, git-cliff part du début de l'historique : poser un premier
  tag sur la branche avant d'activer l'action évite une première version et un
  changelog calculés depuis l'origine du dépôt.

## Boucle

Le commit de release est de type `chore` : il ne déclenche jamais lui-même une
nouvelle version, quelle que soit la façon dont Forgejo traite les pushs faits
avec le jeton du job. Son message porte aussi la balise de saut de CI, comme sur
GitHub.

## Reprise

Chaque exécution vérifie d'abord ce que la précédente a pu laisser inachevé,
puis calcule la version suivante. La même version n'est jamais publiée deux
fois.

- **Commit de release poussé sans son tag.** L'exécution suivante (prochain
  push, ou `workflow_dispatch` sur la branche) le trouve après le dernier tag
  connu, pose le tag sur ce commit et crée sa release.
- **Tag poussé sans sa release Forgejo.** Si le dernier tag porte sur un commit
  de release annonçant cette version, l'exécution suivante crée la release
  manquante. Un tag posé à la main n'est jamais concerné. Cette vérification
  a lieu à chaque exécution et lit l'API de la forge avec le jeton du job ; si
  elle échoue, c'est un avertissement et non un échec.

Relancer le job en échec ne suffit pas : il repart de l'ancien commit et son
push est refusé.

Seul un commit dont le sujet annonce une version sémantique au bon préfixe est
repris. Un commit de release inutilisable (nom de tag refusé par git, tag déjà
porté par un autre commit, version qui n'est pas supérieure à la précédente)
est ignoré avec un avertissement et ne bloque pas les releases suivantes. Sans
aucun tag dans le dépôt, seul le commit en tête de branche peut être repris :
un historique importé n'est pas re-tagué en masse.

Un tag n'est jamais déplacé : si le tag de la nouvelle version existe déjà sur
un autre commit, l'exécution échoue et demande une correction manuelle. Si la
branche a avancé depuis le checkout, le push est refusé et rien n'est publié ;
l'exécution suivante couvre tous les commits depuis le dernier tag.

L'étape de calcul ne laisse aucun tag dans le dépôt local, en `dry-run` comme
en exécution réelle : seule l'étape de publication crée des tags.

## Écarts avec le workflow GitHub

Non repris de `release.yml` : `post-bump-command` (fichiers dérivés comme
`uv.lock`), `validated-sha`, `trigger-cd-workflow` et les notes générées par la
forge quand git-cliff n'en produit pas (la release est alors créée avec un
texte vide).

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
