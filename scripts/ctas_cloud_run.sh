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
# Resilience (RESILIENCE.md): every release check is classified (scripts/check-classes.json).
# When one blocks with the current site code (publisher exit 3) and that code differs from
# the commit that produced the currently published release, the release step is retried
# ONCE with that commit's code on the same frozen database, if scripts/release_checks.py
# decide allows it; the marker then carries code_fallback and a "CTAS published with
# fallback code" issue is opened or updated. Never more than one fallback attempt per run;
# the state is saved exactly as before either way. Each run syncs the check-warning,
# fallback-active and release-blocked issues, and a failed or fallback run pushes a
# sanitized report to the run-reports branch.
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

say() { printf '%s  %s\n' "$(date -u '+%H:%M:%S')" "$*" | tee -a "$LOGS/runner.log"; }
fail() { say "FAIL  $*"; stop_collector; exit 1; }
# shellcheck source=code_fallback.sh
. "$SITE/scripts/code_fallback.sh"
REPO="${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}"
MAIN_COMMIT=$(git -C "$SITE" rev-parse HEAD 2>/dev/null || true)
CHECKS_MAIN="$WORK/checks-main.jsonl"
CHECKS_FALLBACK="$WORK/checks-fallback.jsonl"
DECISION="$WORK/decision.json"
FROZEN="$WORK/frozen-main.db"
FALLBACK_USED=0
RELEASE_COMMIT=""
rm -f -- "$CHECKS_MAIN" "$CHECKS_FALLBACK" "$DECISION" "$FROZEN"
# Every secret-bearing variable the CTAS workflow sets (scripts/run_report.py
# SECRET_ENV_NAMES; scripts/test_run_report.py keeps the list complete), removed from
# everything that becomes public (issues, reports), and the collector's settings file.
SECRET_ENV="STATE_TOKEN DEPLOY_TOKEN GH_TOKEN PUSH_TOKEN BACKEND_ENV AAVSO_AID_API_TOKEN_OVERRIDE"

FALLBACK_SCOPE=ctas-publish
FALLBACK_WORKFLOW=ctas-cloud.yml
decide() { fallback_decide "$@"; }

# The issues and the run report; a failure of either is reported, never fatal.
ISSUES_SYNCED=0
sync_issues() {
  [ "$DRY_RUN" != "1" ] && [ "$ISSUES_SYNCED" -eq 0 ] || return 0
  ISSUES_SYNCED=1
  GH_TOKEN="$DEPLOY_TOKEN" CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" python3 "$SITE/scripts/release_issues.py" sync --scope ctas-publish \
    --results "$CHECKS_MAIN" --decision "$DECISION" --repo "$REPO" --secret-env "$SECRET_ENV" \
    --secret-file "$BACKEND/.env" 2>&1 | sed 's/^/issues: /' || true
}
push_report() {  # push_report <failed|fallback|blocked>
  [ "$DRY_RUN" != "1" ] || return 0
  python3 "$SITE/scripts/run_report.py" build --workflow ctas-cloud.yml --outcome "$1" --out "$WORK/run-report.json" \
    --commit "$MAIN_COMMIT" --log "$LOGS/runner.log" --log "$LOGS/publish/publish.log" --log "$LOGS/publish-fallback/publish.log" \
    --results "$CHECKS_MAIN" --results "$CHECKS_FALLBACK" --decision "$DECISION" \
    --secret-env "$SECRET_ENV" --secret-file "$BACKEND/.env" \
    && python3 "$SITE/scripts/run_report.py" push --report "$WORK/run-report.json" --repo-dir "$SITE" \
    || echo "WARN  could not push the run report"
}

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
on_exit() {
  local status=$?
  stop_collector
  rm -f -- "$FROZEN"
  if [ "$status" -ne 0 ]; then
    [ -s "$DECISION" ] || printf '{"action": "block", "reason": "run-failed", "explanation": "the run failed (exit %s) before or outside the release checks", "main_commit": "%s", "blocking": []}\n' "$status" "$MAIN_COMMIT" >"$DECISION"
    sync_issues
    if [ "$FALLBACK_USED" -eq 1 ]; then push_report fallback
    elif [ -s "$CHECKS_MAIN" ] && grep -q '"outcome": "block"' "$CHECKS_MAIN"; then push_report blocked
    else push_report failed; fi
  elif [ "$FALLBACK_USED" -eq 1 ]; then
    push_report fallback
  fi
  return "$status"
}
trap on_exit EXIT

# ------------------------------------------------------------------ 1. restore
say "restoring the database from the state repository"
RESTORE_LOG="$LOGS/restore.log"
if STATE_RECEIPT="$WORK/state-restored.json" GH_TOKEN="$STATE_TOKEN" \
  python3 "$SITE/scripts/ctas_cloud_state.py" restore "$DATA" >"$RESTORE_LOG" 2>&1; then
  cat "$RESTORE_LOG"
else
  cat "$RESTORE_LOG"
  # A restore refusal is an integrity check without fallback (older code must never accept
  # an archive the current safety rules refuse); it opens a release-blocked issue.
  CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" python3 "$SITE/scripts/release_checks.py" record --scope ctas-publish --id ctas.restore \
    --exit-code 1 --output "$RESTORE_LOG" --results "$CHECKS_MAIN" >/dev/null 2>&1 || true
  decide >/dev/null
  fail "could not restore the state"
fi
# Public marker of this run (which state copy it restored and when that was saved,
# whether the state key still works and when it expires),
# published beside the release as ctas/data/cloud-run.json for the freshness watchdog.
MARKER="$WORK/cloud-run.json"
GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/cloud_run_marker.py" --workflow ctas-cloud.yml \
  --receipt "$WORK/state-restored.json" --output "$MARKER" --probe-token --site-commit "$MAIN_COMMIT" || { say "WARN  could not write the cloud-run marker"; MARKER=""; }

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
# DATA_BRANCH_COMPACT=0: this runner's data store lives for one run, so repacking the
# whole release after the push would only delay the save and the next cycle.
# CTAS_COLLECTOR_COMMIT names the collector code in ctas/data/provenance.json;
# CTAS_RELEASE_TOKEN lets the publisher replace the VOParquet assets of the rolling
# "ctas-data-latest" GitHub Release (contents: write) named in ctas/data/bulk-downloads.json.
COLLECTOR_COMMIT=$(git -C "$BACKEND" rev-parse HEAD 2>/dev/null || true)
# CTAS_CHECK_RESULTS collects the classified check results; CTAS_KEEP_FROZEN_DB keeps the
# frozen database of a run a release check blocked, for the fallback attempt below.
PUBLISH_OUT=$(CTAS_SITE="$SITE" CTAS_DB="$DATA/soc.db" CTAS_LOG_DIR="$LOGS/publish" CTAS_CLOUD_RUN_MARKER="$MARKER" \
  CTAS_COLLECTOR_COMMIT="$COLLECTOR_COMMIT" CTAS_RELEASE_TOKEN="$DEPLOY_TOKEN" \
  CTAS_CHECK_RESULTS="$CHECKS_MAIN" CTAS_KEEP_FROZEN_DB="$FROZEN" CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" \
  DATA_BRANCH_DISPATCHER=0 DATA_BRANCH_COMPACT=0 bash "$SITE/scripts/publish_ctas.sh" "${PUBLISH_ARGS[@]}" 2>&1)
PUBLISH_STATUS=$?
printf '%s\n' "$PUBLISH_OUT" | tail -20
printf '%s\n' "$PUBLISH_OUT" | grep -E '^(FAIL|WARN)  ' >>"$LOGS/runner.log" || true
PUBLISHED=0
printf '%s\n' "$PUBLISH_OUT" | grep -q '^published ' && PUBLISHED=1

# The one fallback attempt (scripts/code_fallback.sh): the code that produced the currently
# published release, on the same frozen database.
publish_with() {  # <site> <log dir> <results> <marker>
  CTAS_SITE="$1" CTAS_DB="$DATA/soc.db" CTAS_FROZEN_DB="$FROZEN" CTAS_LOG_DIR="$2" CTAS_CHECK_RESULTS="$3" \
    CTAS_CLOUD_RUN_MARKER="$4" CTAS_COLLECTOR_COMMIT="$COLLECTOR_COMMIT" CTAS_RELEASE_TOKEN="$DEPLOY_TOKEN" \
    CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" DATA_BRANCH_DISPATCHER=0 DATA_BRANCH_COMPACT=0 \
    bash "$1/scripts/publish_ctas.sh" "${PUBLISH_ARGS[@]}"
}
fallback_ready() { [ -s "$FROZEN" ]; }
if [ "$PUBLISH_STATUS" -eq 3 ]; then
  # The code that produced the currently published release (the publisher did not publish,
  # so its data store still holds that release).
  RELEASE_COMMIT=$(release_commit_of "$SITE" ctas)
  say "the published CTAS release was produced by site commit $(printf '%.10s' "${RELEASE_COMMIT:-unknown}"); this run used $(printf '%.10s' "$MAIN_COMMIT")"
fi
code_fallback_run ctas "$PUBLISH_STATUS"
if [ "$FALLBACK_USED" -eq 1 ]; then
  PUBLISH_STATUS=0
  printf '%s\n' "$FALLBACK_OUT" | grep -q '^published ' && PUBLISHED=1
fi
rm -f -- "$FROZEN"
sync_issues

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
