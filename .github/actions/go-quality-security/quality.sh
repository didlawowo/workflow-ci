#!/usr/bin/env bash
# The scanner result and optional GitHub publication are independent contracts.
set -euo pipefail
emit() { printf '%s=%s\n' "$1" "$2" >> "${GITHUB_OUTPUT:?}"; }
case "${1:-}" in
  resolve)
    version="${LINT_VERSION:-auto}"
    if [[ "$version" == auto ]]; then
      version=v1.64.8
      directory="$PWD"
      root="$(git rev-parse --show-toplevel 2>/dev/null || printf '%s' "$PWD")"
      while :; do
        found=false
        for ext in yml yaml toml json; do
          config="$directory/.golangci.$ext"
          [[ -f "$config" ]] || continue
          found=true
          if [[ "$ext" == json ]]; then
            configured="$(jq -r '.version // "1"' "$config")"
          else
            configured="$(sed -nE "s/^[[:space:]]*version[[:space:]]*[:=][[:space:]]*[\"']?([12])[\"']?([[:space:]]*(#.*)?)?$/\1/p" "$config" | head -1)"
          fi
          [[ "$configured" != 2 ]] || version=v2.13.2
          break
        done
        [[ "$found" == false && "$directory" != "$root" && "$directory" != / ]] || break
        directory="$(dirname "$directory")"
      done
    fi
    [[ "$version" =~ ^v[12]\.[0-9]+\.[0-9]+$ ]] || { echo '::error::golangci-lint-version must be auto or an exact v1/v2 release'; exit 1; }
    emit version "$version"
    emit bin "${RUNNER_TEMP:?}/workflow-ci-tools/$version"
    ;;
  lint)
    args=(run "--timeout=${LINT_TIMEOUT:-5m}")
    [[ "${LINT_VERSION:?}" != v1.* ]] || args+=(--out-format=github-actions)
    if "${LINT_BIN:?}/golangci-lint" "${args[@]}" ./...; then emit status passed; else emit status failed; fi
    ;;
  vet)
    if go vet ./...; then emit status passed; else emit status failed; fi
    ;;
  fmt)
    if unformatted="$(gofmt -l .)" && [[ -z "$unformatted" ]]; then emit status passed
    else printf '%s\n' "${unformatted:-gofmt failed}"; emit status failed; fi
    ;;
  gosec)
    # Delete any tracked/stale report before attempting the actual scanner.
    rm -f gosec-results.sarif
    emit status failed
    emit issues -1
    if [[ ! "${GOSEC_VERSION:?}" =~ ^v2\.[0-9]+\.[0-9]+$ ]]; then echo '::error::gosec-version must be an exact v2 release'; exit 1; fi
    bin="${RUNNER_TEMP:?}/workflow-ci-tools/gosec-${GOSEC_VERSION}"
    mkdir -p "$bin"
    if ! GOBIN="$bin" GOTOOLCHAIN=auto go install "github.com/securego/gosec/v2/cmd/gosec@$GOSEC_VERSION"; then
      echo '::error::GoSec installation failed; no security evidence'; exit 1
    fi
    rc=0
    "$bin/gosec" -fmt sarif -out gosec-results.sarif ./... || rc=$?
    if ! count="$(jq -er '
      if (.runs | type) != "array" or (.runs | length) == 0 then error("missing runs") else . end
      | if all(.runs[]; (.results | type) == "array") then . else error("missing results") end
      | if any(.runs[]; any(.invocations[]?; .executionSuccessful == false)) then error("scanner failed") else . end
      | [.runs[].results | length] | add' gosec-results.sarif)"; then
      echo '::error::GoSec evidence missing or invalid'; exit 1
    fi
    if [[ "$rc" -ne 0 && "$count" -eq 0 ]]; then
      echo "::error::GoSec execution failed (exit=$rc)"; exit 1
    fi
    emit issues "$count"
    if [[ "$count" -gt 0 ]]; then emit status findings; else emit status passed; fi
    ;;
  summary)
    passed=true
    for status in "${LINT_STATUS:-missing}" "${VET_STATUS:-missing}" "${FMT_STATUS:-missing}" "${GOSEC_STATUS:-missing}"; do
      [[ "$status" == passed ]] || passed=false
    done
    issues="${GOSEC_ISSUES:--1}"
    [[ "$issues" == 0 ]] || passed=false
    emit passed "$passed"
    emit security-issues "$issues"
    emit linting-status "${LINT_STATUS:-missing}"
    if [[ "$passed" != true ]]; then
      echo "::error::Go quality failed: lint=${LINT_STATUS:-missing}, vet=${VET_STATUS:-missing}, format=${FMT_STATUS:-missing}, gosec=${GOSEC_STATUS:-missing}, findings=$issues"
    fi
    ;;
  *) echo '::error::Unknown Go quality operation'; exit 1 ;;
esac
