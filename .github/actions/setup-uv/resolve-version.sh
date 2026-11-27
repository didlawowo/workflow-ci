#!/usr/bin/env bash
# Print the uv version to install: the project's own `required-version`
# (uv.toml, or [tool.uv] of pyproject.toml) wins, which prints nothing so that
# setup-uv reads it; otherwise the version pinned by the shared CI.
# This is the single place where the shared uv version is defined.
set -euo pipefail

PINNED_UV_VERSION="0.13.7"
DIR="${1:-.}"

[[ -d "$DIR" ]] || { echo "ERREUR: dossier introuvable: $DIR" >&2; exit 2; }

declared() {
  [[ -f "$DIR/uv.toml" ]] && grep -Eq '^[[:space:]]*required-version[[:space:]]*=' "$DIR/uv.toml" && return 0
  [[ -f "$DIR/pyproject.toml" ]] && awk '
    /^[[:space:]]*\[/ { in_uv = ($0 ~ /^[[:space:]]*\[tool\.uv\][[:space:]]*(#.*)?$/) }
    in_uv && /^[[:space:]]*required-version[[:space:]]*=/ { found = 1 }
    END { exit !found }
  ' "$DIR/pyproject.toml" && return 0
  return 1
}

if declared; then
  echo "OK: required-version declarée par le projet dans $DIR, version du socle non imposée" >&2
else
  echo "OK: uv $PINNED_UV_VERSION (version du socle)" >&2
  printf '%s\n' "$PINNED_UV_VERSION"
fi
