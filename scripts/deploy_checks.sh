#!/bin/bash
# The checks of "Validate and deploy site", one group per workflow step, each classified
# in scripts/check-classes.json (see RESILIENCE.md).
#
#   deploy_checks.sh <group> [<tree>]
#
# groups: worldsindex-static, worldsindex-interop, qualquest, tonight, ctas-static,
#         ctas-schemas, browser, tooling, content, all
#
# A group never fails the step: every result goes to CHECK_RESULTS and the workflow's
# "Choose the site code to deploy" step (scripts/deploy_candidates.sh) decides. The runner
# and the classification are always this script's own (main's); <tree> is the checkout
# whose files are tested: main's, or a fallback candidate's worktree with the same data,
# where a test that does not exist yet is recorded as skipped (CHECK_SKIP_MISSING=1) and
# advisory checks are not run (CHECK_ONLY_BLOCKING=1).
#
# Environment: CHECK_RESULTS (required), CHECK_DIR, CHECK_HISTORY, CHECK_SKIP_MISSING,
# CHECK_ONLY_BLOCKING. The CTAS static release suite compares the certificate's bound code
# with <tree>'s files (step 12): only the code that certified the live CTAS release passes.
set -uo pipefail
GROUP=${1:?usage: deploy_checks.sh <group> [<tree>]}
TREE=${2:-.}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RELEASE_CHECKS=${RELEASE_CHECKS:-$HERE/release_checks.py}
CHECK_SCOPE=deploy
CHECK_RESULTS=${CHECK_RESULTS:?CHECK_RESULTS}
CHECK_DIR=${CHECK_DIR:-${RUNNER_TEMP:-/tmp}/deploy-checks}
CHECK_ECHO=${CHECK_ECHO:-1}
CHECK_RESULTS=$(cd "$(dirname "$CHECK_RESULTS")" && pwd)/$(basename "$CHECK_RESULTS")
mkdir -p "$CHECK_DIR"
CHECK_DIR=$(cd "$CHECK_DIR" && pwd)
# shellcheck source=release_checks.sh
. "$HERE/release_checks.sh"
cd "$TREE" || { echo "no tree at $TREE" >&2; exit 2; }

node_check() {
  # `node --check` on a .js ES module passes even when it does not parse (Node 22 module
  # detection; found 2026-10-22), so a file with top-level import/export is also checked
  # as a module from stdin.
  local file status=0
  for file in "$@"; do
    if ! node --check "$file"; then
      echo "does not parse: $file"; status=1
    elif grep -qE '^[[:space:]]*(import|export)[[:space:]{*]' "$file" && ! node --input-type=module --check <"$file"; then
      echo "does not parse as an ES module: $file"; status=1
    fi
  done
  return "$status"
}

shell_check() {
  local file status=0
  for file in "$@"; do
    bash -n "$file" || { echo "does not parse: $file"; status=1; }
  done
  return "$status"
}

credential_scan() {
  if find worldsindex ctas -type f \( -name '.env*' -o -name '*.pem' -o -name '*.key' \) -print -quit | grep -q .; then
    echo "credential-shaped file under the public tree"; return 1
  fi
  if grep -R -I -E 'ADS_API_TOKEN[[:space:]]*=|Bearer[[:space:]]+[A-Za-z0-9._-]{20,}' worldsindex ctas >/dev/null 2>&1; then
    echo "credential-shaped text under the public tree"; return 1
  fi
  echo "no credential-shaped file or text under ctas/ and worldsindex/"
}

group_worldsindex_static() {
  check worldsindex.checksums python3 "$RELEASE_CHECKS" checksums --product worldsindex --root .
  check worldsindex.test_worldsindex_static python3 scripts/test_worldsindex_static.py
  check worldsindex.front.science node scripts/test_worldsindex_science.mjs
  check worldsindex.front.photometry node scripts/test_worldsindex_photometry.mjs
  check worldsindex.front-syntax node_check worldsindex/assets/lightcurves.js worldsindex/assets/app.js \
    worldsindex/assets/science.js worldsindex/assets/live-mast.js worldsindex/assets/live-nasa.js \
    worldsindex/assets/live-sources.js worldsindex/assets/ephemeris.js worldsindex/assets/sky.js worldsindex/assets/status.js \
    worldsindex/assets/compare.js worldsindex/assets/derived.js worldsindex/assets/photometry.js
  check worldsindex.tooling-syntax node_check scripts/build_worldsindex_static.mjs scripts/worldsindex_transit_ephemerides.mjs \
    scripts/worldsindex_interop.mjs scripts/worldsindex_changelog.mjs scripts/worldsindex_provenance.mjs scripts/worldsindex_contributions.mjs scripts/worldsindex_conflicts.mjs \
    scripts/worldsindex_derived.mjs
  check worldsindex.test_worldsindex_publisher_recovery python3 scripts/test_worldsindex_publisher_recovery.py
  check worldsindex.front.live-mast node scripts/test_worldsindex_live_mast.mjs
  check worldsindex.front.live-nasa node scripts/test_worldsindex_live_nasa.mjs
  check worldsindex.front.live-sources node scripts/test_worldsindex_live_sources.mjs
  check worldsindex.front.ephemerides node scripts/test_worldsindex_ephemerides.mjs
  check worldsindex.test_worldsindex_interop_js node --test scripts/test_worldsindex_interop.mjs
  check worldsindex.test_worldsindex_front node --test scripts/test_worldsindex_front.mjs
  check worldsindex.test_worldsindex_compare node --test scripts/test_worldsindex_compare.mjs
  check worldsindex.test_worldsindex_derived node --test scripts/test_worldsindex_derived.mjs
}

group_worldsindex_interop() {
  check worldsindex.test_worldsindex_interop python3 scripts/test_worldsindex_interop.py
  check worldsindex.test_worldsindex_schemas python3 scripts/test_worldsindex_schemas.py
  check worldsindex.schemas python3 scripts/validate_worldsindex_schemas.py
}

group_qualquest() {
  check qualquest.static python3 scripts/test_qualquest_static.py
}

group_tonight() {
  check tonight.syntax node_check tonight/planner.js tonight/astro.js tools/lib/astrometry.js tools/coordinates/coordinates.js \
    tools/exposure/exposure.js tools/finder/finder.js research/barium-dwarfs/census.js
  check tonight.planner node scripts/test_tonight_planner.mjs
  check tools.coordinates node scripts/test_tools_coordinates.mjs
  check tools.exposure node scripts/test_tools_exposure.mjs
  check tools.finder node scripts/test_tools_finder.mjs
  check research.census node scripts/test_research_census.mjs
}

group_ctas_static() {
  # Checksum self-consistency of the data, with main's checker (always_block).
  check ctas.checksums python3 "$RELEASE_CHECKS" checksums --product ctas --root .
  check ctas.test_ctas_outcomes python3 scripts/test_ctas_outcomes.py
  check ctas.test_ctas_chunks python3 scripts/test_ctas_chunks.py
  check ctas.test_ctas_links python3 scripts/test_ctas_links.py
  check ctas.test_ctas_publisher_recovery python3 scripts/test_ctas_publisher_recovery.py
  check ctas.test_ctas_static python3 scripts/test_ctas_static.py
  check ctas.test_ctas_positional python3 scripts/test_ctas_positional.py
  check ctas.test_ctas_source_health python3 scripts/test_ctas_source_health.py
  check ctas.test_ctas_feed python3 scripts/test_ctas_feed.py
  check ctas.test_ctas_chunk_loader node scripts/test_ctas_chunk_loader.js
  check ctas.front.presentation node scripts/test_ctas_presentation.js
  check ctas.front.live-sources node scripts/test_ctas_live_sources.js
  check ctas.front.live-relay node scripts/test_live_relay.mjs
  check ctas.front.spectrum-ascii node scripts/test_ctas_spectrum_ascii.js
  check ctas.front.spectrum-fits node scripts/test_ctas_spectrum_fits.js
  check ctas.front.lightcurve-summary node scripts/test_ctas_lightcurve_summary.js
  check ctas.front-syntax node_check ctas/followup.js ctas/sky.js ctas/changelog.js ctas/status.js ctas/screens.js ctas/lightcurve-science.js ctas/host-association.js ctas/multimessenger.js ctas/comparison.js
  check ctas.front.followup node scripts/test_ctas_followup.mjs
  check ctas.test_ctas_interop python3 scripts/test_ctas_interop.py
  check ctas.test_ctas_changelog python3 scripts/test_ctas_changelog.py
  check ctas.front.sky node scripts/test_ctas_sky.js
  check ctas.test_ctas_screens python3 scripts/test_ctas_screens.py
  check ctas.front.screens node scripts/test_ctas_screens.js
  check ctas.test_ctas_comparison python3 scripts/test_ctas_comparison.py
  check ctas.front.comparison node scripts/test_ctas_comparison.js
  check ctas.test_ctas_lightcurve_science python3 scripts/test_ctas_lightcurve_science.py
  check ctas.front.lightcurve-science node scripts/test_ctas_lightcurve_science.js
  check ctas.test_ctas_host_association python3 scripts/test_ctas_host_association.py
  check ctas.front.host-association node scripts/test_ctas_host_association.js
  check ctas.test_ctas_multimessenger python3 scripts/test_ctas_multimessenger.py
  check ctas.front.multimessenger node scripts/test_ctas_multimessenger.js
}

group_ctas_schemas() {
  check ctas.schemas python3 scripts/validate_ctas_schemas.py
}

group_browser() {
  # The workflow step runs with continue-on-error (a hanging advisory browser suite must not
  # block through the step's time limit), so the one integrity check of this group runs
  # first and under its own limit: a hang is a recorded failure, never a missing result.
  check ctas.test_ctas_astro_evidence timeout -k 10 300 python3 scripts/test_ctas_astro_evidence.py
  check ctas.test_ctas_browser python3 scripts/test_ctas_browser.py
  check browser.tonight python3 scripts/test_tonight_browser.py
  check browser.tools python3 scripts/test_tools_browser.py
  check browser.research python3 scripts/test_research_browser.py
  check browser.worldsindex_lightcurves python3 scripts/test_worldsindex_lightcurves_browser.py
  check browser.worldsindex_live_sources python3 scripts/test_worldsindex_live_sources_browser.py
  check browser.worldsindex_sky python3 scripts/test_worldsindex_sky_browser.py
  check browser.worldsindex_compare python3 scripts/test_worldsindex_compare_browser.py
}

group_tooling() {
  local name
  for name in watchdog_freshness cloud_run_marker publisher_housekeeping ctas_db_retention data_branch \
              ctas_cloud_state worldsindex_cloud_state release_checks run_report release_issues release_commit; do
    check "ops.$name" python3 "scripts/test_$name.py"
  done
  check ops.shell-syntax shell_check scripts/worldsindex_cloud_run.sh scripts/ctas_cloud_run.sh scripts/deploy_checks.sh \
    scripts/release_checks.sh scripts/code_fallback.sh scripts/deploy_candidates.sh
}

group_content() {
  check site.whitespace git diff --check
  check site.credential-scan credential_scan
}

case "$GROUP" in
  worldsindex-static) group_worldsindex_static ;;
  worldsindex-interop) group_worldsindex_interop ;;
  qualquest) group_qualquest ;;
  tonight) group_tonight ;;
  ctas-static) group_ctas_static ;;
  ctas-schemas) group_ctas_schemas ;;
  browser) group_browser ;;
  tooling) group_tooling ;;
  content) group_content ;;
  all)
    group_worldsindex_static; group_worldsindex_interop; group_qualquest; group_tonight
    group_ctas_static; group_ctas_schemas; group_browser; group_tooling; group_content ;;
  *) echo "unknown group: $GROUP" >&2; exit 2 ;;
esac
exit 0
