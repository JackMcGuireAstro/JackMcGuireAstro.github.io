#!/bin/bash
# One CTAS cloud cycle, run by .github/workflows/ctas-cloud.yml (nothing on the Mac).
#
#   1. restore the collector's database and fixed inputs from the private state repo
#   2. start the collector (`supernova-ops api`, the same program the Mac ran) and let it
#      gather new alerts for COLLECT_MINUTES
#   3. publish a release with scripts/publish_ctas.sh while the collector keeps running
#      (the publisher freezes its own consistent snapshot of the database)
#   4. stop the collector cleanly and save the database back to the state repo
#   5. start the site deployment when a release was published
#
# Environment (set by the workflow):
#   SITE              this repository's checkout (main)
#   BACKEND           the collector's code (checkout of the private state repo)
#   STATE_TOKEN       token for the private state repo (read/write contents)
#   DEPLOY_TOKEN      this repository's workflow token (actions: write)
#   COLLECT_MINUTES   minutes of collection before publishing (default 15)
#   DRY_RUN           1 = collect and build a release but push nothing and save nothing
#   CHAIN             1 (default) = start the next cycle when this one saved its database
#   WORK              scratch folder for logs (default $RUNNER_TEMP or /tmp)
set -uo pipefail

SITE=${SITE:?SITE}
BACKEND=${BACKEND:?BACKEND}
COLLECT_MINUTES=${COLLECT_MINUTES:-15}
DRY_RUN=${DRY_RUN:-0}
WORK=${WORK:-${RUNNER_TEMP:-/tmp}/ctas-cloud}
LOGS="$WORK/logs"
DATA="$BACKEND/data"
PORT=${SOC_PORT:-8787}
mkdir -p "$LOGS"

say() { printf '%s  %s\n' "$(date -u '+%H:%M:%S')" "$*"; }
fail() { say "FAIL  $*"; stop_collector; exit 1; }

COLLECTOR_PID=""
stop_collector() {
  [ -n "$COLLECTOR_PID" ] || return 0
  kill -0 "$COLLECTOR_PID" 2>/dev/null || { COLLECTOR_PID=""; return 0; }
  say "stopping the collector"
  kill -INT "$COLLECTOR_PID" 2>/dev/null
  for _ in $(seq 1 180); do kill -0 "$COLLECTOR_PID" 2>/dev/null || break; sleep 1; done
  if kill -0 "$COLLECTOR_PID" 2>/dev/null; then
    say "collector did not stop within 3 minutes; ending it (SQLite keeps the last committed state)"
    kill -KILL "$COLLECTOR_PID" 2>/dev/null
    sleep 2
  fi
  COLLECTOR_PID=""
}
trap stop_collector EXIT

# ------------------------------------------------------------------ 1. restore
say "restoring the database from the state repository"
GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/ctas_cloud_state.py" restore "$DATA" || fail "could not restore the state"

# ------------------------------------------------------------------ 2. collect
[ -f "$BACKEND/.env" ] || say "no .env for the collector; sources that need keys will be skipped"
say "starting the collector"
( cd "$BACKEND" && exec supernova-ops api --host 127.0.0.1 --port "$PORT" ) >"$LOGS/collector.log" 2>&1 &
COLLECTOR_PID=$!
ready=0
for i in $(seq 1 600); do
  kill -0 "$COLLECTOR_PID" 2>/dev/null || { tail -40 "$LOGS/collector.log"; fail "the collector exited during start-up"; }
  if curl -fsS -m 3 "http://127.0.0.1:$PORT/api/v1/readiness" >/dev/null 2>&1; then ready=1; say "collector ready after ${i}s"; break; fi
  sleep 1
done
[ "$ready" -eq 1 ] || fail "the collector was not ready within 10 minutes"

END=$(( $(date +%s) + COLLECT_MINUTES * 60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  kill -0 "$COLLECTOR_PID" 2>/dev/null || { tail -40 "$LOGS/collector.log"; fail "the collector stopped unexpectedly"; }
  sleep 20
done
say "collected for $COLLECT_MINUTES minutes"

# ------------------------------------------------------------------ 3. publish
PUBLISH_ARGS=()
[ "$DRY_RUN" = "1" ] && PUBLISH_ARGS=(--dry-run)
say "building the release${PUBLISH_ARGS:+ (dry run)}"
# CTAS_SNAPSHOT_METHOD is left unset: "auto" already takes a full backup on Linux (no
# clonefile), and setting it would leak into the publisher's own snapshot tests.
PUBLISH_OUT=$(CTAS_SITE="$SITE" CTAS_DB="$DATA/soc.db" CTAS_LOG_DIR="$LOGS/publish" \
  DATA_BRANCH_DISPATCHER=0 bash "$SITE/scripts/publish_ctas.sh" "${PUBLISH_ARGS[@]}" 2>&1)
PUBLISH_STATUS=$?
printf '%s\n' "$PUBLISH_OUT" | tail -20
PUBLISHED=0
printf '%s\n' "$PUBLISH_OUT" | grep -q '^published ' && PUBLISHED=1

# ------------------------------------------------------------------ 4. save
stop_collector
# Per-source counts for the last day go to the job summary, so a source that quietly
# fails on every request (e.g. a rejected key) shows up on the run page. Report only.
python3 "$SITE/scripts/ctas_source_health.py" --db "$DATA/soc.db" >"$LOGS/source-health.md" 2>&1 \
  && grep -m1 '^\*\*Needs attention' "$LOGS/source-health.md" | sed 's/^/source health: /' || true
# Keep derived bookkeeping bounded (the Mac ran this daily; without it the database
# grew ~165 MB a day): newest analysis run per candidate and type, last 3 days, and
# anything a reference file cites. Source data is never touched.
python3 "$SITE/scripts/ctas_db_retention.py" daily --db "$DATA/soc.db" >"$LOGS/retention.log" 2>&1 \
  && say "trimmed superseded derived records" || say "database retention skipped this cycle (see retention.log)"
if [ "$DRY_RUN" = "1" ]; then
  say "dry run: the database is not saved and nothing was pushed"
else
  say "saving the database to the state repository"
  GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/ctas_cloud_state.py" save "$DATA" \
    || { say "FAIL  could not save the database; the previous state is kept"; exit 1; }
fi

# ------------------------------------------------------------------ 5. deploy
if [ "$PUBLISHED" -eq 1 ] && [ "$DRY_RUN" != "1" ]; then
  # A push made with the workflow token does not start other workflows by itself.
  GH_TOKEN="$DEPLOY_TOKEN" gh workflow run worldsindex-release.yml --repo "${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}" --ref main \
    && say "deployment requested" || say "could not request the deployment; the next run or a main push will deploy it"
fi
# GitHub's hourly schedule is unreliable for this repository (runs arrive hours apart),
# so each successful cycle starts the next one; the hourly schedule only restarts the
# chain if it ever breaks. A cycle that could not save its database does not chain.
if [ "${CHAIN:-1}" = "1" ] && [ "$DRY_RUN" != "1" ]; then
  GH_TOKEN="$DEPLOY_TOKEN" gh workflow run ctas-cloud.yml --repo "${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}" --ref main \
    && say "next cycle requested" || say "could not request the next cycle; the hourly schedule will start it"
fi
[ "$PUBLISH_STATUS" -eq 0 ] || { say "the publisher reported a problem (exit $PUBLISH_STATUS); see the publish log artifact"; exit "$PUBLISH_STATUS"; }
say "done"
