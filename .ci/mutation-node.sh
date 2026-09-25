#!/usr/bin/env bash
set -euo pipefail

BASE_SHA="${MUTATION_BASE_SHA:-}"
HEAD_SHA="${MUTATION_HEAD_SHA:-}"
DEPTH="${MUTATION_DEPTH:-medium}"
WORKDIR="${MUTATION_WORKING_DIRECTORY:-.}"
TEST_COMMAND="${MUTATION_NODE_TEST_COMMAND:-}"

[[ -n "$BASE_SHA" && -n "$HEAD_SHA" ]] || {
  echo "::error::Node mutation requires MUTATION_BASE_SHA and MUTATION_HEAD_SHA."
  exit 1
}
case "$DEPTH" in
  medium|high) ;;
  *) echo "::error::Unsupported MUTATION_DEPTH=$DEPTH"; exit 1 ;;
esac

ROOT="$PWD"
PROJECT="$ROOT/$WORKDIR"
[[ -f "$PROJECT/package.json" ]] || {
  echo "::error::No package.json under mutation working directory: $WORKDIR"
  exit 1
}

mkdir -p "$ROOT/.quality"

mapfile -t CHANGED < <(
  git diff --name-only --diff-filter=ACMR "$BASE_SHA...$HEAD_SHA" -- "$WORKDIR" |
    grep -E '\.(js|jsx|ts|tsx)$' |
    grep -Ev '(^|/)(test|tests|__tests__|node_modules|dist|build)/|\.(test|spec)\.(js|jsx|ts|tsx)$' || true
)

if (( ${#CHANGED[@]} == 0 )); then
  python3 - "$BASE_SHA" "$HEAD_SHA" "$DEPTH" <<'PY'
import json
import sys
from pathlib import Path

Path('.quality/stryker.json').write_text(
    json.dumps({
        'engine': {'name': 'stryker', 'version': '9.6.1'},
        'depth': sys.argv[3],
        'scope': {'base_sha': sys.argv[1], 'head_sha': sys.argv[2], 'no_targets': True},
        'stats': {'killed': 0, 'survived': 0, 'timeouts': 0, 'not_covered': 0, 'total': 0},
    }, indent=2, sort_keys=True) + '\n',
    encoding='utf-8',
)
PY
  echo "::notice::No changed Node/TS production files; Stryker skipped."
  exit 0
fi

mutate_entries=()
for repo_file in "${CHANGED[@]}"; do
  project_file="${repo_file#${WORKDIR%/}/}"
  if [[ "$WORKDIR" == "." ]]; then
    project_file="$repo_file"
  fi
  if [[ "$DEPTH" == "high" ]]; then
    mutate_entries+=("$project_file")
    continue
  fi
  found=false
  while IFS= read -r hunk; do
    [[ -n "$hunk" ]] || continue
    start="${hunk%% *}"
    count="${hunk#* }"
    [[ "$count" != "$hunk" ]] || count=1
    [[ -n "$count" ]] || count=1
    if (( count > 0 )); then
      mutate_entries+=("$project_file:$start-$((start + count - 1))")
      found=true
    fi
  done < <(
    git diff --unified=0 "$BASE_SHA...$HEAD_SHA" -- "$repo_file" |
      sed -nE 's/^@@ -[0-9]+(,[0-9]+)? \+([0-9]+)(,([0-9]+))? @@.*/\2 \4/p'
  )
  if [[ "$found" == false ]]; then
    mutate_entries+=("$project_file")
  fi
done

printf 'Node mutation targets (%s):\n' "$DEPTH"
printf '  - %s\n' "${mutate_entries[@]}"

if [[ -z "$TEST_COMMAND" ]]; then
  if command -v bun >/dev/null 2>&1; then
    TEST_COMMAND="bun run test"
  elif command -v npm >/dev/null 2>&1; then
    TEST_COMMAND="npm test -- --runInBand"
  else
    echo "::error::No default Node test command available."
    exit 1
  fi
fi

TOOLS="${RUNNER_TEMP:-/tmp}/workflow-ci-stryker-tools"
REPORT="${RUNNER_TEMP:-/tmp}/workflow-ci-stryker-report.json"
CONFIG="${RUNNER_TEMP:-/tmp}/workflow-ci-stryker.config.json"
rm -rf "$TOOLS"
mkdir -p "$TOOLS"

cat > "$TOOLS/package.json" <<'JSON'
{
  "private": true,
  "dependencies": {
    "@stryker-mutator/core": "9.6.1",
    "typescript": "5.6.3"
  }
}
JSON

if command -v bun >/dev/null 2>&1; then
  (cd "$TOOLS" && bun install --no-progress)
elif command -v npm >/dev/null 2>&1; then
  (cd "$TOOLS" && npm install --ignore-scripts --no-audit --no-fund)
else
  echo "::error::Neither bun nor npm is available to provision Stryker."
  exit 1
fi

python3 - "$CONFIG" "$REPORT" "$TEST_COMMAND" "${mutate_entries[@]}" <<'PY'
import json
import sys
from pathlib import Path

config, report, command, *mutate = sys.argv[1:]
payload = {
    'mutate': mutate,
    'testRunner': 'command',
    'commandRunner': {'command': command},
    'coverageAnalysis': 'off',
    'reporters': ['clear-text', 'json'],
    'jsonReporter': {'fileName': report},
    'thresholds': {'high': 100, 'low': 100, 'break': 100},
    'concurrency': 2,
    'timeoutMS': 20000,
    'timeoutFactor': 2,
}
Path(config).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
PY

set +e
(cd "$PROJECT" && PATH="$TOOLS/node_modules/.bin:$PATH" stryker run "$CONFIG")
stryker_rc=$?
set -e

[[ -s "$REPORT" ]] || {
  echo "::error::Stryker did not produce mutation evidence (exit=$stryker_rc)."
  exit 1
}

python3 - "$REPORT" "$ROOT/.quality/stryker.json" "$BASE_SHA" "$HEAD_SHA" "$DEPTH" "$stryker_rc" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

raw_path, out_path, base, head, depth, rc = sys.argv[1:]
raw = json.loads(Path(raw_path).read_text(encoding='utf-8'))
mutants = []
for value in (raw.get('files') or {}).values():
    mutants.extend(value.get('mutants') or [])

statuses = Counter(str(item.get('status') or '') for item in mutants)
killed = statuses['Killed']
survived = statuses['Survived']
timeouts = statuses['Timeout']
not_covered = statuses['NoCoverage']
other_bad = sum(
    count for status, count in statuses.items()
    if status not in {'Killed', 'Survived', 'Timeout', 'NoCoverage', 'Ignored'}
)
total = killed + survived + timeouts + not_covered + other_bad

payload = {
    'engine': {'name': 'stryker', 'version': '9.6.1'},
    'depth': depth,
    'scope': {'base_sha': base, 'head_sha': head, 'no_targets': False},
    'stats': {
        'killed': killed,
        'survived': survived,
        'timeouts': timeouts,
        'not_covered': not_covered,
        'other_bad': other_bad,
        'total': total,
    },
}
Path(out_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')

print(f"Stryker evidence: {payload['stats']}")
if total <= 0:
    raise SystemExit('mutation gate failed: no Node mutants were measured')
if survived or timeouts or not_covered or other_bad:
    raise SystemExit(
        'Node mutation gate failed: '
        f'survived={survived}, timeouts={timeouts}, not_covered={not_covered}, other_bad={other_bad}'
    )
if int(rc) != 0:
    raise SystemExit(f'Stryker returned unexpected exit code {rc}')
PY
