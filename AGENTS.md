# Règles locales — workflow-ci

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
