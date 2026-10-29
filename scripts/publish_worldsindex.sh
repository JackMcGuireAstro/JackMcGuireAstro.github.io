#!/bin/bash
# Build a fail-closed WorldsIndex static release from the local ExoNexus source
# and publish only the explicit public data allowlist.
set -uo pipefail

SITE="${WORLDSINDEX_SITE:-$HOME/Library/Application Support/WorldsIndexPublisher/site}"
SOURCE="${WORLDSINDEX_SOURCE:-$HOME/Documents/Codex/CTAS and WorldsIndex/WorldsIndex Development/work/worldsindex}"
BRANCH="${WORLDSINDEX_BRANCH:-main}"
LOG_DIR="${WORLDSINDEX_LOG_DIR:-$HOME/Library/Logs/worldsindex-mirror}"
LOG="$LOG_DIR/publish.log"
LOCKDIR="$LOG_DIR/.publish.lock.d"
DRY=0
FORCE=0
# MODE=fast: follow the local source files — fingerprint the publication inputs, rebuild the
#            static release only when they changed, run the static gates, commit, push. No
#            provider traffic and no ExoNexus test suite; runs every cycle (CTAS parity).
# MODE=full: the fast path preceded by the provider monitor, the Exoplanet.eu promotion gate,
#            typecheck/tests/lint/build, and atlas regeneration; runs every WORLDSINDEX_FULL_EVERY
#            seconds (default one hour) or on demand.
MODE="${WORLDSINDEX_MODE:-full}"

for argument in "$@"; do
  case "$argument" in
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    --fast) MODE=fast ;;
    --full) MODE=full ;;
    *) printf 'unknown option: %s\n' "$argument" >&2; exit 2 ;;
  esac
done
case "$MODE" in fast|full) ;; *) printf 'WORLDSINDEX_MODE must be fast or full\n' >&2; exit 2 ;; esac

PUBLIC_FILES=(
  worldsindex/data/manifest.json
  worldsindex/data/catalog-index.json.gz
  worldsindex/data/registry.json.gz
  worldsindex/data/sky-detections.json.gz
  worldsindex/data/source-monitor.json
  worldsindex/data/transit-ephemerides.json.gz
  worldsindex/data/source-coverage.json
  # Framework v2 Track 1: IVOA exports, bulk-asset index, provenance, changelog and feeds
  # (scripts/worldsindex_interop.mjs, worldsindex_changelog.mjs, worldsindex_provenance.mjs).
  worldsindex/data/exports/index.json
  worldsindex/data/bulk-downloads.json
  worldsindex/data/provenance.json
  worldsindex/data/changelog.json
  worldsindex/data/changelog.md
  worldsindex/data/feed.json
  worldsindex/data/feed.xml
  # Framework v2 Track 2a: conflict sets (256 shards below), their summary and index, and the
  # controversial / retracted / excluded list (scripts/worldsindex_conflicts.mjs). Optional outputs:
  # an allowlisted path the build did not write is simply not committed.
  worldsindex/data/conflict-summary.json
  worldsindex/data/conflict-index.json.gz
  worldsindex/data/controversial-retracted-excluded.json.gz
  # Framework v2 Track 3b: calculated quantities (256 shards below), their summary, the population
  # plot tables and plots, and the ephemeris staleness index (scripts/worldsindex_derived.mjs).
  worldsindex/data/derived-summary.json
  worldsindex/data/populations.json
  worldsindex/data/populations/mass-period.svg
  worldsindex/data/populations/radius-period.svg
  worldsindex/data/populations/mass-radius.svg
  worldsindex/data/populations/discovery-method.svg
  worldsindex/data/populations/discovery-year.svg
  worldsindex/data/staleness.json
  # Framework v2 Track 4: the precomputed 30-night transit list for WIRO and Red Buttes.
  worldsindex/data/observing-windows.json
)
for export_table in objects measurements transit_ephemerides source_coverage identity_states references conflicts selections; do
  PUBLIC_FILES+=("worldsindex/data/exports/$export_table.ecsv.gz" "worldsindex/data/exports/$export_table.vot.gz")
done
for bucket_index in {0..255}; do
  printf -v bucket '%02x' "$bucket_index"
  PUBLIC_FILES+=("worldsindex/data/details/$bucket.json.gz" "worldsindex/data/conflicts/$bucket.json.gz" "worldsindex/data/derived/$bucket.json.gz")
done

mkdir -p "$LOG_DIR"
[ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 2097152 ] && mv -f "$LOG" "$LOG.1" 2>/dev/null
say() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*" >>"$LOG"; printf '%s\n' "$*"; }
# Every release-time check is classified in scripts/check-classes.json (RESILIENCE.md): an
# always_block failure, or a promoted integrity failure, blocks; an advisory failure (or a
# new check still in watch mode) is a WARN line and the release continues. A blocking check
# exits 3, "blocked by a release check", so the cloud runner can rebuild the static release
# once with the code that produced the currently published release; every other failure
# exits 1 as before. ACTIVE_CHECK names the check a `die` in a
# block of inline validation belongs to.
ACTIVE_CHECK=""
die() {
  say "FAIL  $*"
  if [ -n "$ACTIVE_CHECK" ]; then
    record_check "$ACTIVE_CHECK" 1 "" --message "$*" >>"$LOG" 2>&1
    exit 3
  fi
  exit 1
}
block() { say "FAIL  $*"; exit 3; }

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  if [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    rmdir "$LOCKDIR" 2>/dev/null
    mkdir "$LOCKDIR" 2>/dev/null || { say "another publisher run is active; skipping"; exit 0; }
    say "reclaimed a stale publisher lock"
  else
    say "another publisher run is active; skipping"
    exit 0
  fi
fi
# Return every generated file under worldsindex/data to HEAD: tracked
# modifications, staged changes, deletions, and untracked new artifacts. Always
# address the directory, never the expanded PUBLIC_FILES list: `git restore -- a b c`
# restores nothing at all when any listed path is absent from HEAD (a new shard or
# artifact), which left the CTAS publisher's checkout dirty after a refused release
# in September 2026.
discard_generated_files() {
  git -C "$SITE" restore --source=HEAD --staged --worktree -- worldsindex/data 2>/dev/null || true
  git -C "$SITE" clean -fdq -- worldsindex/data 2>/dev/null || true
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ -d "$SITE/.git" ]; then
    discard_generated_files
  fi
  # The VOParquet assets live outside the Pages tree only until they are uploaded (or the
  # run ends); nothing of theirs is kept between runs unless the caller named the folder.
  if [ -n "${BULK_DIR:-}" ] && [ -z "${WORLDSINDEX_RELEASE_ASSETS_DIR:-}" ]; then
    rm -f -- "$BULK_DIR"/*.parquet 2>/dev/null
    rmdir "$BULK_DIR" 2>/dev/null
  fi
  rmdir "$LOCKDIR" 2>/dev/null
  return "$status"
}
trap cleanup EXIT
BULK_DIR=""

[ -d "$SITE/.git" ] || die "dedicated website checkout is missing: $SITE"
[ -f "$SOURCE/package.json" ] || die "ExoNexus source project is missing: $SOURCE"
command -v node >/dev/null || die "node is unavailable"
if [ "$MODE" = "full" ]; then
  [ -x "$SOURCE/scripts/run-source-monitor-noninteractive.sh" ] || die "source-monitor wrapper is missing or not executable"
  command -v npm >/dev/null || die "npm is unavailable"
fi
command -v python3 >/dev/null || die "python3 is unavailable"

# ---- Classified checks ------------------------------------------------------------------
CHECK_DIR="$LOG_DIR/checks"
rm -rf -- "$CHECK_DIR"
mkdir -p "$CHECK_DIR" || die "could not prepare $CHECK_DIR"
CHECK_SCOPE=worldsindex-publish
CHECK_RESULTS="${WORLDSINDEX_CHECK_RESULTS:-$CHECK_DIR/results.jsonl}"
: >"$CHECK_RESULTS" || die "could not write $CHECK_RESULTS"
CHECK_LOG="$LOG"
CHECK_HISTORY=""
CHECK_TREE="$SITE"
RELEASE_CHECKS="$SITE/scripts/release_checks.py"
# shellcheck source=release_checks.sh
. "$SITE/scripts/release_checks.sh" || die "could not load scripts/release_checks.sh"
in_site() { (cd "$SITE" && "$@"); }
in_source() { (cd "$SOURCE" && "$@"); }
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

export GIT_TERMINAL_PROMPT=0
: "${GIT_SSH_COMMAND:=ssh -o BatchMode=yes -o ConnectTimeout=15 -o ConnectionAttempts=2 -o ServerAliveInterval=10 -o ServerAliveCountMax=2}"
export GIT_SSH_COMMAND

# ---- Publication-input fingerprint --------------------------------------------------------
# The static release is a pure function of these local files plus the site's builder and
# assets. If none changed since the last publication and no full run is due, there is nothing
# to publish; the loop exits quietly within a second.
INPUT_STAMP="$LOG_DIR/.published-inputs.sha256"
input_fingerprint() {
  {
    for file in "$SOURCE/public/data/lightcurves/index.json" "$SOURCE/public/data/sky-detections.json.gz" "$SOURCE/public/data/sync/latest.json" \
                "$SOURCE/public/data/conflict-summary.json" "$SITE/scripts/worldsindex_conflicts.mjs" "$SITE/scripts/worldsindex_derived.mjs" "$SITE/scripts/worldsindex_observing.mjs" "$SITE/worldsindex/assets/observe.js" "$SITE/worldsindex/assets/derived.js" "$SITE/worldsindex/assets/photometry.js" "$SITE/worldsindex/assets/compare.js" "$SITE/worldsindex/assets/status.js" "$SITE/worldsindex/status.html" \
                "$SOURCE/data/snapshots/exoplanet-eu/ACTIVE.json" "$SOURCE/data/atlas/release-contract.json" \
                "$SOURCE/outputs/promotion/exoplanet-eu/latest.json" \
                "$SITE/scripts/build_worldsindex_static.mjs" "$SITE/scripts/worldsindex_transit_ephemerides.mjs" "$SITE/worldsindex/assets/ephemeris.js" \
                "$SITE/scripts/worldsindex_interop.mjs" "$SITE/scripts/worldsindex_changelog.mjs" "$SITE/scripts/worldsindex_provenance.mjs" "$SITE/scripts/worldsindex_parquet.py" "$SITE/worldsindex/openapi.yaml" \
                "$SITE/worldsindex/index.html" "$SITE/worldsindex/assets/app.js" \
                "$SITE/worldsindex/assets/lightcurves.js" "$SITE/worldsindex/assets/photometry.js" "$SITE/worldsindex/assets/science.js" "$SITE/worldsindex/assets/app.css"; do
      [ -f "$file" ] && shasum -a 256 "$file" 2>/dev/null || sha256sum "$file" 2>/dev/null || printf 'missing  %s\n' "$file"
    done
    ( cd "$SOURCE" && find data/snapshots -maxdepth 2 -name manifest.json -print0 2>/dev/null | sort -z | xargs -0 shasum -a 256 2>/dev/null || true )
  } | shasum -a 256 2>/dev/null | cut -d' ' -f1 || true
}
if [ "$MODE" = "fast" ]; then
  CURRENT_INPUTS=$(input_fingerprint)
  [ -n "$CURRENT_INPUTS" ] || die "could not fingerprint the publication inputs"
  LAST_INPUTS=$(cat "$INPUT_STAMP" 2>/dev/null || true)
  if [ "$CURRENT_INPUTS" = "$LAST_INPUTS" ] && [ "$FORCE" -eq 0 ]; then
    printf 'no change: publication inputs unchanged since the last publication (%s)\n' "${CURRENT_INPUTS:0:12}"
    exit 0
  fi
  say "fast path: publication inputs changed (${LAST_INPUTS:0:12} → ${CURRENT_INPUTS:0:12}); rebuilding the static release from the local source files"
  MONITOR_STATE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state","UNKNOWN"))' "$SOURCE/outputs/sync/latest-attempt.json" 2>/dev/null || echo "UNKNOWN")
  CHANGED_SOURCES=""
fi
# ----------------------------------------------------------------------------------------

if [ "$MODE" = "full" ]; then
ACTIVE_CHECK=worldsindex.monitor
say "checking all declared provider monitors locally"
MONITOR_EXIT=0
(cd "$SOURCE" && ./scripts/run-source-monitor-noninteractive.sh) >>"$LOG" 2>&1 || MONITOR_EXIT=$?
[ "$MONITOR_EXIT" -eq 0 ] || [ "$MONITOR_EXIT" -eq 2 ] \
  || die "provider monitor failed before producing a typed receipt; public last-good release remains unchanged"

MONITOR_STATE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state","UNKNOWN"))' "$SOURCE/outputs/sync/latest-attempt.json" 2>/dev/null || echo "UNREADABLE")
case "$MONITOR_STATE" in
  VALIDATED_UNCHANGED|VALIDATED_CHANGED|QUARANTINED) ;;
  *) die "monitor state is $MONITOR_STATE; refusing publication" ;;
esac
record_check worldsindex.monitor 0 "" >/dev/null
ACTIVE_CHECK=""

CHANGED_SOURCES=$(python3 -c '
import json,sys
doc=json.load(open(sys.argv[1]))
print(",".join(sorted(str(row.get("sourceId")) for row in doc.get("observations",[]) if row.get("changed") is True)))
' "$SOURCE/outputs/sync/latest-attempt.json" 2>/dev/null || true)
if [ "$MONITOR_STATE" = "VALIDATED_CHANGED" ]; then
  say "upstream change markers detected: ${CHANGED_SOURCES:-unspecified}; each source's promotion gate decides separately"
fi

# ---- Source-specific promotion gates ------------------------------------------------------
# Exoplanet.eu is the only source with an automatic gate so far. The gate retrieves, diffs row
# by row, proposes and bounds a new release contract, rebuilds strictly, replays the database
# from empty, activates, and verifies — or leaves everything exactly as it was. Its exit code is
# informational here: a withheld, rejected, or failed promotion is a valid outcome that keeps
# the previous release active, and the outcome is recorded for the public status file.
# A failed activation must retry even when the provider-monitor fingerprint is
# unchanged: observed bytes and successfully activated bytes are different states.
PREVIOUS_PROMOTION=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("outcome",""))' "$SOURCE/outputs/promotion/exoplanet-eu/latest.json" 2>/dev/null || true)
case "$PREVIOUS_PROMOTION" in ACTIVATION_REVERSED|RETRIEVAL_FAILED) CHANGED_SOURCES="$CHANGED_SOURCES,exoplanet-eu" ;; esac
case ",$CHANGED_SOURCES," in
  *,exoplanet-eu,*)
    say "running the Exoplanet.eu promotion gate"
    PROMOTE_EXIT=0
    (cd "$SOURCE" && npm run exoplanet-eu:promote) >>"$LOG" 2>&1 || PROMOTE_EXIT=$?
    PROMOTE_OUTCOME=$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("outcome","UNKNOWN")+": "+d.get("detail",""))' "$SOURCE/outputs/promotion/exoplanet-eu/latest.json" 2>/dev/null || echo "UNRECORDED")
    say "Exoplanet.eu promotion gate exit $PROMOTE_EXIT — $PROMOTE_OUTCOME"
    ;;
esac
# ----------------------------------------------------------------------------------------
if [ "$MONITOR_STATE" = "QUARANTINED" ]; then
  say "one or more providers failed; publishing the typed failure receipt while retaining every catalog measurement from the last-good reconciled snapshots"
fi

# ---- Sustained-quarantine alert ---------------------------------------------------------
# An honest status file nobody reads is not an alert. If the attempt is quarantined and the
# last validated run is older than one scheduled interval (six hours), raise a macOS
# notification and leave an alert file beside the logs; clear the file once a run validates.
ALERT_FILE="$LOG_DIR/QUARANTINE_ALERT.txt"
if [ "$MONITOR_STATE" = "QUARANTINED" ]; then
  QUARANTINE_DETAIL=$(python3 - "$SOURCE/outputs/sync/latest-attempt.json" "$SOURCE/outputs/sync/last-good.json" <<'PY_ALERT'
import json, sys, datetime
attempt = json.load(open(sys.argv[1]))
try: last_good = json.load(open(sys.argv[2]))
except Exception: last_good = {}
last_good_at = last_good.get("completedAt")
age_min = -1
if last_good_at:
    then = datetime.datetime.fromisoformat(last_good_at.replace("Z", "+00:00"))
    age_min = int((datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() // 60)
failing = [f'{o.get("sourceId")}: {o.get("state")}' + (f' HTTP {o.get("httpStatus")}' if o.get("httpStatus") else '') + (f' — {o.get("error")}' if o.get("error") else '')
           for o in attempt.get("observations", []) if o.get("required") and o.get("state") != "AVAILABLE"]
print(age_min)
print(last_good_at or "never")
print(" | ".join(failing) or "required provider failure (no detail recorded)")
PY_ALERT
)
  AGE_MIN=$(printf '%s\n' "$QUARANTINE_DETAIL" | sed -n '1p')
  LAST_GOOD_AT=$(printf '%s\n' "$QUARANTINE_DETAIL" | sed -n '2p')
  FAILING=$(printf '%s\n' "$QUARANTINE_DETAIL" | sed -n '3p')
  if [ "$AGE_MIN" -lt 0 ] || [ "$AGE_MIN" -gt 360 ]; then
    AGE_H=$(( AGE_MIN > 0 ? AGE_MIN / 60 : 0 ))
    say "ALERT  quarantined for ${AGE_H}h (last validated run: $LAST_GOOD_AT); failing required checks: $FAILING"
    {
      printf 'WorldsIndex quarantine alert\n'
      printf 'written: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
      printf 'quarantined for: %sh (last validated run %s)\n' "$AGE_H" "$LAST_GOOD_AT"
      printf 'failing required checks: %s\n' "$FAILING"
      printf 'effect: catalog promotion withheld; the public site keeps serving the last validated release.\n'
      printf 'log: %s\n' "$LOG"
    } >"$ALERT_FILE"
    if command -v osascript >/dev/null; then
      SHORT=$(printf '%s' "$FAILING" | cut -c1-180 | sed 's/"/\\"/g')
      osascript -e "display notification \"$SHORT\" with title \"WorldsIndex quarantined ${AGE_H}h\" subtitle \"Catalog promotion withheld — see QUARANTINE_ALERT.txt\"" >/dev/null 2>&1 || true
    fi
  fi
else
  [ -f "$ALERT_FILE" ] && { rm -f "$ALERT_FILE"; say "quarantine cleared; alert file removed"; }
fi
# ----------------------------------------------------------------------------------------

say "refreshing public light curves locally (last good products retained on failure)"
(cd "$SOURCE" && npm run lightcurves:refresh) >>"$LOG" 2>&1 || say "light-curve refresh failed; retaining prior observation products"
SCIENCE_PYTHON="${WORLDSINDEX_SCIENCE_PYTHON:-$HOME/Documents/Codex/JackMcGuireAstro Website/Development/Science Data Tools/.venv/bin/python}"
if [ -x "$SCIENCE_PYTHON" ]; then
  "$SCIENCE_PYTHON" "$SITE/scripts/refresh_worldsindex_k2.py" --source "$SOURCE" >>"$LOG" 2>&1 || say "K2 refresh failed; retaining prior observed photometry"
else
  say "K2 refresh runtime unavailable; retaining prior observed photometry"
fi
say "running ExoNexus scientific and production gates"
exonexus_gates() { (cd "$SOURCE" && npm run typecheck && npm test && npm run lint && npm run build); }
check worldsindex.exonexus exonexus_gates \
  || block "ExoNexus validation failed; nothing published"

say "regenerating the source-resolved atlas from the active frozen snapshots"
check worldsindex.atlas in_source npm run sky:generate \
  || block "atlas generation failed; nothing published"
date -u '+%Y-%m-%dT%H:%M:%SZ' >"$LOG_DIR/.last-full-run"
fi  # MODE=full

# Releases live on their own branch as one latest-only commit (scripts/data_branch.sh);
# main holds only code. Every build starts from an empty generated tree.
DATA_BRANCH="${WORLDSINDEX_DATA_BRANCH:-worldsindex-data}"
DATA_STORE="$SITE/.git/worldsindex-data-store"
if RETRY_NOTE=$(cd "$SITE" && bash scripts/data_branch.sh retry "$DATA_STORE" "$DATA_BRANCH"); then
  [ -z "$RETRY_NOTE" ] || say "$RETRY_NOTE"
else
  say "the release kept from a failed push is still unpublished: $RETRY_NOTE"
fi
DATA_TIP=$(cd "$SITE" && bash scripts/data_branch.sh sync "$DATA_STORE" "$DATA_BRANCH") \
  || die "could not read the published $DATA_BRANCH release: $DATA_TIP"
git -C "$SITE" clean -fdqX -- worldsindex/data || die "could not clear the previous generated files"
# Watch-mode pass counts of the newer checks travel with the releases (check-history.json).
git --git-dir="$DATA_STORE" show "$DATA_TIP:worldsindex/data/check-history.json" >"$CHECK_DIR/previous-history.json" 2>/dev/null \
  || printf '{}\n' >"$CHECK_DIR/previous-history.json"
CHECK_HISTORY="$CHECK_DIR/previous-history.json"

say "building the GitHub-native static release"
# The VOParquet bulk tables are written outside the Pages tree and uploaded below as assets
# of the rolling GitHub Release that worldsindex/data/bulk-downloads.json names; only that
# small index enters the release commit. The previous published release (the data-branch
# tip, read through the data store) is the base of the changelog and the feeds.
BULK_DIR="${WORLDSINDEX_RELEASE_ASSETS_DIR:-$LOG_DIR/release-assets}"
mkdir -p "$BULK_DIR" || die "could not prepare the bulk asset folder $BULK_DIR"
build_static() {
  (cd "$SITE" && WORLDSINDEX_SOURCE_DIR="$SOURCE" WORLDSINDEX_RELEASE_ASSETS_DIR="$BULK_DIR" \
    WORLDSINDEX_RELEASE_BASE_GIT_DIR="$DATA_STORE" WORLDSINDEX_RELEASE_BASE_REF="$DATA_TIP" \
    node scripts/build_worldsindex_static.mjs)
}
check worldsindex.static-build build_static \
  || block "static release build failed"
# Derive observation files from the completed manifest; never guess shard names.
while IFS= read -r artifact; do
  PUBLIC_FILES+=("$artifact")
done < <(python3 - "$SITE/worldsindex/data/manifest.json" <<'PY_OBSERVATIONS'
import json,re,sys
for path in json.load(open(sys.argv[1])).get("artifacts", {}):
    if re.fullmatch(r"lightcurves/(?:index\.json|HAT-P-\d+b\.json\.gz|K2-EPIC246199087-C12\.json\.gz)", path):
        print("worldsindex/data/" + path)
PY_OBSERVATIONS
)
check worldsindex.test_worldsindex_static in_site python3 scripts/test_worldsindex_static.py \
  || block "static release validation failed"
check worldsindex.front-syntax in_site node_check worldsindex/assets/app.js worldsindex/assets/sky.js worldsindex/assets/status.js worldsindex/assets/compare.js worldsindex/assets/derived.js worldsindex/assets/photometry.js worldsindex/assets/lightcurves.js worldsindex/assets/observe.js \
  || block "static release validation failed: a front-end script does not parse"
check worldsindex.test_worldsindex_front in_site node --test scripts/test_worldsindex_front.mjs \
  || block "static release validation failed: front-end helpers"
check worldsindex.test_worldsindex_compare in_site node --test scripts/test_worldsindex_compare.mjs \
  || block "static release validation failed: Compare tab and conflict sets"
check worldsindex.test_worldsindex_derived in_site node --test scripts/test_worldsindex_derived.mjs \
  || block "static release validation failed: calculated quantities, population plots, staleness index"
check worldsindex.test_worldsindex_observe in_site node --test scripts/test_worldsindex_observe.mjs \
  || block "static release validation failed: transit planner and the 30-night list"
check worldsindex.whitespace in_site git diff --check \
  || block "static release validation failed: whitespace"
# Track 1 gates: the exports read back with Astropy and pyarrow cell for cell (values, units,
# UCDs, limit flags, provenance columns), and every published JSON file matches its schema
# under worldsindex/schemas (named by worldsindex/openapi.yaml; the detail-shard sample is
# bounded by WORLDSINDEX_SCHEMA_BUDGET_SECONDS, default 30). Either failure refuses the release.
check worldsindex.test_worldsindex_interop_js in_site node --test scripts/test_worldsindex_interop.mjs \
  || block "interoperability exports do not round-trip; nothing published"
check worldsindex.test_worldsindex_interop in_site python3 scripts/test_worldsindex_interop.py --assets "$BULK_DIR" \
  || block "interoperability exports do not round-trip; nothing published"
check worldsindex.test_worldsindex_schemas in_site python3 scripts/test_worldsindex_schemas.py \
  || block "the release schemas fail their real-shape tests; nothing published"
check worldsindex.schemas in_site python3 scripts/validate_worldsindex_schemas.py \
  || block "published JSON does not validate against worldsindex/schemas; nothing published"
# The cloud run's public marker (scripts/cloud_run_marker.py: restored state, run id) is
# published beside the release. It is not a manifest artifact (the static test above and
# scripts/test_worldsindex_static.py leave it out), but it is allowlisted like everything else.
if [ -n "${WORLDSINDEX_CLOUD_RUN_MARKER:-}" ] && [ -f "$WORLDSINDEX_CLOUD_RUN_MARKER" ]; then
  cp "$WORLDSINDEX_CLOUD_RUN_MARKER" "$SITE/worldsindex/data/cloud-run.json" || die "could not place the cloud-run marker"
  PUBLIC_FILES+=(worldsindex/data/cloud-run.json)
fi

# Checksum self-consistency (always_block): every artifact the manifest declares has exactly
# the sha256 it states. Catastrophic data loss (integrity, watch mode at first): the object
# count may not drop by more than 20% against the published release.
check worldsindex.checksums in_site python3 scripts/release_checks.py checksums --product worldsindex --root . \
  || block "the release's artifacts differ from the checksums its manifest states; nothing published"
git --git-dir="$DATA_STORE" show "$DATA_TIP:worldsindex/data/manifest.json" >"$CHECK_DIR/previous-manifest.json" 2>/dev/null \
  || rm -f -- "$CHECK_DIR/previous-manifest.json"
check worldsindex.count-drop python3 "$SITE/scripts/release_checks.py" count-drop --product worldsindex \
  --current "$SITE/worldsindex/data/manifest.json" --previous "$CHECK_DIR/previous-manifest.json" \
  || block "the object count dropped by more than 20% against the published release; nothing published"

EXPECTED=$(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1])).get("detailShards",[])))' "$SITE/worldsindex/data/manifest.json" 2>/dev/null || echo 0)
check worldsindex.shard-count test "$EXPECTED" = "256" \
  || block "release manifest does not declare exactly 256 detail shards"

credential_scan() {
  if find "$SITE/worldsindex" -type f \( -name '.env*' -o -name '*.pem' -o -name '*.key' \) -print -quit | grep -q .; then
    echo "credential-shaped file found under the public WorldsIndex tree"; return 1
  fi
  if grep -R -I -E 'ADS_API_TOKEN[[:space:]]*=|Bearer[[:space:]]+[A-Za-z0-9._-]{20,}' "$SITE/worldsindex" >/dev/null 2>&1; then
    echo "credential-shaped text found under the public WorldsIndex tree"; return 1
  fi
}
check worldsindex.credential-scan credential_scan \
  || block "credential-shaped file or text found under the public WorldsIndex tree"

cd "$SITE" || die "cannot enter the website checkout"

ACTIVE_CHECK=worldsindex.artifact-guard
# ---- Generic artifact guard -------------------------------------------------------------
# 1. Every artifact the manifest declares must be in the allowlist. The set is derived from
#    the manifest itself (manifest.artifacts, falling back to the hash/shard fields), so a
#    new builder output cannot be referenced by the manifest yet omitted from publication.
REQUIRED_ARTIFACTS=$(python3 - "$SITE/worldsindex/data/manifest.json" <<'PY_GUARD'
import json, sys
m = json.load(open(sys.argv[1]))
required = {"worldsindex/data/manifest.json"}
if isinstance(m.get("artifacts"), dict):
    required.update("worldsindex/data/" + p for p in m["artifacts"])
else:
    if "atlasSha256" in m: required.add("worldsindex/data/sky-detections.json.gz")
    if "catalogIndexSha256" in m: required.add("worldsindex/data/catalog-index.json.gz")
    required.update("worldsindex/data/details/%s.json.gz" % s for s in m.get("detailShards", []))
print("\n".join(sorted(required)))
PY_GUARD
) || die "could not derive the required artifact set from the release manifest"
[ -n "$REQUIRED_ARTIFACTS" ] || die "release manifest declares no artifacts"
MISSING_FROM_ALLOWLIST=""
while IFS= read -r required; do
  [ -f "$required" ] || die "manifest-declared artifact is absent from the build: $required"
  allowed=0
  for public_file in "${PUBLIC_FILES[@]}"; do [ "$required" = "$public_file" ] && allowed=1; done
  [ "$allowed" -eq 1 ] || MISSING_FROM_ALLOWLIST="$MISSING_FROM_ALLOWLIST $required"
done <<<"$REQUIRED_ARTIFACTS"
[ -z "$MISSING_FROM_ALLOWLIST" ] \
  || die "manifest-declared artifacts are not in the publication allowlist; refusing to publish a release the site could not load:$MISSING_FROM_ALLOWLIST"

# 2. Nothing the build left dirty or untracked under worldsindex/data may fall outside the
#    allowlist, or the commit would advance the manifest while leaving stale bytes behind.
UNLISTED_DIRTY=""
SYNC_DUPLICATES=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  path=${line:3}
  path=${path#\"}; path=${path%\"}
  # macOS/iCloud sync conflict copies ("catalog-index 2.json.gz") are untracked, never staged,
  # never checked out by CI; report them, do not let them block a release.
  case "$path" in *" "[0-9]*.*) SYNC_DUPLICATES="$SYNC_DUPLICATES $path"; continue ;; esac
  allowed=0
  for public_file in "${PUBLIC_FILES[@]}"; do [ "$path" = "$public_file" ] && allowed=1; done
  [ "$allowed" -eq 1 ] || UNLISTED_DIRTY="$UNLISTED_DIRTY $path"
done < <(cd "$SITE" && find worldsindex/data -type f | sort | sed 's/^/?? /')
[ -z "$SYNC_DUPLICATES" ] || say "warning: sync duplicate files under worldsindex/data are ignored:$SYNC_DUPLICATES"
[ -z "$UNLISTED_DIRTY" ] \
  || die "build changed files outside the publication allowlist; refusing a partial release:$UNLISTED_DIRTY"
REQUIRED_COUNT=$(printf '%s\n' "$REQUIRED_ARTIFACTS" | grep -c .)
say "artifact guard: $REQUIRED_COUNT manifest-declared artifacts are all present and allowlisted"
ACTIVE_CHECK=""
record_check worldsindex.artifact-guard 0 "" >/dev/null

# ---- Check history ----------------------------------------------------------------------
# The watch-mode pass counts after this release (scripts/release_checks.py history),
# published beside the release like the cloud-run marker and, like it, not a manifest
# artifact: it is written after the checks that judged the release, so the manifest those
# checks verified cannot carry its checksum.
if python3 "$SITE/scripts/release_checks.py" history --scope worldsindex-publish --results "$CHECK_RESULTS" \
     --previous "$CHECK_HISTORY" --site-commit "$(git -C "$SITE" rev-parse HEAD 2>/dev/null)" \
     --out "$SITE/worldsindex/data/check-history.json" >>"$LOG" 2>&1; then
  PUBLIC_FILES+=(worldsindex/data/check-history.json)
else
  say "WARN  could not write worldsindex/data/check-history.json; watch-mode pass counts do not advance with this release"
fi
# ----------------------------------------------------------------------------------------

OBJECTS=$(python3 -c 'import json;print(json.load(open("worldsindex/data/manifest.json"))["objectCount"])' 2>/dev/null || echo '?')
RECORDS=$(python3 -c 'import json;print(json.load(open("worldsindex/data/manifest.json"))["detailRecordCount"])' 2>/dev/null || echo '?')

if [ "$DRY" -eq 1 ]; then
  say "--dry-run: validated $OBJECTS objects and $RECORDS native rows; would publish ${#PUBLIC_FILES[@]} allowlisted artifacts covering $REQUIRED_COUNT manifest-declared artifacts to $DATA_BRANCH"
  printf '%s\n' "$REQUIRED_ARTIFACTS" | sed 's/^/  would publish: /' >>"$LOG"
  discard_generated_files
  exit 0
fi

# ---- Bulk assets ------------------------------------------------------------------------
# The VOParquet tables go to the rolling GitHub Release named by bulk-downloads.json (tag
# "worldsindex-data-latest"), not into the Pages tree: Release assets have no bandwidth limit
# and no 1 GB site budget. The release is created when missing and each asset is replaced
# (--clobber). An upload failure is reported, not fatal: the Pages release carries the asset
# SHA-256 digests, so a reader can tell a lagging asset from the current one, and the next
# cycle uploads again. WORLDSINDEX_RELEASE_TOKEN (the workflow token, contents: write)
# authenticates gh; without gh or a token the step is skipped and said so.
upload_bulk_assets() {
  local tag repo asset count=0 bytes total=0 sizes=""
  tag=$(python3 -c "import json;print(json.load(open('worldsindex/data/bulk-downloads.json'))['releaseTag'])" 2>/dev/null) \
    || { say "WARN  bulk-downloads.json has no release tag; VOParquet assets not uploaded"; return 0; }
  repo=$(python3 -c "import json;print(json.load(open('worldsindex/data/bulk-downloads.json'))['repository'])" 2>/dev/null)
  [ -n "$repo" ] || { say "WARN  could not name the GitHub repository; VOParquet assets not uploaded"; return 0; }
  if ! ls "$BULK_DIR"/*.parquet >/dev/null 2>&1; then say "no VOParquet assets were built this run (see bulk-downloads.json unavailableReason); nothing to upload"; return 0; fi
  command -v gh >/dev/null 2>&1 || { say "no GitHub CLI on this publisher; VOParquet assets not uploaded"; return 0; }
  if [ -n "${WORLDSINDEX_RELEASE_TOKEN:-}" ]; then export GH_TOKEN="$WORLDSINDEX_RELEASE_TOKEN"; fi
  if ! gh release view "$tag" --repo "$repo" >/dev/null 2>&1; then
    gh release create "$tag" --repo "$repo" --title "WorldsIndex bulk data (rolling)" --latest=false \
      --notes "Rolling VOParquet bulk tables of the latest WorldsIndex release. Names, sizes and SHA-256 digests: https://jackmcguireastro.github.io/worldsindex/data/bulk-downloads.json. Replaced on every release; verify the digest." >>"$LOG" 2>&1 \
      || { say "WARN  could not create the $tag release on $repo; VOParquet assets not uploaded"; return 0; }
  fi
  for asset in "$BULK_DIR"/*.parquet; do
    [ -f "$asset" ] || continue
    bytes=$(wc -c <"$asset" | tr -d ' ')
    gh release upload "$tag" "$asset" --repo "$repo" --clobber >>"$LOG" 2>&1 \
      || { say "WARN  upload of $(basename "$asset") ($bytes bytes) to release $tag failed; the previous asset stays until the next cycle"; return 0; }
    count=$((count + 1))
    total=$((total + bytes))
    sizes="$sizes $(basename "$asset")=$bytes"
  done
  # Size log line: one per release, so the asset growth can be read from the run logs.
  say "uploaded $count VOParquet asset(s), $total bytes, to release $tag of $repo:$sizes"
}
upload_bulk_assets
# ----------------------------------------------------------------------------------------

# Replace the data branch with one commit holding exactly the allowlisted artifacts.
# main is never committed to or force-pushed here; only this publisher writes it.
RELEASE_LIST=$(mktemp "${TMPDIR:-/tmp}/worldsindex-release-files.XXXXXX") || die "could not list the release files"
for public_file in "${PUBLIC_FILES[@]}"; do
  [ -f "$public_file" ] && printf '%s\n' "$public_file"
done >"$RELEASE_LIST"
PUBLISH_OUTPUT=$(bash scripts/data_branch.sh publish "$DATA_STORE" "$DATA_BRANCH" \
  "WorldsIndex data: $OBJECTS objects, $RECORDS rows ($(date -u '+%Y-%m-%d %H:%M UTC'))" "$RELEASE_LIST")
PUBLISH_STATUS=$?
rm -f "$RELEASE_LIST"
case "$PUBLISH_STATUS" in
  0)
    input_fingerprint >"$INPUT_STAMP" 2>/dev/null || true
    SHA=$(printf '%s' "$PUBLISH_OUTPUT" | awk '{print substr($2, 1, 11)}')
    say "published $SHA to $DATA_BRANCH; GitHub Actions validates and deploys the static release"
    ;;
  10)
    say "validated public release already matches the published $DATA_BRANCH release; nothing to publish"
    input_fingerprint >"$INPUT_STAMP" 2>/dev/null || true
    ;;
  *)
    printf '%s\n' "$PUBLISH_OUTPUT" >>"$LOG"
    die "push failed; the release is kept for the next run and nothing was forced"
    ;;
esac
