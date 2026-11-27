import importlib.util
import json
import re
from pathlib import Path

import pytest


RACINE = Path(__file__).resolve().parents[1]

CHEMIN_WORKFLOW_QUALITE = RACINE / ".github" / "workflows" / "quality-evidence.yml"
CHEMIN_WORKFLOW_MUTATION = RACINE / ".github" / "workflows" / "mutation-policy.yml"
CHEMIN_README = RACINE / "README.md"
CHEMIN_MUTATION_POLICY = RACINE / ".github" / "scripts" / "mutation_policy.py"

SECTION_README = "## Pull request events that keep the running evidence"


def _charger_labels_complexite():
    spec = importlib.util.spec_from_file_location("mutation_policy", CHEMIN_MUTATION_POLICY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.COMPLEXITY_LABELS)


COMPLEXITY_LABELS = _charger_labels_complexite()


class ErreurExpression(Exception):
    pass


class _Analyseur:
    def __init__(self, tokens):
        self.tokens = tokens
        self.position = 0

    def _aperçu(self):
        if self.position >= len(self.tokens):
            return None
        return self.tokens[self.position]

    def _consommer(self, attendu=None):
        token = self._aperçu()
        if token is None:
            raise ErreurExpression("expression tronquée")
        if attendu is not None and token != attendu:
            raise ErreurExpression(f"attendu {attendu!r}, obtenu {token!r}")
        self.position += 1
        return token

    def analyser(self):
        noeud = self._ou()
        if self.position != len(self.tokens):
            raise ErreurExpression(f"tokens restants: {self.tokens[self.position:]}")
        return noeud

    def _ou(self):
        gauche = self._et()
        while self._aperçu() == ("operateur", "||"):
            self._consommer()
            droite = self._et()
            gauche = ("ou", gauche, droite)
        return gauche

    def _et(self):
        gauche = self._comparaison()
        while self._aperçu() == ("operateur", "&&"):
            self._consommer()
            droite = self._comparaison()
            gauche = ("et", gauche, droite)
        return gauche

    def _comparaison(self):
        gauche = self._negation()
        while self._aperçu() in (("operateur", "=="), ("operateur", "!=")):
            operateur = self._consommer()[1]
            droite = self._negation()
            gauche = ("comparaison", operateur, gauche, droite)
        return gauche

    def _negation(self):
        if self._aperçu() == ("operateur", "!"):
            self._consommer()
            return ("negation", self._negation())
        return self._primaire()

    def _primaire(self):
        token = self._aperçu()
        if token is None:
            raise ErreurExpression("expression vide")

        if token == ("operateur", "("):
            self._consommer()
            noeud = self._ou()
            self._consommer(("operateur", ")"))
            return noeud

        if token[0] == "chaine":
            self._consommer()
            return ("litteral", token[1])

        if token[0] == "nombre":
            self._consommer()
            return ("litteral", token[1])

        if token[0] == "identificateur":
            nom = token[1]
            self._consommer()

            if self._aperçu() == ("operateur", "("):
                self._consommer()
                arguments = []
                if self._aperçu() != ("operateur", ")"):
                    arguments.append(self._ou())
                    while self._aperçu() == ("operateur", ","):
                        self._consommer()
                        arguments.append(self._ou())
                self._consommer(("operateur", ")"))
                return ("fonction", nom, arguments)

            if nom == "null":
                return ("litteral", None)
            if nom == "true":
                return ("litteral", True)
            if nom == "false":
                return ("litteral", False)

            return ("chemin", nom)

        raise ErreurExpression(f"token inattendu: {token!r}")


def _tokeniser(expression):
    motif = re.compile(
        r"""
        (?P<espace>\s+)
      | (?P<chaine>'(?:''|[^'])*')
      | (?P<nombre>\d+(?:\.\d+)?)
      | (?P<identificateur>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
      | (?P<operateur>==|!=|&&|\|\||!|\(|\)|,)
        """,
        re.VERBOSE,
    )

    tokens = []
    position = 0
    while position < len(expression):
        correspondance = motif.match(expression, position)
        if correspondance is None:
            raise ErreurExpression(f"token invalide à {position}: {expression[position:position + 20]!r}")

        position = correspondance.end()
        categorie = correspondance.lastgroup
        valeur = correspondance.group()

        if categorie == "espace":
            continue

        if categorie == "chaine":
            interieur = valeur[1:-1].replace("''", "'")
            tokens.append(("chaine", interieur))
        elif categorie == "nombre":
            tokens.append(("nombre", int(valeur) if "." not in valeur else float(valeur)))
        elif categorie == "identificateur":
            tokens.append(("identificateur", valeur))
        else:
            tokens.append(("operateur", valeur))

    return tokens


def _est_vrai(valeur):
    if valeur is None or valeur is False:
        return False
    if valeur == "":
        return False
    if valeur == 0:
        return False
    if isinstance(valeur, (list, tuple, dict)) and len(valeur) == 0:
        return False
    return True


def _est_nombre(valeur):
    return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)


def _convertir_en_nombre(valeur):
    if _est_nombre(valeur):
        return float(valeur)
    if isinstance(valeur, str):
        try:
            return float(valeur)
        except ValueError:
            return None
    return None


def _egalite(gauche, droite):
    if gauche is None and droite is None:
        return True
    if gauche is None or droite is None:
        return False

    if isinstance(gauche, bool) or isinstance(droite, bool):
        return gauche == droite

    gauche_nombre = _convertir_en_nombre(gauche)
    droite_nombre = _convertir_en_nombre(droite)

    if _est_nombre(gauche) and isinstance(droite, str):
        return droite_nombre is not None and gauche_nombre == droite_nombre
    if isinstance(gauche, str) and _est_nombre(droite):
        return gauche_nombre is not None and gauche_nombre == droite_nombre

    if isinstance(gauche, str) and isinstance(droite, str):
        return gauche.casefold() == droite.casefold()

    if gauche_nombre is not None and droite_nombre is not None:
        return gauche_nombre == droite_nombre

    return gauche == droite


def _resoudre_chemin(chemin, contexte):
    courant = contexte
    for partie in chemin.split("."):
        if isinstance(courant, dict) and partie in courant:
            courant = courant[partie]
        else:
            return None
    return courant


def _evaluer_fonction(nom, arguments):
    nom_normalise = nom.lower()

    if nom_normalise == "contains":
        if len(arguments) != 2:
            raise ErreurExpression("contains() attend 2 arguments")
        tableau, valeur = arguments
        if tableau is None:
            return False
        if isinstance(tableau, (list, tuple)):
            return any(_egalite(valeur, element) for element in tableau)
        if isinstance(tableau, str):
            return str(valeur).casefold() in tableau.casefold()
        return False

    if nom_normalise == "fromjson":
        if len(arguments) != 1:
            raise ErreurExpression("fromJSON() attend 1 argument")
        valeur = arguments[0]
        if isinstance(valeur, str):
            return json.loads(valeur)
        return valeur

    if nom_normalise == "startswith":
        if len(arguments) != 2:
            raise ErreurExpression("startsWith() attend 2 arguments")
        gauche, droite = arguments
        if gauche is None or droite is None:
            return False
        return str(gauche).casefold().startswith(str(droite).casefold())

    if nom_normalise == "format":
        if not arguments:
            raise ErreurExpression("format() attend au moins un argument")
        gabarit = str(arguments[0])
        valeurs = arguments[1:]

        def remplacer(correspondance):
            index = int(correspondance.group(1))
            if index >= len(valeurs):
                return ""
            valeur = valeurs[index]
            if valeur is None:
                return ""
            return str(valeur)

        return re.sub(r"\{(\d+)\}", remplacer, gabarit)

    raise ErreurExpression(f"fonction inconnue: {nom}")


def _evaluer_noeeud(noeud, contexte):
    categorie = noeud[0]

    if categorie == "litteral":
        return noeud[1]

    if categorie == "chemin":
        return _resoudre_chemin(noeud[1], contexte)

    if categorie == "fonction":
        _, nom, arguments = noeud
        return _evaluer_fonction(nom, [_evaluer_noeeud(argument, contexte) for argument in arguments])

    if categorie == "negation":
        return not _est_vrai(_evaluer_noeeud(noeud[1], contexte))

    if categorie == "et":
        gauche = _evaluer_noeeud(noeud[1], contexte)
        if not _est_vrai(gauche):
            return gauche
        return _evaluer_noeeud(noeud[2], contexte)

    if categorie == "ou":
        gauche = _evaluer_noeeud(noeud[1], contexte)
        if _est_vrai(gauche):
            return gauche
        return _evaluer_noeeud(noeud[2], contexte)

    if categorie == "comparaison":
        _, operateur, gauche_noeeud, droite_noeeud = noeud
        gauche = _evaluer_noeeud(gauche_noeeud, contexte)
        droite = _evaluer_noeeud(droite_noeeud, contexte)
        if operateur == "==":
            return _egalite(gauche, droite)
        return not _egalite(gauche, droite)

    raise ErreurExpression(f"noeud inconnu: {noeud!r}")


def evaluer_expression(expression, contexte):
    tokens = _tokeniser(expression)
    noeud = _Analyseur(tokens).analyser()
    return _evaluer_noeeud(noeud, contexte)


def evaluer_gabarit(valeur, contexte):
    def remplacer(correspondance):
        resultat = evaluer_expression(correspondance.group(1), contexte)
        if resultat is None:
            return ""
        if isinstance(resultat, bool):
            return "true" if resultat else "false"
        return str(resultat)

    return re.sub(r"\$\{\{(.*?)\}\}", remplacer, valeur, flags=re.DOTALL)


def _lire(chemin):
    return chemin.read_text(encoding="utf-8")


def _extraire_noop_depuis_cancel_in_progress(texte, chemin):
    motif = re.compile(
        r"^\s*cancel-in-progress:\s*\$\{\{\s*!\((.*)\)\s*\}\}\s*$",
        re.MULTILINE,
    )
    correspondances = motif.findall(texte)
    if len(correspondances) != 1:
        raise AssertionError(
            f"La ligne cancel-in-progress de {chemin} doit être exactement de la forme `${{ !(…) }}`."
        )
    noop = correspondances[0].strip()
    assert noop, f"NOOP vide dans {chemin}"
    return noop


def _extraire_bloc_yaml_readme(texte):
    debut_section = texte.find(SECTION_README)
    assert debut_section != -1, f"Section README absente: {SECTION_README}"

    debut_bloc = texte.find("```yaml", debut_section)
    assert debut_bloc != -1, "Bloc ```yaml absent après la section README demandée"

    fin_bloc = texte.find("```", debut_bloc + len("```yaml"))
    assert fin_bloc != -1, "Bloc ```yaml non fermé dans la section README demandée"

    return texte[debut_bloc + len("```yaml"):fin_bloc]


def _extraire_ligne_bloc(bloc, cle):
    motif = re.compile(rf"^\s*{re.escape(cle)}:\s*(.*)$", re.MULTILINE)
    correspondances = motif.findall(bloc)
    assert len(correspondances) == 1, f"Ligne `{cle}:` unique attendue dans le bloc README"
    return correspondances[0].strip()


def _extraire_noop_depuis_ligne_if(ligne):
    motif = re.compile(r"^\$\{\{\s*!\((.*)\)\s*\}\}$")
    correspondance = motif.match(ligne)
    assert correspondance is not None, "La ligne if du README doit être de la forme `${{ !(…) }}`"
    noop = correspondance.group(1).strip()
    assert noop, "NOOP vide dans la ligne if du README"
    return noop


def _extraire_noop_depuis_ligne_group(ligne):
    expressions = re.findall(r"\$\{\{(.*?)\}\}", ligne, flags=re.DOTALL)
    assert expressions, "Aucune expression ${{ … }} dans la ligne group du README"

    motif = re.compile(
        r"^\s*\((.*)\)\s*&&\s*format\('-noop-\{0\}',\s*github\.run_id\)\s*\|\|\s*''\s*$",
        re.DOTALL,
    )

    for expression in expressions:
        correspondance = motif.match(expression)
        if correspondance is not None:
            noop = correspondance.group(1).strip()
            assert noop, "NOOP vide dans la ligne group du README"
            return noop

    raise AssertionError(
        "La ligne group du README doit contenir l'expression "
        "`(NOOP) && format('-noop-{0}', github.run_id) || ''`."
    )


def _contexte(action, *, label=None, changes=None, event_name="pull_request", run_attempt="1"):
    event = {"action": action}
    if label is not None:
        event["label"] = {"name": label}
    if changes is not None:
        event["changes"] = changes

    return {
        "github": {
            "event_name": event_name,
            "run_attempt": run_attempt,
            "run_id": "42",
            "repository": "o/r",
            "ref_name": "x",
            "event": event,
            "pull_request": {"number": 7},
        }
    }


def _sources_noop():
    texte_qualite = _lire(CHEMIN_WORKFLOW_QUALITE)
    texte_mutation = _lire(CHEMIN_WORKFLOW_MUTATION)
    texte_readme = _lire(CHEMIN_README)

    bloc_readme = _extraire_bloc_yaml_readme(texte_readme)
    ligne_group = _extraire_ligne_bloc(bloc_readme, "group")
    ligne_if = _extraire_ligne_bloc(bloc_readme, "if")

    return {
        "workflow quality-evidence": _extraire_noop_depuis_cancel_in_progress(
            texte_qualite, CHEMIN_WORKFLOW_QUALITE
        ),
        "workflow mutation-policy": _extraire_noop_depuis_cancel_in_progress(
            texte_mutation, CHEMIN_WORKFLOW_MUTATION
        ),
        "README if": _extraire_noop_depuis_ligne_if(ligne_if),
        "README group": _extraire_noop_depuis_ligne_group(ligne_group),
    }


def _evenements_annulent():
    evenements = [
        ("opened", None, None, "pull_request", "1"),
        ("synchronize", None, None, "pull_request", "1"),
        ("reopened", None, None, "pull_request", "1"),
    ]

    for label in COMPLEXITY_LABELS:
        evenements.append(("labeled", label, None, "pull_request", "1"))
        evenements.append(("unlabeled", label, None, "pull_request", "1"))

    evenements.append(("labeled", "mutation:requested", None, "pull_request", "1"))
    evenements.append(("unlabeled", "mutation:requested", None, "pull_request", "1"))

    evenements.append(("edited", None, {"body": {"from": "ancien corps"}}, "pull_request", "1"))
    evenements.append(("edited", None, {"base": {"from": "main"}}, "pull_request", "1"))
    evenements.append(("edited", None, {}, "pull_request", "1"))

    evenements.append(("labeled", "reviewed", None, "pull_request", "2"))
    evenements.append(("unlabeled", "human-review", None, "pull_request", "2"))
    evenements.append(("edited", None, {"title": {"from": "ancien titre"}}, "pull_request", "2"))

    evenements.append(("labeled", "reviewed", None, "push", "1"))
    evenements.append(("unlabeled", "priority:high", None, "workflow_dispatch", "1"))

    return evenements


def _evenements_annulent_pas():
    evenements = []

    for label in ("reviewed", "human-review", "priority:high"):
        evenements.append(("labeled", label, None, "pull_request", "1"))
        evenements.append(("unlabeled", label, None, "pull_request", "1"))

    evenements.append(("edited", None, {"title": {"from": "ancien titre"}}, "pull_request", "1"))

    return evenements


def test_noop_identique_partout():
    sources = _sources_noop()
    valeurs = list(sources.values())

    assert len(valeurs) == 4
    assert all(valeur == valeurs[0] for valeur in valeurs)


@pytest.mark.parametrize(
    ("action", "label", "changes", "event_name", "run_attempt"),
    _evenements_annulent(),
)
@pytest.mark.parametrize("source", ("workflow quality-evidence", "workflow mutation-policy", "README if"))
def test_evenements_annulants_rendent_noop_fausse(source, action, label, changes, event_name, run_attempt):
    noop = _sources_noop()[source]
    contexte = _contexte(
        action,
        label=label,
        changes=changes,
        event_name=event_name,
        run_attempt=run_attempt,
    )

    assert not _est_vrai(evaluer_expression(noop, contexte))


@pytest.mark.parametrize(
    ("action", "label", "changes", "event_name", "run_attempt"),
    _evenements_annulent_pas(),
)
@pytest.mark.parametrize("source", ("workflow quality-evidence", "workflow mutation-policy", "README if"))
def test_evenements_sans_effet_rendent_noop_vraie(source, action, label, changes, event_name, run_attempt):
    noop = _sources_noop()[source]
    contexte = _contexte(
        action,
        label=label,
        changes=changes,
        event_name=event_name,
        run_attempt=run_attempt,
    )

    assert _est_vrai(evaluer_expression(noop, contexte))


@pytest.mark.parametrize(
    ("action", "label", "changes", "event_name", "run_attempt"),
    _evenements_annulent() + _evenements_annulent_pas(),
)
@pytest.mark.parametrize("source", ("workflow quality-evidence", "workflow mutation-policy", "README if"))
def test_cancel_in_progress_est_l_inverse_de_noop(source, action, label, changes, event_name, run_attempt):
    noop = _sources_noop()[source]
    contexte = _contexte(
        action,
        label=label,
        changes=changes,
        event_name=event_name,
        run_attempt=run_attempt,
    )

    valeur_noop = evaluer_expression(noop, contexte)
    valeur_cancel = evaluer_expression(f"!({noop})", contexte)

    assert _est_vrai(valeur_cancel) is not _est_vrai(valeur_noop)


@pytest.mark.parametrize(
    ("action", "label", "changes", "event_name", "run_attempt"),
    _evenements_annulent_pas(),
)
def test_groupe_readme_ajoute_suffixe_noop_pour_evenement_sans_effet(action, label, changes, event_name, run_attempt):
    bloc_readme = _extraire_bloc_yaml_readme(_lire(CHEMIN_README))
    ligne_group = _extraire_ligne_bloc(bloc_readme, "group")
    contexte = _contexte(
        action,
        label=label,
        changes=changes,
        event_name=event_name,
        run_attempt=run_attempt,
    )

    groupe = evaluer_gabarit(ligne_group, contexte)
    assert groupe.endswith(f"-noop-{contexte['github']['run_id']}")


@pytest.mark.parametrize(
    ("action", "label", "changes", "event_name", "run_attempt"),
    _evenements_annulent(),
)
def test_groupe_readme_n_ajoute_pas_suffixe_noop_pour_evenement_annulant(action, label, changes, event_name, run_attempt):
    bloc_readme = _extraire_bloc_yaml_readme(_lire(CHEMIN_README))
    ligne_group = _extraire_ligne_bloc(bloc_readme, "group")
    contexte = _contexte(
        action,
        label=label,
        changes=changes,
        event_name=event_name,
        run_attempt=run_attempt,
    )

    groupe = evaluer_gabarit(ligne_group, contexte)
    assert not groupe.endswith(f"-noop-{contexte['github']['run_id']}")


def test_extraction_refuse_cancel_in_progress_true():
    texte = """
name: test
on: pull_request
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - run: echo
"""
    with pytest.raises(AssertionError):
        _extraire_noop_depuis_cancel_in_progress(texte, Path("exemple.yml"))


def test_extraction_refuse_cancel_in_progress_sans_negation():
    texte = """
concurrency:
  group: x
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""
    with pytest.raises(AssertionError):
        _extraire_noop_depuis_cancel_in_progress(texte, Path("exemple.yml"))
