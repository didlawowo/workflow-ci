"""Execute the actual composite-action shell with offline scanner doubles."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "python-quality-security" / "action.yml"
FINDINGS = {"vulnerabilities": [{"package_name": "example", "vulnerability_id": "123"}]}
CLEAN = {"vulnerabilities": []}


def safety_script() -> str:
    block = ACTION.read_text().split("    - name: Safety dependency check\n", 1)[1]
    block = block.split("    - name: Analyze results\n", 1)[0]
    return textwrap.dedent(
        block.split("      run: |\n", 1)[1].split("      continue-on-error:", 1)[0]
    )


def run_safety(
    tmp_path, exit_code, report_mode, payload, source="pyproject", export_code=0
):
    project = tmp_path / "project with spaces"
    project.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    scratch = tmp_path / "runner temp"
    scratch.mkdir()
    output = tmp_path / "outputs"
    uv_log = tmp_path / "uv-args.json"
    scan_log = tmp_path / "scanner-args.json"
    if source == "pyproject":
        (project / "pyproject.toml").write_text(
            '[project]\nname="fixture"\nversion="1"\n'
        )
    elif source == "requirements":
        (project / "requirements.txt").write_text("fallback-package==2.0\n")
    (project / "safety-report.json").write_text(json.dumps(FINDINGS))
    (scratch / "safety-requirements.txt").write_text("stale-package==0.0\n")

    uv = tools / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent("""\
        import json, os, sys
        from pathlib import Path
        args = sys.argv[1:]
        Path(os.environ["FAKE_UV_LOG"]).write_text(json.dumps(args))
        if int(os.environ["FAKE_EXPORT_RC"]):
            raise SystemExit(int(os.environ["FAKE_EXPORT_RC"]))
        assert args[0] == "export"
        target = args[args.index("--output-file") + 1]
        Path(target).write_text("production-package==1.0\\n")
        """)
    )
    uv.chmod(0o755)
    uvx = tools / "uvx"
    uvx.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent("""\
        import json, os, sys
        from pathlib import Path
        args = sys.argv[1:]
        assert args[:4] == ["--from", "safety==3.8.1", "safety", "check"]
        requirements = Path(args[args.index("-r") + 1]).read_text()
        Path(os.environ["FAKE_SCAN_LOG"]).write_text(json.dumps({"args": args, "requirements": requirements}))
        print("+====================+\\nDEPRECATED: check command\\n+====================+")
        payload = os.environ["FAKE_PAYLOAD"]
        print(payload)
        print("+====================+\\nDEPRECATED: check command\\n+====================+")
        if "--save-json" in args:
            target = Path(args[args.index("--save-json") + 1])
            mode = os.environ["FAKE_REPORT_MODE"]
            if mode == "valid":
                target.write_text(payload)
            elif mode == "invalid":
                target.write_text("{broken JSON")
        if int(os.environ["FAKE_SCAN_RC"]) not in (0, 64):
            print("scanner network failure", file=sys.stderr)
        raise SystemExit(int(os.environ["FAKE_SCAN_RC"]))
        """)
    )
    uvx.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(scratch),
        "GITHUB_ACTION_PATH": str(ACTION.parent),
        "GITHUB_OUTPUT": str(output),
        "FAKE_UV_LOG": str(uv_log),
        "FAKE_SCAN_LOG": str(scan_log),
        "FAKE_EXPORT_RC": str(export_code),
        "FAKE_SCAN_RC": str(exit_code),
        "FAKE_REPORT_MODE": report_mode,
        "FAKE_PAYLOAD": json.dumps(payload),
    }
    result = subprocess.run(
        ["bash", "-c", safety_script()],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, output, scan_log, uv_log, project


@pytest.mark.parametrize(
    "exit_code,mode,payload,expected",
    [
        (64, "valid", FINDINGS, "findings"),
        (0, "valid", CLEAN, "passed"),
        (64, "missing", FINDINGS, "error"),
        (64, "invalid", FINDINGS, "error"),
        (0, "missing", CLEAN, "error"),
        (0, "invalid", CLEAN, "error"),
        (64, "valid", CLEAN, "error"),
        (1, "valid", FINDINGS, "error"),
        (70, "missing", CLEAN, "error"),
        (0, "valid", {"error": "failed"}, "error"),
    ],
)
def test_safety_step_uses_saved_report_not_banner_stdout(
    tmp_path, exit_code, mode, payload, expected
):
    result, output, scan_log, uv_log, project = run_safety(
        tmp_path, exit_code, mode, payload
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"status={expected}"
    scan = json.loads(scan_log.read_text())
    assert scan["requirements"] == "production-package==1.0\n"
    args = scan["args"]
    assert args[args.index("--save-json") + 1] == "safety-report.json"
    assert "--continue-on-error" not in args
    assert "--ignore" not in args
    exported = json.loads(uv_log.read_text())
    assert "--no-dev" in exported
    assert "--no-emit-project" in exported
    if mode == "valid":
        assert json.loads((project / "safety-report.json").read_text()) == payload
    if mode == "missing":
        assert not (project / "safety-report.json").exists()
    if exit_code not in (0, 64):
        assert "scanner network failure" in result.stderr


@pytest.mark.parametrize(
    "source,expected",
    [
        ("requirements", "fallback-package==2.0\n"),
        ("none", ""),
    ],
)
def test_safety_step_preserves_non_pyproject_inputs(tmp_path, source, expected):
    result, output, scan_log, uv_log, _ = run_safety(
        tmp_path, 0, "valid", CLEAN, source=source
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == "status=passed"
    assert json.loads(scan_log.read_text())["requirements"] == expected
    assert not uv_log.exists()


def test_dependency_export_failure_does_not_run_scanner_or_emit_success(tmp_path):
    result, output, scan_log, _, _ = run_safety(
        tmp_path, 0, "valid", CLEAN, export_code=2
    )
    assert result.returncode != 0
    assert not scan_log.exists()
    assert not output.exists()
