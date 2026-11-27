"""Tests pour l'issue 317 : budget de mutation Node, cause lisible, audit npm illisible."""

from __future__ import annotations

import importlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ci"))

budget = importlib.import_module("mutation_run_budget")
deadline = importlib.import_module("mutation_deadline")

ACTION_PATH = ROOT / ".github" / "actions" / "node-quality-security" / "action.yml"
QUALITY_EVIDENCE_PATH = ROOT / ".github" / "workflows" / "quality-evidence.yml"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
MUTATION_POLICY_PATH = ROOT / ".github" / "workflows" / "mutation-policy.yml"
NODE_MUTATION_EVIDENCE_PATH = ROOT / ".github" / "workflows" / "node-mutation-evidence.yml"


def _git(repo: Path, *args: str) -> str:
    """Exécute git dans le dépôt temporaire et renvoie stdout nettoyé."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _write_lines(path: Path, count: int, prefix: str) -> None:
    """Écrit un fichier de `count` lignes terminées par un saut de ligne."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{prefix}{i}" for i in range(count)) + "\n", encoding="utf-8")


def _commit_all(repo: Path, message: str) -> str:
    """Commit tout le dépôt temporaire avec un auteur déterministe."""
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.email=a@b",
        "-c",
        "user.name=a",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _make_node_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Dépôt git : 50 lignes de base, puis +100 lignes et fichiers de test exclus."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    _write_lines(repo / "src" / "a.ts", 50, "base")
    base = _commit_all(repo, "base")

    existing = (repo / "src" / "a.ts").read_text(encoding="utf-8")
    (repo / "src" / "a.ts").write_text(
        existing + "\n".join(f"added{i}" for i in range(100)) + "\n",
        encoding="utf-8",
    )
    _write_lines(repo / "tests" / "t.ts", 1, "test")
    _write_lines(repo / "src" / "a.test.ts", 1, "test")
    head = _commit_all(repo, "head")
    return repo, base, head


def _make_deletion_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Dépôt git où la tête ne fait que supprimer des lignes d'un fichier Node."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    _write_lines(repo / "src" / "b.ts", 30, "line")
    base = _commit_all(repo, "base")

    _write_lines(repo / "src" / "b.ts", 25, "line")
    head = _commit_all(repo, "head")
    return repo, base, head


def _clean_node_env(**overrides: str) -> dict[str, str]:
    """Environnement sans variables de budget Node, sauf overrides explicites."""
    env = os.environ.copy()
    for name in ("MUTATION_NODE_PACE_SECONDS", "MUTATION_NODE_SETUP_MINUTES"):
        env.pop(name, None)
    env.update(overrides)
    return env


def _run_budget_cli(
    repo: Path,
    base: str,
    head: str,
    *,
    source: str = "workflow-ci-node",
    depth: str = "medium",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Exécute le CLI de budget dans un sous-processus."""
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / ".ci" / "mutation_run_budget.py"),
            "--source",
            source,
            "--repo",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--depth",
            depth,
        ],
        env=env if env is not None else _clean_node_env(),
        capture_output=True,
        text=True,
    )


def test_node_scope_lines_medium_exclut_les_fichiers_de_test(tmp_path: Path) -> None:
    """Au niveau medium, seules les lignes ajoutées aux sources Node comptent."""
    repo, base, head = _make_node_repo(tmp_path)
    assert budget.node_scope_lines(repo, base, head, ".", "medium") == 100


def test_node_scope_lines_high_compte_le_fichier_entier(tmp_path: Path) -> None:
    """Au niveau high, le fichier modifié entier à la tête compte."""
    repo, base, head = _make_node_repo(tmp_path)
    assert budget.node_scope_lines(repo, base, head, ".", "high") == 150


def test_node_scope_lines_suppression_pure_compte_le_fichier_restant(tmp_path: Path) -> None:
    """Une suppression pure force la mutation du fichier restant au niveau medium."""
    repo, base, head = _make_deletion_repo(tmp_path)
    assert budget.node_scope_lines(repo, base, head, ".", "medium") == 25


def test_node_scope_lines_refuse_profondeur_invalide(tmp_path: Path) -> None:
    """Une profondeur non supportée est refusée explicitement."""
    repo, base, head = _make_node_repo(tmp_path)
    with pytest.raises(ValueError):
        budget.node_scope_lines(repo, base, head, ".", "low")


def test_budget_cli_default_reads_scope_and_time_budget(tmp_path: Path) -> None:
    """Sans variables, le CLI Node dérive le budget du périmètre exact."""
    repo, base, head = _make_node_repo(tmp_path)
    proc = _run_budget_cli(repo, base, head)
    assert proc.returncode == 0
    assert "time-budget=82" in proc.stdout
    assert "scope-lines=100" in proc.stdout


def test_budget_cli_honours_node_pace_and_setup_variables(tmp_path: Path) -> None:
    """Les variables Node modifient le budget sans changer le périmètre."""
    repo, base, head = _make_node_repo(tmp_path)
    proc = _run_budget_cli(
        repo,
        base,
        head,
        env=_clean_node_env(
            MUTATION_NODE_PACE_SECONDS="120",
            MUTATION_NODE_SETUP_MINUTES="10",
        ),
    )
    assert proc.returncode == 0
    assert "time-budget=210" in proc.stdout
    assert "scope-lines=100" in proc.stdout


def test_budget_cli_other_engine_keeps_floor(tmp_path: Path) -> None:
    """Les autres moteurs gardent le plancher, quel que soit le périmètre."""
    repo, base, head = _make_node_repo(tmp_path)
    proc = _run_budget_cli(repo, base, head, source="workflow-ci-go")
    assert proc.returncode == 0
    assert "time-budget=45" in proc.stdout


def test_budget_cli_rejects_zero_pace(tmp_path: Path) -> None:
    """Un rythme nul est une erreur de configuration, pas un budget infini."""
    repo, base, head = _make_node_repo(tmp_path)
    proc = _run_budget_cli(
        repo,
        base,
        head,
        env=_clean_node_env(MUTATION_NODE_PACE_SECONDS="0"),
    )
    assert proc.returncode == 1
    assert "::error::" in proc.stderr


def test_run_budget_minutes_respects_floor_and_ceiling() -> None:
    """Le budget d'exécution reste borné par le plancher et le plafond du shard."""
    assert budget.run_budget_minutes(0, 40, 15) == 45
    assert budget.run_budget_minutes(10000, 40, 15) == 360


def _call_deadline_run(
    command: list[str],
    minutes: int,
    *,
    grace: float = 0.1,
    unit: float = 0.3,
) -> int:
    """Appelle deadline.run en restaurant les gestionnaires de signaux."""
    handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        return deadline.run(command, minutes, grace=grace, unit=unit)
    finally:
        for signum, handler in handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


def test_deadline_stops_whole_process_group_and_reports_budget(capfd, tmp_path: Path) -> None:
    """Une expiration tue tout le groupe et imprime une cause lisible."""
    pidfile = tmp_path / "pid"
    command = [
        "sh",
        "-c",
        f"sleep 20 & echo $! > {pidfile}; sleep 20; wait",
    ]

    capfd.readouterr()
    started = time.monotonic()
    status = _call_deadline_run(command, 1, unit=0.5)
    elapsed = time.monotonic() - started

    assert status == 124
    assert elapsed < 5

    captured = capfd.readouterr()
    assert "::error::" in captured.out
    assert "time budget" in captured.out

    pid = int(pidfile.read_text(encoding="utf-8").strip())
    deadline_at = time.monotonic() + 2.0
    while time.monotonic() < deadline_at:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("le processus sleep en arrière-plan est encore vivant")


def test_deadline_does_not_confuse_command_exit_124_with_expiry() -> None:
    """Un 124 produit par la commande n'est jamais une expiration du budget."""
    assert _call_deadline_run(["sh", "-c", "exit 124"], 1, unit=0.3) == 1


def test_deadline_preserves_ordinary_exit_codes() -> None:
    """Les codes ordinaires sont conservés."""
    assert _call_deadline_run(["sh", "-c", "exit 3"], 1, unit=0.3) == 3
    assert _call_deadline_run(["sh", "-c", "true"], 1, unit=0.3) == 0


def test_deadline_rejects_non_positive_minutes() -> None:
    """Un budget nul est refusé avant d'exécuter quoi que ce soit."""
    with pytest.raises(ValueError):
        deadline.run(["sh", "-c", "true"], 0)


def test_deadline_main_requires_command() -> None:
    """Le CLI exige une commande à exécuter."""
    assert deadline.main(["--minutes", "1"]) == 1


def _action_script(step_id: str) -> str:
    """Extrait le script bash d'une étape de l'action Node."""
    action = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    return next(step["run"] for step in action["runs"]["steps"] if step.get("id") == step_id)


def _read_output_file(path: Path) -> dict[str, str]:
    """Lit un fichier GITHUB_OUTPUT au format clé=valeur."""
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def _vulnerability_payload(info: int, low: int, moderate: int, high: int, critical: int) -> str:
    """Construit un rapport npm lisible avec la décomposition demandée."""
    total = info + low + moderate + high + critical
    return (
        '{"metadata":{"vulnerabilities":{'
        f'"info":{info},"low":{low},"moderate":{moderate},'
        f'"high":{high},"critical":{critical},"total":{total}'
        "}}}"
    )


def _run_audit_step(
    tmp_path: Path,
    *,
    first_payload: str,
    first_status: int,
    second_payload: str,
    second_status: int,
    level: str = "high",
) -> tuple[dict[str, str], subprocess.CompletedProcess[str], int]:
    """Exécute l'étape audit avec un faux npm séquentiel."""
    script = (
        _action_script("audit")
        .replace("${{ inputs.package-manager }}", "npm")
        .replace("${{ inputs.audit-level }}", level)
    )

    npm = tmp_path / "npm"
    npm.write_text(
        """#!/bin/sh
json_count_file="$FIXTURE_JSON_COUNT_FILE"
n=0
if [ -f "$json_count_file" ]; then
  n=$(cat "$json_count_file")
fi

json=0
for arg in "$@"; do
  if [ "$arg" = "--json" ]; then
    json=1
  fi
done

if [ "$json" = 1 ]; then
  n=$((n + 1))
  printf '%s\\n' "$n" > "$json_count_file"
  if [ "$n" -eq 1 ]; then
    printf '%s\\n' "$FIXTURE_FIRST_JSON"
    exit "$FIXTURE_FIRST_STATUS"
  fi
  printf '%s\\n' "$FIXTURE_SECOND_JSON"
  exit "$FIXTURE_SECOND_STATUS"
fi

exit 0
""",
        encoding="utf-8",
    )
    npm.chmod(0o755)

    output = tmp_path / "output"
    json_count = tmp_path / "json_count"
    env = os.environ | {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "FIXTURE_JSON_COUNT_FILE": str(json_count),
        "FIXTURE_FIRST_JSON": first_payload,
        "FIXTURE_FIRST_STATUS": str(first_status),
        "FIXTURE_SECOND_JSON": second_payload,
        "FIXTURE_SECOND_STATUS": str(second_status),
    }

    proc = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0

    calls = int(json_count.read_text(encoding="utf-8")) if json_count.exists() else 0
    return _read_output_file(output), proc, calls


def test_audit_unreadable_then_readable_is_retried_and_passes(tmp_path: Path) -> None:
    """Un premier rapport illisible est relu une fois ; un rapport lisible décide."""
    readable = _vulnerability_payload(0, 0, 11, 0, 0)
    outputs, proc, json_calls = _run_audit_step(
        tmp_path,
        first_payload="not json",
        first_status=1,
        second_payload=readable,
        second_status=0,
    )

    assert outputs["status"] == "passed"
    assert outputs["vuln-count"] == "0"
    assert outputs["scan-errors"] == "0"
    assert json_calls == 2
    assert "::warning::" in proc.stdout


def test_audit_two_unreadable_reports_are_scanner_error(tmp_path: Path) -> None:
    """Deux rapports illisibles sont une erreur de scanner, jamais un vert."""
    outputs, proc, json_calls = _run_audit_step(
        tmp_path,
        first_payload="not json",
        first_status=1,
        second_payload="not json",
        second_status=1,
    )

    assert outputs["status"] == "error"
    assert outputs["vuln-count"] == "0"
    assert outputs["scan-errors"] == "1"
    assert json_calls == 2
    assert "::error::" in proc.stdout
    assert "scanner execution error" in proc.stdout


def test_audit_readable_findings_are_not_scanner_error(tmp_path: Path) -> None:
    """Un rapport lisible avec findings reste un verdict de sécurité."""
    readable = _vulnerability_payload(0, 0, 0, 2, 0)
    outputs, proc, json_calls = _run_audit_step(
        tmp_path,
        first_payload=readable,
        first_status=1,
        second_payload=readable,
        second_status=0,
    )

    assert outputs["status"] == "findings"
    assert outputs["vuln-count"] == "2"
    assert outputs["scan-errors"] == "0"
    assert json_calls == 1


def test_audit_readable_zero_findings_with_failure_is_scanner_error(tmp_path: Path) -> None:
    """Un rapport lisible sans finding mais en échec est incohérent."""
    readable = _vulnerability_payload(0, 0, 11, 0, 0)
    outputs, proc, json_calls = _run_audit_step(
        tmp_path,
        first_payload=readable,
        first_status=1,
        second_payload=readable,
        second_status=1,
    )

    assert outputs["status"] == "error"
    assert outputs["vuln-count"] == "0"
    assert outputs["scan-errors"] == "1"
    assert json_calls == 2
    assert "::error::" in proc.stdout


def _run_summary_step(
    tmp_path: Path,
    *,
    audit_status: str,
    audit_vuln_count: str,
    run_audit: str,
    lint_status: str = "passed",
    format_status: str = "",
) -> dict[str, str]:
    """Exécute l'étape summary avec des sorties d'étapes simulées."""
    script = (
        _action_script("summary")
        .replace("${{ steps.audit.outputs.status }}", audit_status)
        .replace("${{ steps.audit.outputs.vuln-count || '0' }}", audit_vuln_count)
        .replace("${{ inputs.run-audit }}", run_audit)
        .replace("${{ steps.lint.outputs.status }}", lint_status)
        .replace("${{ steps.format.outputs.status }}", format_status)
    )

    output = tmp_path / "output"
    env = os.environ | {"GITHUB_OUTPUT": str(output)}
    proc = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    return _read_output_file(output)


def test_summary_audit_error_is_scan_error_not_issue(tmp_path: Path) -> None:
    """Une erreur d'audit bloque la qualité sans créer de faille."""
    outputs = _run_summary_step(
        tmp_path,
        audit_status="error",
        audit_vuln_count="0",
        run_audit="true",
    )
    assert outputs["passed"] == "false"
    assert outputs["security-scan-errors"] == "1"
    assert outputs["security-issues"] == "0"


def test_summary_missing_audit_output_is_scan_error(tmp_path: Path) -> None:
    """Une étape audit plantée est une erreur de scanner quand l'audit est requis."""
    outputs = _run_summary_step(
        tmp_path,
        audit_status="",
        audit_vuln_count="0",
        run_audit="true",
    )
    assert outputs["passed"] == "false"
    assert outputs["security-scan-errors"] == "1"


def test_summary_passed_audit_is_quality_pass(tmp_path: Path) -> None:
    """Un audit passé ne génère aucune erreur de scanner."""
    outputs = _run_summary_step(
        tmp_path,
        audit_status="passed",
        audit_vuln_count="0",
        run_audit="true",
    )
    assert outputs["passed"] == "true"
    assert outputs["security-scan-errors"] == "0"


def test_summary_missing_audit_output_is_ok_when_audit_disabled(tmp_path: Path) -> None:
    """Sans audit requis, une sortie audit vide n'est pas une erreur."""
    outputs = _run_summary_step(
        tmp_path,
        audit_status="",
        audit_vuln_count="0",
        run_audit="false",
    )
    assert outputs["passed"] == "true"
    assert outputs["security-scan-errors"] == "0"


def test_quality_evidence_propagates_security_scan_errors() -> None:
    """Le workflow de preuve expose l'erreur de scan du job de vérification."""
    workflow = yaml.safe_load(QUALITY_EVIDENCE_PATH.read_text(encoding="utf-8"))
    outputs = workflow["jobs"]["independent-verification"]["outputs"]
    assert "security-scan-errors" in outputs
    assert "steps.node-quality.outputs.security-scan-errors" in outputs["security-scan-errors"]


def _references_scan_errors(value: object) -> bool:
    """Vérifie qu'une valeur GitHub Actions référence l'erreur de scan."""
    if not isinstance(value, str):
        return False
    return "security-scan-errors" in value or "scan-errors" in value


def _find_quality_report_steps(job: dict) -> list[dict]:
    """Retrouve les étapes qui consomment l'action quality-report."""
    steps = []
    for step in job.get("steps", []):
        uses = step.get("uses", "")
        if isinstance(uses, str) and "quality-report" in uses:
            steps.append(step)
    return steps


def test_ci_quality_job_propagates_security_scan_errors() -> None:
    """Le job quality de ci.yml expose security-scan-errors et utilise quality-report."""
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    quality_job = workflow["jobs"]["quality"]

    outputs = quality_job.get("outputs") or {}
    assert "security-scan-errors" in outputs
    assert _references_scan_errors(outputs["security-scan-errors"])

    report_steps = [
        step for job in workflow["jobs"].values() for step in _find_quality_report_steps(job)
    ]
    assert report_steps, "aucune étape quality-report trouvée"
    assert any(
        step.get("with", {}).get("security-scan-errors") == "${{ needs.quality.outputs.security-scan-errors }}"
        for step in report_steps
    )


def _workflow_step(workflow: dict, job_name: str, step_id: str) -> dict:
    """Retrouve une étape par identifiant dans un job."""
    return next(step for step in workflow["jobs"][job_name]["steps"] if step.get("id") == step_id)


def test_mutation_policy_job_has_budget_and_stop_reason() -> None:
    """Le job de mutation a un plafond, un budget et une cause d'arrêt."""
    workflow = yaml.safe_load(MUTATION_POLICY_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["mutation-run"]

    assert job["timeout-minutes"] == 375
    assert "time-budget" in job["outputs"]
    assert "stop-reason" in job["outputs"]

    budget_step = _workflow_step(workflow, "mutation-run", "budget")
    run_step = _workflow_step(workflow, "mutation-run", "run-mutation")

    assert ".ci/mutation_run_budget.py" in budget_step["run"]
    assert ".ci/mutation_deadline.py" in run_step["run"]
    assert budget_step["env"]["MUTATION_NODE_PACE_SECONDS"] == "${{ vars.MUTATION_NODE_PACE_SECONDS || '40' }}"
    assert budget_step["env"]["MUTATION_NODE_SETUP_MINUTES"] == "${{ vars.MUTATION_NODE_SETUP_MINUTES || '15' }}"


def test_mutation_policy_run_step_writes_stop_reason_only_for_124(tmp_path: Path) -> None:
    """Le bloc 124 écrit stop-reason uniquement quand le budget expire."""
    workflow = yaml.safe_load(MUTATION_POLICY_PATH.read_text(encoding="utf-8"))
    run_script = _workflow_step(workflow, "mutation-run", "run-mutation")["run"]

    start = run_script.index('if [[ "$status" -eq 124 ]]')
    end = run_script.index("fi", start) + len("fi")
    block = run_script[start:end]

    output_124 = tmp_path / "output-124"
    output_1 = tmp_path / "output-1"
    output_124.write_text("", encoding="utf-8")
    output_1.write_text("", encoding="utf-8")

    env_124 = os.environ | {
        "GITHUB_OUTPUT": str(output_124),
        "MUTATION_TIME_BUDGET": "82",
    }
    subprocess.run(
        ["bash", "-c", f"status=124\n{block}"],
        env=env_124,
        check=True,
    )
    assert "stop-reason=time-budget" in output_124.read_text(encoding="utf-8")

    env_1 = os.environ | {
        "GITHUB_OUTPUT": str(output_1),
        "MUTATION_TIME_BUDGET": "82",
    }
    subprocess.run(
        ["bash", "-c", f"status=1\n{block}"],
        env=env_1,
        check=True,
    )
    assert "stop-reason=time-budget" not in output_1.read_text(encoding="utf-8")


def _run_mutation_verify_require_step(
    *,
    run_result: str,
    stop_reason: str,
    time_budget: str,
) -> subprocess.CompletedProcess[str]:
    """Exécute l'étape de gate du job mutation-verify."""
    workflow = yaml.safe_load(MUTATION_POLICY_PATH.read_text(encoding="utf-8"))
    step = next(
        step
        for step in workflow["jobs"]["mutation-verify"]["steps"]
        if step.get("name") == "Require successful mutation execution"
    )
    env = os.environ | {
        "RUN_RESULT": run_result,
        "STOP_REASON": stop_reason,
        "TIME_BUDGET": time_budget,
    }
    return subprocess.run(
        ["bash", "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
    )


def test_mutation_verify_names_time_budget_cause() -> None:
    """Un arrêt sur budget est signalé comme limite de durée."""
    proc = _run_mutation_verify_require_step(
        run_result="failure",
        stop_reason="time-budget",
        time_budget="82",
    )
    assert proc.returncode == 1
    assert "82-minute time budget" in proc.stdout
    assert "duration limit" in proc.stdout


def test_mutation_verify_names_cancellation_cause() -> None:
    """Une annulation est signalée comme annulation."""
    proc = _run_mutation_verify_require_step(
        run_result="cancelled",
        stop_reason="",
        time_budget="",
    )
    assert proc.returncode == 1
    assert "cancelled" in proc.stdout


def test_mutation_verify_generic_failure_does_not_claim_time_budget() -> None:
    """Un échec ordinaire ne doit pas être présenté comme une expiration."""
    proc = _run_mutation_verify_require_step(
        run_result="failure",
        stop_reason="",
        time_budget="",
    )
    assert proc.returncode == 1
    assert "did not complete successfully" in proc.stdout
    assert "time budget" not in proc.stdout


def test_mutation_verify_accepts_success() -> None:
    """Un succès de mutation passe le gate."""
    proc = _run_mutation_verify_require_step(
        run_result="success",
        stop_reason="",
        time_budget="",
    )
    assert proc.returncode == 0


def test_node_mutation_evidence_job_uses_deadline_and_ceiling() -> None:
    """Le job Node mutation a un plafond et exécute la commande sous deadline."""
    workflow = yaml.safe_load(NODE_MUTATION_EVIDENCE_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["node-mutation"]

    assert job["timeout-minutes"] == 375
    execute_step = _workflow_step(workflow, "node-mutation", "execute")
    assert ".ci/mutation_deadline.py" in execute_step["run"]


def test_node_mutation_collect_reports_time_budget_failure(tmp_path: Path) -> None:
    """La collecte Node signale un échec dû au budget sans rapport."""
    workflow = yaml.safe_load(NODE_MUTATION_EVIDENCE_PATH.read_text(encoding="utf-8"))
    collect_step = _workflow_step(workflow, "node-mutation", "collect")

    output = tmp_path / "output"
    env = os.environ | {
        "GITHUB_OUTPUT": str(output),
        "REQUIRED": "true",
        "LABELS": "node-production-change",
        "EXECUTION": "failure",
        "STOP_REASON": "time-budget",
        "TIME_BUDGET": "82",
    }

    proc = subprocess.run(
        ["bash", "-c", collect_step["run"]],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    outputs = _read_output_file(output)
    assert outputs["status"] == "fail"
    assert "time budget" in proc.stdout


def test_deadline_kills_a_member_that_ignores_sigterm(tmp_path):
    """Revue PR 324 (R1) : le chef sort au SIGTERM, un membre l'ignore ; tout le groupe meurt."""
    pidfile = tmp_path / "pid"
    script = f"(trap '' TERM; echo $BASHPID > {pidfile}; exec sleep 300) & wait"
    assert deadline.run(["bash", "-c", script], minutes=1, grace=1, unit=0.5) == 124
    pid = pidfile.read_text().strip()
    for _ in range(20):
        state = subprocess.run(["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True).stdout.strip()
        if not state or state.startswith("Z"):
            break
        time.sleep(0.1)
    assert not state or state.startswith("Z"), state
