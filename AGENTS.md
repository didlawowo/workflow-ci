# Règles locales — workflow-ci

## Publication et adoption

Après chaque nouvelle release de `workflow-ci`, mettre à jour la version utilisée
par `didlawowo/github-manager` : `WORKFLOW_CI_VERSION` dans
`src/quality_policy.py`, les références statiques GitHub/Forgejo et les tests
associés. Utiliser uniquement un tag effectivement publié, puis vérifier les
tests et la CI de la PR de mise à jour. Cette étape fait partie du suivi de
release ; une publication centrale ne met pas automatiquement à jour les
consommateurs. Un merge ou une publication n'autorise pas un déploiement.

Voir la procédure dans [README.md](README.md#release-follow-up).
