#!/usr/bin/env bash
# Preserve verified-only scanning; distinguish findings (183) from execution errors.
set -Eeuo pipefail
umask 077
STATUS=error
FINDINGS=''
MODE=unknown
SCAN_EXIT=1
TMP=''
TRUFFLEHOG_VERSION=3.97.7
finish() {
  rc=$?
  trap - EXIT
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'status=%s\nfindings=%s\nmode=%s\nexit-code=%s\n' "$STATUS" "$FINDINGS" "$MODE" "$SCAN_EXIT" >> "$GITHUB_OUTPUT"
  fi
  [[ -z "$TMP" ]] || rm -rf -- "$TMP"
  if [[ "$STATUS" == error ]]; then
    echo "::error::TruffleHog execution failed (exit $SCAN_EXIT); this is not a confirmed secret finding."
    exit 1
  fi
  exit "$rc"
}
trap finish EXIT
ROOT=$(git -C "${GITHUB_WORKSPACE:?}" rev-parse --show-toplevel)
cd "$ROOT"
[[ $(git rev-parse --is-shallow-repository) == false ]] || { echo '::error::Secret scanning requires fetch-depth: 0'; exit 1; }
BASE=''
HEAD=${GITHUB_SHA:-}
case "${GITHUB_EVENT_NAME:-}" in
  pull_request|pull_request_target)
    BASE=$(jq -er '.pull_request.base.sha' "${GITHUB_EVENT_PATH:?}")
    HEAD=$(jq -er '.pull_request.head.sha' "$GITHUB_EVENT_PATH")
    ;;
  push)
    BASE=$(jq -r '.before // ""' "${GITHUB_EVENT_PATH:?}")
    HEAD=$(jq -r --arg head "$HEAD" '.after // $head' "$GITHUB_EVENT_PATH")
    [[ "$BASE" != 0000000000000000000000000000000000000000 ]] || BASE=''
    ;;
  *) ;; # Manual/scheduled execution scans the full reachable history.
esac
[[ "$HEAD" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]]
HEAD=$(git rev-parse --verify "$HEAD^{commit}")
if [[ -n "$BASE" ]]; then
  [[ "$BASE" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]]
  if ! BASE_COMMIT=$(git rev-parse --verify "$BASE^{commit}" 2>/dev/null); then
    if [[ "${GITHUB_EVENT_NAME:-}" == pull_request* ]]; then
      echo '::error::PR base commit is absent; fetch complete base/head history.'
      exit 1
    fi
    BASE='' # A force push can orphan the old tip: broaden, never skip.
  else
    BASE=$BASE_COMMIT
  fi
fi
# Equal refs or diverging histories must not turn into a successful empty scan.
if [[ -n "$BASE" ]] && { [[ "$BASE" == "$HEAD" ]] || ! git merge-base --is-ancestor "$BASE" "$HEAD"; }; then
  BASE=''
fi
MODE=full
LOCAL_TRUFFLEHOG=""
if [[ "${WORKFLOW_CI_DISABLE_PREINSTALLED_TOOLS:-false}" != true ]]; then
  candidate="$(command -v trufflehog 2>/dev/null || true)"
  if [[ -n "$candidate" ]] && "$candidate" --version 2>&1 | grep -Fq "$TRUFFLEHOG_VERSION"; then
    LOCAL_TRUFFLEHOG="$candidate"
    echo "::notice::Using preinstalled TruffleHog $TRUFFLEHOG_VERSION from $LOCAL_TRUFFLEHOG"
  fi
fi

if [[ -n "$LOCAL_TRUFFLEHOG" ]]; then
  SOURCE_URI="file://$ROOT/"
else
  SOURCE_URI="file:///repo/"
fi
ARGS=(git "$SOURCE_URI" --branch "$HEAD" --fail --fail-on-scan-errors --no-update --only-verified --json)
if [[ -n "$BASE" ]]; then
  MODE=range
  ARGS+=(--since-commit "$BASE")
fi
TMP=$(mktemp -d "${RUNNER_TEMP:-/tmp}/trufflehog.XXXXXX")
# Prefer the verified binary baked into arc-runner. The pinned official image
# remains the portable fallback. Never expose credential-bearing JSON/stderr.
SCAN_EXIT=0
if [[ -n "$LOCAL_TRUFFLEHOG" ]]; then
  "$LOCAL_TRUFFLEHOG" "${ARGS[@]}" \
    > "$TMP/results.jsonl" 2> "$TMP/stderr.log" || SCAN_EXIT=$?
else
  docker run --rm -v "$ROOT:/repo:ro" -w /repo \
    "ghcr.io/trufflesecurity/trufflehog:$TRUFFLEHOG_VERSION" "${ARGS[@]}" \
    > "$TMP/results.jsonl" 2> "$TMP/stderr.log" || SCAN_EXIT=$?
fi
if ! jq -se 'all(.[]; type == "object" and .Verified == true)' "$TMP/results.jsonl" >/dev/null; then
  exit 1
fi
FINDINGS=$(jq -s 'length' "$TMP/results.jsonl")
case "$SCAN_EXIT:$FINDINGS" in
  0:0) STATUS=success ;;
  183:*) [[ "$FINDINGS" -gt 0 ]] || exit 1; STATUS=findings ;;
  *) exit 1 ;;
esac
printf 'TruffleHog: %s; verified findings=%s; scan=%s\n' "$STATUS" "$FINDINGS" "$MODE"
[[ "$STATUS" == success ]]
