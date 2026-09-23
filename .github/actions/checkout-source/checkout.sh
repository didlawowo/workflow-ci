#!/usr/bin/env bash
# Full-history, exact-commit checkout. Never invokes git submodule or saves credentials.
set -euo pipefail
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${CHECKOUT_REPOSITORY:?repository is required}"
: "${CHECKOUT_REF:?an exact commit SHA is required}"
[[ "$CHECKOUT_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo '::error::Invalid repository'; exit 1; }
[[ "$CHECKOUT_REF" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]] || { echo '::error::Checkout requires an exact commit SHA'; exit 1; }
[[ "${CHECKOUT_PERSIST_CREDENTIALS:-false}" == false && "${CHECKOUT_FETCH_DEPTH:-0}" == 0 ]] || { echo '::error::Only credential-free, full-history checkout is supported'; exit 1; }
[[ "${CHECKOUT_REPOSITORY%%/*}" != . && "${CHECKOUT_REPOSITORY%%/*}" != .. && "${CHECKOUT_REPOSITORY##*/}" != . && "${CHECKOUT_REPOSITORY##*/}" != .. ]] || { echo '::error::Invalid repository segments'; exit 1; }
mkdir -p "$GITHUB_WORKSPACE" "${RUNNER_TEMP:-/tmp}"
workspace="$(cd "$GITHUB_WORKSPACE" && pwd -P)"
[[ "$workspace" != / ]] || { echo '::error::Refusing filesystem root as workspace'; exit 1; }
tmp="$(mktemp -d "${RUNNER_TEMP:-/tmp}/checkout-source.XXXXXX")"
trap 'rm -rf -- "$tmp"' EXIT
[[ "$tmp" != "$workspace/"* ]] || { echo '::error::Runner temporary directory must be outside the checkout'; exit 1; }
# Isolate Git configuration from a previous job or a PR-controlled worktree.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
mkdir "$tmp/repository"
git -C "$tmp/repository" init -q
git -C "$tmp/repository" config core.hooksPath /dev/null
git -C "$tmp/repository" config submodule.recurse false
url="${GITHUB_SERVER_URL:-https://github.com}/${CHECKOUT_REPOSITORY}.git"
git -C "$tmp/repository" remote add origin "$url"
cat > "$tmp/askpass" <<'ASKPASS'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' x-access-token ;;
  *Password*) printf '%s\n' "${CHECKOUT_TOKEN:-}" ;;
  *) exit 1 ;;
esac
ASKPASS
chmod 700 "$tmp/askpass"
FETCH_REFS=("$CHECKOUT_REF")
for related in "${CHECKOUT_BASE_REF:-}" "${CHECKOUT_HEAD_REF:-}"; do
  [[ -z "$related" || "$related" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]] || {
    echo '::error::Related checkout refs must be exact commit SHAs'
    exit 1
  }
  if [[ -n "$related" && "$related" != "$CHECKOUT_REF" ]]; then
    FETCH_REFS+=("$related")
  fi
done
# Fetch the exact execution commit plus the PR event base/head in one operation.
# This preserves the graph needed by diff and secret-scanning gates without
# downloading unrelated branches or creating mutable refs in the checkout.
GIT_ASKPASS="$tmp/askpass" GIT_TERMINAL_PROMPT=0 \
  git -C "$tmp/repository" -c credential.helper= fetch --no-tags --no-recurse-submodules origin "${FETCH_REFS[@]}"
git -C "$tmp/repository" -c advice.detachedHead=false checkout -q --detach "$CHECKOUT_REF"
actual="$(git -C "$tmp/repository" rev-parse HEAD)"
[[ "$actual" == "${CHECKOUT_REF,,}" ]] || { echo '::error::Checkout SHA mismatch'; exit 1; }
unset CHECKOUT_TOKEN
# Only replace the disposable Actions workspace after a successful verified fetch.
find "$workspace" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "$tmp/repository/." "$workspace/"
while IFS= read -r -d '' entry; do
  if [[ "$entry" == 160000\ * ]]; then
    path="${entry#*$'\t'}"
    printf '::warning::Gitlink retained without submodule traversal: %q. Remove stale gitlinks in a dedicated migration PR.\n' "$path"
  fi
done < <(git -C "$workspace" ls-files --stage -z)
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then printf 'sha=%s\n' "$actual" >> "$GITHUB_OUTPUT"; fi
printf 'Checked out exact commit %s without stored credentials or submodule traversal.\n' "$actual"
