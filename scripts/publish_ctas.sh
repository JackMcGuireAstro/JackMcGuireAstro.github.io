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
# Return every generated file under ctas/data to HEAD: tracked modifications,
# staged changes, deletions, and untracked new pages or parts. Always address
# the directory, never the expanded PUBLIC_FILES list: `git restore -- a b c`
# restores nothing at all when any listed path is absent from HEAD (a freshly
# generated part or page), which on 2026-09-24 left ~4,000 regenerated files
# behind after a refused release and stalled the publisher for two days.
discard_generated_files() {
  git -C "$SITE" restore --source=HEAD --staged --worktree -- ctas/data 2>/dev/null || true
  git -C "$SITE" clean -fdq -- ctas/data 2>/dev/null || true
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$SITE_READY" -eq 1 ]; then
    discard_generated_files
  fi
  if [ -n "$PUBLISH_DB" ]; then
    rm -f -- "$PUBLISH_DB" "$PUBLISH_DB-journal" "$PUBLISH_DB-wal" "$PUBLISH_DB-shm"
  fi
  rmdir "$LOCKDIR" 2>/dev/null
  return "$status"
}
trap cleanup EXIT

[ -d "$SITE" ] || die "website repo not found: $SITE"
[ -f "$DB" ]   || die "CTAS database not found: $DB"
cd "$SITE"     || die "cannot enter $SITE"
SITE_READY=1

# ------------------------------------------------------------- rate guard
# Checked before the frozen snapshot, so a run that is going to wait out the floor
# does not take one (where cloning is unavailable it is a full database copy).
if [ "$FORCE" -eq 0 ] && [ "$MIN_INTERVAL" -gt 0 ] && [ -f "$STAMP" ]; then
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  age=$(( $(date +%s) - last ))
  if [ "$age" -lt "$MIN_INTERVAL" ]; then
    say "last publish ${age}s ago; waiting out the ${MIN_INTERVAL}s floor"
    exit 0
  fi
fi

# ------------------------------------------------------ published data branch
# Releases live on their own branch as one latest-only commit (scripts/data_branch.sh);
# main holds only code. The exporter reads the previous release from that commit
# through the store's objects, and every run starts from an empty generated tree.
DATA_BRANCH="${CTAS_DATA_BRANCH:-ctas-data}"
DATA_STORE="$SITE/.git/ctas-data-store"
with_release() { GIT_ALTERNATE_OBJECT_DIRECTORIES="$DATA_STORE/objects" "$@"; }
if RETRY_NOTE=$(bash scripts/data_branch.sh retry "$DATA_STORE" "$DATA_BRANCH"); then
  [ -z "$RETRY_NOTE" ] || say "$RETRY_NOTE"
else
  say "the release kept from a failed push is still unpublished: $RETRY_NOTE"
fi
DATA_TIP=$(bash scripts/data_branch.sh sync "$DATA_STORE" "$DATA_BRANCH") \
  || die "could not read the published $DATA_BRANCH release: $DATA_TIP"
git clean -fdqX -- ctas/data || die "could not clear the previous generated files"

# Freeze one transactionally consistent SQLite view for the whole release.
# The live pipeline may continue writing to the canonical database while link
# checks and assurance artifacts are generated, but no publication mixes two
# database states.
#
# On APFS the frozen view is a copy-on-write clone (clonefile) of the database and
# its WAL, taken inside one read transaction on the live file. The clone shares
# every block with the original, so a release no longer writes a full copy of the
# multi-gigabyte database (30+ times a day); only blocks either side changes while
# the release is built take new space, and they are freed with the clone. Where
# cloning is unavailable the SQLite backup API copies the pinned state instead.
# CTAS_SNAPSHOT_METHOD=backup forces the copy.
SNAPSHOT_DIR="${CTAS_SNAPSHOT_DIR:-$SITE/.git}"
# a run killed outright (power loss, kill -9) cannot remove its snapshot; runs never
# last an hour (the lock is reclaimed after 30 minutes), so older ones are debris
for folder in "$SNAPSHOT_DIR" "${TMPDIR:-/tmp}"; do
  find "$folder" -maxdepth 1 -name 'ctas-publish.*' -type f -mmin +60 -exec rm -f {} + 2>/dev/null || true
done
PUBLISH_DB=$(mktemp "$SNAPSHOT_DIR/ctas-publish.XXXXXX") \
  || die "could not allocate a temporary database snapshot"
# Establish an explicit read transaction before cloning or copying. In WAL mode it
# stops checkpoints from moving any state newer than this snapshot into the main
# file and stops the WAL from being reset, so the main file and WAL cloned in turn
# always recover to one committed state; in rollback mode it keeps writers out of
# the main file. It also stops SQLite restarting a long backup on every live
# ingestion write. WAL writers continue throughout either way.
if ! CTAS_SNAPSHOT_METHOD="${CTAS_SNAPSHOT_METHOD:-auto}" python3 - "$DB" "$PUBLISH_DB" >>"$LOG" 2>&1 <<'PYCTASBACKUP'
from contextlib import closing
from pathlib import Path
import ctypes
import errno
import os
import shutil
import sqlite3
import sys
import time

source_path = Path(sys.argv[1]).resolve()
destination_path = Path(sys.argv[2])
method = os.environ.get("CTAS_SNAPSHOT_METHOD", "auto")
complete = False
started = time.monotonic()
DEADLINE = 600
UNSUPPORTED = {errno.ENOTSUP, errno.EXDEV, errno.ENOSYS, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}


def sidecar(path, suffix):
    return Path(str(path) + suffix)


def remove_snapshot():
    for suffix in ("", "-journal", "-wal", "-shm"):
        sidecar(destination_path, suffix).unlink(missing_ok=True)


def clone(source, target):
    """clonefile(2): True when cloned, False when this system or volume cannot clone."""
    function = None
    for library in (None, "/usr/lib/libSystem.B.dylib"):
        try:
            function = ctypes.CDLL(library, use_errno=True).clonefile
            break
        except (AttributeError, OSError):
            continue
    if function is None:
        return False
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32)
    function.restype = ctypes.c_int
    if function(os.fsencode(str(source)), os.fsencode(str(target)), 1) == 0:  # CLONE_NOFOLLOW
        return True
    code = ctypes.get_errno()
    if code in UNSUPPORTED:
        return False
    raise OSError(code, "clonefile failed: " + os.strerror(code), str(target))


def past_deadline():
    return time.monotonic() - started > DEADLINE


def settle_clone():
    """Recover the cloned WAL into the clone and make it a standalone rollback-journal
    database, then check its structure. Returns its event count."""
    with closing(sqlite3.connect(str(destination_path), timeout=15)) as frozen:
        frozen.set_progress_handler(past_deadline, 100000)
        frozen.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        frozen.execute("PRAGMA journal_mode=DELETE")
        verdict = frozen.execute("PRAGMA quick_check").fetchall()
        if verdict != [("ok",)]:
            raise ValueError("Cloned database failed its structural check: " + str(verdict[:3]))
        return frozen.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def pin_and_freeze(allow_clone):
    """Pin one read transaction on the live file and freeze it: clone when allowed and
    possible, otherwise copy through the backup API. Returns (cloned, pinned events)."""
    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=15)) as source:
        source.execute("BEGIN")
        candidate_count = source.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        wal_mode = source.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        cloned = False
        if allow_clone:
            destination_path.unlink(missing_ok=True)  # clonefile creates its target
            cloned = clone(source_path, destination_path)
            source_wal = sidecar(source_path, "-wal")
            if cloned and wal_mode and source_wal.exists() and not clone(source_wal, sidecar(destination_path, "-wal")):
                cloned = False
        if not cloned:
            remove_snapshot()
            expected_bytes = source.execute("PRAGMA page_count").fetchone()[0] * source.execute("PRAGMA page_size").fetchone()[0]
            if shutil.disk_usage(destination_path.parent).free < expected_bytes + 64 * 1024 * 1024:
                raise OSError("Insufficient free disk space for the complete frozen database snapshot")
            with closing(sqlite3.connect(str(destination_path), timeout=15)) as destination:
                def progress(status, remaining, total):
                    if past_deadline():
                        raise TimeoutError("Frozen database backup exceeded its 600-second deadline")
                source.backup(destination, pages=4096, progress=progress, sleep=0.05)
                copied_count = destination.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if copied_count != candidate_count:
                    raise ValueError("Frozen database event count differs from the pinned source")
        source.rollback()
    return cloned, candidate_count


try:
    if source_path == destination_path.resolve() or destination_path.is_symlink():
        raise ValueError("Snapshot destination must be distinct from the source and not redirected")
    if method not in ("auto", "backup"):
        raise ValueError("CTAS_SNAPSHOT_METHOD must be auto or backup")
    cloned, candidate_count = pin_and_freeze(method == "auto")
    if cloned:
        # The clone holds the pinned state plus any transactions committed to the WAL
        # before it was cloned: one committed state, which is all the release needs.
        try:
            frozen_count = settle_clone()
        except (sqlite3.DatabaseError, ValueError) as error:
            if past_deadline():
                raise TimeoutError("Frozen database clone check exceeded its 600-second deadline") from error
            print(f"Cloned snapshot unusable ({error}); taking a full copy instead", file=sys.stderr)
            remove_snapshot()
            cloned, candidate_count = pin_and_freeze(False)
    if cloned:
        print(f"Frozen read-only database snapshot (copy-on-write clone): {frozen_count} events "
              f"({candidate_count} when pinned) in {time.monotonic() - started:.1f}s")
    else:
        print(f"Frozen read-only database snapshot (full copy): {candidate_count} events in {time.monotonic() - started:.1f}s")
    complete = True
except (sqlite3.Error, OSError, ValueError, TimeoutError) as error:
    print(f"Database snapshot failed: {error}", file=sys.stderr)
    raise SystemExit(1)
finally:
    if not complete and source_path != destination_path.resolve():
        remove_snapshot()
PYCTASBACKUP
then
  die "could not create a complete pinned database snapshot; nothing exported or committed"
fi
[ -s "$PUBLISH_DB" ] || die "database snapshot is empty"

# ----------------------------------------------------------------- export
with_release python3 scripts/export_ctas_snapshot.py --database "$PUBLISH_DB" --output-dir ctas/data \
  --release-base-ref "$DATA_TIP" >>"$LOG" 2>&1 \
  || die "export failed; nothing committed"
python3 scripts/check_ctas_links.py --catalog-index ctas/data/catalog-index.json \
  --candidate-manifest ctas/data/candidate-chunks/manifest.json \
  --source-universe ctas/data/source-universe.json --output ctas/data/link-health.json >>"$LOG" 2>&1 \
  || die "public link validation failed; nothing committed"
# Rebuild once so the verification report binds the current link-health artifact and
# its catalog-content checksum. The exported scientific rows are deterministic.
with_release python3 scripts/export_ctas_snapshot.py --database "$PUBLISH_DB" --output-dir ctas/data \
  --release-base-ref "$DATA_TIP" >>"$LOG" 2>&1 \
  || die "verification-report rebuild failed; nothing committed"

# ----------------------------------------------------------- collect detail files
# The completed manifest is the only source of root/part paths. Overflow parts
# grow with public evidence, while the 4096 stable dossier URLs remain fixed.
DETAIL_FILES=$(python3 - <<'PYCTASDETAIL'
import hashlib
import json
import re
from pathlib import Path

root = Path.cwd().resolve()
manifest = json.loads(Path("ctas/data/candidate-chunks/manifest.json").read_text())
chunks, parts = manifest.get("chunks"), manifest.get("parts", [])
if not isinstance(chunks, list) or len(chunks) != manifest.get("chunk_count"):
    raise ValueError("Detail root count differs from manifest")
if not isinstance(parts, list):
    raise ValueError("Detail parts must be a list")
expected = [f"ctas/data/candidate-chunks/{index:03x}.json" for index in range(4096)]
if [row.get("path") for row in chunks] != expected:
    raise ValueError("Detail roots must be the exact ordered 000..fff set")
part_paths = [row.get("path") for row in parts]
if any(not isinstance(path, str) for path in part_paths):
    raise ValueError("Detail part path must be a string")
if part_paths != sorted(set(part_paths)):
    raise ValueError("Detail part paths must be unique and ordered")
part_metadata = {row["path"]: row for row in parts}
used_parts = set()
paths = []

def read_bound(row, pattern):
    relative = row["path"]
    if not isinstance(relative, str) or not re.fullmatch(pattern, relative):
        raise ValueError("Unexpected detail artifact path")
    target = root / relative
    if target.is_symlink() or target.resolve() != target or not target.is_file():
        raise ValueError("Detail artifact is missing or redirected: " + relative)
    raw = target.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Detail artifact exceeds the request budget: " + relative)
    if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("Detail artifact integrity mismatch: " + relative)
    paths.append(relative)
    return json.loads(raw)

for metadata in chunks:
    document = read_bound(metadata, r"ctas/data/candidate-chunks/[0-9a-f]{3}\.json")
    bucket = Path(metadata["path"]).stem
    if document.get("bucket") != bucket or document.get("candidate_count") != metadata.get("candidate_count"):
        raise ValueError("Detail root metadata mismatch: " + bucket)
    if document.get("schema") == "ctas.candidate-chunk-parts@1.0.0":
        references = document.get("parts")
        if not isinstance(references, list) or not references:
            raise ValueError("Detail descriptor has no parts: " + bucket)
        fragments = []
        for index, reference in enumerate(references, 1):
            relative = reference.get("path")
            if relative != f"ctas/data/candidate-chunks/{bucket}.part-{index:06d}.json":
                raise ValueError("Detail descriptor parts must be consecutive in their own bucket")
            declared = part_metadata.get(relative)
            if declared is None or any(reference.get(key) != declared.get(key) for key in ("path", "bytes", "sha256")):
                raise ValueError("Descriptor and manifest disagree about a detail part")
            if relative in used_parts:
                raise ValueError("Detail part is referenced more than once")
            part = read_bound(declared, r"ctas/data/candidate-chunks/[0-9a-f]{3}\.part-[0-9]{6}\.json")
            if part.get("schema") != "ctas.candidate-json-part@1.0.0" or part.get("bucket") != bucket or part.get("part") != index:
                raise ValueError("Detail part identity mismatch")
            fragment = part.get("json_fragment")
            if not isinstance(fragment, str):
                raise ValueError("Detail part fragment must be text")
            fragments.append(fragment)
            used_parts.add(relative)
        assembled = "".join(fragments).encode("utf-8")
        if len(assembled) != document.get("assembled_bytes") or hashlib.sha256(assembled).hexdigest() != document.get("assembled_sha256"):
            raise ValueError("Reconstructed detail root integrity mismatch")
        document = json.loads(assembled)
    if document.get("schema") != "ctas.public-candidate-chunk@1.0.0" or document.get("bucket") != bucket:
        raise ValueError("Unsupported detail root schema or bucket")
    rows = document.get("candidates")
    if not isinstance(rows, list) or len(rows) != metadata.get("candidate_count") or document.get("candidate_count") != len(rows):
        raise ValueError("Detail root candidate count mismatch")
if used_parts != set(part_metadata):
    raise ValueError("Manifest contains an unreachable detail part")
print("\n".join(paths))
PYCTASDETAIL
) || die "detail manifest validation failed; nothing committed"
while IFS= read -r detail; do
  [ -n "$detail" ] && PUBLIC_FILES+=("$detail")
done <<<"$DETAIL_FILES"

# --------------------------------------------------------- collect catalog pages
# Discover only the completed export's checksum-bound pages, never yesterday's
# directory contents. Fresh checkouts and a growing catalog may have no pages
# until export finishes. Command substitution propagates validation failures.
CATALOG_PAGE_FILES=$(python3 - <<'PYCTASPAGES'
import hashlib
import json
import re
from pathlib import Path

root = Path.cwd().resolve()
manifest = json.loads(Path("ctas/data/catalog-pages/manifest.json").read_text())
pages = manifest["pages"]
if not isinstance(pages, list) or len(pages) != manifest["page_count"]:
    raise ValueError("Complete-catalog page count differs from manifest")
paths, candidates = [], 0
for index, row in enumerate(pages, 1):
    relative = row["path"]
    if not isinstance(relative, str) or not re.fullmatch(r"ctas/data/catalog-pages/[0-9]{4}\.json", relative):
        raise ValueError("Unexpected complete-catalog page path")
    if relative != f"ctas/data/catalog-pages/{index:04d}.json" or row["page"] != index:
        raise ValueError("Complete-catalog pages must be unique and consecutive")
    target = root / relative
    if target.is_symlink() or target.resolve() != target or not target.is_file():
        raise ValueError("Complete-catalog page is missing or is a redirected path")
    raw = target.read_bytes()
    if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("Complete-catalog page integrity mismatch: " + relative)
    candidate_rows = json.loads(raw)["candidate_rows"]
    if not isinstance(candidate_rows, list) or len(candidate_rows) != row["candidate_count"]:
        raise ValueError("Complete-catalog row count mismatch: " + relative)
    candidates += len(candidate_rows)
    paths.append(relative)
if candidates != manifest["candidate_count"]:
    raise ValueError("Complete-catalog candidate count differs from manifest")
print("\n".join(paths))
PYCTASPAGES
) || die "complete-catalog page validation failed; nothing committed"
while IFS= read -r page; do
  [ -n "$page" ] && PUBLIC_FILES+=("$page")
done <<<"$CATALOG_PAGE_FILES"

# ------------------------------------------------- retire superseded artifacts
# The partition width and the first-screen artifact can change between code
# releases. Any previously published data file the current manifest no longer
# declares would otherwise stay on the site forever, serving a stale dossier at
# a live URL. Retire exactly those, and add them to the allowlist so the
# deletion is committed under the same explicit rule as everything else.
RETIRED=$(python3 - <<'PYRETIRE'
import json
import re
import subprocess
from pathlib import Path

manifest = json.loads(Path("ctas/data/candidate-chunks/manifest.json").read_text())
pages = json.loads(Path("ctas/data/catalog-pages/manifest.json").read_text())
current = {row["path"] for row in manifest.get("chunks", [])}
current |= {row["path"] for row in manifest.get("parts", [])}
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
retired = sorted(set(tracked) - current)
for path in retired:
    if not re.fullmatch(r"ctas/data/(?:candidate-chunks/[0-9a-f]{2,3}(?:\.part-[0-9]{6})?\.json|catalog-pages/[0-9]{4}\.json|catalog-bootstrap\.json)", path):
        raise ValueError("Refusing to retire an unexpected tracked path: " + path)
    target = Path(path)
    if target.is_symlink() or target.absolute().resolve() != target.absolute():
        raise ValueError("Refusing to retire a redirected artifact: " + path)
for path in retired:
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
             scripts/test_ctas_astro_evidence.py scripts/test_ctas_browser.py \
             scripts/test_ctas_chunks.py scripts/test_ctas_publisher_recovery.py; do
  python3 "$suite" >>"$LOG" 2>&1 || die "$suite failed against the generated release; nothing committed"
done
# Compare every exported ingest score/factor record with this cycle's frozen DB.
python3 scripts/test_ctas_ingest_provenance.py --database "$PUBLISH_DB" --data-dir ctas/data >>"$LOG" 2>&1 \
  || die "ingest-score provenance differs from the frozen database; nothing committed"
# The catalog model is the browser's copy of the reader-facing rules, so it is
# checked in a JavaScript runtime. A publisher without one is reported rather
# than blocked: the Python suites above already cover the published artifacts,
# and an absent interpreter is an environment fact, not a failing assertion.
NODE=$(python3 scripts/ctas_node.py 2>/dev/null || true)
if [ -n "$NODE" ]; then
  "$NODE" scripts/test_ctas_catalog_model.js >>"$LOG" 2>&1 \
    || die "catalog-model assertions failed against the generated release; nothing committed"
  "$NODE" scripts/test_ctas_chunk_loader.js >>"$LOG" 2>&1 \
    || die "detail-part loader assertions failed; nothing committed"
else
  say "no JavaScript runtime on this publisher; catalog-model assertions were not run"
fi


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
      discard_generated_files
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
HEAD_META=$(git --git-dir="$DATA_STORE" show "$DATA_TIP:ctas/data/status.json" 2>/dev/null | python3 -c '
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
HEAD_CODE_BINDING=$(git --git-dir="$DATA_STORE" show "$DATA_TIP:ctas/data/certification.json" 2>/dev/null | python3 -c '
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
# A release kept from a failed push was retried above, so nothing is pending here.
PENDING_CTAS_COMMIT=0

if [ "$CURRENT_STATE" = "$HEAD_STATE" ] && [ "$PENDING_CTAS_COMMIT" -eq 0 ] \
   && [ "$CODE_BINDING_CHANGED" -eq 0 ]; then
  case "$HEAD_PUBLISHED_EPOCH" in
    ''|*[!0-9]*) HEARTBEAT_AGE=$HEARTBEAT_INTERVAL ;;
    *) HEARTBEAT_AGE=$((NOW_EPOCH - HEAD_PUBLISHED_EPOCH)) ;;
  esac
  if [ "$FORCE" -eq 0 ] && [ "$HEARTBEAT_AGE" -ge 0 ] && [ "$HEARTBEAT_AGE" -lt "$HEARTBEAT_INTERVAL" ]; then
    say "publication state unchanged; next freshness heartbeat in $((HEARTBEAT_INTERVAL - HEARTBEAT_AGE))s"
    discard_generated_files
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

COUNT=$(python3 -c "import json;print(json.load(open('ctas/data/catalog-index.json'))['candidate_count'])" 2>/dev/null || echo "?")

if [ "$DRY" -eq 1 ]; then
  say "--dry-run: $COUNT candidates; would publish ${#PUBLIC_FILES[@]} allowlisted public CTAS artifacts to $DATA_BRANCH"
  discard_generated_files
  exit 0
fi

# ----------------------------------------------------------------- publish
# Replace the data branch with one commit holding exactly the allowlisted release
# files (retired paths simply are not listed). main is never committed to or
# force-pushed here; only this publisher writes $DATA_BRANCH.
RELEASE_LIST=$(mktemp "${TMPDIR:-/tmp}/ctas-release-files.XXXXXX") || die "could not list the release files"
for public_file in "${PUBLIC_FILES[@]}"; do
  [ -f "$public_file" ] && printf '%s\n' "$public_file"
done >"$RELEASE_LIST"
PUBLISH_OUTPUT=$(bash scripts/data_branch.sh publish "$DATA_STORE" "$DATA_BRANCH" \
  "CTAS data: $COUNT candidates ($(date -u '+%Y-%m-%d %H:%M UTC'))" "$RELEASE_LIST")
PUBLISH_STATUS=$?
rm -f "$RELEASE_LIST"
case "$PUBLISH_STATUS" in
  0)
    date +%s >"$STAMP"
    SHA=$(printf '%s' "$PUBLISH_OUTPUT" | awk '{print substr($2, 1, 11)}')
    say "published $SHA  ($COUNT candidates) to $DATA_BRANCH"
    ;;
  10)
    say "public artifacts already match the published $DATA_BRANCH release; nothing to publish"
    ;;
  *)
    printf '%s\n' "$PUBLISH_OUTPUT" >>"$LOG"
    say "push failed; the release is kept for the next run and nothing was forced"
    say "git said: $(printf '%s' "$PUBLISH_OUTPUT" | tr '\n' ' ' | cut -c1-300)"
    exit 1
    ;;
esac
