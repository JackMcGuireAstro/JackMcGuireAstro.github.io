#!/bin/bash
# One WorldsIndex cloud cycle, run by .github/workflows/worldsindex-cloud.yml (nothing on
# the Mac): restore the builder's working data from the private state repo, run the
# unchanged full publisher (provider monitor, promotion gate, ExoNexus gates, atlas,
# static release, worldsindex-data push), save the working data back, and start the
# site deployment when a release was published.
#
# Environment (set by the workflow):
#   SITE          this repository's checkout (main)
#   SOURCE        the ExoNexus builder (checkout of the private state repo, npm ci done)
#   STATE_TOKEN   token for the private state repo (read/write contents)
#   DEPLOY_TOKEN  this repository's workflow token (actions: write)
#   DRY_RUN       1 = build and validate but push nothing and save nothing
#   WORK          scratch folder for logs
#
# Resilience (RESILIENCE.md): every release check is classified (scripts/check-classes.json).
# When one blocks with the current site code (publisher exit 3) and that code differs from
# the commit that produced the currently published release, the static release is rebuilt
# ONCE with that commit's code from the same builder outputs (publish_worldsindex.sh --fast
# --force: the monitor, the ExoNexus gates and the atlas are not run again), if
# scripts/release_checks.py decide allows it; the marker then carries code_fallback and a
# "WorldsIndex published with fallback code" issue is opened or updated. The working data
# is saved exactly as before either way. Each run syncs the check-warning, fallback-active
# and release-blocked issues, and a failed or fallback run pushes a sanitized report to the
# run-reports branch.
set -uo pipefail
SITE=${SITE:?SITE}
SOURCE=${SOURCE:?SOURCE}
DRY_RUN=${DRY_RUN:-0}
WORK=${WORK:-${RUNNER_TEMP:-/tmp}/worldsindex-cloud}
LOGS="$WORK/logs"
mkdir -p "$LOGS"
say() { printf '%s  %s\n' "$(date -u '+%H:%M:%S')" "$*" | tee -a "$LOGS/runner.log"; }
# shellcheck source=code_fallback.sh
. "$SITE/scripts/code_fallback.sh"
REPO="${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}"
MAIN_COMMIT=$(git -C "$SITE" rev-parse HEAD 2>/dev/null || true)
CHECKS_MAIN="$WORK/checks-main.jsonl"
CHECKS_FALLBACK="$WORK/checks-fallback.jsonl"
DECISION="$WORK/decision.json"
FALLBACK_USED=0
RELEASE_COMMIT=""
rm -f -- "$CHECKS_MAIN" "$CHECKS_FALLBACK" "$DECISION"
# Every secret-bearing variable the WorldsIndex workflow sets (scripts/run_report.py
# SECRET_ENV_NAMES; scripts/test_run_report.py keeps the list complete).
SECRET_ENV="STATE_TOKEN DEPLOY_TOKEN GH_TOKEN PUSH_TOKEN BUILDER_ENV"

FALLBACK_SCOPE=worldsindex-publish
FALLBACK_WORKFLOW=worldsindex-cloud.yml
decide() { fallback_decide "$@"; }
ISSUES_SYNCED=0
sync_issues() {
  [ "$DRY_RUN" != "1" ] && [ "$ISSUES_SYNCED" -eq 0 ] || return 0
  ISSUES_SYNCED=1
  GH_TOKEN="$DEPLOY_TOKEN" python3 "$SITE/scripts/release_issues.py" sync --scope worldsindex-publish \
    --results "$CHECKS_MAIN" --decision "$DECISION" --repo "$REPO" --secret-env "$SECRET_ENV" \
    --secret-file "$SOURCE/.env.local" 2>&1 | sed 's/^/issues: /' || true
}
push_report() {  # push_report <failed|fallback|blocked>
  [ "$DRY_RUN" != "1" ] || return 0
  python3 "$SITE/scripts/run_report.py" build --workflow worldsindex-cloud.yml --outcome "$1" --out "$WORK/run-report.json" \
    --commit "$MAIN_COMMIT" --log "$LOGS/runner.log" --log "$LOGS/publish/publish.log" --log "$LOGS/publish-fallback/publish.log" \
    --results "$CHECKS_MAIN" --results "$CHECKS_FALLBACK" --decision "$DECISION" \
    --secret-env "$SECRET_ENV" --secret-file "$SOURCE/.env.local" \
    && python3 "$SITE/scripts/run_report.py" push --report "$WORK/run-report.json" --repo-dir "$SITE" \
    || echo "WARN  could not push the run report"
}
on_exit() {
  local status=$?
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

say "restoring WorldsIndex's working data from the state repository"
RESTORE_LOG="$LOGS/restore.log"
if STATE_RECEIPT="$WORK/state-restored.json" GH_TOKEN="$STATE_TOKEN" \
  python3 "$SITE/scripts/worldsindex_cloud_state.py" restore "$SOURCE" >"$RESTORE_LOG" 2>&1; then
  cat "$RESTORE_LOG"
else
  cat "$RESTORE_LOG"
  # An integrity check without fallback: older code must never accept an archive the
  # current safety rules refuse (2026-10-18: hard links in the state archive).
  CHECK_SECRET_ENV="$SECRET_ENV" python3 "$SITE/scripts/release_checks.py" record --scope worldsindex-publish --id worldsindex.restore \
    --exit-code 1 --output "$RESTORE_LOG" --results "$CHECKS_MAIN" >/dev/null 2>&1 || true
  decide >/dev/null
  say "FAIL  could not restore the working data"
  exit 1
fi
# Public marker of this run (which state archive it restored and when that was saved,
# whether the state key still works and when it expires),
# published beside the release as worldsindex/data/cloud-run.json for the freshness watchdog.
MARKER="$WORK/cloud-run.json"
GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/cloud_run_marker.py" --workflow worldsindex-cloud.yml \
  --receipt "$WORK/state-restored.json" --output "$MARKER" --probe-token --site-commit "$MAIN_COMMIT" || { say "WARN  could not write the cloud-run marker"; MARKER=""; }

# Reviewed identity-rule upgrades (v1 -> v2 2026-10-02, v2 -> v3 2026-10-03): the builder
# re-links exoplanet.eu rows under newer identity rules only if the result matches the
# reviewed, pinned list committed with its code; once accepted this is a no-op. A pin made
# for an older snapshot changes nothing (REIDENTIFY_PIN_STALE) and the release keeps its
# reviewed rules. Its files are inside the saved working data, so the save below keeps it.
say "checking the reviewed identity rules"
(cd "$SOURCE" && npm run --silent exoplanet-eu:reidentify -- --accept-reviewed) >"$LOGS/reidentify.log" 2>&1 \
  || { tail -20 "$LOGS/reidentify.log"; say "FAIL  the identity re-linking did not match the reviewed list; nothing published or saved"; exit 1; }
say "identity rules: $(grep -m1 -o '"outcome": "[A-Z_]*"' "$LOGS/reidentify.log" || echo 'outcome not recorded')"

ARGS=(--full)
[ "$DRY_RUN" = "1" ] && ARGS+=(--dry-run)
say "running the WorldsIndex publisher (full cycle)"
# WORLDSINDEX_RELEASE_TOKEN: the publisher replaces the VOParquet assets of the rolling
# "worldsindex-data-latest" GitHub Release with it (contents: write; never a state token).
OUT=$(WORLDSINDEX_SITE="$SITE" WORLDSINDEX_SOURCE="$SOURCE" WORLDSINDEX_LOG_DIR="$LOGS/publish" \
  WORLDSINDEX_CLOUD_RUN_MARKER="$MARKER" WORLDSINDEX_RELEASE_TOKEN="$DEPLOY_TOKEN" \
  WORLDSINDEX_CHECK_RESULTS="$CHECKS_MAIN" CHECK_SECRET_ENV="$SECRET_ENV" \
  DATA_BRANCH_DISPATCHER=0 bash "$SITE/scripts/publish_worldsindex.sh" "${ARGS[@]}" 2>&1)
STATUS=$?
printf '%s\n' "$OUT" | tail -25
printf '%s\n' "$OUT" | grep -E '^(FAIL|WARN)  ' >>"$LOGS/runner.log" || true
PUBLISHED=0
printf '%s\n' "$OUT" | grep -q '^published ' && PUBLISHED=1

# The one fallback attempt (scripts/code_fallback.sh): the static release rebuilt with the
# code that produced the currently published release, from the builder outputs this run
# already produced (--fast skips the monitor, the ExoNexus gates and the atlas).
publish_with() {  # <site> <log dir> <results> <marker>
  local args=(--fast --force)
  [ "$DRY_RUN" = "1" ] && args+=(--dry-run)
  WORLDSINDEX_SITE="$1" WORLDSINDEX_SOURCE="$SOURCE" WORLDSINDEX_LOG_DIR="$2" WORLDSINDEX_CHECK_RESULTS="$3" \
    WORLDSINDEX_CLOUD_RUN_MARKER="$4" WORLDSINDEX_RELEASE_TOKEN="$DEPLOY_TOKEN" CHECK_SECRET_ENV="$SECRET_ENV" \
    DATA_BRANCH_DISPATCHER=0 bash "$1/scripts/publish_worldsindex.sh" "${args[@]}"
}
if [ "$STATUS" -eq 3 ]; then
  # The code that produced the currently published release (the publisher did not publish,
  # so its data store still holds that release).
  RELEASE_COMMIT=$(release_commit_of "$SITE" worldsindex)
  say "the published WorldsIndex release was produced by site commit $(printf '%.10s' "${RELEASE_COMMIT:-unknown}"); this run used $(printf '%.10s' "$MAIN_COMMIT")"
fi
code_fallback_run worldsindex "$STATUS"
if [ "$FALLBACK_USED" -eq 1 ]; then
  STATUS=0
  printf '%s\n' "$FALLBACK_OUT" | grep -q '^published ' && PUBLISHED=1
fi
sync_issues

# The monitor and promotion gate update the working data even when publication is
# refused (fail-closed), so it is saved either way; a dry run saves nothing.
if [ "$DRY_RUN" = "1" ]; then
  say "dry run: working data not saved and nothing pushed"
else
  # Keep the saved state from growing without bound: promotion evidence keeps only the gzip
  # copies of the atlas (verified byte-identical first), probe artifacts older than 7 days
  # that nothing references are dropped and identical probe responses are hard-linked. Lossless for the audit trail; see
  # scripts/compact-state.ts in the builder. A failure here is reported, not fatal.
  say "compacting the working data before saving"
  if (cd "$SOURCE" && npm run --silent state:compact -- --retain-sync-days 7 --summary-line) >"$LOGS/compact.json" 2>"$LOGS/compact.err"; then
    say "compacted: $(tail -1 "$LOGS/compact.err" 2>/dev/null || echo done)"
  else
    say "WARN  compaction failed; saving the working data as it is ($(tail -1 "$LOGS/compact.err" 2>/dev/null))"
  fi
  say "saving the working data to the state repository"
  GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/worldsindex_cloud_state.py" save "$SOURCE" \
    || { say "FAIL  could not save the working data; the previous state is kept"; exit 1; }
fi

if [ "$PUBLISHED" -eq 1 ] && [ "$DRY_RUN" != "1" ]; then
  GH_TOKEN="$DEPLOY_TOKEN" gh workflow run worldsindex-release.yml --repo "${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}" --ref main \
    && say "deployment requested" || say "could not request the deployment; the next run or a main push will deploy it"
fi
[ "$STATUS" -eq 0 ] || { say "the publisher reported a problem (exit $STATUS); see the publish log artifact"; exit "$STATUS"; }
say "done"
