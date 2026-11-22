#!/usr/bin/env bash
# Copy the central implementation the runner already loaded into the workspace.
#
# A workflow of this repository used to fetch its own repository again to read
# its scripts. The runner has already downloaded the whole repository, at the
# commit of the called workflow, to load this action through the `$/`
# self-reference: the scripts are copied from there, without any network
# access and without requiring the repository to be readable by the caller.
#
# The copy is refused unless the runner loaded this action from the repository
# and the commit of the called workflow, so a job never runs central scripts of
# another version than the workflow that drives it.
set -euo pipefail

ACTION_DIRECTORY=".github/actions/central-implementation"

fail() {
  echo "::error::Central implementation: $*" >&2
  exit 1
}

action_path="${GITHUB_ACTION_PATH:?GITHUB_ACTION_PATH is required}"
workspace="${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
output="${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
repository="${CENTRAL_WORKFLOW_REPOSITORY:-}"
sha="${CENTRAL_WORKFLOW_SHA:-}"
destination="${CENTRAL_DESTINATION:-}"

[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] ||
  fail "invalid workflow repository '$repository'."
[[ "$sha" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] ||
  fail "the workflow reference '$sha' is not a full commit SHA."

# The runner stores an action loaded by reference under
# <actions root>/<owner>/<repository>/<reference>/<path of the action>.
action_path="${action_path%/}"
[[ "$action_path" == */"$ACTION_DIRECTORY" ]] ||
  fail "unexpected action location '$action_path'."
loaded_root="${action_path%/"$ACTION_DIRECTORY"}"
loaded_reference="${loaded_root##*/}"
loaded_repository="${loaded_root%/*}"
loaded_repository="${loaded_repository##*/}"
loaded_owner="${loaded_root%/*/*}"
loaded_owner="${loaded_owner##*/}"

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

if [[ "$loaded_reference" != "$sha" ]] ||
  [[ "$(lower "$loaded_owner/$loaded_repository")" != "$(lower "$repository")" ]]; then
  fail "the runner loaded '$loaded_owner/$loaded_repository@$loaded_reference'," \
    "the called workflow is '$repository@$sha'. Reference this action with" \
    "the \`\$/\` self-reference only."
fi

case "$destination" in
  ""|/*|.|..|./*|../*|*/|*/.|*/..|*/./*|*/../*|*//*)
    fail "the destination must be a plain path relative to the workspace."
    ;;
esac

source_root="$(cd "$loaded_root" && pwd -P)"
workspace_root="$(cd "$workspace" && pwd -P)"
# A checkout of the job is not what the runner loaded for the called workflow.
case "$source_root" in
  "$workspace_root"|"$workspace_root"/*)
    fail "the implementation must not be read from the workspace."
    ;;
esac

inside_workspace() {
  local resolved
  resolved="$(cd "$1" && pwd -P)"
  case "$resolved" in
    "$workspace_root"|"$workspace_root"/*) return 0 ;;
  esac
  return 1
}

target="$workspace/$destination"
parent="$(dirname "$target")"
# The copy must land inside the workspace even when a parent is a symlink:
# checked on the closest existing parent before anything is created.
existing="$parent"
while [[ ! -d "$existing" ]]; do
  existing="$(dirname "$existing")"
done
inside_workspace "$existing" || fail "the destination escapes the workspace."
mkdir -p "$parent"

rm -rf "$target"
mkdir "$target"
cp -R "$source_root/." "$target/"
diff -rq "$source_root" "$target" >/dev/null ||
  fail "the copy differs from the implementation loaded by the runner."

echo "Central implementation $repository@$sha copied to $destination"
echo "source=$source_root" >> "$output"
