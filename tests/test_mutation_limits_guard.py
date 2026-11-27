"""Tests du sous-programme guard de .ci/mutation_limits.py."""

from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))
limits = importlib.import_module("mutation_limits")
SCRIPT = ROOT / ".ci" / "mutation_limits.py"


def run_guard(marker, memory_exit, command, *, separator=True):
    """Exécute `mutation_limits.py guard` dans un sous-processus."""
    arguments = [
        sys.executable,
        str(SCRIPT),
        "guard",
        "--marker",
        str(marker),
        "--memory-exit",
        str(memory_exit),
    ]
    if separator:
        arguments.append("--")
    arguments.extend(command)
    return subprocess.run(arguments, capture_output=True, text=True, timeout=60)


def sh_print(stream, message, exit_code):
    """Imprime `message` sur le flux choisi via `sh`, puis sort avec `exit_code`.

    L'argument `sh -c` est volontairement sans saut de ligne : le garde écrit la
    commande dans le témoin, et la spec attend une ligne par exécution.
    """
    redirect = " >&2" if stream == "stderr" else ""
    return [
        "sh",
        "-c",
        f'echo "$1"{redirect}; exit {exit_code}',
        "sh",
        message,
    ]


@pytest.mark.parametrize("exit_code", [0, 1, 5])
def test_statut_normal_relaye_sans_temoign(tmp_path, exit_code):
    marker = tmp_path / "marker"
    result = run_guard(marker, 3, ["sh", "-c", f"exit {exit_code}"])

    assert result.returncode == exit_code
    assert not marker.exists()


def test_sorties_relayees_separement(tmp_path):
    marker = tmp_path / "marker"
    result = run_guard(marker, 3, ["sh", "-c", "echo dehors; echo erreur >&2"])

    assert result.returncode == 0
    assert "dehors" in result.stdout
    assert "erreur" not in result.stdout
    assert "erreur" in result.stderr
    assert "dehors" not in result.stderr
    assert not marker.exists()


def test_signature_go_sur_stderr_declenche_memory_exit(tmp_path):
    marker = tmp_path / "marker"
    signature = "fatal error: runtime: out of memory"
    result = run_guard(marker, 3, sh_print("stderr", signature, 1))

    assert result.returncode == 3
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") != ""


def test_signature_v8_sur_stdout_declenche_memory_exit(tmp_path):
    marker = tmp_path / "marker"
    signature = (
        "FATAL ERROR: Reached heap limit Allocation failed - "
        "JavaScript heap out of memory"
    )
    result = run_guard(marker, 0, sh_print("stdout", signature, 0))

    assert result.returncode == 0
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") != ""


# Figées ici : retirer une signature du garde doit casser ce test.
EXPECTED_SIGNATURES = (
    "fatal error: runtime: out of memory",
    "fatal error: out of memory",
    "JavaScript heap out of memory",
    "Reached heap limit",
    "Fatal process out of memory",
    "Allocation failed - process out of memory",
    "JS heap out of memory",
    "Array buffer allocation failed",
    "signal SIGKILL",
    'signal "SIGKILL"',
    "signal=SIGKILL",
    "signal: killed",
)


def test_signatures_attendues_toutes_presentes():
    assert set(EXPECTED_SIGNATURES) <= set(limits.MEMORY_SIGNATURES) | set(limits.MEMORY_LINES)


def test_signal_killed_dans_un_texte_de_test_reste_un_echec(tmp_path):
    """Revue PR 313 (R4) : seule la ligne de bilan exacte de go test compte."""
    marker = tmp_path / "marker"
    result = run_guard(marker, 3, sh_print("stdout", "cmd: signal: killed by context", 1))
    assert result.returncode == 1 and not marker.exists()


@pytest.mark.parametrize("signature", EXPECTED_SIGNATURES)
def test_toutes_les_signatures_declenchent_memory_exit(tmp_path, signature):
    marker = tmp_path / "marker"
    result = run_guard(marker, 3, sh_print("stderr", signature, 1))

    assert result.returncode == 3
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") != ""


def test_sigkill_declenche_memory_exit(tmp_path):
    marker = tmp_path / "marker"
    result = run_guard(marker, 3, ["sh", "-c", "kill -9 $$"])

    assert result.returncode == 3
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") != ""


def test_autre_signal_renvoie_128_plus_signal_sans_temoign(tmp_path):
    marker = tmp_path / "marker"
    result = run_guard(marker, 3, ["sh", "-c", "kill -15 $$"])

    assert result.returncode == 128 + 15
    assert not marker.exists()


@pytest.mark.parametrize("separator", [True, False])
def test_double_dash_facultatif(tmp_path, separator):
    marker = tmp_path / "marker"
    signature = "fatal error: out of memory"
    result = run_guard(
        marker,
        3,
        sh_print("stderr", signature, 1),
        separator=separator,
    )

    assert result.returncode == 3
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") != ""


def test_temoign_cumule_deux_executions(tmp_path):
    marker = tmp_path / "marker"
    signature = "fatal error: out of memory"

    first = run_guard(marker, 3, sh_print("stderr", signature, 1))
    second = run_guard(marker, 3, sh_print("stderr", signature, 1))

    assert first.returncode == 3
    assert second.returncode == 3
    assert len(marker.read_text(encoding="utf-8").splitlines()) == 2


def test_children_affiche_un_entre_1_et_4():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "children"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0
    assert 1 <= int(result.stdout.strip()) <= 4
