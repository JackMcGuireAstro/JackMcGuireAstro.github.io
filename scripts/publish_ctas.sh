#!/bin/bash
# =============================================================================
# publish_ctas.sh - mirror the local CTAS database onto the public website.
#
# The local database is the original. This makes GitHub follow it. Normally run
# by a launchd WatchPaths agent, which fires when the database changes rather
# than on a clock, so there is no polling interval to wait out.
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

# Floor between published commits, not a schedule. 0 = publish as soon as the
# data actually changes. Nothing happens at all unless the data changed.
MIN_INTERVAL="${CTAS_MIN_INTERVAL:-0}"

LOG_DIR="${CTAS_LOG_DIR:-$HOME/Library/Logs/ctas-mirror}"
LOG="$LOG_DIR/publish.log"
STAMP="$LOG_DIR/.last-publish"
LOCKDIR="$LOG_DIR/.lock.d"

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

# ------------------------------------------------------------- single run
# macOS has no flock(1), so use an atomic mkdir. A lock older than 30 minutes
# is a crashed run, not a live one, and is reclaimed.
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  if [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
    rmdir "$LOCKDIR" 2>/dev/null
    mkdir "$LOCKDIR" 2>/dev/null || { say "could not take the lock; skipping"; exit 0; }
    say "reclaimed a stale lock from a crashed run"
  else
    say "another publish is running; skipping"
    exit 0
  fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

[ -d "$SITE" ] || die "website repo not found: $SITE"
[ -f "$DB" ]   || die "CTAS database not found: $DB"
cd "$SITE"     || die "cannot enter $SITE"

# ------------------------------------------------------------- rate guard
if [ "$FORCE" -eq 0 ] && [ "$MIN_INTERVAL" -gt 0 ] && [ -f "$STAMP" ]; then
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  age=$(( $(date +%s) - last ))
  if [ "$age" -lt "$MIN_INTERVAL" ]; then
    say "last publish ${age}s ago; waiting out the ${MIN_INTERVAL}s floor"
    exit 0
  fi
fi

# ----------------------------------------------------------------- export
python3 scripts/export_ctas_snapshot.py --database "$DB" --output-dir ctas/data >>"$LOG" 2>&1 \
  || die "export failed; nothing committed"

# ------------------------------------------------------------ changed at all?
# Compare against HEAD, not the index: a previous run (or GitHub Desktop) may
# have left the file staged, and comparing against the index would then report
# "no change" for data that has never actually been published.
if git diff --quiet HEAD -- ctas/data/candidates.json 2>/dev/null; then
  say "no change in candidate data since the last commit; nothing to publish"
  git checkout -- ctas/data/status.json 2>/dev/null || true
  exit 0
fi

COUNT=$(python3 -c "import json;print(json.load(open('ctas/data/candidates.json'))['candidate_count'])" 2>/dev/null || echo "?")

if [ "$DRY" -eq 1 ]; then
  say "--dry-run: $COUNT candidates; would commit ctas/data and push to $BRANCH"
  exit 0
fi

# ------------------------------------------------------- refuse a dirty index
# Only ctas/data may be committed. If anything else is already staged, stop
# rather than sweeping work-in-progress into an automated commit.
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
