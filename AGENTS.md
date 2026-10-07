# Règles locales — workflow-ci

## Architecture des proxies de dépendances

Les proxies de dépendances sont l'autorité de cache partagée. `workflow-ci` ne
doit pas créer de cache local par runner, de cache NFS supplémentaire, de
réécriture de lockfile ou de fallback réseau complexe pour masquer un problème
d'accès à ces proxies.

Services de référence :
- Python/PyPI : Proxpi ;
- npm : Verdaccio ;
- Go modules : Athens.

Le choix de l'endpoint est une responsabilité d'infrastructure, pas du projet
consommateur ni des actions génériques de `workflow-ci` :
- les runners ARC dans Kubernetes utilisent les Services/DNS Kubernetes
  (`*.svc.cluster.local`) ;
- les runners système/hors cluster utilisent un endpoint LAN/DNS classique vers
  les mêmes proxies ;
- un défaut de routage, DNS ou exposition entre un runner et un proxy doit être
  corrigé dans l'infrastructure du runner ou du proxy.

Règles impératives pour tout changement CI :
- ne jamais ajouter un cache local persistant pour contourner un proxy
  inaccessible ;
- ne jamais ajouter un fallback PyPI/npm/proxy.golang.org spécifique à un type
  de runner si le problème réel est l'accès au proxy interne ;
- ne jamais faire dépendre une action générique Python/Node/Go d'une logique
  spécialisée de mutation, de sécurité ou d'un autre sous-système pour gérer
  les proxies ;
- ne pas réécrire, exporter ou dégrader un lockfile uniquement parce qu'il
  contient un endpoint inaccessible depuis un runner : traiter d'abord la
  topologie réseau, le DNS et la provenance du lock ;
- ne pas dupliquer les politiques de sélection de registry/proxy dans plusieurs
  actions. Si une sélection d'endpoint est nécessaire, elle doit provenir de la
  configuration du runner ;
- les répertoires locaux temporaires restent autorisés pour les workspaces,
  builds et sandboxes jetables. Un scratch local n'est pas un cache de
  dépendances et doit être supprimable sans perte fonctionnelle.

Avant de proposer une correction de performance ou de disponibilité liée aux
dépendances, vérifier dans cet ordre :
1. le proxy partagé existe et est sain ;
2. le runner peut résoudre et joindre l'endpoint prévu pour son environnement ;
3. la configuration du runner pointe bien vers ce proxy ;
4. seulement ensuite, chercher un défaut dans `workflow-ci`.

Une PR qui compense un défaut d'infrastructure par un nouveau cache, un fallback
public ou une réécriture de lockfile doit être considérée comme
architecturalement incorrecte tant que la cause réseau/DNS n'a pas été écartée.

## Publication et adoption

Après chaque nouvelle release de `workflow-ci`, mettre à jour la version utilisée
par `didlawowo/github-manager` : `WORKFLOW_CI_VERSION` dans
`src/quality_policy.py`, les références statiques GitHub/Forgejo et les tests
associés. Utiliser uniquement un tag effectivement publié, puis vérifier les
tests et la CI de la PR de mise à jour. Cette étape fait partie du suivi de
release ; une publication centrale ne met pas automatiquement à jour les
consommateurs. Un merge ou une publication n'autorise pas un déploiement.

Ce suivi est automatique pour l'agent : ne pas attendre une nouvelle demande
de l'utilisateur après la publication. Enchaîner sur la mise à jour de
`github-manager`, les tests, le commit, le push, l'ouverture ou la mise à jour
de sa PR et la vérification de sa CI. Ne pas annoncer le suivi de release
terminé tant que cette PR n'est pas à jour et sa CI verte. Si le tag n'est pas
encore publié, conserver cette étape comme restant à faire ; ne jamais
anticiper une version ni utiliser une branche à la place du tag. La fusion
de cette PR et la synchronisation des consommateurs nécessitent leur mandat
propre ; cette règle ne les autorise pas implicitement.

Voir la procédure dans [README.md](README.md#release-follow-up).
