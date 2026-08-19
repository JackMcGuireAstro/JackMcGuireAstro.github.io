#!/bin/bash
# =============================================================================
# publish_ctas.sh - mirror the local CTAS database onto the public website.
#
# The local database is the original. This makes GitHub follow it. Normally
# run by a launchd WatchPaths agent, which fires when the database changes
# rather than on a clock, so there is no polling interval to wait out.
#
#   ./scripts/publish_ctas.sh              export; commit and push if changed
#   ./scripts/publish_ctas.sh --dry-run    export and report; push nothing
#   ./scripts/publish_ctas.sh --force      ignore the minimum-interval guard
#
# It commits ONLY ctas/data/. Any other work in progress is left untouched.
# It never force-pushes and never rewrites history.
# =============================================================================
set -uo pipefail

SITE="${CTAS_SITE:-$HOME/Documents/GitHub/JackMcGuireAstro.github.io}"
DB="${CTAS_DB:-$HOME/.codex/.chatgpt-projects/g-p-6a5d91be2e688191b7333527fcd488b3/data/soc.db}"
BRANCH="${CTAS_BRANCH:-main}"

# The database is written continuously while CTAS ingests, so the watcher can
# fire often. This is the floor between published commits, not a schedule:
# nothing happens at all unless the data actually changed.
MIN_INTERVAL="${CTAS_MIN_INTERVAL:-900}"      # seconds; 0 disables the guard

LOG_DIR="${CTAS_LOG_DIR:-$HOME/Library/Logs/ctas-mirror}"
LOG="$LOG_DIR/publish.log"
STAMP="$LOG_DIR/.last-publish"
LOCK="$LOG_DIR/.lock"

DRY=0; FORCE=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --force)   FORCE=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

mkdir -p "$LOG_DIR"
[ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 1048576 ] && mv -f "$LOG" "$LOG.1" 2>/dev/null

say() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*" >>"$LOG"; printf '%s\n' "$*"; }
die() { say "FAIL  $*"; exit 1; }

export GIT_TERMINAL_PROMPT=0        # never hang waiting for a credential

# Only one publish at a time; the watcher can fire while a run is in flight.
exec 9>"$LOCK"
flock -n 9 2>/dev/null || { say "another publish is running; skipping"; exit 0; }

[ -d "$SITE" ] || die "website repo not found: $SITE"
[ -f "$DB" ]   || die "CTAS database not found: $DB"
cd "$SITE"     || die "cannot enter $SITE"

# ------------------------------------------------------- rate guard
if [ "$FORCE" -eq 0 ] && [ "$MIN_INTERVAL" -gt 0 ] && [ -f "$STAMP" ]; then
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  now=$(date +%s)
  age=$((now - last))
  if [ "$age" -lt "$MIN_INTERVAL" ]; then
    say "last publish ${age}s ago; waiting out the ${MIN_INTERVAL}s floor"
    exit 0
  fi
fi

# ----------------------------------------------------------- export
python3 scripts/export_ctas_snapshot.py --database "$DB" --output-dir ctas/data >>"$LOG" 2>&1 \
  || die "export failed; nothing committed"

# ------------------------------------------------------ changed at all?
# status.json carries a fresh timestamp every run, so the candidate data is
# what decides whether there is anything worth publishing.
if git diff --quiet -- ctas/data/candidates.json 2>/dev/null; then
  say "no change in candidate data; nothing to publish"
  git checkout -- ctas/data/status.json 2>/dev/null || true
  exit 0
fi

COUNT=$(python3 -c "import json;print(json.load(open('ctas/data/candidates.json'))['candidate_count'])" 2>/dev/null || echo "?")

if [ "$DRY" -eq 1 ]; then
  say "--dry-run: $COUNT candidates; would commit ctas/data and push to $BRANCH"
  exit 0
fi

# ------------------------------------------------- refuse a dirty index
# Only ctas/data may be committed. If anything else is already staged, stop
# rather than sweeping someone's work-in-progress into an automated commit.
STAGED_OTHER=$(git diff --cached --name-only | grep -v '^ctas/data/' || true)
if [ -n "$STAGED_OTHER" ]; then
  die "other files are already staged; refusing to commit. Staged: $(echo "$STAGED_OTHER" | tr '\n' ' ')"
fi

git add -- ctas/data || die "git add failed"
git commit -q -m "CTAS data: $COUNT candidates ($(date -u '+%Y-%m-%d %H:%M UTC'))" \
  || die "git commit failed"
SHA=$(git rev-parse --short HEAD)

if git push -q origin "$BRANCH" 2>>"$LOG"; then
  date +%s >"$STAMP"
  say "published $SHA  ($COUNT candidates)"
else
  die "push failed; commit $SHA stays local, nothing forced"
fi
