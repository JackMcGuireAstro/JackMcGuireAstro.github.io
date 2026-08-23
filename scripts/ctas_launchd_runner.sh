#!/bin/bash
# Synchronize the dedicated CTAS publisher checkout, then publish one frozen
# database snapshot. This checkout contains only public repository state.
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SITE="${CTAS_SITE:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}"
DB="${CTAS_DB:-$HOME/.codex/.chatgpt-projects/g-p-6a5d91be2e688191b7333527fcd488b3/data/soc.db}"
BRANCH="${CTAS_BRANCH:-main}"
LOG_DIR="${CTAS_LOG_DIR:-$HOME/Library/Logs/ctas-mirror}"
RUNNER_LOG="$LOG_DIR/runner.log"
RUNNER_LOCK="$LOG_DIR/.runner.lock.d"

mkdir -p "$LOG_DIR"
say() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*" >>"$RUNNER_LOG"; printf '%s\n' "$*"; }
die() { say "FAIL  $*"; exit 1; }

if ! mkdir "$RUNNER_LOCK" 2>/dev/null; then
  if [ -n "$(find "$RUNNER_LOCK" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then
    rmdir "$RUNNER_LOCK" 2>/dev/null
    mkdir "$RUNNER_LOCK" 2>/dev/null || { say "another scheduled run is active; skipping"; exit 0; }
    say "reclaimed a stale scheduled-run lock"
  else
    say "another scheduled run is active; skipping"
    exit 0
  fi
fi
trap 'rmdir "$RUNNER_LOCK" 2>/dev/null' EXIT

[ -d "$SITE/.git" ] || die "dedicated publisher checkout is missing: $SITE"
[ -f "$DB" ] || die "CTAS database is missing: $DB"
cd "$SITE" || die "cannot enter dedicated publisher checkout"

CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
[ "$CURRENT_BRANCH" = "$BRANCH" ] || die "publisher checkout must remain on $BRANCH (found ${CURRENT_BRANCH:-detached})"

# Only generated public data may be dirty in the operational checkout. Preserve
# such files in a named stash before syncing; the next export reproduces them.
NON_DATA_DIRTY=""
while IFS= read -r line; do
  path=${line:3}
  case "$path" in ctas/data/*) ;; *) NON_DATA_DIRTY="$NON_DATA_DIRTY $path" ;; esac
done < <(git status --porcelain --untracked-files=all)
[ -z "$NON_DATA_DIRTY" ] || die "unexpected non-data changes in publisher checkout:$NON_DATA_DIRTY"
if [ -n "$(git status --porcelain --untracked-files=all -- ctas/data)" ]; then
  git stash push --include-untracked -m "CTAS generated recovery $(date -u '+%Y%m%dT%H%M%SZ')" -- ctas/data >/dev/null \
    || die "could not preserve generated files before sync"
  say "preserved unfinished generated files before repository sync"
fi

git fetch --quiet origin "$BRANCH" || die "could not fetch origin/$BRANCH"
if git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
  git merge --quiet --ff-only "origin/$BRANCH" || die "could not fast-forward publisher checkout"
elif git merge-base --is-ancestor "origin/$BRANCH" HEAD 2>/dev/null; then
  say "one unpublished local CTAS data commit is retained for the publisher to amend or push"
else
  if git rebase "origin/$BRANCH" >/dev/null 2>&1; then
    say "rebased an unpublished CTAS data commit onto current public code"
  else
    git rebase --abort >/dev/null 2>&1 || true
    die "publisher checkout diverged and could not be rebased automatically; no data were published"
  fi
fi

env CTAS_SITE="$SITE" CTAS_DB="$DB" CTAS_BRANCH="$BRANCH" CTAS_LOG_DIR="$LOG_DIR" \
  /bin/bash "$SITE/scripts/publish_ctas.sh"
exit $?
