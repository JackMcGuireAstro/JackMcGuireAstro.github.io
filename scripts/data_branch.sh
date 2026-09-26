#!/bin/bash
# Publish generated site data as the single latest snapshot on its own branch.
#
# Every CTAS and WorldsIndex release used to be committed to `main`, so the site's
# history kept every snapshot forever (about 95 MB a day in September 2026) and the
# two publishers raced each other for `main`. Instead each publisher owns one data
# branch (`ctas-data`, `worldsindex-data`) holding exactly one commit: the latest
# release. `main` holds only code. The deploy workflow combines `main` with both
# data branches. Replacing a data branch is safe because its publisher is its only
# writer; `main` is never force-pushed.
#
# The snapshot is built in a small bare repository (the "store", kept inside the
# runtime checkout's .git directory), never in the runtime repository itself, so the
# ~90 MB of fresh objects each release writes are discarded after the push instead
# of lingering for git's two-week prune window.
#
#   data_branch.sh sync    <store> <branch>
#       Create the store if needed (remote `origin` = this checkout's origin URL),
#       fetch the branch, print its commit. Exit 4 when the branch does not exist.
#   data_branch.sh publish <store> <branch> <message> <path-list-file>
#       Build a tree from the listed working-tree paths (relative to this checkout)
#       plus this checkout's .github/workflows/data-branch-deploy.yml (so the push
#       triggers a deploy), and replace the branch with one parentless commit.
#       Prints `published <sha>` (exit 0), `unchanged <sha>` (exit 10) when the tree
#       equals the current release, or keeps the commit as `refs/pending/<branch>`
#       and exits 1 when the push fails.
#   data_branch.sh retry   <store> <branch>
#       Push a pending commit left by a failed push. Exit 0 when pushed or when
#       nothing is pending, 1 when the push fails again.
set -uo pipefail

MODE=${1:?usage: data_branch.sh sync|publish|retry <store> <branch> ...}
STORE=${2:?store}
BRANCH=${3:?branch}
DISPATCHER=.github/workflows/data-branch-deploy.yml
TRACKING="refs/remotes/origin/$BRANCH"
PENDING="refs/pending/$BRANCH"

store() { git --git-dir="$STORE" "$@"; }

ensure_store() {
  if [ ! -d "$STORE/objects" ]; then
    git init -q --bare "$STORE" || return 1
    store config gc.auto 0
    store config core.logAllRefUpdates false
  fi
  local url
  url=$(git remote get-url origin 2>/dev/null) || return 1
  if store remote get-url origin >/dev/null 2>&1; then
    store remote set-url origin "$url"
  else
    store remote add origin "$url"
  fi
}

# Keep only what the current release (and a pending commit, if any) needs.
compact() {
  store reflog expire --expire=now --all 2>/dev/null || true
  store repack -a -d -q --window=0 2>/dev/null || true
  store prune --expire=now 2>/dev/null || true
}

push_commit() {
  local commit=$1
  git --git-dir="$STORE" push --quiet origin "+$commit:refs/heads/$BRANCH" 2>&1
}

case "$MODE" in
  sync)
    ensure_store || { echo "could not prepare the data store $STORE"; exit 1; }
    if ! store ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      echo "branch $BRANCH does not exist on origin"
      exit 4
    fi
    store fetch --quiet --no-tags origin "+refs/heads/$BRANCH:$TRACKING" || { echo "could not fetch $BRANCH"; exit 1; }
    store rev-parse "$TRACKING"
    ;;

  publish)
    MESSAGE=${4:?message}
    LIST=${5:?path list file}
    ensure_store || { echo "could not prepare the data store $STORE"; exit 1; }
    INDEX=$(mktemp "${TMPDIR:-/tmp}/data-branch-index.XXXXXX") || exit 1
    rm -f "$INDEX"
    trap 'rm -f "$INDEX"' EXIT
    export GIT_INDEX_FILE="$INDEX"
    ROOT=$(pwd)
    missing=$(while IFS= read -r path; do [ -z "$path" ] || [ -f "$ROOT/$path" ] || printf '%s\n' "$path"; done <"$LIST")
    [ -z "$missing" ] || { echo "listed files are missing: $(printf '%s' "$missing" | head -5 | tr '\n' ' ')"; exit 1; }
    store --work-tree="$ROOT" read-tree --empty || exit 1
    grep -v "^$" "$LIST" | GIT_LITERAL_PATHSPECS=1 store --work-tree="$ROOT" add --force --pathspec-from-file=- || { echo "could not stage the release files"; exit 1; }
    DISPATCH_BLOB=$(git cat-file blob "HEAD:$DISPATCHER" 2>/dev/null | store hash-object -w --stdin) \
      && [ -n "$DISPATCH_BLOB" ] || { echo "HEAD has no $DISPATCHER"; exit 1; }
    store update-index --add --cacheinfo "100644,$DISPATCH_BLOB,$DISPATCHER" || exit 1
    TREE=$(store write-tree) || exit 1
    unset GIT_INDEX_FILE
    CURRENT=$(store rev-parse --verify --quiet "$TRACKING^{commit}" || true)
    if [ -n "$CURRENT" ] && [ "$(store rev-parse "$CURRENT^{tree}")" = "$TREE" ]; then
      echo "unchanged $CURRENT"
      exit 10
    fi
    # The store has no identity of its own: use the one this checkout resolves (its own
    # config or the user's global config), falling back to a neutral publisher identity.
    NAME=$(git config user.name 2>/dev/null || true)
    EMAIL=$(git config user.email 2>/dev/null || true)
    COMMIT=$(printf '%s\n' "$MESSAGE" | GIT_AUTHOR_NAME="${NAME:-Site data publisher}" GIT_AUTHOR_EMAIL="${EMAIL:-publisher@localhost}" \
      GIT_COMMITTER_NAME="${NAME:-Site data publisher}" GIT_COMMITTER_EMAIL="${EMAIL:-publisher@localhost}" \
      store commit-tree "$TREE") || { echo "could not create the release commit"; exit 1; }
    store update-ref "$PENDING" "$COMMIT"
    if OUTPUT=$(push_commit "$COMMIT"); then
      store update-ref "$TRACKING" "$COMMIT"
      store update-ref -d "$PENDING"
      compact
      echo "published $COMMIT"
      exit 0
    fi
    echo "push failed; $COMMIT kept as $PENDING: $(printf '%s' "$OUTPUT" | tr '\n' ' ' | cut -c1-240)"
    exit 1
    ;;

  retry)
    [ -d "$STORE/objects" ] || exit 0
    COMMIT=$(store rev-parse --verify --quiet "$PENDING" || true)
    [ -n "$COMMIT" ] || exit 0
    ensure_store || exit 1
    if OUTPUT=$(push_commit "$COMMIT"); then
      store update-ref "$TRACKING" "$COMMIT"
      store update-ref -d "$PENDING"
      compact
      echo "published $COMMIT (retained from a failed push)"
      exit 0
    fi
    echo "retry failed: $(printf '%s' "$OUTPUT" | tr '\n' ' ' | cut -c1-240)"
    exit 1
    ;;

  *)
    echo "unknown mode: $MODE" >&2
    exit 2
    ;;
esac
