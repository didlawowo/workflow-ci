"""Exercise the action's curl defaults against a local HTTP server."""

import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/git-cliff-bump/action.yml"


def action_steps():
    return yaml.safe_load(ACTION.read_text())["runs"]["steps"]


@pytest.fixture
def curl_home(tmp_path):
    step = next(s for s in action_steps() if s.get("id") == "download-config")
    output = tmp_path / "output"
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    subprocess.run(
        ["bash", "-c", step["run"]],
        env={
            **os.environ,
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(output),
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    path = Path(output.read_text().removeprefix("curl-home=").strip())
    assert path.parent == runner_temp
    return path


@pytest.mark.parametrize(
    "statuses, expected_returncode, expected_requests",
    [([500, 200], 0, 2), ([500], 22, 4), ([404], 22, 1)],
)
def test_http_retry_behavior(
    curl_home, tmp_path, statuses, expected_returncode, expected_requests
):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = statuses[min(len(requests), len(statuses) - 1)]
            requests.append(status)
            body = b"archive" if status == 200 else b"failed"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = tmp_path / "archive.tar.gz"
    try:
        result = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--location",
                "--output",
                str(target),
                f"http://127.0.0.1:{server.server_port}/archive",
            ],
            env={**os.environ, "CURL_HOME": str(curl_home), "NO_PROXY": "127.0.0.1"},
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == expected_returncode, result.stderr
    assert len(requests) == expected_requests
    if expected_returncode == 0:
        assert target.read_bytes() == b"archive"


def test_time_limits_are_finite_without_retrying_all_errors(curl_home):
    config = (curl_home / ".curlrc").read_text()
    assert "retry = 3" in config
    assert "retry-max-time = 120" in config
    assert "connect-timeout = 15" in config
    assert "max-time = 60" in config
    assert "retry-all-errors" not in config
    assert "insecure" not in config


def test_curl_config_is_scoped_to_every_upstream_call_not_the_job():
    steps = action_steps()
    upstream = [
        s for s in steps if s.get("uses", "").startswith("orhun/git-cliff-action@")
    ]
    assert len(upstream) == 4
    for step in upstream:
        assert (
            step["env"]["CURL_HOME"] == "${{ steps.download-config.outputs.curl-home }}"
        )
    for step in steps:
        if step not in upstream:
            assert "CURL_HOME" not in step.get("env", {})
        assert "GITHUB_ENV" not in step.get("run", "")
        assert "continue-on-error" not in step
