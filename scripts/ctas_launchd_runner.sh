#!/bin/bash
# Synchronize the dedicated CTAS publisher checkout, then publish one frozen
# database snapshot. This checkout contains only public repository state.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1
export GIT_TERMINAL_PROMPT=0
# launchd supplies the identity selection; append bounded network options so
# a stalled fetch cannot hold the runner lock indefinitely.
GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh} -o BatchMode=yes -o ConnectTimeout=15 -o ConnectionAttempts=2 -o ServerAliveInterval=10 -o ServerAliveCountMax=2"
export GIT_SSH_COMMAND

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SITE="${CTAS_SITE:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}"
DB="${CTAS_DB:-$HOME/.codex/.chatgpt-projects/g-p-6a5d91be2e688191b7333527fcd488b3/data/soc.db}"
BRANCH="${CTAS_BRANCH:-main}"
LOG_DIR="${CTAS_LOG_DIR:-$HOME/Library/Logs/ctas-mirror}"
RUNNER_LOG="$LOG_DIR/runner.log"
RUNNER_LOCK="$LOG_DIR/.runner.lock.d"

mkdir -p "$LOG_DIR"
say() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*" >>"$RUNNER_LOG"; printf '%s\n' "$*"; }
die() { say "FAIL  $*"; exit 1; }

if ! mkdir "$RUNNER_LOCK" 2>/dev/null; then
  if [ -n "$(find "$RUNNER_LOCK" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then
    rmdir "$RUNNER_LOCK" 2>/dev/null
    mkdir "$RUNNER_LOCK" 2>/dev/null || { say "another scheduled run is active; skipping"; exit 0; }
    say "reclaimed a stale scheduled-run lock"
  else
    say "another scheduled run is active; skipping"
    exit 0
  fi
fi
trap 'rmdir "$RUNNER_LOCK" 2>/dev/null' EXIT

[ -d "$SITE/.git" ] || die "dedicated publisher checkout is missing: $SITE"
[ -f "$DB" ] || die "CTAS database is missing: $DB"
cd "$SITE" || die "cannot enter dedicated publisher checkout"

CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
[ "$CURRENT_BRANCH" = "$BRANCH" ] || die "publisher checkout must remain on $BRANCH (found ${CURRENT_BRANCH:-detached})"

# Only generated public data may be dirty in the operational checkout. Preserve
# such files in a named stash before syncing; the next export reproduces them.
NON_DATA_DIRTY=""
while IFS= read -r line; do
  path=${line:3}
  if ! [[ "$path" =~ ^ctas/data/((live-summary|catalog-index|catalog-bootstrap|source-matrix-patterns|alias-index|status|source-universe|release-history|link-health|certification)\.json|candidate-chunks/(manifest|[0-9a-f]{2,3}(\.part-[0-9]{6})?)\.json|catalog-pages/(manifest|[0-9]{4})\.json|research/(manifest\.json|events\.(csv|vot)|aliases\.csv|sources\.csv|tom-targets\.csv))$ ]]; then
    NON_DATA_DIRTY="$NON_DATA_DIRTY $path"
  fi
done < <(git status --porcelain --untracked-files=all)
[ -z "$NON_DATA_DIRTY" ] || die "unexpected non-generated changes in publisher checkout:$NON_DATA_DIRTY"
if [ -n "$(git status --porcelain --untracked-files=all -- ctas/data)" ]; then
  git stash push --include-untracked -m "CTAS generated recovery $(date -u '+%Y%m%dT%H%M%SZ')" -- ctas/data >/dev/null \
    || die "could not preserve generated files before sync"
  say "preserved unfinished generated files before repository sync"
fi

# ------------------------------------------------- recover generated-only commits
# JSON releases must be regenerated against current code after remote changes.
# Preserve every unpublished commit on a recovery ref before replacing only
# this dedicated checkout's generated-data branch. Any code/user commit stops.
recover_generated_commits() {
  python3 - "$BRANCH" "$1" <<'PYCTASRECOVER'
import datetime
import re
import subprocess
import sys

def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

branch, mode = sys.argv[1:]
if git("symbolic-ref", "--quiet", "--short", "HEAD") != branch:
    raise SystemExit("Refusing recovery outside the configured publisher branch")
if git("status", "--porcelain=v1", "--untracked-files=all"):
    raise SystemExit("Refusing recovery while the dedicated checkout has unfinished changes")
remote = "refs/remotes/origin/" + branch
remote_head = git("rev-parse", remote)
head = git("rev-parse", "HEAD")
commits = git("rev-list", remote + "..HEAD").splitlines()
if not commits:
    raise SystemExit("No unpublished generated data commits to recover")
allowed = re.compile(
    r"ctas/data/(?:"
    r"(?:live-summary|catalog-index|catalog-bootstrap|source-matrix-patterns|alias-index|"
    r"status|source-universe|release-history|link-health|certification)\.json|"
    r"candidate-chunks/(?:manifest|[0-9a-f]{2,3}(?:\.part-[0-9]{6})?)\.json|"
    r"catalog-pages/(?:manifest|[0-9]{4})\.json|"
    r"research/(?:manifest\.json|events\.(?:csv|vot)|aliases\.csv|sources\.csv|tom-targets\.csv))"
)
for commit in commits:
    parents = git("show", "-s", "--format=%P", commit).split()
    subject = git("show", "-s", "--format=%s", commit)
    if len(parents) != 1 or not subject.startswith("CTAS data: "):
        raise SystemExit("Refusing recovery of a non-automatic or merge commit: " + commit)
    names = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-z", "-r", commit],
        check=True, capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    paths = [path for path in names if path]
    if not paths or any(not allowed.fullmatch(path) for path in paths):
        raise SystemExit("Refusing recovery of non-generated paths in commit " + commit)
if mode == "verify":
    print(head)
elif mode == "recover":
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    recovery = "refs/ctas-recovery/" + stamp + "-" + head[:12]
    git("update-ref", recovery, head, "")
    if git("rev-parse", recovery) != head:
        raise SystemExit("Could not verify the preserved recovery reference")
    git("reset", "--hard", remote_head)
    print(recovery)
else:
    raise SystemExit("Unsupported recovery mode")
PYCTASRECOVER
}

git fetch --quiet origin "$BRANCH" || die "could not fetch origin/$BRANCH"
if git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
  git merge --quiet --ff-only "origin/$BRANCH" || die "could not fast-forward publisher checkout"
elif git merge-base --is-ancestor "origin/$BRANCH" HEAD 2>/dev/null; then
  recover_generated_commits verify >/dev/null \
    || die "unpublished commits are not exclusively automatic public CTAS data; checkout preserved"
  say "unpublished generated CTAS data is retained for the publisher to amend or push"
else
  RECOVERY_REF=$(recover_generated_commits recover) \
    || die "diverged checkout contains unrecognized work; preserved unchanged for inspection"
  say "preserved unpublished generated data at $RECOVERY_REF; rebuilding on current public code"

fi

env CTAS_SITE="$SITE" CTAS_DB="$DB" CTAS_BRANCH="$BRANCH" CTAS_LOG_DIR="$LOG_DIR" \
  /bin/bash "$SITE/scripts/publish_ctas.sh"
exit $?
