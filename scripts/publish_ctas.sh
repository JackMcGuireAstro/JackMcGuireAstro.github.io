#!/bin/bash
# =============================================================================
# publish_ctas.sh - mirror the local CTAS database onto the public website.
#
# The local database is the original. This makes GitHub follow it. Normally run
# by a launchd agent every 120 seconds while the publishing Mac is awake and
# logged in. Unchanged state exits without a data commit until its bounded
# freshness heartbeat is due.
#
#   ./scripts/publish_ctas.sh              export; commit and push if changed
#   ./scripts/publish_ctas.sh --dry-run    export and report; push nothing
#   ./scripts/publish_ctas.sh --force      publish a checksum-bound refresh now
#
# It commits ONLY the explicit public-artifact allowlist below. Any other work
# in progress is left untouched.
# It never force-pushes and never rewrites published history.
# =============================================================================
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1

SITE="${CTAS_SITE:-$HOME/Documents/GitHub/JackMcGuireAstro.github.io}"
DB="${CTAS_DB:-$HOME/.codex/.chatgpt-projects/g-p-6a5d91be2e688191b7333527fcd488b3/data/soc.db}"
BRANCH="${CTAS_BRANCH:-main}"
PUBLIC_FILES=(
  ctas/data/live-summary.json
  ctas/data/catalog-index.json
  ctas/data/catalog-pages/manifest.json
  ctas/data/source-matrix-patterns.json
  ctas/data/alias-index.json
  ctas/data/candidate-chunks/manifest.json
  ctas/data/research/manifest.json
  ctas/data/research/events.csv
  ctas/data/research/aliases.csv
  ctas/data/research/sources.csv
  ctas/data/research/events.vot
  ctas/data/research/tom-targets.csv
  ctas/data/status.json
  ctas/data/source-universe.json
  ctas/data/release-history.json
  ctas/data/link-health.json
  ctas/data/certification.json
)
for bucket_index in {0..4095}; do
  printf -v bucket '%03x' "$bucket_index"
  PUBLIC_FILES+=("ctas/data/candidate-chunks/$bucket.json")
done
# Complete-catalog pages are bounded and few; publish every page the exporter
# produced rather than a fixed count, so a growing catalog cannot silently drop
# its tail from the release.
while IFS= read -r page; do
  [ -n "$page" ] && PUBLIC_FILES+=("$page")
done < <(find ctas/data/catalog-pages -maxdepth 1 -name '[0-9][0-9][0-9][0-9].json' 2>/dev/null | sort)

# Floor between published commits, not a schedule. 0 = publish as soon as the
# data actually changes. Nothing happens at all unless the data changed.
MIN_INTERVAL="${CTAS_MIN_INTERVAL:-0}"

# The watcher still checks every two minutes. When neither candidate content nor
# durable source state changed, publish only a bounded freshness heartbeat.
# This avoids a meaningless large catalog commit every poll while keeping the
# public snapshot report comfortably inside its 30-minute verification window.
HEARTBEAT_INTERVAL="${CTAS_HEARTBEAT_INTERVAL:-900}"

LOG_DIR="${CTAS_LOG_DIR:-$HOME/Library/Logs/ctas-mirror}"
LOG="$LOG_DIR/publish.log"
STAMP="$LOG_DIR/.last-publish"
LOCKDIR="$LOG_DIR/.lock.d"
PUBLISH_DB=""
SITE_READY=0

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

case "$HEARTBEAT_INTERVAL" in
  ''|0*|*[!0-9]*) die "CTAS_HEARTBEAT_INTERVAL must be an integer from 120 to 900 seconds" ;;
esac
[ "$HEARTBEAT_INTERVAL" -ge 120 ] && [ "$HEARTBEAT_INTERVAL" -le 900 ] \
  || die "CTAS_HEARTBEAT_INTERVAL must stay between 120 and 900 seconds"

export GIT_TERMINAL_PROMPT=0        # never hang waiting for a credential
: "${GIT_SSH_COMMAND:=ssh -o BatchMode=yes -o ConnectTimeout=15 -o ConnectionAttempts=2 -o ServerAliveInterval=10 -o ServerAliveCountMax=2}"
export GIT_SSH_COMMAND

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
cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$SITE_READY" -eq 1 ]; then
    git -C "$SITE" restore --source=HEAD --staged --worktree -- "${PUBLIC_FILES[@]}" 2>/dev/null || true
  fi
  if [ -n "$PUBLISH_DB" ] && [ -f "$PUBLISH_DB" ]; then
    rm -f -- "$PUBLISH_DB"
  fi
  rmdir "$LOCKDIR" 2>/dev/null
  return "$status"
}
trap cleanup EXIT

[ -d "$SITE" ] || die "website repo not found: $SITE"
[ -f "$DB" ]   || die "CTAS database not found: $DB"
cd "$SITE"     || die "cannot enter $SITE"
SITE_READY=1

# Freeze one transactionally consistent SQLite view for the whole release.
# The live pipeline may continue writing to the canonical database while link
# checks and assurance artifacts are generated, but no publication mixes two
# database states.
PUBLISH_DB=$(mktemp "${TMPDIR:-/tmp}/ctas-publish.XXXXXX") \
  || die "could not allocate a temporary database snapshot"
sqlite3 "$DB" ".backup '$PUBLISH_DB'" \
  || die "could not create a consistent database snapshot"
[ -s "$PUBLISH_DB" ] || die "database snapshot is empty"

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
python3 scripts/export_ctas_snapshot.py --database "$PUBLISH_DB" --output-dir ctas/data \
  --release-base-ref origin/main >>"$LOG" 2>&1 \
  || die "export failed; nothing committed"
python3 scripts/check_ctas_links.py --catalog-index ctas/data/catalog-index.json \
  --candidate-manifest ctas/data/candidate-chunks/manifest.json \
  --source-universe ctas/data/source-universe.json --output ctas/data/link-health.json >>"$LOG" 2>&1 \
  || die "public link validation failed; nothing committed"
# Rebuild once so the verification report binds the current link-health artifact and
# its catalog-content checksum. The exported scientific rows are deterministic.
python3 scripts/export_ctas_snapshot.py --database "$PUBLISH_DB" --output-dir ctas/data \
  --release-base-ref origin/main >>"$LOG" 2>&1 \
  || die "verification-report rebuild failed; nothing committed"

# ------------------------------------------------- retire superseded artifacts
# The partition width and the first-screen artifact can change between code
# releases. Any previously published data file the current manifest no longer
# declares would otherwise stay on the site forever, serving a stale dossier at
# a live URL. Retire exactly those, and add them to the allowlist so the
# deletion is committed under the same explicit rule as everything else.
RETIRED=$(python3 - <<'PYRETIRE'
import json
import subprocess
from pathlib import Path

manifest = json.loads(Path("ctas/data/candidate-chunks/manifest.json").read_text())
pages = json.loads(Path("ctas/data/catalog-pages/manifest.json").read_text())
current = {row["path"] for row in manifest.get("chunks", [])}
current |= {row["path"] for row in pages.get("pages", [])}
current |= {
    "ctas/data/candidate-chunks/manifest.json",
    "ctas/data/catalog-pages/manifest.json",
}
tracked = subprocess.run(
    ["git", "ls-files", "ctas/data/candidate-chunks", "ctas/data/catalog-pages",
     "ctas/data/catalog-bootstrap.json"],
    capture_output=True, text=True, check=True,
).stdout.split()
for path in sorted(set(tracked) - current):
    Path(path).unlink(missing_ok=True)
    print(path)
PYRETIRE
) || die "could not determine which published artifacts this release retires"
if [ -n "$RETIRED" ]; then
  while IFS= read -r retired; do
    [ -n "$retired" ] && PUBLIC_FILES+=("$retired")
  done <<<"$RETIRED"
  say "retiring $(printf '%s\n' "$RETIRED" | grep -c .) superseded public data files"
fi

# ------------------------------------------------------------------ tests
# A release may not be committed on the strength of its own report alone. The
# suites below read the artifacts that were just written, so they run after the
# export and before anything is staged.
for suite in scripts/test_ctas_static.py scripts/test_ctas_links.py scripts/test_ctas_identity.py \
             scripts/test_ctas_astro_evidence.py scripts/test_ctas_browser.py; do
  python3 "$suite" >>"$LOG" 2>&1 || die "$suite failed against the generated release; nothing committed"
done
# The catalog model is the browser's copy of the reader-facing rules, so it is
# checked in a JavaScript runtime. A publisher without one is reported rather
# than blocked: the Python suites above already cover the published artifacts,
# and an absent interpreter is an environment fact, not a failing assertion.
if command -v node >/dev/null 2>&1; then
  node scripts/test_ctas_catalog_model.js >>"$LOG" 2>&1 \
    || die "catalog-model assertions failed against the generated release; nothing committed"
else
  say "node is not installed on this publisher; catalog-model assertions were not run"
fi

EXPECTED_SHARDS=$(python3 - <<'PY'
import json
from pathlib import Path

manifest = json.loads(Path("ctas/data/candidate-chunks/manifest.json").read_text())
actual = sorted(row.get("path") for row in manifest.get("chunks", []))
expected = [f"ctas/data/candidate-chunks/{index:03x}.json" for index in range(4096)]
if actual != expected:
    raise SystemExit("detail-shard manifest is not the exact 000..fff release set")
print(len(actual))
PY
) || die "detail-shard manifest does not match the explicit publisher allowlist"
[ "$EXPECTED_SHARDS" = "4096" ] || die "detail-shard manifest does not declare 4096 shards"

CERT_STATUS=$(python3 -c "import json;print(json.load(open('ctas/data/certification.json'))['status'])" 2>/dev/null || echo "unreadable")
if [ "$CERT_STATUS" != "verified-static-snapshot" ]; then
  FAILED_GATES=$(python3 -c '
import json
report = json.load(open("ctas/data/certification.json"))
print(",".join(sorted(gate["id"] for gate in report.get("gates", []) if gate.get("passed") is not True)))
' 2>/dev/null || echo "unreadable")
  case "$FAILED_GATES" in
    deployed-code-binding,local-origin-code-alignment|deployed-code-binding|local-origin-code-alignment)
      say "local checksum-bound code successor is not published; publication paused"
      exit 0
      ;;
    *) die "static-snapshot verification is $CERT_STATUS ($FAILED_GATES); refusing publication" ;;
  esac
fi

# ------------------------------------------------------------ changed at all?
# publication_state_checksum_sha256 covers semantic candidate content plus
# durable source states/counts/limitations. Poll timestamps and generated
# timestamps are intentionally excluded, so an unchanged source re-check does
# not become a new release every two minutes.
CURRENT_STATE=$(python3 -c "import json;print(json.load(open('ctas/data/status.json')).get('publication_state_checksum_sha256',''))" 2>/dev/null || echo "")
[ -n "$CURRENT_STATE" ] || die "status.json has no publication-state checksum"
CURRENT_CODE_BINDING=$(python3 -c '
import hashlib, json, sys
doc = json.load(open(sys.argv[1]))
rows = {key: value.get("sha256") for key, value in doc.get("files", {}).items() if not key.startswith("ctas/data/")}
print(hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
' ctas/data/certification.json 2>/dev/null || echo "")
HEAD_META=$(git show HEAD:ctas/data/status.json 2>/dev/null | python3 -c '
import datetime, json, sys
doc = json.load(sys.stdin)
stamp = doc.get("last_successful_update") or doc.get("generated_at") or ""
try:
    parsed = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    epoch = int(parsed.timestamp())
except (TypeError, ValueError):
    epoch = 0
print("{}\t{}".format(doc.get("publication_state_checksum_sha256", ""), epoch))
' 2>/dev/null || true)
HEAD_CODE_BINDING=$(git show HEAD:ctas/data/certification.json 2>/dev/null | python3 -c '
import hashlib, json, sys
doc = json.load(sys.stdin)
rows = {key: value.get("sha256") for key, value in doc.get("files", {}).items() if not key.startswith("ctas/data/")}
print(hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
' 2>/dev/null || echo "")
HEAD_STATE=${HEAD_META%%$'\t'*}
HEAD_PUBLISHED_EPOCH=${HEAD_META#*$'\t'}
NOW_EPOCH=$(date +%s)
CODE_BINDING_CHANGED=0
[ -n "$CURRENT_CODE_BINDING" ] && [ "$CURRENT_CODE_BINDING" = "$HEAD_CODE_BINDING" ] \
  || CODE_BINDING_CHANGED=1
PENDING_CTAS_COMMIT=0
if git log -1 --pretty=%s 2>/dev/null | grep -q '^CTAS data: ' \
   && ! git merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
  PENDING_CTAS_COMMIT=1
fi

if [ "$CURRENT_STATE" = "$HEAD_STATE" ] && [ "$PENDING_CTAS_COMMIT" -eq 0 ] \
   && [ "$CODE_BINDING_CHANGED" -eq 0 ]; then
  case "$HEAD_PUBLISHED_EPOCH" in
    ''|*[!0-9]*) HEARTBEAT_AGE=$HEARTBEAT_INTERVAL ;;
    *) HEARTBEAT_AGE=$((NOW_EPOCH - HEAD_PUBLISHED_EPOCH)) ;;
  esac
  if [ "$FORCE" -eq 0 ] && [ "$HEARTBEAT_AGE" -ge 0 ] && [ "$HEARTBEAT_AGE" -lt "$HEARTBEAT_INTERVAL" ]; then
    say "publication state unchanged; next freshness heartbeat in $((HEARTBEAT_INTERVAL - HEARTBEAT_AGE))s"
    git checkout -- "${PUBLIC_FILES[@]}" 2>/dev/null || true
    exit 0
  fi
  if [ "$FORCE" -eq 1 ]; then
    say "publication state unchanged; publishing the requested checksum-bound refresh"
  else
    say "publication state unchanged; publishing the bounded freshness heartbeat"
  fi
elif [ "$CODE_BINDING_CHANGED" -eq 1 ]; then
  say "bound public code changed; publishing a matching snapshot-verification refresh"
fi

if git diff --quiet HEAD -- "${PUBLIC_FILES[@]}" 2>/dev/null; then
  say "public artifacts already match HEAD; nothing to publish"
  exit 0
fi

COUNT=$(python3 -c "import json;print(json.load(open('ctas/data/catalog-index.json'))['candidate_count'])" 2>/dev/null || echo "?")

if [ "$DRY" -eq 1 ]; then
  say "--dry-run: $COUNT candidates; would commit ${#PUBLIC_FILES[@]} allowlisted public CTAS artifacts and push to $BRANCH"
  git restore --source=HEAD --worktree -- "${PUBLIC_FILES[@]}" 2>/dev/null || true
  exit 0
fi

# ------------------------------------------------------- refuse a dirty index
# Only ctas/data may be committed. If anything else is already staged, stop
# rather than sweeping work-in-progress into an automated commit.
STAGED_OTHER=$(git diff --cached --name-only | while IFS= read -r staged; do
  allowed=0
  for public_file in "${PUBLIC_FILES[@]}"; do [ "$staged" = "$public_file" ] && allowed=1; done
  [ "$allowed" -eq 1 ] || printf '%s\n' "$staged"
done)
if [ -n "$STAGED_OTHER" ]; then
  die "other files are already staged; refusing to commit. Staged: $(echo "$STAGED_OTHER" | tr '\n' ' ')"
fi

git add -- "${PUBLIC_FILES[@]}" || die "git add failed"

STAGED=$(git diff --cached --name-only)
for staged in $STAGED; do
  allowed=0
  for public_file in "${PUBLIC_FILES[@]}"; do [ "$staged" = "$public_file" ] && allowed=1; done
  [ "$allowed" -eq 1 ] || die "unexpected staged path after allowlisted add: $staged"
done

# If the last commit is one of ours and has not reached origin yet, amend it
# instead of stacking a new commit every cycle. A run of failing pushes then
# leaves ONE pending commit carrying the newest data, not dozens of stale ones.
AMEND=""
if [ "$PENDING_CTAS_COMMIT" -eq 1 ]; then
  AMEND="--amend"
  say "previous CTAS commit is still unpushed; amending it rather than stacking"
fi

git commit -q $AMEND -m "CTAS data: $COUNT candidates ($(date -u '+%Y-%m-%d %H:%M UTC'))" \
  || die "git commit failed"
SHA=$(git rev-parse --short HEAD)

PUSH_ERR=$(git push origin "$BRANCH" 2>&1)
PUSH_STATUS=$?
if [ "$PUSH_STATUS" -ne 0 ]; then
  say "first push attempt failed; refreshing origin before one safe retry"
  if git fetch --quiet origin "$BRANCH"; then
    REBASE_ERR=$(git rebase "origin/$BRANCH" 2>&1)
    REBASE_STATUS=$?
    if [ "$REBASE_STATUS" -eq 0 ]; then
      SHA=$(git rev-parse --short HEAD)
      # A concurrent site-only commit is safe to integrate. If it changed any
      # checksum-bound CTAS code, keep the local data commit and let the next
      # scheduled run rebuild the release against that code before publishing.
      if python3 - <<'PY'
import hashlib
import json
from pathlib import Path

report = json.loads(Path("ctas/data/certification.json").read_text())
mismatches = []
for path, row in report.get("files", {}).items():
    if path.startswith("ctas/data/"):
        continue
    file_path = Path(path)
    expected = row.get("sha256") if isinstance(row, dict) else None
    actual = hashlib.sha256(file_path.read_bytes()).hexdigest() if file_path.is_file() else None
    if actual != expected:
        mismatches.append(path)
if mismatches:
    raise SystemExit("checksum-bound code changed: " + ", ".join(mismatches))
PY
      then
        say "remote update preserved checksum-bound CTAS code; retrying without force"
        PUSH_ERR=$(git push origin "$BRANCH" 2>&1)
        PUSH_STATUS=$?
      else
        PUSH_ERR="remote update changed checksum-bound CTAS code; the next scheduled run must rebuild the release"
        PUSH_STATUS=1
      fi
    else
      git rebase --abort >/dev/null 2>&1 || true
      PUSH_ERR="safe rebase onto origin/$BRANCH failed: $(printf '%s' "$REBASE_ERR" | tr '\n' ' ' | cut -c1-220)"
      PUSH_STATUS=1
    fi
  else
    PUSH_ERR="could not refresh origin/$BRANCH after the rejected push"
    PUSH_STATUS=1
  fi
fi
if [ "$PUSH_STATUS" -eq 0 ]; then
  date +%s >"$STAMP"
  say "published $SHA  ($COUNT candidates)"
else
  printf '%s\n' "$PUSH_ERR" >>"$LOG"
  say "push failed; commit $SHA stays local, nothing forced"
  say "git said: $(printf '%s' "$PUSH_ERR" | tr '\n' ' ' | cut -c1-300)"
  exit 1
fi
