#!/usr/bin/env bash
set -euo pipefail

GREMLINS_VERSION="0.6.0"
BASE_SHA="${MUTATION_BASE_SHA:-}"
HEAD_SHA="${MUTATION_HEAD_SHA:-}"

if [[ -z "$BASE_SHA" || -z "$HEAD_SHA" ]]; then
  echo "::error::Go mutation scope requires MUTATION_BASE_SHA and MUTATION_HEAD_SHA."
  exit 1
fi

case "$(uname -s)" in
  Linux) os="linux" ;;
  *) echo "::error::Central Gremlins runner currently supports Linux CI runners only."; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64) arch="amd64" ;;
  aarch64|arm64) arch="arm64" ;;
  *) echo "::error::Unsupported runner architecture for Gremlins: $(uname -m)"; exit 1 ;;
esac

GREMLINS=""
tmp=""
cleanup() {
  [[ -z "$tmp" ]] || rm -rf "$tmp"
}
trap cleanup EXIT

if [[ "${WORKFLOW_CI_DISABLE_PREINSTALLED_TOOLS:-false}" != true ]]; then
  candidate="$(command -v gremlins 2>/dev/null || true)"
  if [[ -n "$candidate" ]] && "$candidate" --version 2>&1 | grep -Fq "$GREMLINS_VERSION"; then
    GREMLINS="$candidate"
    echo "::notice::Using preinstalled Gremlins $GREMLINS_VERSION from $GREMLINS"
  fi
fi

if [[ -z "$GREMLINS" ]]; then
  asset="gremlins_${GREMLINS_VERSION}_${os}_${arch}.tar.gz"
  base_url="https://github.com/go-gremlins/gremlins/releases/download/v${GREMLINS_VERSION}"
  tmp="$(mktemp -d "${RUNNER_TEMP:-/tmp}/gremlins.XXXXXX")"

  curl -fsSL "$base_url/$asset" -o "$tmp/$asset"
  curl -fsSL "$base_url/checksums.txt" -o "$tmp/checksums.txt"
  (
    cd "$tmp"
    grep -E "[[:space:]]${asset}$" checksums.txt | sha256sum -c -
  )
  tar -xzf "$tmp/$asset" -C "$tmp"
  GREMLINS="$(find "$tmp" -maxdepth 2 -type f -name gremlins -perm -u+x -print -quit)"
  if [[ -z "$GREMLINS" ]]; then
    echo "::error::Gremlins binary missing from verified release archive."
    exit 1
  fi
fi

mkdir -p .quality
changed_go="$(git diff --name-only "$BASE_SHA...$HEAD_SHA" -- '*.go' | grep -Ev '(^|/).*_test\.go$' || true)"
if [[ -z "$changed_go" ]]; then
  uv run --no-project --python 3.12 python - "$BASE_SHA" "$HEAD_SHA" <<'PY'
import json
import sys
from pathlib import Path

Path(".quality/gremlins.json").write_text(
    json.dumps(
        {
            "stats": {
                "killed": 0,
                "survived": 0,
                "timeouts": 0,
                "suspicious": 0,
                "total": 0,
            },
            "scope": {
                "base_sha": sys.argv[1],
                "head_sha": sys.argv[2],
                "no_targets": True,
            },
            "engine": {"name": "gremlins", "version": "0.6.0"},
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
  echo "::notice::No changed non-test Go files in $BASE_SHA...$HEAD_SHA; Gremlins skipped."
  exit 0
fi

set +e
"$GREMLINS" unleash --diff "$BASE_SHA" --output .quality/gremlins-raw.json
gremlins_rc=$?
set -e

if [[ ! -s .quality/gremlins-raw.json ]]; then
  echo "::error::Gremlins did not produce machine-readable evidence (exit=$gremlins_rc)."
  exit "${gremlins_rc:-1}"
fi

uv run --no-project --python 3.12 python - "$BASE_SHA" "$HEAD_SHA" "$GREMLINS_VERSION" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

raw = json.loads(Path(".quality/gremlins-raw.json").read_text(encoding="utf-8"))
statuses = Counter()
for file_result in raw.get("files", []):
    for mutation in file_result.get("mutations", []):
        status = str(mutation.get("status", "")).strip().upper().replace("_", " ")
        if status:
            statuses[status] += 1

killed = statuses["KILLED"] or int(raw.get("mutants_killed", 0))
lived = statuses["LIVED"] or int(raw.get("mutants_lived", 0))
not_covered = statuses["NOT COVERED"] or int(raw.get("mutants_not_covered", 0))
timeouts = statuses["TIMED OUT"]
survived = lived + not_covered
total = killed + survived + timeouts

payload = {
    "stats": {
        "killed": killed,
        "survived": survived,
        "timeouts": timeouts,
        "suspicious": 0,
        "total": total,
    },
    "scope": {
        "base_sha": sys.argv[1],
        "head_sha": sys.argv[2],
        "no_targets": False,
        "changed_go_files": True,
    },
    "engine": {
        "name": "gremlins",
        "version": sys.argv[3],
        "mutants_lived": lived,
        "mutants_not_covered": not_covered,
        "mutants_not_viable": int(raw.get("mutants_not_viable", 0)),
    },
}
Path(".quality/gremlins.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

if total <= 0:
    raise SystemExit(
        "Gremlins produced no viable mutation evidence for changed non-test Go files"
    )
if survived or timeouts:
    raise SystemExit(
        f"Go mutation gate failed: killed={killed}, lived={lived}, "
        f"not_covered={not_covered}, timeouts={timeouts}"
    )
print(f"Go mutation gate passed: {killed}/{total} mutants killed")
PY

if [[ "$gremlins_rc" -ne 0 ]]; then
  echo "::error::Gremlins execution failed with exit code $gremlins_rc."
  exit "$gremlins_rc"
fi
