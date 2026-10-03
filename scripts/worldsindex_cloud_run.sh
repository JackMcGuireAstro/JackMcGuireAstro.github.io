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
set -uo pipefail
SITE=${SITE:?SITE}
SOURCE=${SOURCE:?SOURCE}
DRY_RUN=${DRY_RUN:-0}
WORK=${WORK:-${RUNNER_TEMP:-/tmp}/worldsindex-cloud}
LOGS="$WORK/logs"
mkdir -p "$LOGS"
say() { printf '%s  %s\n' "$(date -u '+%H:%M:%S')" "$*"; }

say "restoring WorldsIndex's working data from the state repository"
GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/worldsindex_cloud_state.py" restore "$SOURCE" \
  || { say "FAIL  could not restore the working data"; exit 1; }

# One-time, reviewed identity-rule upgrade (2026-10-03): the builder re-links exoplanet.eu
# rows under the v2 identity rule only if the result matches the reviewed, pinned list
# committed with its code; once accepted this is a no-op. Its files are inside the saved
# working data, so the save below keeps the acceptance.
say "checking the reviewed identity rules"
(cd "$SOURCE" && npm run --silent exoplanet-eu:reidentify -- --accept-reviewed) >"$LOGS/reidentify.log" 2>&1 \
  || { tail -20 "$LOGS/reidentify.log"; say "FAIL  the identity re-linking did not match the reviewed list; nothing published or saved"; exit 1; }

ARGS=(--full)
[ "$DRY_RUN" = "1" ] && ARGS+=(--dry-run)
say "running the WorldsIndex publisher (full cycle)"
OUT=$(WORLDSINDEX_SITE="$SITE" WORLDSINDEX_SOURCE="$SOURCE" WORLDSINDEX_LOG_DIR="$LOGS/publish" \
  DATA_BRANCH_DISPATCHER=0 bash "$SITE/scripts/publish_worldsindex.sh" "${ARGS[@]}" 2>&1)
STATUS=$?
printf '%s\n' "$OUT" | tail -25
PUBLISHED=0
printf '%s\n' "$OUT" | grep -q '^published ' && PUBLISHED=1

# The monitor and promotion gate update the working data even when publication is
# refused (fail-closed), so it is saved either way; a dry run saves nothing.
if [ "$DRY_RUN" = "1" ]; then
  say "dry run: working data not saved and nothing pushed"
else
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
