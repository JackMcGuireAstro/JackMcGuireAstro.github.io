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
)
for bucket_index in {0..255}; do
  printf -v bucket '%02x' "$bucket_index"
  PUBLIC_FILES+=("worldsindex/data/details/$bucket.json.gz")
done

mkdir -p "$LOG_DIR"
[ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 2097152 ] && mv -f "$LOG" "$LOG.1" 2>/dev/null
say() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*" >>"$LOG"; printf '%s\n' "$*"; }
die() { say "FAIL  $*"; exit 1; }

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
cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ -d "$SITE/.git" ]; then
    git -C "$SITE" restore --source=HEAD --staged --worktree -- "${PUBLIC_FILES[@]}" 2>/dev/null || true
  fi
  rmdir "$LOCKDIR" 2>/dev/null
  return "$status"
}
trap cleanup EXIT

[ -d "$SITE/.git" ] || die "dedicated website checkout is missing: $SITE"
[ -f "$SOURCE/package.json" ] || die "ExoNexus source project is missing: $SOURCE"
command -v node >/dev/null || die "node is unavailable"
if [ "$MODE" = "full" ]; then
  [ -x "$SOURCE/scripts/run-source-monitor-noninteractive.sh" ] || die "source-monitor wrapper is missing or not executable"
  command -v npm >/dev/null || die "npm is unavailable"
fi
command -v python3 >/dev/null || die "python3 is unavailable"

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
    for file in "$SOURCE/public/data/sky-detections.json.gz" "$SOURCE/public/data/sync/latest.json" \
                "$SOURCE/data/snapshots/exoplanet-eu/ACTIVE.json" "$SOURCE/data/atlas/release-contract.json" \
                "$SOURCE/outputs/promotion/exoplanet-eu/latest.json" \
                "$SITE/scripts/build_worldsindex_static.mjs" "$SITE/worldsindex/index.html" "$SITE/worldsindex/assets/app.js" \
                "$SITE/worldsindex/assets/science.js" "$SITE/worldsindex/assets/app.css"; do
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

say "running ExoNexus scientific and production gates"
(cd "$SOURCE" && npm run typecheck && npm test && npm run lint && npm run build) >>"$LOG" 2>&1 \
  || die "ExoNexus validation failed; nothing published"

say "regenerating the source-resolved atlas from the active frozen snapshots"
(cd "$SOURCE" && npm run sky:generate) >>"$LOG" 2>&1 \
  || die "atlas generation failed; nothing published"
date -u '+%Y-%m-%dT%H:%M:%SZ' >"$LOG_DIR/.last-full-run"
fi  # MODE=full

say "building the GitHub-native static release"
(cd "$SITE" && WORLDSINDEX_SOURCE_DIR="$SOURCE" node scripts/build_worldsindex_static.mjs) >>"$LOG" 2>&1 \
  || die "static release build failed"
(cd "$SITE" && python3 scripts/test_worldsindex_static.py && node --check worldsindex/assets/app.js && git diff --check) >>"$LOG" 2>&1 \
  || die "static release validation failed"

EXPECTED=$(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1])).get("detailShards",[])))' "$SITE/worldsindex/data/manifest.json" 2>/dev/null || echo 0)
[ "$EXPECTED" = "256" ] || die "release manifest does not declare exactly 256 detail shards"

if find "$SITE/worldsindex" -type f \( -name '.env*' -o -name '*.pem' -o -name '*.key' \) -print -quit | grep -q .; then
  die "credential-shaped file found under the public WorldsIndex tree"
fi
if grep -R -I -E 'ADS_API_TOKEN[[:space:]]*=|Bearer[[:space:]]+[A-Za-z0-9._-]{20,}' "$SITE/worldsindex" >/dev/null 2>&1; then
  die "credential-shaped text found under the public WorldsIndex tree"
fi

cd "$SITE" || die "cannot enter the website checkout"

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
done < <(git status --porcelain --untracked-files=all -- worldsindex/data)
[ -z "$SYNC_DUPLICATES" ] || say "warning: sync duplicate files under worldsindex/data are ignored:$SYNC_DUPLICATES"
[ -z "$UNLISTED_DIRTY" ] \
  || die "build changed files outside the publication allowlist; refusing a partial release:$UNLISTED_DIRTY"
REQUIRED_COUNT=$(printf '%s\n' "$REQUIRED_ARTIFACTS" | grep -c .)
say "artifact guard: $REQUIRED_COUNT manifest-declared artifacts are all present and allowlisted"
# ----------------------------------------------------------------------------------------

if git diff --quiet HEAD -- "${PUBLIC_FILES[@]}" 2>/dev/null && [ "$FORCE" -eq 0 ]; then
  say "validated public release already matches HEAD; nothing to publish"
  input_fingerprint >"$INPUT_STAMP" 2>/dev/null || true
  exit 0
fi

OBJECTS=$(python3 -c 'import json;print(json.load(open("worldsindex/data/manifest.json"))["objectCount"])' 2>/dev/null || echo '?')
RECORDS=$(python3 -c 'import json;print(json.load(open("worldsindex/data/manifest.json"))["detailRecordCount"])' 2>/dev/null || echo '?')

if [ "$DRY" -eq 1 ]; then
  say "--dry-run: validated $OBJECTS objects and $RECORDS native rows; would stage ${#PUBLIC_FILES[@]} allowlisted artifacts covering $REQUIRED_COUNT manifest-declared artifacts"
  printf '%s\n' "$REQUIRED_ARTIFACTS" | sed 's/^/  would stage: /' >>"$LOG"
  git restore --source=HEAD --worktree -- "${PUBLIC_FILES[@]}" 2>/dev/null || true
  exit 0
fi

STAGED_OTHER=$(git diff --cached --name-only)
[ -z "$STAGED_OTHER" ] || die "other files are already staged; refusing automated commit: $(printf '%s' "$STAGED_OTHER" | tr '\n' ' ')"
git add -- "${PUBLIC_FILES[@]}" || die "could not stage the explicit WorldsIndex artifact allowlist"
LEFT_BEHIND=$(git status --porcelain --untracked-files=all -- worldsindex/data | grep -v '^[MADRC] ' | grep -v -E ' [0-9]+\.[^/]*$' || true)
[ -z "$LEFT_BEHIND" ] || die "artifacts remain unstaged after allowlist staging; refusing a partial release: $(printf '%s' "$LEFT_BEHIND" | tr '\n' ' ')"

while IFS= read -r staged; do
  allowed=0
  for public_file in "${PUBLIC_FILES[@]}"; do [ "$staged" = "$public_file" ] && allowed=1; done
  [ "$allowed" -eq 1 ] || die "unexpected staged path: $staged"
done < <(git diff --cached --name-only)

PENDING=0
if git log -1 --pretty=%s | grep -q '^WorldsIndex data: ' \
   && ! git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
  PENDING=1
fi
AMEND=""
if [ "$PENDING" -eq 1 ]; then
  AMEND="--amend"
  say "amending the previous unpublished WorldsIndex data commit"
fi
git commit -q $AMEND -m "WorldsIndex data: $OBJECTS objects, $RECORDS rows ($(date -u '+%Y-%m-%d %H:%M UTC'))" \
  || die "could not create the WorldsIndex release commit"
SHA=$(git rev-parse --short HEAD)

PUSH_OUTPUT=$(git push origin "$BRANCH" 2>&1)
PUSH_STATUS=$?
if [ "$PUSH_STATUS" -ne 0 ]; then
  say "push raced with another site update; checking whether a safe rebase is possible"
  git fetch --quiet origin "$BRANCH" || die "could not refresh origin/$BRANCH after rejected push"
  BOUND_CODE=$(git diff --name-only HEAD "origin/$BRANCH" -- worldsindex/index.html worldsindex/assets scripts/build_worldsindex_static.mjs scripts/test_worldsindex_static.py)
  [ -z "$BOUND_CODE" ] || die "remote WorldsIndex code changed during the build; next run must rebuild against it"
  if git rebase "origin/$BRANCH" >/dev/null 2>&1; then
    SHA=$(git rev-parse --short HEAD)
    PUSH_OUTPUT=$(git push origin "$BRANCH" 2>&1)
    PUSH_STATUS=$?
  else
    git rebase --abort >/dev/null 2>&1 || true
    die "could not safely rebase the release onto origin/$BRANCH"
  fi
fi
if [ "$PUSH_STATUS" -ne 0 ]; then
  printf '%s\n' "$PUSH_OUTPUT" >>"$LOG"
  die "push failed; commit $SHA remains local and nothing was forced"
fi

input_fingerprint >"$INPUT_STAMP" 2>/dev/null || true
say "published $SHA; GitHub Actions validates and deploys the static release"
