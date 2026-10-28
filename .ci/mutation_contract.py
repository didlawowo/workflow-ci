"""Shared mutation runner selection, policy protection and evidence validation.

This code belongs to workflow-ci, never to provisioners or consumer repositories.
It only reads PR code as data; test execution is handled separately in a sandbox.
"""

from __future__ import annotations

import configparser
import fnmatch
import json
from pathlib import Path
import subprocess
import tomllib


def select_engine(trusted: Path) -> str:
    """Select a central engine from protected-base project metadata."""
    if (trusted / "go.mod").is_file():
        return "go"
    if any((trusted / name).is_file() for name in ("pyproject.toml", "setup.cfg")):
        return "python"
    raise ValueError("No central mutation engine for protected-base project metadata")


def mutation_config(root: Path) -> dict:
    result = {}
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        configured = tomllib.loads(pyproject.read_text()).get("tool", {}).get("mutmut")
        if configured is not None:
            result["pyproject"] = configured
    setup = root / "setup.cfg"
    if setup.is_file():
        parser = configparser.ConfigParser()
        parser.read(setup)
        if parser.has_section("mutmut"):
            result["setup"] = dict(parser.items("mutmut"))
    for name in (".gremlins.yaml", ".gremlins.yml"):
        if (root / name).is_file():
            result[name] = (root / name).read_text()
    return result


def protect_config(trusted: Path, proposed: Path, base: str, head: str) -> None:
    if mutation_config(trusted) != mutation_config(proposed):
        raise ValueError("Mutation configuration differs from the protected base")
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(proposed),
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=0",
            f"{base}...{head}",
            "--",
            "*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # Keep the marker out of added diff lines: the trusted gate also scans
    # this detector's source and cannot distinguish strings from comments.
    suppression_marker = "pragma: " + "no mutate"
    if any(
        line.startswith("+")
        and not line.startswith("+++")
        and suppression_marker in line
        for line in diff.splitlines()
    ):
        raise ValueError("PR adds a mutation suppression")


def project_sync_args(root: Path) -> list[str]:
    """Keep a consumer's dependency lock and install its declared test dependencies."""
    data = tomllib.loads((root / "pyproject.toml").read_text())
    args = ["sync"]
    if (root / "uv.lock").exists():
        args.append("--locked")
    if "dev" in data.get("dependency-groups", {}):
        args += ["--group", "dev"]
    if "dev" in data.get("project", {}).get("optional-dependencies", {}):
        args += ["--extra", "dev"]
    return args


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key in mutation evidence: {key}")
        result[key] = value
    return result


def read_json(path: Path, root: Path) -> dict:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Mutation evidence escapes the sandbox")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
        raise ValueError(f"Missing, symlinked or oversized mutation evidence: {path}")
    data = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError("Mutation evidence must be an object")
    return data


def validate_python_evidence(
    repo: Path, targets: tuple[str, ...], base: str, head: str
) -> dict:
    """Require real results for EVERY selected function, including class methods."""
    if not targets:
        data = read_json(repo / ".quality" / "mutation-no-targets.json", repo)
        if data.get("scope") != {
            "base_sha": base,
            "head_sha": head,
            "no_targets": True,
        }:
            raise ValueError(
                "No-target evidence does not match the independently computed scope"
            )
        stats = data.get("stats")
        if stats != dict(killed=0, survived=0, timeouts=0, suspicious=0, total=0):
            raise ValueError("Nonzero or missing counters in no-target evidence")
        return data
    statuses = {}
    for path in (repo / "mutants").rglob("*.meta"):
        payload = read_json(path, repo).get("exit_code_by_key")
        if not isinstance(payload, dict):
            raise ValueError("Malformed per-mutant evidence")
        for key, code in payload.items():
            if key in statuses:
                raise ValueError(f"Duplicate mutation evidence: {key}")
            statuses[key] = code
    from mutation_scope import matches_function

    selected = {}
    for target in targets:
        matches = {
            key: code for key, code in statuses.items() if matches_function(key, target)
        }
        if not matches:
            raise ValueError(
                f"Changed function produced no mutation evidence: {target}"
            )
        selected.update(matches)
    # Mutmut 3: pytest exit 1 means the mutant was killed. All other values,
    # including None, skipped, timeout, crash and suspicious, fail closed.
    bad = {
        key: code
        for key, code in selected.items()
        if type(code) is not int or code != 1
    }
    if bad:
        raise ValueError(f"Mutation gate failed: non-killed mutants {bad}")
    return {
        "stats": {
            "killed": len(selected),
            "survived": 0,
            "timeouts": 0,
            "suspicious": 0,
            "total": len(selected),
        },
        "scope": {"base_sha": base, "head_sha": head, "targets": list(targets)},
    }


def validate_go_evidence(repo: Path, has_targets: bool, base: str, head: str) -> dict:
    data = read_json(repo / ".quality" / "gremlins.json", repo)
    scope = data.get("scope", {})
    if scope.get("base_sha") != base or scope.get("head_sha") != head:
        raise ValueError("Gremlins evidence does not match base/head")
    if scope.get("no_targets") is not (not has_targets):
        raise ValueError(
            "Gremlins evidence contradicts the independently computed scope"
        )
    stats = data.get("stats", {})
    names = ("killed", "survived", "timeouts", "suspicious", "total")
    if any(type(stats.get(name)) is not int or stats[name] < 0 for name in names):
        raise ValueError("Invalid Gremlins counters")
    if stats["total"] != sum(stats[name] for name in names[:-1]):
        raise ValueError("Inconsistent Gremlins counters")
    if stats["survived"] or stats["timeouts"] or stats["suspicious"]:
        raise ValueError("Mutation gate failed: Gremlins non-killed mutants")
    if has_targets != (stats["total"] > 0):
        raise ValueError("Missing Gremlins measurements")
    return data


def export_python_diagnostics(
    repo: Path, patterns: tuple[str, ...], destination: Path
) -> dict[str, int]:
    """Read native Mutmut metadata instead of guessing killed rows from source text."""
    result = {}
    all_statuses = {}
    for path in (repo / "mutants").rglob("*.meta"):
        entries = read_json(path, repo).get("exit_code_by_key")
        if not isinstance(entries, dict):
            raise ValueError("Malformed per-mutant metadata")
        for key, code in entries.items():
            if key in all_statuses:
                raise ValueError(f"Duplicate mutant metadata: {key}")
            if type(code) is not int:
                status = "skipped" if code is None else "suspicious"
            else:
                status = {
                    0: "survived",
                    1: "killed",
                    -24: "timeouts",
                    5: "no_tests",
                    33: "no_tests",
                    -11: "segfault",
                }.get(code, "suspicious")
            all_statuses[key] = status
            if not patterns or any(
                fnmatch.fnmatchcase(key, pattern) for pattern in patterns
            ):
                result[key] = status
    if not result:
        raise ValueError("No native per-mutant metadata for the selected functions")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(f"{key}: {result[key]}\n" for key in sorted(result)))
    counts = {
        key: 0
        for key in (
            "killed",
            "survived",
            "timeouts",
            "suspicious",
            "no_tests",
            "skipped",
            "segfault",
        )
    }
    for status in all_statuses.values():
        counts[status] += 1
    counts["total"] = len(all_statuses)
    return counts


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["diagnostics"]:
        counts = export_python_diagnostics(
            Path.cwd(), tuple(sys.argv[3:]), Path(sys.argv[2])
        )
        report = Path("mutants/mutmut-cicd-stats.json")
        payload = json.loads(report.read_text())
        for key in (
            "killed",
            "survived",
            "timeout",
            "suspicious",
            "no_tests",
            "skipped",
            "segfault",
            "check_was_interrupted_by_user",
            "total",
        ):
            payload.pop(key, None)
        payload.update(counts)
        report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        # NUL-delimited arguments; never interpolate metadata into shell code.
        sys.stdout.write("\0".join(project_sync_args(Path.cwd())) + "\0")
