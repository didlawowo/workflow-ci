"""Exercise real shell gate logic with deterministic fake tools, including failures."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github/actions/go-quality-security/quality.sh'


def run(tmp_path, operation, **overrides):
    output = tmp_path / 'outputs'
    output.write_text('')
    env = {
        **os.environ,
        'GITHUB_OUTPUT': str(output),
        'RUNNER_TEMP': str(tmp_path / 'temp'),
        'WORKFLOW_CI_DISABLE_PREINSTALLED_TOOLS': 'true',
    }
    env.update(overrides)
    process = subprocess.run(['bash', str(SCRIPT), operation], cwd=tmp_path, env=env,
                             capture_output=True, text=True, timeout=15)
    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
    return process, values


@pytest.mark.parametrize('extension,config,expected', [
    ('yml', 'version: "2"\n', 'v2.13.2'), ('yaml', "version: '2' # current\n", 'v2.13.2'),
    ('toml', 'version = "2"\n', 'v2.13.2'), ('json', '{"linters":{},"version":"2"}', 'v2.13.2'),
    ('yml', 'linters:\n  enable: [govet]\n', 'v1.64.8'),
])
def test_resolve_config_major_version(tmp_path, extension, config, expected):
    (tmp_path / f'.golangci.{extension}').write_text(config)
    process, values = run(tmp_path, 'resolve', LINT_VERSION='auto')
    assert process.returncode == 0, process.stderr
    assert values['version'] == expected


def test_explicit_version_and_invalid_pin(tmp_path):
    _, values = run(tmp_path, 'resolve', LINT_VERSION='v2.10.0')
    assert values['version'] == 'v2.10.0'
    process, _ = run(tmp_path, 'resolve', LINT_VERSION='latest; echo bad')
    assert process.returncode != 0


@pytest.mark.parametrize('version,has_v1_flag', [('v1.64.8', True), ('v2.13.2', False)])
def test_lint_uses_correct_cli_for_major(tmp_path, version, has_v1_flag):
    binary = tmp_path / 'golangci-lint'
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > arguments\nexit "${FAKE_RC:-0}"\n')
    binary.chmod(0o755)
    for code in ('0', '1'):
        process, values = run(tmp_path, 'lint', LINT_VERSION=version, LINT_BIN=str(tmp_path), FAKE_RC=code)
        assert process.returncode == 0
        assert values['status'] == ('passed' if code == '0' else 'failed')
        assert ('--out-format=github-actions' in (tmp_path / 'arguments').read_text()) is has_v1_flag


def fake_go(tmp_path):
    tools = tmp_path / 'tools'
    tools.mkdir()
    go = tools / 'go'
    go.write_text('''#!/usr/bin/env bash
[[ "${INSTALL_FAIL:-0}" != 1 ]] || exit 2
mkdir -p "$GOBIN"
cat > "$GOBIN/gosec" <<'SCANNER'
#!/usr/bin/env bash
[[ "${MISSING_REPORT:-0}" != 1 ]] || exit 2
printf '%s' "$FAKE_SARIF" > gosec-results.sarif
exit "${SCANNER_RC:-0}"
SCANNER
chmod +x "$GOBIN/gosec"
''')
    go.chmod(0o755)
    return str(tools) + ':' + os.environ['PATH']


@pytest.mark.parametrize('report,rc,status,issues', [
    ('{"runs":[{"results":[]}]}', '0', 'passed', '0'),
    ('{"runs":[{"results":[{}]},{"results":[{}]}]}', '1', 'findings', '2'),
    ('{"runs":[{"results":[]}]}', '2', 'failed', '-1'),
    ('{"runs":[]}', '0', 'failed', '-1'), ('broken', '0', 'failed', '-1'),
    ('{"runs":[{}]}', '0', 'failed', '-1'),
    ('{"runs":[{"results":[],"invocations":[{"executionSuccessful":false}]}]}', '0', 'failed', '-1'),
])
def test_gosec_requires_real_valid_evidence(tmp_path, report, rc, status, issues):
    process, values = run(tmp_path, 'gosec', PATH=fake_go(tmp_path), GOSEC_VERSION='v2.29.0',
                          FAKE_SARIF=report, SCANNER_RC=rc)
    assert values['status'] == status
    assert values['issues'] == issues
    assert (process.returncode == 0) is (status != 'failed')


def test_install_failure_does_not_reuse_stale_sarif(tmp_path):
    (tmp_path / 'gosec-results.sarif').write_text('{"runs":[{"results":[]}]}')
    process, values = run(tmp_path, 'gosec', PATH=fake_go(tmp_path), GOSEC_VERSION='v2.29.0', INSTALL_FAIL='1')
    assert process.returncode != 0
    assert values == {'status': 'failed', 'issues': '-1'}
    assert not (tmp_path / 'gosec-results.sarif').exists()


def test_upload_failure_cannot_override_local_checks_but_local_failures_block(tmp_path):
    env = dict(LINT_STATUS='passed', VET_STATUS='passed', FMT_STATUS='passed',
               GOSEC_STATUS='passed', GOSEC_ISSUES='0', CODEQL_ACTION_JOB_STATUS='JOB_STATUS_CONFIGURATION_ERROR')
    _, values = run(tmp_path, 'summary', **env)
    assert values['passed'] == 'true'
    for key in ('LINT_STATUS', 'VET_STATUS', 'FMT_STATUS', 'GOSEC_STATUS'):
        process, values = run(tmp_path, 'summary', **{**env, key: 'failed'})
        assert values['passed'] == 'false'
        assert 'Go quality failed:' in process.stdout
    _, values = run(tmp_path, 'summary', **{**env, 'GOSEC_ISSUES': '1'})
    assert values['passed'] == 'false'


def test_publication_is_opt_in_and_not_an_input_to_gate():
    action = (ROOT / '.github/actions/go-quality-security/action.yml').read_text()
    assert "upload-sarif:" in action and "default: 'false'" in action
    assert "inputs.upload-sarif == 'true'" in action
    upload = action.split('    - name: Upload GoSec SARIF', 1)[1]
    assert 'continue-on-error: true' in upload
    assert 'Retain local GoSec evidence' in action


def test_gosec_uses_matching_preinstalled_binary_without_go_install(tmp_path):
    tools = tmp_path / 'preinstalled'
    tools.mkdir()
    scanner = tools / 'gosec'
    scanner.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$1" == "-version" ]]; then echo "Version: 2.29.0"; exit 0; fi\n'
        'printf \'%s\' \'{"runs":[{"results":[]}]}\' > gosec-results.sarif\n'
    )
    scanner.chmod(0o755)

    process, values = run(
        tmp_path,
        'gosec',
        PATH=str(tools) + ':' + os.environ['PATH'],
        GOSEC_VERSION='v2.29.0',
        WORKFLOW_CI_DISABLE_PREINSTALLED_TOOLS='false',
    )
    assert process.returncode == 0, process.stderr
    assert values == {'status': 'passed', 'issues': '0'}
    assert 'Using preinstalled GoSec 2.29.0' in process.stdout
