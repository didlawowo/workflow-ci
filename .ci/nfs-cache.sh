#!/usr/bin/env bash
# Explicit validator for the few workflows that intentionally require runner-provided NFS storage.
# Package dependency availability is handled by Proxpi/Verdaccio/Athens, not by this helper.
set -euo pipefail

require_nfs_cache() {
  local variable="${1:?cache variable}"
  case "$variable" in UV_CACHE_DIR|GOCACHE|GOMODCACHE|NPM_CONFIG_CACHE) ;; *) return 1 ;; esac

  local cache="${!variable:-}"
  if [[ "$cache" != /* ]]; then
    echo "::error::$variable must name an absolute runner-provided NFS directory." >&2
    return 1
  fi

  # shellcheck disable=SC2016
  if ! timeout 6 bash -euo pipefail -c '
    cache="$1"
    [[ -d "$cache" ]]
    filesystem="$(findmnt -n -T "$cache" -o FSTYPE)"
    [[ "$filesystem" == nfs || "$filesystem" == nfs4 ]]
    probe="$(mktemp "$cache/.workflow-ci-write-test.XXXXXX")"
    trap "rm -f \"$probe\"" EXIT
    printf "%s\n" workflow-ci > "$probe"
    cat "$probe" >/dev/null
  ' _ "$cache"; then
    echo "::error::$variable is unavailable, unwritable or not on NFS." >&2
    return 1
  fi
  export "$variable"
}

# Compatibility alias for callers that used the generic name before the local-profile experiment.
require_runner_cache() { require_nfs_cache "$@"; }
