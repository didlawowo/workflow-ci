# Règles locales — workflow-ci

## Périmètre : un socle générique, pas un patch par consommateur

`workflow-ci` est le socle CI commun à tous les dépôts. Il définit les
conventions ; les consommateurs s'y conforment. Un agent qui travaille sur un
dépôt consommateur et rencontre un échec de CI corrige d'abord **chez lui**. Il
ne vient pas adapter `workflow-ci` à son dépôt.

### Test d'éligibilité

Une issue ou une PR sur `workflow-ci` n'est recevable que si les trois
conditions sont réunies :

1. **Générique** : le changement profite à tout consommateur qui a le même
   besoin, pas à un seul dépôt. La description l'énonce sans nommer de
   consommateur. Un dépôt ne sert que d'exemple ou de reproduction.
2. **Conforme au contrat existant** : le comportement suit une convention déjà
   documentée (`.python-version`, `go.mod`, lockfile, outillage verrouillé du
   consommateur, runner fourni par l'appelant) ou un standard de l'outil. Une
   nouvelle convention de nommage ou de configuration commune à plusieurs
   dépôts exige une décision de Chris, référencée dans l'issue. Un `input` ou
   une variable opt-in, sans effet par défaut, n'en exige pas.
3. **Contrôles inchangés ou durcis** : une option nouvelle est un `input` ou
   une variable explicite, sans effet tant qu'elle n'est pas positionnée. Un
   gate, un seuil ou un scan ne s'assouplit jamais pour débloquer un dépôt. Un
   défaut fonctionnel erroné (mauvaise version lue, chemin mal résolu) peut
   être corrigé s'il satisfait les conditions 1 et 2.

### Refus systématiques

Refuser, et rediriger vers le dépôt consommateur ou, pour un défaut de
proxy, de DNS ou de runner, vers l'infrastructure (`continuous-delivery`) :

- un nom de consommateur, un chemin, une constante ou un comportement propre à
  un dépôt dans `.github/`, `.ci/`, `.forgejo/` ou les actions (`if repo ==`,
  profil dédié à un runner ou à une machine, valeur métier) ;
- un seuil, un gate, un niveau d'audit, un scan ou une règle d'intégrité
  affaibli parce qu'un consommateur échoue ;
- un nouveau template, une nouvelle documentation ou un nouvel exemple écrit
  pour un seul dépôt (`templates/<repo>-*.yaml`, `docs/` d'incident d'un
  consommateur) ;
- une nouvelle consigne d'exploitation pour un dépôt nommé dans `README.md`.
  Les résidus existants (template moto-tracker, consigne Ioniq) ne s'étendent
  pas et sont à retirer ;
- un cache, un fallback public ou une réécriture de lockfile (voir la section
  sur les proxies ci-dessous).

La réponse d'un agent à une telle demande est courte : « Hors périmètre de
`workflow-ci`. La correction va dans `<dépôt>` : <fichier ou réglage à
modifier, ou point à investiguer>. » Si un vrai manque générique apparaît, ouvrir une issue
`workflow-ci` qui décrit ce besoin générique et ses défauts ; ne pas livrer le
patch spécifique en attendant.

### Évaluateurs cachés

`hidden-evaluators/` héberge par conception un oracle par dépôt, hors de
portée du candidat. `hidden-evaluators/<repo>/` et `registry.json` échappent
donc à la condition 1 et aux refus ci-dessus, et relèvent des règles
suivantes :

- un oracle ne change jamais dans le flux d'une PR candidate. La création ou
  la modification d'un oracle passe par une PR `workflow-ci` dédiée, qui cite
  la décision de Chris ;
- un oracle ne s'aligne jamais sur le comportement du candidat qu'il évalue.
  Un oracle rouge signale d'abord un écart du candidat ;
- un changement de contrat métier (seuil, invariant, interface) exige cette
  décision avant tout changement de l'oracle. Seul un bug de l'oracle prouvé
  indépendamment du candidat (flake, import cassé) se corrige sur la même
  base ;
- le framework commun (`run.py`, `common/`, `hidden-evidence.yml`) reste
  générique et suit le test d'éligibilité.

### Revue d'une PR entrante

Avant toute revue détaillée, vérifier le test d'éligibilité. Une PR qui échoue
reçoit une revue de refus avec la redirection ci-dessus et une recommandation
de fermeture, même si le code est correct et la CI verte. La fermeture revient
à Chris.

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
encore publié, attendre la release contenant les derniers changements mergés,
en suivant le run en arrière-plan, puis ouvrir la PR `github-manager` sans
nouvelle demande. Si une PR d’adoption est déjà ouverte, la mettre à jour
plutôt que d’en créer une seconde. Ne jamais anticiper une version ni utiliser
une branche à la place du tag. La fusion
de cette PR et la synchronisation des consommateurs nécessitent leur mandat
propre ; cette règle ne les autorise pas implicitement.

Voir la procédure dans [README.md](README.md#release-follow-up).
