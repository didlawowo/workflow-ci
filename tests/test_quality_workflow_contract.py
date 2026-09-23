from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "quality-evidence.yml"


def test_quality_evidence_is_reusable_and_not_recursive():
    content = WORKFLOW.read_text()

    assert "workflow_call:" in content
    assert "pull_request:" not in content
    assert "uses: didlawowo/workflow-ci/.github/workflows/quality-evidence.yml@" not in content


def test_quality_evidence_cancels_stale_caller_revisions():
    content = WORKFLOW.read_text()
    header = content.split("\njobs:", 1)[0]

    assert "concurrency:" in header
    assert (
        "group: quality-evidence-${{ github.repository }}-"
        "${{ github.event.pull_request.number || github.ref_name }}-"
        "${{ inputs.repo-type }}-${{ inputs.working-directory }}"
    ) in header
    assert "cancel-in-progress: true" in header


def test_quality_evidence_requires_explicit_runner_and_same_commit_actions():
    content = WORKFLOW.read_text()

    assert "runner:" in content
    assert "runs-on: ${{ inputs.runner }}" in content
    assert "workflow-ci-ref:" in content
    assert 'default: ""' in content
    assert "repository: didlawowo/workflow-ci" not in content
    assert "uses: $/.github/actions/run-python-tests" in content
    assert "uses: $/.github/actions/run-go-tests" in content
    assert "uses: $/.github/actions/run-node-tests" in content
    assert "ubuntu-latest" not in content
    assert "@main" not in content


def test_quality_evidence_dependency_chain_uses_same_commit_self_refs():
    root = Path(__file__).resolve().parents[1]
    go_tests = (root / ".github" / "actions" / "run-go-tests" / "action.yml").read_text()
    node_tests = (root / ".github" / "actions" / "run-node-tests" / "action.yml").read_text()
    python_quality = (root / ".github" / "actions" / "python-quality-security" / "action.yml").read_text()
    go_quality = (root / ".github" / "actions" / "go-quality-security" / "action.yml").read_text()
    node_quality = (root / ".github" / "actions" / "node-quality-security" / "action.yml").read_text()

    for content in (go_tests, node_tests, python_quality, go_quality, node_quality):
        assert "@main" not in content
        assert "didlawowo/workflow-ci/.github/actions/" not in content

    assert "uses: $/.github/actions/setup-go-env" in go_tests
    assert "uses: $/.github/actions/setup-node-env" in node_tests


def test_mutation_policy_classifies_every_pr_event_it_subscribes_to():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()
    header = content.split("\njobs:", 1)[0]
    mutation_run = content.split("  mutation-run:", 1)[1].split(
        "  mutation-verify:", 1
    )[0]
    mutation_verify = content.split("  mutation-verify:", 1)[1]

    assert "workflow_call:" in header
    assert "types: [opened, synchronize, reopened, labeled, unlabeled, edited]" in header
    assert "issues:" not in header
    assert "concurrency:" in header
    assert "group: mutation-policy-${{ github.repository }}-" in header
    assert "github.event.pull_request.number || github.run_id" in header
    assert "inputs.scope-key || 'default'" in header
    assert "cancel-in-progress: true" in header
    # Publishing is fail-closed when mutation classification is absent, so a
    # title-only edited event must still classify instead of skipping both jobs.
    assert "github.event.changes.body != null" not in mutation_run
    assert "github.event.changes.body != null" not in mutation_verify
    assert "if: github.event_name == 'pull_request'" in mutation_run
    assert "if: always() && github.event_name == 'pull_request'" in mutation_verify

def test_forgejo_filters_irrelevant_edits_and_isolates_noop_concurrency():
    root = Path(__file__).resolve().parents[1]
    content = (root / "templates" / "forgejo" / "mutation-policy.yml").read_text()
    header = content.split("\njobs:", 1)[0]
    mutation_run = content.split("  mutation-run:", 1)[1].split(
        "  mutation-verify:", 1
    )[0]
    mutation_verify = content.split("  mutation-verify:", 1)[1]

    assert "github.run_id" in header
    assert "github.event.label.name == 'complexity:high'" in header
    assert "github.event.label.name == 'priority:high'" in header
    assert "github.event.changes.body != null" in header
    assert "github.event.changes.body != null" in mutation_run
    assert "github.event.changes.body != null" in mutation_verify


def test_mutation_policy_separates_untrusted_execution_from_trusted_verification():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "pull_request_target:" not in content
    assert "pull_request:" in content
    assert "Fetch trusted base policy without submodule traversal" in content
    assert "Fetch pull request code without submodule traversal" in content
    assert "vars.UNTRUSTED_RUNNER || vars.RUNNER || format('arc-runner-{0}', github.event.repository.name)" in content
    assert "Resolve trusted mutation runner" in content
    assert ".workflow-ci/.ci/mutation-go.sh" in content
    assert ".workflow-ci/.ci/mutation.sh" in content
    assert "Setup Go for central Gremlins runner" in content
    assert "go-version-file: pr/go.mod" in content
    assert 'bash "${{ steps.runner.outputs.path }}"' in content
    assert "job.workflow_repository" in content
    assert "job.workflow_sha" in content
    assert "needs: [mutation-run]" in content
    assert "actions/download-artifact@v6" in content


def test_central_go_mutation_runner_is_pinned_and_strict():
    root = Path(__file__).resolve().parents[1]
    runner = (root / ".ci" / "mutation-go.sh").read_text()

    assert 'GREMLINS_VERSION="0.6.0"' in runner
    assert '--diff "$BASE_SHA"' in runner
    assert "--output .quality/gremlins-raw.json" in runner
    assert '"survived": survived' in runner
    assert "not_covered" in runner
    assert "timeouts" in runner
    assert "sha256sum -c -" in runner


def test_mutation_policy_requires_machine_readable_evidence_and_zero_survivors():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "Mandatory mutation run produced no supported mutation evidence" in content
    assert "mutation evidence is missing killed/survived counters" in content
    assert "mutation evidence contains no measured mutants" in content
    assert "if survived or timeouts or suspicious:" in content



def test_trusted_quality_enforces_ruff_and_sonarqube_quality_gate():
    root = Path(__file__).resolve().parents[1]
    workflow = WORKFLOW.read_text()
    sonar = (root / ".github" / "actions" / "sonarqube-scan" / "action.yml").read_text()

    assert "Verify Python quality and security" in workflow
    assert "uses: $/.github/actions/python-quality-security" in workflow
    python_quality = (
        root / ".github" / "actions" / "python-quality-security" / "action.yml"
    ).read_text()
    assert "Run Ruff linting" in python_quality
    assert "uvx --from ruff==0.16.8 ruff check ." in python_quality

    assert "sonar-enabled:" not in workflow
    assert "sonar-project-key:" not in workflow
    assert "sonar-extra-args:" not in workflow
    assert "SONAR_TOKEN:" in workflow
    assert "uses: $/.github/actions/sonarqube-scan" in workflow
    assert "project-key: ${{ vars.SONAR_PROJECT_KEY }}" in workflow
    assert "steps.sonarqube.outcome" in workflow
    assert "SonarQube Quality Gate failed, is not configured, or analysis could not complete" in workflow
    assert "sonar-project.properties is protected quality policy" in workflow
    assert "vars.SONAR_ENABLED == 'true'" in workflow
    assert "github.repository != 'didlawowo/workflow-ci'" not in workflow

    assert "SonarSource/sonarqube-scan-action@v8.2.2" in sonar
    assert "-Dsonar.projectKey=${{ inputs.project-key }}" in sonar
    assert "-Dsonar.host.url=https://sonarqube.dc-tech.work" in sonar
    assert "-Dsonar.qualitygate.wait=true" in sonar
    assert "-Dsonar.qualitygate.timeout=300" in sonar
    assert "coverage-args:" not in sonar
    assert "repo-type:" in sonar
    assert "working-directory:" in sonar
    assert "python-coverage-report-path:" in sonar
    assert "Invalid $label path for SonarQube" in sonar
    assert "extra-args:" not in sonar
    assert "wait-for-quality-gate:" not in sonar

def test_language_templates_make_quality_failures_blocking():
    root = Path(__file__).resolve().parents[1]
    for language in ("python", "go", "node"):
        content = (root / "templates" / language / "ci-branch-pipeline.yml").read_text()
        quality_index = content.index("quality-security:")
        gate_index = content.index("- name: Enforce quality and security gate")

        assert gate_index > quality_index
        assert content.count("- name: Enforce quality and security gate") == 1
        assert "push:\n    branches: [main]" in content
        assert "cancel-in-progress: true" in content
        assert (
            "if: always() && github.event_name != 'pull_request' && "
            "needs.tests.result == 'success' && "
            "needs.quality-security.result == 'success'"
        ) in content

    for language in ("python", "go"):
        content = (root / "templates" / language / "ci-branch-pipeline.yml").read_text()
        assert 'fail-on-coverage: "true"' in content


def test_mutation_verify_is_read_only_and_scoped_to_changed_functions():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    verify = content.split("  mutation-verify:", 1)[1]
    assert "issues: write" not in verify
    assert "pull-requests: write" not in verify
    assert "inputs.trusted-runner || vars.RUNNER || format('arc-runner-{0}', github.event.repository.name)" in verify
    assert "git\", \"-C\", str(repo), \"diff\", \"--unified=0\"" in verify
    assert "mutation gate failed for changed functions" in verify
    assert "scoped-mutation-evidence-" in verify
    assert "quality-report@main" not in verify


def test_python_security_action_propagates_requested_check_failures():
    root = Path(__file__).resolve().parents[1]
    content = (
        root / ".github" / "actions" / "python-quality-security" / "action.yml"
    ).read_text()

    assert "id: mypy" in content
    assert "id: trufflehog" in content
    assert "id: safety" in content
    assert "steps.mypy.outputs.status" in content
    assert "steps.trufflehog.outcome" in content
    assert "steps.safety.outputs.status" in content
    assert "SECURITY_ISSUES=$((SECURITY_ISSUES + 1))" in content


def test_mutation_policy_locks_engine_configuration_to_protected_base():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "Verify mutation configuration is unchanged" in content
    assert "Mutation-engine configuration differs from the protected base" in content
    assert 'result["pyproject.toml"] = mutmut' in content
    assert 'result["setup.cfg"] = dict(parser.items("mutmut"))' in content


def test_mutation_policy_rejects_changed_functions_without_mutants():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert "source_paths is part of the protected policy" in content
    assert "in_trusted_source_path" in content
    assert "changed functions produced no mutation " in content
    assert "evidence (not exercised or excluded)" in content
    assert "pragma: no mutate" in content


def test_mutation_policy_uses_uv_without_system_venv_dependency():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()
    runner = (root / ".ci" / "mutation.sh").read_text()

    assert "uv python install" in workflow
    assert 'uv venv "$VENV" --python "$PYTHON_VERSION" --seed' in workflow
    assert "python3 -m venv" not in workflow
    assert "python3 -m venv" not in runner
    assert "Mutation bootstrap failure" in workflow
    assert "Mutation bootstrap failure" in runner


def test_mutation_policy_passes_exact_pull_request_scope_to_runner():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()
    runner = (root / ".ci" / "mutation.sh").read_text()

    assert "MUTATION_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "MUTATION_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in workflow
    assert 'MUTATION_BASE_SHA="$MUTATION_BASE_SHA"' in workflow
    assert 'MUTATION_HEAD_SHA="$MUTATION_HEAD_SHA"' in workflow
    assert "mutation_scope.py" in runner
    assert "mutation-no-targets.json" in runner
    assert "No mutation targets in" in runner


def test_mutation_jobs_avoid_actions_checkout_and_diagnose_invalid_gitlinks():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    mutation_jobs = content.split("  mutation-run:", 1)[1]
    mutation_run, mutation_verify = mutation_jobs.split("  mutation-verify:", 1)

    assert "actions/checkout@" not in mutation_run
    assert "actions/checkout@" not in mutation_verify
    assert "Fetch trusted base policy without submodule traversal" in mutation_run
    assert "Fetch pull request code without submodule traversal" in mutation_run
    assert "Mutation checkout diagnostic: gitlink" in mutation_run
    assert "Mutation checkout failure:" in mutation_run
    assert "Mutation checkout diagnostic: gitlink" in mutation_verify


def test_issue_59_gitlink_parser_preserves_untrusted_paths_and_hidden_evidence():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert content.count("ls-files --stage -z") >= 2
    assert content.count('grep -Fxq -- "$gitlink"') >= 2
    assert content.count("include-hidden-files: true") >= 2


def test_issue_59_mutation_policy_never_uses_system_python():
    root = Path(__file__).resolve().parents[1]
    content = (
        root / ".github" / "workflows" / "mutation-policy.yml"
    ).read_text()

    assert "run: python " not in content
    assert "\n          python -" not in content
    assert "python3 -m venv" not in content
    assert content.count(
        "uv run --no-project --python 3.12 python"
    ) >= 4


def test_issue_59_deletion_only_hunks_remain_in_mutation_scope():
    root = Path(__file__).resolve().parents[1]
    scope = (root / ".ci" / "mutation_scope.py").read_text()
    workflow = (
        root / ".github" / "workflows" / "mutation-policy.yml"
    ).read_text()

    assert "A deletion-only hunk has no lines on the head side" in scope
    assert "max(start - 1, 1)" in scope
    assert 'raw.startswith("+++ /dev/null")' in workflow
    assert "Deletion-only hunks have no new-side lines" in workflow


def test_issue_59_mutation_policy_uses_exact_tree_range_and_isolated_home():
    root = Path(__file__).resolve().parents[1]
    content = (
        root / ".github" / "workflows" / "mutation-policy.yml"
    ).read_text()

    assert 'f"{base}...{head}"' in content
    assert 'f"{base}..{head}"' not in content.replace('f"{base}...{head}"', "")
    assert 'HOME="$ISOLATED_HOME"' in content
    assert 'UV_CACHE_DIR="$ISOLATED_UV_CACHE"' in content
    assert 'HOME="$HOME"' not in content


def test_language_templates_split_pr_fast_path_from_main_heavy_path():
    root = Path(__file__).resolve().parents[1]
    for language in ("python", "go", "node"):
        content = (root / "templates" / language / "ci-branch-pipeline.yml").read_text()
        header = content.split("\njobs:", 1)[0]

        assert "\n  push:\n    branches: [main]" in header
        assert "pull_request:" in header
        assert "workflow_dispatch:" in header
        assert "concurrency:" in header
        assert "group: ci-${{ github.workflow }}-${{ github.head_ref || github.ref_name }}" in header
        assert "cancel-in-progress: true" in header
        assert "github.event_name != 'pull_request'" in content
        assert "timeout-minutes:" in content


def test_internal_workflow_ci_refs_follow_immutable_version_contract():
    root = Path(__file__).resolve().parents[1]
    version = (root / ".workflow-ci-version").read_text().strip()
    assert version == "v1.8.0"

    scan_roots = (
        root / ".github" / "actions",
        root / ".github" / "workflows",
        root / "templates",
    )
    paths = [root / "README.md"]
    for scan_root in scan_roots:
        paths.extend(
            path
            for path in scan_root.rglob("*")
            if path.is_file() and path.suffix in {".yml", ".yaml", ".md"}
        )

    for path in paths:
        for line in path.read_text().splitlines():
            if "didlawowo/workflow-ci/" not in line:
                continue
            if "uses:" not in line:
                continue
            assert f"@{version}" in line, f"{path}: mutable/stale internal ref: {line}"

    release = (root / ".github" / "workflows" / "release.yml").read_text()
    assert 'workflow-ci-ref:' in release
    assert 'default: "v1.8.0"' in release


def test_internal_ref_sync_helper_is_present_and_checkable():
    root = Path(__file__).resolve().parents[1]
    helper = (root / ".github" / "scripts" / "sync_internal_refs.py").read_text()

    assert ".workflow-ci-version" in helper
    assert "--check" in helper
    assert "didlawowo/workflow-ci/" in helper


def test_issue_56_reacts_to_issue_label_add_and_remove():
    root = Path(__file__).resolve().parents[1]
    workflow = (
        root / ".github" / "workflows" / "mutation-issue-policy.yml"
    ).read_text()

    assert "types: [labeled, unlabeled]" in workflow
    assert "refresh-linked-prs:" in workflow
    assert "actions: write" in workflow
    assert "mutation_policy.py refresh" in workflow
    assert "inputs.runner || vars.RUNNER || format('arc-runner-{0}', github.event.repository.name)" in workflow
    assert "job.workflow_repository" in workflow
    assert "job.workflow_sha" in workflow
    notify = workflow.split("  notify:", 1)[1].split("  refresh-linked-prs:", 1)[0]
    assert "github.event.action == 'labeled'" in notify


def test_release_workflow_is_idempotent_and_recoverable():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "release.yml").read_text()

    assert "Detect recoverable release state" in content
    assert "Publish or recover release" in content
    assert 'git ls-remote origin "refs/tags/$NEW_VERSION"' in content
    assert 'gh release view "$NEW_VERSION"' in content
    assert "--generate-notes --verify-tag" in content
    assert "steps.result.outputs.released" in content
    assert "steps.publish.outputs.dispatch == 'true'" in content

    steps = content.split("    steps:", 1)[1]
    before_result = steps.split("      - name: Resolve release result", 1)[0]
    assert "steps.result.outputs.version" not in before_result


def test_forgejo_mutation_policy_template_matches_label_refresh_contract():
    root = Path(__file__).resolve().parents[1]
    github = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()
    forgejo = (root / "templates" / "forgejo" / "mutation-policy.yml").read_text()

    assert "types: [labeled, unlabeled]" in forgejo
    assert "types: [opened, synchronize, reopened, labeled, unlabeled, edited]" in forgejo
    assert "concurrency:" in forgejo.split("\njobs:", 1)[0]
    assert "cancel-in-progress: true" in forgejo.split("\njobs:", 1)[0]
    assert "refresh-linked-prs:" in forgejo
    assert "POLICY_PROVIDER: forgejo" in forgejo
    assert "POLICY_PROVIDER: github" not in forgejo
    assert "mutation_policy.py refresh" in forgejo
    assert "MUTATION_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in forgejo
    assert "MUTATION_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in forgejo

    # Keep the actual mutation execution/verification structure in parity.
    for job in ("mutation-run:", "mutation-verify:"):
        assert job in github
        assert job in forgejo


def test_quality_evidence_separates_read_only_execution_from_privileged_publication():
    content = WORKFLOW.read_text()

    execution = content.split("  independent-verification:", 1)[1].split(
        "  publish-evidence:", 1
    )[0]
    publisher = content.split("  publish-evidence:", 1)[1]

    assert "issues: write" not in execution
    assert "pull-requests: write" not in execution
    assert execution.count("persist-credentials: false") >= 1
    assert "uses: $/.github/actions/quality-report" not in execution
    assert "repository: didlawowo/workflow-ci" not in execution

    assert "needs: [independent-verification, mutation]" in publisher
    assert "scope-key: ${{ format('{0}-{1}', inputs.repo-type, inputs.working-directory) }}" in content
    assert "Download trusted mutation evidence" in publisher
    assert "needs.mutation.outputs.report-file" in publisher
    assert "format('.mutation-evidence/{0}', needs.mutation.outputs.report-file)" in publisher
    assert "mutation-required: ${{ needs.mutation.outputs.required || 'unknown' }}" in publisher
    assert "issues: write" not in publisher
    assert "pull-requests: write" in publisher
    assert publisher.count("persist-credentials: false") >= 1
    assert "uses: $/.github/actions/quality-report" in publisher
    assert "repository: didlawowo/workflow-ci" not in publisher
    assert 'junit-glob: "${{ runner.temp }}/quality-evidence/no-junit.xml"' in publisher
    assert 'coverage-glob: "${{ runner.temp }}/quality-evidence/no-coverage.xml"' in publisher


def test_consumer_selftest_grants_reusable_publisher_pr_write_permission():
    root = Path(__file__).resolve().parents[1]
    content = (
        root / ".github" / "workflows" / "consumer-integration-selftest.yml"
    ).read_text()

    assert content.count("pull-requests: write") == 3
    assert "sonar-enabled:" not in content
    assert "pull-requests: read" not in content


def test_executable_actions_never_use_mutable_main_refs():
    root = Path(__file__).resolve().parents[1]
    scan_roots = (
        root / ".github" / "actions",
        root / ".github" / "workflows",
        root / "templates",
    )

    for scan_root in scan_roots:
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".yml", ".yaml"}:
                continue
            for line in path.read_text().splitlines():
                if "uses:" not in line:
                    continue
                assert "@main" not in line, f"{path}: mutable action ref: {line}"


def test_release_workflow_never_commits_workflow_ci_checkout():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "release.yml").read_text()

    assert "Exclude workflow-ci checkout from release commits" in content
    assert "'.workflow-ci/' >> .git/info/exclude" in content
    assert "git reset -- .workflow-ci 2>/dev/null || true" in content



def test_mutation_jobs_force_uv_cache_into_runner_temp():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    mutation_run = content.split("  mutation-run:", 1)[1].split(
        "  mutation-verify:", 1
    )[0]
    mutation_verify = content.split("  mutation-verify:", 1)[1]

    for job in (mutation_run, mutation_verify):
        assert "Configure writable uv cache" in job
        assert 'UV_CACHE="${RUNNER_TEMP:-/tmp}/uv-cache"' in job
        assert 'echo "UV_CACHE_DIR=$UV_CACHE" >> "$GITHUB_ENV"' in job
        assert "enable-cache: false" in job

    assert "UV_CACHE_DIR: ${{ runner.temp }}/uv-cache" not in content



def test_consumer_mutation_runner_is_materialized_from_protected_tree_only():
    root = Path(__file__).resolve().parents[1]
    content = (root / ".github" / "workflows" / "mutation-policy.yml").read_text()

    assert 'TRUSTED_DIR="$GITHUB_WORKSPACE/pr/.workflow-ci-trusted-runner"' in content
    assert 'rm -rf -- "$TRUSTED_DIR"' in content
    assert 'mkdir -m 700 -- "$TRUSTED_DIR"' in content
    assert 'TRUSTED_REAL="$(realpath -e "$TRUSTED_DIR")"' in content
    assert '"$PR_ROOT"/*' in content
    assert 'cp -a "$GITHUB_WORKSPACE/.policy/.ci/." "$TRUSTED_DIR/"' in content
    assert 'MATERIALIZED="$TRUSTED_DIR/mutation.sh"' in content
    assert 'cmp --silent "$CUSTOM" "$MATERIALIZED"' in content
    assert 'MATERIALIZED="$GITHUB_WORKSPACE/pr/.ci/mutation.sh"' not in content
    assert "publishes no supported trusted evidence; falling back to the central runner" in content


def test_mutation_diagnostics_accept_machine_readable_stats_without_mutmut_binary():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        ".github/workflows/mutation-policy.yml",
        "templates/forgejo/mutation-policy.yml",
    ):
        content = (root / relative).read_text()
        capture = content.split("- name: Capture mutmut diagnostics", 1)[1].split(
            "- name: Locate mutation evidence", 1
        )[0]

        stats_guard = 'elif [[ -f mutants/mutmut-cicd-stats.json ]]; then'
        assert stats_guard in capture
        assert "Mutmut completed successfully; machine-readable diagnostics are in " in capture
        assert capture.index(stats_guard) < capture.index(
            "Mutation diagnostics unavailable"
        )
