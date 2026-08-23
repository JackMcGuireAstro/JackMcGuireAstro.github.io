#!/bin/bash
# =============================================================================
# mirror_loop.sh - keep GitHub following the local CTAS database.
#
# Diagnostic foreground fallback only. Normal operation uses the installed
# launchd service and does not require an open Terminal. To run the fallback:
#     bash ~/Documents/GitHub/JackMcGuireAstro.github.io/scripts/mirror_loop.sh
#
# Every cycle it re-exports the database and publishes if the data changed.
# Errors print here, on screen, instead of vanishing into a log file.
# Ctrl-C stops it. It survives nothing: closing the window ends it, which is
# the point - you can always see whether it is running.
# =============================================================================
set -uo pipefail

SITE="${CTAS_SITE:-$HOME/Documents/GitHub/JackMcGuireAstro.github.io}"
EVERY="${CTAS_EVERY:-120}"          # seconds between checks
export CTAS_MIN_INTERVAL=0          # publish as soon as data changes

cd "$SITE" || { echo "cannot find the website repo at $SITE"; exit 1; }

printf '\n  CTAS mirror running.  checking every %ss.  Ctrl-C to stop.\n' "$EVERY"
printf '  repo: %s\n\n' "$SITE"

trap 'printf "\n  stopped.\n\n"; exit 0' INT

n=0
while true; do
  n=$((n + 1))
  ts=$(date '+%H:%M:%S')
  out=$(bash "$SITE/scripts/publish_ctas.sh" 2>&1)
  status=$?

  # Collapse the publisher's output to its last meaningful line.
  line=$(printf '%s\n' "$out" | grep -v '^$' | tail -n 1)

  case "$out" in
    *published*)      printf '  %s  \033[32m%s\033[0m\n' "$ts" "$line" ;;
    *"no change"*)    printf '  %s  up to date\n' "$ts" ;;
    *FAIL*|*error*)   printf '  %s  \033[31m%s\033[0m\n' "$ts" "$line"
                      printf '%s\n' "$out" | sed 's/^/            /' ;;
    *)                printf '  %s  %s\n' "$ts" "$line" ;;
  esac

  sleep "$EVERY"
done
