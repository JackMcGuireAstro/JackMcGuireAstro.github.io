#!/bin/bash
# Synchronize the dedicated public-site checkout before one local ExoNexus run.
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SITE="${WORLDSINDEX_SITE:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}"
SOURCE="${WORLDSINDEX_SOURCE:-$HOME/Documents/Codex/CTAS and WorldsIndex/WorldsIndex Development/work/worldsindex}"
BRANCH="${WORLDSINDEX_BRANCH:-main}"
LOG_DIR="${WORLDSINDEX_LOG_DIR:-$HOME/Library/Logs/worldsindex-mirror}"
RUNNER_LOG="$LOG_DIR/runner.log"
RUNNER_LOCK="$LOG_DIR/.runner.lock.d"

mkdir -p "$LOG_DIR"
say() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*" >>"$RUNNER_LOG"; printf '%s\n' "$*"; }
die() { say "FAIL  $*"; exit 1; }

if ! mkdir "$RUNNER_LOCK" 2>/dev/null; then
  if [ -n "$(find "$RUNNER_LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    rmdir "$RUNNER_LOCK" 2>/dev/null
    mkdir "$RUNNER_LOCK" 2>/dev/null || { say "another scheduled run is active; skipping"; exit 0; }
    say "reclaimed a stale runner lock"
  else
    say "another scheduled run is active; skipping"
    exit 0
  fi
fi
trap 'rmdir "$RUNNER_LOCK" 2>/dev/null' EXIT

[ -d "$SITE/.git" ] || die "publisher checkout is missing: $SITE"
[ -f "$SOURCE/package.json" ] || die "local ExoNexus source is unavailable: $SOURCE"
cd "$SITE" || die "cannot enter the publisher checkout"

CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
[ "$CURRENT_BRANCH" = "$BRANCH" ] || die "publisher checkout must remain on $BRANCH"

NON_DATA_DIRTY=""
while IFS= read -r line; do
  path=${line:3}
  case "$path" in worldsindex/data/*) ;; *) NON_DATA_DIRTY="$NON_DATA_DIRTY $path" ;; esac
done < <(git status --porcelain --untracked-files=all)
[ -z "$NON_DATA_DIRTY" ] || die "unexpected non-data changes in publisher checkout:$NON_DATA_DIRTY"
if [ -n "$(git status --porcelain --untracked-files=all -- worldsindex/data)" ]; then
  git stash push --include-untracked -m "WorldsIndex generated recovery $(date -u '+%Y%m%dT%H%M%SZ')" -- worldsindex/data >/dev/null \
    || die "could not preserve unfinished generated files"
  say "preserved unfinished generated data before repository sync"
fi

export GIT_TERMINAL_PROMPT=0
: "${GIT_SSH_COMMAND:=ssh -o BatchMode=yes -o ConnectTimeout=15 -o ConnectionAttempts=2}"
export GIT_SSH_COMMAND
git fetch --quiet origin "$BRANCH" || die "could not fetch origin/$BRANCH"
if git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
  git merge --quiet --ff-only "origin/$BRANCH" || die "could not fast-forward the publisher checkout"
elif git merge-base --is-ancestor "origin/$BRANCH" HEAD 2>/dev/null; then
  say "one unpublished WorldsIndex data commit remains for retry"
else
  if git rebase "origin/$BRANCH" >/dev/null 2>&1; then
    say "rebased an unpublished WorldsIndex data commit onto current public code"
  else
    git rebase --abort >/dev/null 2>&1 || true
    die "publisher checkout diverged and could not be rebased safely"
  fi
fi

# Cadence: every scheduled cycle runs the fast path, which follows the local source files and
# publishes only when the publication inputs changed and every static gate passed. Once every
# WORLDSINDEX_FULL_EVERY seconds (default one hour) — or when no full run has ever completed —
# the cycle runs the full path instead: provider monitor, promotion gates, ExoNexus test suite,
# build, and atlas regeneration, whose outputs the next fast cycle then publishes.
FULL_EVERY="${WORLDSINDEX_FULL_EVERY:-3600}"
FULL_STAMP="$LOG_DIR/.last-full-run"
MODE=fast
if [ ! -f "$FULL_STAMP" ]; then
  MODE=full
else
  LAST_FULL=$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$(cat "$FULL_STAMP")" '+%s' 2>/dev/null || date -u -d "$(cat "$FULL_STAMP")" '+%s' 2>/dev/null || echo 0)
  [ $(( $(date -u '+%s') - LAST_FULL )) -ge "$FULL_EVERY" ] && MODE=full
fi
[ "${WORLDSINDEX_FORCE_FULL:-0}" = "1" ] && MODE=full
say "cycle mode: $MODE"
env WORLDSINDEX_SITE="$SITE" WORLDSINDEX_SOURCE="$SOURCE" WORLDSINDEX_BRANCH="$BRANCH" WORLDSINDEX_LOG_DIR="$LOG_DIR" WORLDSINDEX_MODE="$MODE" \
  /bin/bash "$SITE/scripts/publish_worldsindex.sh"
