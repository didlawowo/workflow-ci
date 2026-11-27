import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))

import go_mutation_sites


def _git(repo: Path, *args: str) -> str:
    """Exécute git dans le dépôt et renvoie stdout nettoyé."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    """Initialise un dépôt git local avec un utilisateur de test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit_all(repo: Path, message: str) -> str:
    """Commit tous les fichiers et renvoie le SHA du commit."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, relative: str, content: str) -> None:
    """Écrit un fichier dans le dépôt en créant les dossiers parents."""
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _line_number(path: Path, marker: str) -> int:
    """Retrouve le numéro de ligne d'un marqueur dans un fichier."""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line == marker:
            return number
    raise AssertionError(f"Marqueur introuvable dans {path}: {marker!r}")


def _make_go_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Crée le dépôt Go imposé par le brief pour les tests du runner."""
    repo = _init_repo(tmp_path)
    _write(repo, "go.mod", "module example.test/consumer\ngo 1.25\n")
    _write(
        repo,
        "a.go",
        "package consumer\n"
        "// package declaration\n"
        "func Add(a, b int) int { return a + b }\n"
        "// fin\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package consumer\n"
        "// package declaration\n"
        "func Add(a, b int) int { return a + b }\n"
        "// fin\n"
        "// commentaire\n",
    )
    head_sha = _commit_all(repo, "head")
    return repo, base_sha, head_sha


def _make_gremlins_json(site_line: int) -> str:
    """Construit le JSON Gremlins fourni par le brief, avec la ligne de site choisie."""
    return json.dumps(
        {
            "files": [
                {
                    "file_name": "a.go",
                    "mutations": [
                        {
                            "type": "ARITHMETIC_BASE",
                            "status": "SKIPPED",
                            "line": site_line,
                            "column": 35,
                        }
                    ],
                }
            ],
            "mutants_total": 0,
            "mutants_killed": 0,
            "mutants_lived": 0,
            "mutants_not_viable": 0,
            "mutants_not_covered": 0,
        }
    )


def _make_gremlins_json_with_killed_mutant() -> str:
    """Construit un JSON Gremlins avec un mutant tué, pour tester la propagation du code de sortie."""
    return json.dumps(
        {
            "files": [
                {
                    "file_name": "a.go",
                    "mutations": [
                        {
                            "type": "ARITHMETIC_BASE",
                            "status": "KILLED",
                            "line": 3,
                            "column": 35,
                        }
                    ],
                }
            ],
            "mutants_total": 1,
            "mutants_killed": 1,
            "mutants_lived": 0,
            "mutants_not_viable": 0,
            "mutants_not_covered": 0,
        }
    )


def _make_stubs(tmp_path: Path, gremlins_json: str, stub_rc: int) -> tuple[Path, Path]:
    """Crée les stubs imposés pour neutraliser uname, uv et gremlins."""
    tools = tmp_path / "tools"
    tools.mkdir()

    (tools / "uname").write_text(
        '#!/bin/sh\n[ "$1" = -s ] && echo Linux || echo arm64\n',
        encoding="utf-8",
    )

    (tools / "uv").write_text(
        "#!/bin/sh\n"
        'while [ "$1" != python ]; do shift; done\n'
        "shift\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )

    (tools / "gremlins").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo 0.6.0; exit 0; fi\n'
        'out=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = --output ]; then out="$2"; shift 2; else shift; fi\n'
        "done\n"
        'if [ -n "$out" ] && [ -n "${STUB_JSON_FILE:-}" ]; then cat "$STUB_JSON_FILE" > "$out"; fi\n'
        'exit "${STUB_RC:-0}"\n',
        encoding="utf-8",
    )

    for name in ("uname", "uv", "gremlins"):
        (tools / name).chmod(0o755)

    raw_json = tmp_path / "gremlins-raw.json"
    raw_json.write_text(gremlins_json, encoding="utf-8")
    return tools, raw_json


def _run_mutation_go(
    repo: Path,
    base_sha: str,
    head_sha: str,
    tools: Path,
    raw_json: Path,
    stub_rc: int,
) -> subprocess.CompletedProcess[str]:
    """Lance `.ci/mutation-go.sh` dans le dépôt avec les stubs fournis."""
    env = os.environ.copy()
    env.pop("WORKFLOW_CI_DISABLE_PREINSTALLED_TOOLS", None)
    env["PATH"] = f"{tools}{os.pathsep}{env.get('PATH', '')}"
    env["MUTATION_BASE_SHA"] = base_sha
    env["MUTATION_HEAD_SHA"] = head_sha
    env["MUTATION_WORKING_DIRECTORY"] = "."
    env["STUB_JSON_FILE"] = str(raw_json)
    env["STUB_RC"] = str(stub_rc)

    return subprocess.run(
        ["bash", str(ROOT / ".ci" / "mutation-go.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def test_changed_lines_retourne_la_ligne_ajoutee_au_milieu(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "a.go",
        "package a\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package a\n"
        "// AJOUT_MILIEU\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    head_sha = _commit_all(repo, "head")

    ligne_ajoutee = _line_number(repo / "a.go", "// AJOUT_MILIEU")
    changed = go_mutation_sites.changed_lines(repo, base_sha, head_sha)

    assert changed == {"a.go": {ligne_ajoutee}}


def test_changed_lines_retourne_la_ligne_modifiee_seule(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "a.go",
        "package a\n"
        "// LIGNE_UNIQUE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package a\n"
        "// LIGNE_UNIQUE_MODIFIEE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    head_sha = _commit_all(repo, "head")

    ligne_modifiee = _line_number(repo / "a.go", "// LIGNE_UNIQUE_MODIFIEE")
    changed = go_mutation_sites.changed_lines(repo, base_sha, head_sha)

    assert changed == {"a.go": {ligne_modifiee}}


def test_changed_lines_exclut_test_vendor_testdata(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "a.go",
        "package a\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    _write(
        repo,
        "a_test.go",
        "package a\n"
        "func TestAdd() {}\n",
    )
    _write(
        repo,
        "vendor/v.go",
        "package v\n"
        "func V() {}\n",
    )
    _write(
        repo,
        "testdata/t.go",
        "package t\n"
        "func T() {}\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package a\n"
        "// AJOUT_MILIEU\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    _write(
        repo,
        "a_test.go",
        "package a\n"
        "func TestAdd() { // modifie\n}\n",
    )
    _write(
        repo,
        "vendor/v.go",
        "package v\n"
        "func V() { // modifie\n}\n",
    )
    _write(
        repo,
        "testdata/t.go",
        "package t\n"
        "func T() { // modifie\n}\n",
    )
    head_sha = _commit_all(repo, "head")

    ligne_ajoutee = _line_number(repo / "a.go", "// AJOUT_MILIEU")
    changed = go_mutation_sites.changed_lines(repo, base_sha, head_sha)

    assert changed == {"a.go": {ligne_ajoutee}}


def test_check_no_mutable_sites_accepte_commentaire_ajoute_et_site_ailleurs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "a.go",
        "package a\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package a\n"
        "// COMMENTAIRE_AJOUTE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    head_sha = _commit_all(repo, "head")

    changed = go_mutation_sites.check_no_mutable_sites(
        repo,
        base_sha,
        head_sha,
        [{"file": "a.go", "line": 3}],
    )

    assert changed == {"a.go": {_line_number(repo / "a.go", "// COMMENTAIRE_AJOUTE")}}


def test_check_no_mutable_sites_refuse_site_sur_ligne_modifiee(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "a.go",
        "package a\n"
        "// LIGNE_UNIQUE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package a\n"
        "// LIGNE_UNIQUE_MODIFIEE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    head_sha = _commit_all(repo, "head")

    ligne_modifiee = _line_number(repo / "a.go", "// LIGNE_UNIQUE_MODIFIEE")

    with pytest.raises(ValueError):
        go_mutation_sites.check_no_mutable_sites(
            repo,
            base_sha,
            head_sha,
            [{"file": "a.go", "line": ligne_modifiee}],
        )


def test_check_no_mutable_sites_refuse_module_imbrique(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, "go.mod", "module example.test/root\ngo 1.25\n")
    _write(repo, "sub/go.mod", "module example.test/sub\ngo 1.25\n")
    _write(
        repo,
        "sub/pkg/a.go",
        "package pkg\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "sub/pkg/a.go",
        "package pkg\n"
        "// COMMENTAIRE_AJOUTE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    head_sha = _commit_all(repo, "head")

    with pytest.raises(ValueError):
        go_mutation_sites.check_no_mutable_sites(repo, base_sha, head_sha, [])


def test_check_no_mutable_sites_refuse_site_sans_ligne(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "a.go",
        "package a\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package a\n"
        "// COMMENTAIRE_AJOUTE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    head_sha = _commit_all(repo, "head")

    with pytest.raises(ValueError):
        go_mutation_sites.check_no_mutable_sites(repo, base_sha, head_sha, [{"file": "a.go"}])


def test_check_no_mutable_sites_refuse_liste_de_sites_non_list(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "a.go",
        "package a\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    base_sha = _commit_all(repo, "base")

    _write(
        repo,
        "a.go",
        "package a\n"
        "// COMMENTAIRE_AJOUTE\n"
        "func Add(a, b int) int { return a + b }\n",
    )
    head_sha = _commit_all(repo, "head")

    with pytest.raises(ValueError):
        go_mutation_sites.check_no_mutable_sites(repo, base_sha, head_sha, "x")


def test_reported_sites_garde_seulement_les_fichiers_demandes_tous_statuts(tmp_path: Path) -> None:
    raw = {
        "files": [
            {
                "file_name": "b.go",
                "mutations": [
                    {"status": "KILLED", "line": 2},
                    {"status": "SKIPPED", "line": 1},
                ],
            },
            {
                "file_name": "a.go",
                "mutations": [
                    {"status": "SKIPPED", "line": 3},
                ],
            },
            {
                "file_name": "c.go",
                "mutations": [
                    {"status": "LIVED", "line": 1},
                ],
            },
        ]
    }

    sites = go_mutation_sites.reported_sites(raw, {"a.go", "b.go"})

    assert sites == [
        {"file": "a.go", "line": 3},
        {"file": "b.go", "line": 1},
        {"file": "b.go", "line": 2},
    ]


def test_runner_accepte_zero_mutant_quand_aucun_site_sur_ligne_modifiee(tmp_path: Path) -> None:
    repo, base_sha, head_sha = _make_go_repo(tmp_path)
    tools, raw_json = _make_stubs(tmp_path, _make_gremlins_json(3), stub_rc=0)

    result = _run_mutation_go(repo, base_sha, head_sha, tools, raw_json, stub_rc=0)

    assert result.returncode == 0, result.stderr

    report = json.loads((repo / ".quality" / "gremlins.json").read_text(encoding="utf-8"))
    assert report["scope"]["no_targets"] is True
    assert report["scope"]["reason"] == "no-mutable-sites-on-changed-lines"
    assert report["scope"]["sites"] == [{"file": "a.go", "line": 3}]
    assert report["stats"]["total"] == 0


def test_runner_refuse_zero_mutant_quand_site_sur_ligne_modifiee(tmp_path: Path) -> None:
    repo, base_sha, head_sha = _make_go_repo(tmp_path)
    tools, raw_json = _make_stubs(tmp_path, _make_gremlins_json(5), stub_rc=0)

    result = _run_mutation_go(repo, base_sha, head_sha, tools, raw_json, stub_rc=0)

    assert result.returncode == 1

    report = json.loads((repo / ".quality" / "gremlins.json").read_text(encoding="utf-8"))
    assert report["scope"]["no_targets"] is False
    assert "reason" not in report["scope"]
    assert "sites" not in report["scope"]


def test_runner_refuse_quand_gremlins_sort_avec_code_non_nul(tmp_path: Path) -> None:
    repo, base_sha, head_sha = _make_go_repo(tmp_path)
    tools, raw_json = _make_stubs(tmp_path, _make_gremlins_json(3), stub_rc=1)

    result = _run_mutation_go(repo, base_sha, head_sha, tools, raw_json, stub_rc=1)

    assert result.returncode == 1


def test_runner_refuse_quand_rapport_gremlins_est_vide(tmp_path: Path) -> None:
    repo, base_sha, head_sha = _make_go_repo(tmp_path)
    tools, raw_json = _make_stubs(tmp_path, "", stub_rc=2)

    result = _run_mutation_go(repo, base_sha, head_sha, tools, raw_json, stub_rc=2)

    assert result.returncode == 2
    assert not (repo / ".quality" / "gremlins.json").exists()


def test_runner_propage_code_sortie_gremlins_quand_preuve_valide(tmp_path: Path) -> None:
    repo, base_sha, head_sha = _make_go_repo(tmp_path)
    tools, raw_json = _make_stubs(
        tmp_path,
        _make_gremlins_json_with_killed_mutant(),
        stub_rc=2,
    )

    result = _run_mutation_go(repo, base_sha, head_sha, tools, raw_json, stub_rc=2)

    assert result.returncode == 2

    report = json.loads((repo / ".quality" / "gremlins.json").read_text(encoding="utf-8"))
    assert report["stats"]["total"] == 1
    assert report["stats"]["killed"] == 1
    assert report["scope"]["no_targets"] is False


def test_check_no_mutable_sites_ignore_gitattributes_qui_masque_le_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, "a.go", "package a\n\nfunc Add(a, b int) int { return a + b }\n")
    base_sha = _commit_all(repo, "base")
    _write(repo, ".gitattributes", "*.go -diff\n")
    _write(repo, "a.go", "package a\n\nfunc Add(a, b int) int { return a - b }\n")
    head_sha = _commit_all(repo, "head")

    with pytest.raises(ValueError):
        go_mutation_sites.check_no_mutable_sites(
            repo, base_sha, head_sha, [{"file": "a.go", "line": 3}]
        )
