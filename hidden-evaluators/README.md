# Hidden evaluators

`hidden-evaluators/` contient les oracles fonctionnels centralisés utilisés par les
repos consommateurs de `workflow-ci`. Ils complètent les tests publics et le mutation
testing : leur objectif est de vérifier des invariants fonctionnels sur des vecteurs
qui ne sont pas définis dans la PR candidate.

## Arborescence

```text
hidden-evaluators/
├── common/
│   └── report.py
├── registry.json
├── run.py
├── ioniq-control/
│   └── calibration/
│       └── evaluator.py
├── keryx/                  # prévu par didlawowo/keryx#469
├── jet-racer-v2/           # prévu par didlawowo/jet-racer-v2#58
└── solar-monitoring/       # prévu par didlawowo/solar-monitoring#256
```

Le workflow réutilisable est `.github/workflows/hidden-evidence.yml`.

## Contrat de confiance

Un consumer choisit uniquement un identifiant d'evaluator et un runner. Le registre
central lie chaque identifiant à **un repository exact**, un entrypoint central et une
liste de paths sensibles. `run.py` refuse donc qu'une PR de `keryx` demande, par
exemple, l'oracle d'`ioniq-control`.

Le workflow :

1. matérialise l'exact `job.workflow_sha` de `workflow-ci` dans un checkout trusted ;
2. matérialise l'exact `pull_request.head.sha` du consumer dans un checkout séparé ;
3. vérifie les SHA avant toute exécution ;
4. calcule le diff `base...head` et décide le scope à partir de `registry.json` ;
5. génère un seed runtime aléatoire ;
6. exécute l'oracle central contre le checkout candidat ;
7. publie un rapport compact sans le seed brut ;
8. échoue le gate uniquement sur `fail` ou `error` de l'oracle.

Les permissions du job sont limitées à `contents: read` et les checkouts utilisent
`persist-credentials: false`.

## Rapport

Le rapport public suit `schema_version=1` :

```json
{
  "schema_version": 1,
  "evaluator": "ioniq-control/calibration",
  "repository": "didlawowo/ioniq-control",
  "base_sha": "...",
  "head_sha": "...",
  "seed_id": "opaque-hash",
  "status": "pass",
  "checks": [{"name": "...", "status": "pass"}],
  "duration_ms": 123
}
```

`pass`, `fail`, `error` et `skipped` ont des sens distincts. Une panne de l'oracle
n'est jamais transformée en succès.

## Replay

Le seed brut est conservé uniquement dans un **replay capsule** diagnostique, uploadé
best-effort avec une rétention courte. Cet artifact n'est pas requis pour le verdict :
un quota GitHub Actions plein ne doit pas transformer un gate fonctionnel vert en rouge.
Pour un replay trusted local, définir `HIDDEN_EVALUATOR_REPLAY_SEED` avant d'appeler
`hidden-evaluators/run.py run`.

## Threat model

Ce mécanisme empêche une PR consumer de modifier l'oracle qui évalue cette même PR et
rend les vecteurs exacts imprévisibles avant l'exécution. Il réduit donc fortement
l'overfitting accidentel ou agentique aux fixtures publiques.

Ce n'est pas une sandbox hostile complète : l'oracle et le candidat sont exécutés sur
le même runner et un acteur disposant d'un accès en lecture à `workflow-ci` peut lire
la logique générale des evaluators. Les valeurs exactes restent randomisées par run.
Une isolation processus/container plus forte pourra être ajoutée sans changer le
contrat consumer.

## Ajouter un evaluator

1. ajouter son entrypoint sous `hidden-evaluators/<repo>/<domaine>/` avec une fonction
   `evaluate(candidate: Path, seed: int) -> list[dict]` ;
2. enregistrer repository, entrypoint et paths dans `registry.json` ;
3. ajouter des tests de contrat dans `tests/` ;
4. ne jamais prendre les seuils de décision depuis la PR candidate lorsqu'ils font
   partie de l'oracle ;
5. privilégier une implémentation de référence indépendante pour générer/mesurer les
   données synthétiques.
