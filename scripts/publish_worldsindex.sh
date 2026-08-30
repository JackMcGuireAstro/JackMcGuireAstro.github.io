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

for argument in "$@"; do
  case "$argument" in
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    *) printf 'unknown option: %s\n' "$argument" >&2; exit 2 ;;
  esac
done

PUBLIC_FILES=(
  worldsindex/data/manifest.json
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
[ -x "$SOURCE/scripts/run-source-monitor-noninteractive.sh" ] || die "source-monitor wrapper is missing or not executable"
command -v node >/dev/null || die "node is unavailable"
command -v npm >/dev/null || die "npm is unavailable"
command -v python3 >/dev/null || die "python3 is unavailable"

export GIT_TERMINAL_PROMPT=0
: "${GIT_SSH_COMMAND:=ssh -o BatchMode=yes -o ConnectTimeout=15 -o ConnectionAttempts=2 -o ServerAliveInterval=10 -o ServerAliveCountMax=2}"
export GIT_SSH_COMMAND

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
  say "upstream change markers detected: ${CHANGED_SOURCES:-unspecified}; publishing the receipt but retaining the reconciled catalog until its source-specific promotion gate passes"
fi
if [ "$MONITOR_STATE" = "QUARANTINED" ]; then
  say "one or more providers failed; publishing the typed failure receipt while retaining every catalog measurement from the last-good reconciled snapshots"
fi

say "running ExoNexus scientific and production gates"
(cd "$SOURCE" && npm run typecheck && npm test && npm run lint && npm run build) >>"$LOG" 2>&1 \
  || die "ExoNexus validation failed; nothing published"

say "regenerating the source-resolved atlas from the active frozen snapshots"
(cd "$SOURCE" && npm run sky:generate) >>"$LOG" 2>&1 \
  || die "atlas generation failed; nothing published"

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
if git diff --quiet HEAD -- "${PUBLIC_FILES[@]}" 2>/dev/null && [ "$FORCE" -eq 0 ]; then
  say "validated public release already matches HEAD; nothing to publish"
  exit 0
fi

OBJECTS=$(python3 -c 'import json;print(json.load(open("worldsindex/data/manifest.json"))["objectCount"])' 2>/dev/null || echo '?')
RECORDS=$(python3 -c 'import json;print(json.load(open("worldsindex/data/manifest.json"))["detailRecordCount"])' 2>/dev/null || echo '?')

if [ "$DRY" -eq 1 ]; then
  say "--dry-run: validated $OBJECTS objects and $RECORDS native rows; would stage ${#PUBLIC_FILES[@]} allowlisted artifacts"
  git restore --source=HEAD --worktree -- "${PUBLIC_FILES[@]}" 2>/dev/null || true
  exit 0
fi

STAGED_OTHER=$(git diff --cached --name-only)
[ -z "$STAGED_OTHER" ] || die "other files are already staged; refusing automated commit: $(printf '%s' "$STAGED_OTHER" | tr '\n' ' ')"
git add -- "${PUBLIC_FILES[@]}" || die "could not stage the explicit WorldsIndex artifact allowlist"

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

say "published $SHA; GitHub Actions and Pages now validate and deploy the static release"
