#!/bin/bash
# Install the persistent, two-minute CTAS public-catalog publisher.
# The operational checkout lives outside macOS-protected Documents so launchd
# can run it after Terminal and Codex close.
set -uo pipefail

LABEL="io.github.jackmcguireastro.ctas-mirror"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AUTHORING_SITE=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SRC="$AUTHORING_SITE/scripts/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME_ROOT="$HOME/Library/Application Support/CTASPublisher"
RUNTIME_SITE="$RUNTIME_ROOT/site"
DB="$HOME/.codex/.chatgpt-projects/g-p-6a5d91be2e688191b7333527fcd488b3/data/soc.db"
LOG_DIR="$HOME/Library/Logs/ctas-mirror"
DOMAIN="gui/$(id -u)"

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }
info() { printf '        %s\n' "$*"; }

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f -- "$DEST"
  echo "Removed $LABEL. The recoverable runtime checkout remains at $RUNTIME_SITE."
  exit 0
fi

echo
echo "Installing the persistent CTAS publisher"
echo "========================================"

fail=0
[ -d "$AUTHORING_SITE/.git" ] && ok "authoring repo   $AUTHORING_SITE" || { bad "authoring repo not found"; fail=1; }
[ -f "$SRC" ] && ok "agent template  scripts/$LABEL.plist" || { bad "missing $SRC"; fail=1; }
[ -f "$DB" ] && ok "CTAS database   $(du -h "$DB" | cut -f1)" || { bad "database not found: $DB"; fail=1; }
command -v python3 >/dev/null && ok "python3         $(python3 -V 2>&1)" || { bad "python3 not found"; fail=1; }
command -v sqlite3 >/dev/null && ok "sqlite3         available" || { bad "sqlite3 not found"; fail=1; }
[ "$fail" -eq 0 ] || exit 1

REMOTE=$(git -C "$AUTHORING_SITE" remote get-url origin 2>/dev/null || true)
[ -n "$REMOTE" ] || { bad "authoring repo has no origin"; exit 1; }

echo
echo "Checking unattended GitHub access"
PUSHTEST=$(cd "$AUTHORING_SITE" && GIT_TERMINAL_PROMPT=0 \
  GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $HOME/.ssh/id_ed25519" \
  git push --dry-run origin main 2>&1)
if [ $? -eq 0 ]; then
  ok "unattended SSH push works"
else
  bad "unattended push failed"
  printf '%s\n' "$PUSHTEST" | sed 's/^/        /'
  exit 1
fi

mkdir -p "$RUNTIME_ROOT" "$HOME/Library/LaunchAgents" "$LOG_DIR"
if [ ! -d "$RUNTIME_SITE/.git" ]; then
  echo
  echo "Creating the dedicated publisher checkout"
  GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $HOME/.ssh/id_ed25519" \
    git clone --quiet --branch main --single-branch "$REMOTE" "$RUNTIME_SITE" \
    || { bad "could not create $RUNTIME_SITE"; exit 1; }
  ok "runtime checkout $RUNTIME_SITE"
else
  [ -z "$(git -C "$RUNTIME_SITE" status --porcelain)" ] \
    || { bad "existing runtime checkout is dirty; inspect $RUNTIME_SITE"; exit 1; }
  GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $HOME/.ssh/id_ed25519" \
    git -C "$RUNTIME_SITE" fetch --quiet origin main \
    || { bad "could not refresh runtime checkout"; exit 1; }
  git -C "$RUNTIME_SITE" merge --quiet --ff-only origin/main \
    || { bad "runtime checkout is not a clean fast-forward"; exit 1; }
  ok "runtime checkout current"
fi

sed "s|REPLACE_WITH_HOME|$HOME|g" "$SRC" > "$DEST" \
  || { bad "could not write $DEST"; exit 1; }
plutil -lint "$DEST" >/dev/null 2>&1 \
  && ok "agent written   ~/Library/LaunchAgents/$LABEL.plist" \
  || { bad "generated agent is malformed"; exit 1; }

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
rmdir "$LOG_DIR/.runner.lock.d" 2>/dev/null || true
if [ -s "$LOG_DIR/launchd.err.log" ] && grep -q 'Documents/GitHub.*Operation not permitted' "$LOG_DIR/launchd.err.log"; then
  mv "$LOG_DIR/launchd.err.log" "$LOG_DIR/launchd.err.pre-runtime-$(date -u '+%Y%m%dT%H%M%SZ').log"
  ok "archived legacy Documents/TCC errors"
fi
launchctl bootstrap "$DOMAIN" "$DEST" 2>/dev/null \
  || { bad "launchctl bootstrap failed"; exit 1; }
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
ok "agent loaded"

echo
echo "Running the background service once now"
BASELINE_RUNS=$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F'= ' '/runs =/{print $2; exit}')
BASELINE_RUNS=${BASELINE_RUNS:-0}
launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null \
  || { bad "could not start the agent"; exit 1; }
for _ in {1..30}; do
  AGENT_STATE=$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null || true)
  state=$(printf '%s\n' "$AGENT_STATE" | awk -F'= ' '/state =/{print $2; exit}')
  runs=$(printf '%s\n' "$AGENT_STATE" | awk -F'= ' '/runs =/{print $2; exit}')
  exit_code=$(printf '%s\n' "$AGENT_STATE" | awk -F'= ' '/last exit code =/{print $2; exit}')
  if [ "${runs:-0}" -gt "$BASELINE_RUNS" ] && [ "$state" != "running" ] && [ -n "$exit_code" ]; then break; fi
  sleep 2
done

AGENT_STATE=$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null || true)
if printf '%s\n' "$AGENT_STATE" | grep -q 'last exit code = 0'; then
  ok "first launchd run exited successfully"
else
  bad "first launchd run did not report exit 0"
  info "See $LOG_DIR/launchd.err.log and $LOG_DIR/runner.log"
  exit 1
fi

cat <<EOF

Done. CTAS now checks and publishes without an open Terminal.

  Schedule       every 120 seconds while this Mac is awake, online, and logged in
  Runtime        $RUNTIME_SITE
  Public page    https://jackmcguireastro.github.io/ctas.html
  Logs           $LOG_DIR
  Diagnose       $AUTHORING_SITE/scripts/diagnose_ctas_mirror.sh
  Turn it off    $AUTHORING_SITE/scripts/install_ctas_mirror.sh --uninstall

The runtime checkout contains only the public GitHub repository. The source
database remains local; no API key or secret manager is copied into the site.
EOF
