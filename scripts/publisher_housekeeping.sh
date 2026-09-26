#!/bin/bash
# Keep a dedicated publisher checkout bounded on disk, and step aside when the disk
# is nearly full. Called by ctas_launchd_runner.sh and worldsindex_launchd_runner.sh
# from inside the runtime checkout; never fatal to a publication run.
#
#   publisher_housekeeping.sh disk <bytes-this-run-needs>
#       Exit 3 (and print why) when free space minus what this run needs would fall
#       below PUBLISHER_MIN_FREE_GB (default 25 GB). The runner then skips the run,
#       so a publisher can never be the thing that fills the Mac's disk. A skipped
#       run leaves the live site on its last release, which the freshness watchdog
#       reports.
#
#   publisher_housekeeping.sh maintain <recovery-namespace> <branch> <stamp-file>
#       At most once every PUBLISHER_HOUSEKEEPING_EVERY seconds (default one day):
#       - drop refs/<namespace>-recovery/* older than PUBLISHER_RECOVERY_KEEP_DAYS
#         (default 14); every one is regenerable from the local database or source;
#       - clear the stash when every entry is an automatic "generated recovery" one;
#       - keep only the newest PUBLISHER_HISTORY_DEPTH commits of history (default
#         400, several days of data releases) as a shallow clone, expire this
#         checkout's reflogs and run `git gc --prune=now`.
#       The exporters read only HEAD and origin/main, so a shallow runtime checkout
#       loses nothing. Without this, every data release since August stays in each
#       runtime checkout forever: about 95 MB a day per checkout in September 2026.
set -uo pipefail

MODE=${1:?usage: publisher_housekeeping.sh disk|maintain ...}
GB=1073741824

case "$MODE" in
  disk)
    NEEDED=${2:-0}
    case "$NEEDED" in ''|*[!0-9]*) NEEDED=0 ;; esac
    FLOOR_GB=${PUBLISHER_MIN_FREE_GB:-25}
    FREE=$(df -Pk . 2>/dev/null | awk 'NR==2 {printf "%.0f", $4 * 1024}')
    case "$FREE" in ''|*[!0-9]*) exit 0 ;; esac   # unknown free space never blocks publication
    if [ $((FREE - NEEDED)) -lt $((FLOOR_GB * GB)) ]; then
      printf 'only %s GB free; this run needs %s GB and the publishers always leave %s GB for everything else' \
        "$((FREE / GB))" "$(( (NEEDED + GB - 1) / GB ))" "$FLOOR_GB"
      exit 3
    fi
    exit 0
    ;;
  maintain)
    NS=${2:?recovery namespace}
    BRANCH=${3:?branch}
    STAMP=${4:?stamp file}
    EVERY=${PUBLISHER_HOUSEKEEPING_EVERY:-86400}
    KEEP_DAYS=${PUBLISHER_RECOVERY_KEEP_DAYS:-14}
    DEPTH=${PUBLISHER_HISTORY_DEPTH:-100}
    NOW=$(date +%s)
    LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
    case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
    [ $((NOW - LAST)) -ge "$EVERY" ] || exit 0
    [ -d .git ] || { echo "not a git checkout"; exit 1; }

    BEFORE_KB=$(du -sk .git 2>/dev/null | cut -f1)
    CUTOFF=$((NOW - KEEP_DAYS * 86400))
    PRUNED=0
    while read -r ref when; do
      [ -n "$ref" ] || continue
      if [ "${when:-0}" -lt "$CUTOFF" ] && git update-ref -d "$ref"; then PRUNED=$((PRUNED + 1)); fi
    done < <(git for-each-ref --format='%(refname) %(committerdate:unix)' "refs/$NS-recovery/")

    STASHES=$(git stash list --format=%gs 2>/dev/null | grep -c . || true)
    FOREIGN=$(git stash list --format=%gs 2>/dev/null | grep -v -c 'generated recovery' || true)
    CLEARED=0
    if [ "${STASHES:-0}" -gt 0 ] && [ "${FOREIGN:-0}" -eq 0 ]; then
      git stash clear && CLEARED=$STASHES
    fi

    if ! git fetch --quiet --depth="$DEPTH" origin "$BRANCH" 2>/dev/null; then
      echo "housekeeping: pruned $PRUNED recovery refs, cleared $CLEARED stash entries; history trim skipped (fetch failed; retried next cycle)"
      exit 0
    fi
    git reflog expire --expire=now HEAD "refs/heads/$BRANCH" "refs/remotes/origin/$BRANCH" 2>/dev/null || true
    git gc --quiet --prune=now 2>/dev/null || { echo "housekeeping: git gc failed; retried next cycle"; exit 0; }
    AFTER_KB=$(du -sk .git 2>/dev/null | cut -f1)
    printf '%s\n' "$NOW" >"$STAMP"
    echo "housekeeping: .git $((BEFORE_KB / 1024)) MB -> $((AFTER_KB / 1024)) MB; newest $DEPTH commits kept; pruned $PRUNED recovery refs; cleared $CLEARED stash entries"
    ;;
  *)
    echo "unknown mode: $MODE" >&2
    exit 2
    ;;
esac
