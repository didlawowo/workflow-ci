# Forgejo CI — V1

## Périmètre

Trois workflows réutilisables partagent la CI actuellement utilisée par
moto-tracker, sans dépendance aux actions internes `$/…` de GitHub :

| Workflow | Entrées | Comportement |
| --- | --- | --- |
| `taskfiles.yaml` | `runner`, `validation-script` | Installe Task puis lance le script de validation du consommateur |
| `python-postgres.yaml` | `runner`, `working-directory`, `python-version`, `requirements-file` | Installe uv et les dépendances, puis lance pytest avec couverture et PostgreSQL 16 |
| `node-build.yaml` | `runner`, `working-directory`, `node-version`, `build-test-script` | Installe npm, construit et lance éventuellement un script de vérification du build |

`runner` est obligatoire. Sur le pilote moto-tracker, `ubuntu-latest` est
le label existant du runner **interne** Forgejo, associé à `node:20-bookworm`.
Le consommateur ne définit pas `runs-on` sur les jobs qui utilisent `uses`.
Le runner doit créer un réseau Docker par job pour que le service `postgres`
soit joignable par ce nom. Le workflow Python fournit `TEST_DATABASE_URL`
vers la base jetable `ci_test`, et vide `DATABASE_URL` et `DB_HOST` comme la
CI actuelle de moto-tracker. Il suppose un projet utilisant un fichier de
requirements et pytest ; ce n'est pas encore un workflow Python universel.

Le workflow Node utilise `npm ci` si un lockfile npm existe ; sinon il
conserve `npm install`, nécessaire au pilote qui n'a pas de lockfile.
Les installateurs Task/uv sont ceux du pilote actuel. Leur versionnement
pourra être traité séparément. Les nouveaux checkouts désactivent la
persistance des identifiants Git dans la copie de travail.

La V1 conserve les logs et la couverture textuelle. Les rapports de PR,
les artefacts, les scans, la politique de mutation, les images et les
releases restent hors de cette extraction initiale.

## Publication et activation

Actions de l'agent lors de la livraison :

1. Faire relire et publier ces ajouts dans le dépôt canonique `didlawowo/workflow-ci`.
2. Publier une release selon le mécanisme existant du dépôt.
3. Remplacer les trois références du template par le tag effectivement publié
   contenant les corrections. Le template utilise `v1.16.0`, première release
   publiée contenant les trois workflows. Ce tag ne contient pas encore la
   validation NFS ajoutée par la PR #159 : adopter la release de cette correction
   pour appliquer la politique NFS obligatoire.
4. Copier `templates/forgejo/moto-tracker-ci.yaml` dans
   `moto-tracker/.forgejo/workflows/ci.yaml` puis ouvrir une PR pilote.
5. Vérifier le run Forgejo réel : expansion des trois workflows, checkout du
   dépôt consommateur, résolution DNS de `postgres`, tests et build.
6. Vérifier les noms de checks obtenus et adapter la protection de branche si
   elle exige les anciens noms avant de fusionner la PR pilote.

Les appels utilisent le dépôt GitHub public comme source unique. Aucun token
GitHub supplémentaire ni nouveau secret n'est nécessaire pour télécharger ces
workflows. Les appels privés vers un éventuel miroir Forgejo ne sont pas
validés par cette V1 ; l'accès au dépôt central devra être vérifié avant de
changer les URLs. Une synchronisation vers Forgejo pourra reprendre ces mêmes
fichiers, sans deuxième implémentation des jobs.

L'activation du pilote attend la publication centrale : ne pas remplacer une
CI fonctionnelle par des appels à une référence inexistante.
Aucune intervention utilisateur ni aucun déploiement de production n'est
nécessaire pour les validations locales. Le merge et toute opération de
production restent soumis aux autorisations habituelles.

## Vérification

Les quatre fichiers, y compris le template consommateur, passent le schéma
natif de `forgejo-runner validate` de la version 12.7.3 déployée. Le serveur
pilote répond en Forgejo 15.0.6. Les commandes applicatives sont vérifiées
sur moto-tracker avec PostgreSQL 16 jetable et Node 22 localement :
24 tests backend passent (couverture 69,18 %), ainsi que les 2 tests de
build PWA et la validation des 3 Taskfiles. Le build PWA passe également.
Cette validation ne remplace pas un run Forgejo après publication : le schéma
ne vérifie ni l'existence du tag central ni l'expansion distante ni les droits
sur le dépôt consommateur. Le conteneur Node et l'installation des outils Linux
seront également vérifiés par ce run.

## Risques et rollback

L'expansion des workflows peut changer les noms des checks. L'accès réseau
GitHub reste nécessaire tant que les appels ne ciblent pas un miroir Forgejo.
Le pilote permet de vérifier ces points avant d'onboarder d'autres dépôts.

Le rollback consiste à restaurer le fichier CI autonome précédent de
moto-tracker. Aucun changement de données ni de déploiement applicatif n'est
introduit par cette migration.

## Politique de cache Python NFS

Le job Python conserve le `UV_CACHE_DIR` fourni par le runner. Le répertoire
absolu doit exister, être inscriptible et appartenir à un montage `nfs`/`nfs4`
visible dans le conteneur du job. Le contrôle lecture/écriture est borné à six
secondes et précède l'installation de uv et des dépendances. Un chemin absent,
local ou indisponible bloque le job ; aucun cache local de secours n'est créé.
Le runner doit fournir bash, GNU timeout et util-linux findmnt. Configurer
uniquement le montage sur l'hôte sans le rendre visible dans le job ne suffit pas.

Le workflow Forgejo embarque le même validateur que `.ci/nfs-cache.sh` pour
éviter un checkout de code du consommateur comme source du contrôle. Un test
vérifie leur identité et l'ordre des étapes. Les autres contrats couvrent la
base PostgreSQL, les paramètres npm et la dépendance à la validation Taskfiles.
Ces tests ne remplacent pas la recette distante Forgejo après publication.
