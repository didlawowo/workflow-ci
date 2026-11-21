#!/usr/bin/env bash
# Print the `uv sync` arguments that select the interpreter, one per line.
#
# The requested version decides, not the newest interpreter installed on the
# runner. Two project conventions keep precedence and leave uv's own discovery
# untouched: a .python-version pin, and a requires-python that excludes the
# requested version.
set -euo pipefail

requested="${1:?requested Python version}"

if uv python pin >/dev/null 2>&1; then
  echo "Project pins its Python version: python-version=$requested is not forced on uv sync." >&2
  exit 0
fi

if uv python find "$requested" 2>&1 >/dev/null |
  grep -q "incompatible with the project's Python requirement"; then
  echo "::warning::python-version $requested does not satisfy the project's requires-python; uv selects the interpreter." >&2
  exit 0
fi

printf '%s\n' --python "$requested"
