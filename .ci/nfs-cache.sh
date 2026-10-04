#!/usr/bin/env bash
# Validate runner-provided NFS paths before any language tool can choose a local cache.
set -euo pipefail
require_nfs_cache() {
  local variable="$1" cache="${!1:-}"
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
    echo "::error::$variable is unavailable, unwritable or not on NFS; no local fallback." >&2
    return 1
  fi
  export "${variable?}"
}
