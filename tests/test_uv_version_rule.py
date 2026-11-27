"""Contrat de version de uv : version épinglée du socle ou required-version du projet."""

from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RESOLVE_SCRIPT = ROOT / ".github/actions/setup-uv/resolve-version.sh"

SEARCH_DIRS = (".github", ".forgejo", "templates")
SETUP_UV_MARK = "astral-sh/setup-uv@"
WRAPPER_SUFFIX = ".github/actions/setup-uv"
FORBIDDEN_INSTALL = "astral.sh/uv/install"

ALLOWED_LITERAL_PINNED_FILES = {
    "templates/forgejo/mutation-policy.yml",
    ".github/workflows/bench-uv-cache.yml",
}

VERSION_EXPRESSION = re.compile(
    r"^\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.version\s*\}\}$"
)


def pinned_uv_version() -> str:
    """Lit la version épinglée dans le script, sans la recopier dans le test."""
    content = RESOLVE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"""PINNED_UV_VERSION\s*=\s*["']([^"']+)["']""",
        content,
    )
    assert match is not None, "PINNED_UV_VERSION introuvable dans resolve-version.sh"
    return match.group(1)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def step_label(step: dict) -> str:
    return str(step.get("name") or step.get("id") or "sans nom")


def fail_step(path: Path, step: dict, message: str) -> None:
    raise AssertionError(
        f"{relative(path)} : étape {step_label(step)} "
        f"(uses={step.get('uses')!r}) : {message}"
    )


def run_contains_resolve(step: dict) -> bool:
    """Vérifie qu'une étape précédente prépare la version résolue."""
    run = step.get("run")
    if isinstance(run, str):
        text = run
    elif isinstance(run, list):
        text = "\n".join(str(item) for item in run)
    else:
        return False

    return "resolve-version.sh" in text or "required-version" in text


def iter_yaml_files() -> list[Path]:
    files: list[Path] = []
    for dirname in SEARCH_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}:
                files.append(path)
    return files


def iter_all_files() -> list[Path]:
    files: list[Path] = []
    for dirname in SEARCH_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                files.append(path)
    return files


def iter_steps_lists(node) -> list[list]:
    """Retourne récursivement toutes les listes YAML associées à une clé `steps`."""
    found: list[list] = []

    if isinstance(node, dict):
        for key, value in node.items():
            if key == "steps" and isinstance(value, list):
                found.append(value)
            found.extend(iter_steps_lists(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(iter_steps_lists(item))

    return found


def iter_steps(path: Path) -> list[tuple[list, int, dict]]:
    """Charge un YAML et retourne chaque étape dict avec sa liste parente."""
    try:
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except Exception as exc:  # pragma: no cover - dépend des fichiers du dépôt
        raise AssertionError(f"YAML invalide : {relative(path)} : {exc}") from exc

    steps: list[tuple[list, int, dict]] = []
    for document in documents:
        for steps_list in iter_steps_lists(document):
            for index, step in enumerate(steps_list):
                if isinstance(step, dict):
                    steps.append((steps_list, index, step))
    return steps


@pytest.mark.parametrize(
    ("nom", "fichiers", "attendu"),
    [
        (
            "sans_fichier",
            {},
            "epinglee",
        ),
        (
            "uv_toml_required_version",
            {"uv.toml": 'required-version = ">=0.9"\n'},
            "",
        ),
        (
            "pyproject_tool_uv_required_version",
            {"pyproject.toml": '[tool.uv]\nrequired-version = ">=0.9"\n'},
            "",
        ),
        (
            "pyproject_project_required_version_seulement",
            {
                "pyproject.toml": (
                    '[project]\n'
                    'name = "exemple"\n'
                    'required-version = ">=0.9"\n'
                )
            },
            "epinglee",
        ),
        (
            "pyproject_tool_uv_sans_required_version",
            {"pyproject.toml": "[tool.uv]\nno-cache = true\n"},
            "epinglee",
        ),
    ],
)
def test_resolve_version_script(nom, fichiers, attendu, tmp_path):
    """Le script imprime la version épinglée sauf si le projet déclare required-version."""
    for name, content in fichiers.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(RESOLVE_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    if attendu == "epinglee":
        assert result.stdout == pinned_uv_version() + "\n"
    else:
        assert result.stdout == ""


def test_resolve_version_rejects_missing_directory(tmp_path):
    """Un dossier inexistant doit être refusé sans imprimer de version."""
    missing = tmp_path / "absent"

    result = subprocess.run(
        ["bash", str(RESOLVE_SCRIPT), str(missing)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ""


def test_setup_uv_steps_use_resolved_version_or_allowed_pinned_version():
    """Toute étape setup-uv directe doit respecter le contrat de version."""
    pinned = pinned_uv_version()
    examined = 0

    for path in iter_yaml_files():
        for steps_list, index, step in iter_steps(path):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue

            if uses.endswith(WRAPPER_SUFFIX):
                continue

            if SETUP_UV_MARK not in uses:
                continue

            examined += 1
            rel = relative(path)
            with_ = step.get("with")

            if not isinstance(with_, dict):
                fail_step(path, step, "avec setup-uv, une entrée `with.version` est obligatoire")

            version = with_.get("version")

            if isinstance(version, str):
                version_text = version.strip()

                expression = VERSION_EXPRESSION.fullmatch(version_text)
                if expression:
                    step_id = expression.group(1)
                    previous_steps = steps_list[:index]

                    if not any(
                        isinstance(previous, dict)
                        and previous.get("id") == step_id
                        and run_contains_resolve(previous)
                        for previous in previous_steps
                    ):
                        fail_step(
                            path,
                            step,
                            f"la version `${{{{ steps.{step_id}.outputs.version }}}}` "
                            "doit provenir d'une étape précédente contenant "
                            "resolve-version.sh ou required-version",
                        )
                    continue

                if version_text == pinned and rel in ALLOWED_LITERAL_PINNED_FILES:
                    continue

            fail_step(
                path,
                step,
                "version setup-uv invalide : utiliser une sortie d'étape résolue, "
                f"ou la version épinglée uniquement dans {sorted(ALLOWED_LITERAL_PINNED_FILES)}",
            )

    assert examined >= 10, (
        "Garde-fou : au moins 10 étapes setup-uv doivent être examinées, "
        f"mais seulement {examined} l'ont été."
    )


def test_no_direct_uv_install_script():
    """Le socle ne doit pas installer uv via astral.sh/uv/install."""
    offenders = []

    for path in iter_all_files():
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if FORBIDDEN_INSTALL in content:
            offenders.append(relative(path))

    assert not offenders, (
        "Fichiers contenant l'installation directe interdite "
        f"{FORBIDDEN_INSTALL} : {offenders}"
    )


def test_wrapper_steps_are_accepted_as_wrapper_calls():
    """Les étapes qui appellent le wrapper ne sont pas des étapes setup-uv directes."""
    for path in iter_yaml_files():
        for _, _, step in iter_steps(path):
            uses = step.get("uses")
            if isinstance(uses, str) and uses.endswith(WRAPPER_SUFFIX):
                assert SETUP_UV_MARK not in uses, (
                    f"{relative(path)} : le wrapper {uses!r} ne doit pas être "
                    "confondu avec une étape setup-uv directe"
                )
