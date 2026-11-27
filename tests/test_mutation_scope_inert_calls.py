"""#307 : une fonction faite uniquement d'appels sans argument n'a aucun mutant mutmut.

Cas mesurés sur mutmut 3.8.1 (``mutate_file_contents``) : les formes exclues
ne produisent aucun mutant, les formes conservées en produisent au moins un,
sauf ``f(*a)`` gardé par prudence.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / ".ci" / "mutation_scope.py"
SPEC = importlib.util.spec_from_file_location("mutation_scope", MODULE_PATH)
assert SPEC and SPEC.loader
mutation_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mutation_scope)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@example.test")
    _git(repo, "config", "user.name", "CI Test")
    (repo / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["src/"]\n',
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    return repo


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _targets(tmp_path: Path, before: str, after: str) -> tuple[str, ...]:
    """Portée calculée entre deux versions de ``src/m.py``."""
    repo = _init_repo(tmp_path)
    source = repo / "src" / "m.py"
    source.write_text(before, encoding="utf-8")
    base = _commit(repo, "base")
    source.write_text(after, encoding="utf-8")
    head = _commit(repo, "head")
    return mutation_scope.mutation_targets(repo, base, head, depth="medium")


# Chaque modèle porte {} à la place du nom appelé : « a » en base, « b » en head.
INERT = {
    "appel-nu": "def f():\n    {}()\n",
    "await-appel": "async def f():\n    await {}()\n",
    "chaine-attributs": "def f(self):\n    self.x.{}()\n",
    "await-chaine-attributs": "async def f(self):\n    await self.x.{}()\n",
    "appel-du-resultat": "def f():\n    {}()()\n",
    "methode-du-resultat": "def f():\n    {}().g()\n",
    "return-appel": "def f():\n    return {}()\n",
    "return-await-attribut": "async def f(self):\n    return await self.{}()\n",
}

MUTABLE = {
    "argument-positionnel": "def f():\n    {}(x)\n",
    "argument-nomme": "def f():\n    {}(x=y)\n",
    "argument-etoile": "def f(a):\n    {}(*a)\n",
    "appele-indexe": "def f():\n    {}[0]()\n",
    "await-avec-argument": "async def f():\n    await {}(x)\n",
    "return-avec-argument": "def f():\n    return {}(x)\n",
}


@pytest.mark.parametrize("template", INERT.values(), ids=INERT.keys())
def test_call_without_argument_is_not_a_target(tmp_path: Path, template: str):
    assert _targets(tmp_path, template.format("a"), template.format("b")) == ()


@pytest.mark.parametrize("template", MUTABLE.values(), ids=MUTABLE.keys())
def test_call_with_mutable_part_stays_a_target(tmp_path: Path, template: str):
    assert _targets(tmp_path, template.format("a"), template.format("b")) == (
        "m.*f__mutmut_*",
    )


@pytest.mark.parametrize("name", ["deepcopy", "True"])
def test_call_of_a_name_mutmut_rewrites_stays_a_target(tmp_path: Path, name: str):
    before = "def f():\n    a()\n"
    after = f"def f():\n    a()\n    {name}()\n"
    assert _targets(tmp_path, before, after) == ("m.*f__mutmut_*",)


def _orchestrator(last_call: str) -> str:
    return (
        "class K:\n"
        "    async def run(self):\n"
        '        """Cycle."""\n'
        "        await self.check()\n"
        "        await recover()\n"
        f"        {last_call}\n"
    )


def test_orchestration_method_made_of_calls_is_not_a_target(tmp_path: Path):
    """Cas réel : une méthode qui n'enchaîne que des ``await`` sans argument."""
    before = _orchestrator("await self.notify()")
    after = _orchestrator("await self.alert()")
    assert _targets(tmp_path, before, after) == ()


def test_orchestration_method_with_an_argument_stays_a_target(tmp_path: Path):
    before = _orchestrator("await self.notify()")
    after = _orchestrator("await self.alert(1)")
    assert _targets(tmp_path, before, after) == ("m.*K*run__mutmut_*",)
