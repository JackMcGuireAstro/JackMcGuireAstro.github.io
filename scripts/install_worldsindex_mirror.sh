#!/bin/bash
# Install the persistent WorldsIndex publisher: a two-minute launchd cycle that follows the
# local source files, with the full provider/test gate every hour.
set -uo pipefail

LABEL="io.github.jackmcguireastro.worldsindex-mirror"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AUTHORING_SITE=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
AUTHORING_SOURCE="${WORLDSINDEX_AUTHORING_SOURCE:-$HOME/Documents/Codex/CTAS and WorldsIndex/WorldsIndex Development/work/worldsindex}"
TEMPLATE="$AUTHORING_SITE/scripts/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME_ROOT="$HOME/Library/Application Support/WorldsIndexPublisher"
RUNTIME_SITE="$RUNTIME_ROOT/site"
RUNTIME_SOURCE="$RUNTIME_ROOT/source"
# WORLDSINDEX_SOURCE_MODE=copy   (default) launchd runs against an rsync copy of the editable
#                                source; required while that source lives under ~/Documents,
#                                ~/Desktop or ~/Downloads, which background agents cannot read.
#                                Promotions the agent makes land in the copy, not in git.
# WORLDSINDEX_SOURCE_MODE=direct launchd runs against the editable checkout itself, so the
#                                site follows every local change and promotions are committed
#                                into the real repository. Requires the checkout to live
#                                outside the TCC-protected folders.
SOURCE_MODE="${WORLDSINDEX_SOURCE_MODE:-copy}"
LOG_DIR="$HOME/Library/Logs/worldsindex-mirror"
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
echo "Installing the persistent WorldsIndex publisher"
echo "================================================"

fail=0
[ -d "$AUTHORING_SITE/.git" ] && ok "authoring repo   $AUTHORING_SITE" || { bad "authoring repo not found"; fail=1; }
[ -f "$TEMPLATE" ] && ok "agent template  scripts/$LABEL.plist" || { bad "missing $TEMPLATE"; fail=1; }
[ -f "$AUTHORING_SOURCE/package.json" ] && ok "ExoNexus source $AUTHORING_SOURCE" || { bad "source project not found: $AUTHORING_SOURCE"; fail=1; }
[ -x "$AUTHORING_SOURCE/scripts/run-source-monitor-noninteractive.sh" ] && ok "provider monitor executable" || { bad "provider monitor is unavailable"; fail=1; }
command -v node >/dev/null && ok "node            $(node --version 2>&1)" || { bad "node not found"; fail=1; }
command -v npm >/dev/null && ok "npm             $(npm --version 2>&1)" || { bad "npm not found"; fail=1; }
command -v python3 >/dev/null && ok "python3         $(python3 -V 2>&1)" || { bad "python3 not found"; fail=1; }
command -v git >/dev/null && ok "git             $(git --version 2>&1)" || { bad "git not found"; fail=1; }
command -v rsync >/dev/null && ok "rsync           available" || { bad "rsync not found"; fail=1; }
[ "$fail" -eq 0 ] || exit 1

REMOTE=$(git -C "$AUTHORING_SITE" remote get-url origin 2>/dev/null || true)
[ -n "$REMOTE" ] || { bad "authoring repo has no origin"; exit 1; }
NODE_BIN=$(dirname "$(command -v node)")

mkdir -p "$RUNTIME_ROOT" "$HOME/Library/LaunchAgents" "$LOG_DIR"
if [ ! -d "$RUNTIME_SITE/.git" ]; then
  echo
  echo "Creating the dedicated publisher checkout"
  GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $HOME/.ssh/id_ed25519" \
    git clone --quiet --branch main --single-branch "$REMOTE" "$RUNTIME_SITE" \
    || { bad "could not create $RUNTIME_SITE"; exit 1; }
  ok "runtime checkout $RUNTIME_SITE"
else
  NON_DATA_DIRTY=""
  while IFS= read -r line; do
    path=${line:3}
    case "$path" in worldsindex/data/*) ;; *) NON_DATA_DIRTY="$NON_DATA_DIRTY $path" ;; esac
  done < <(git -C "$RUNTIME_SITE" status --porcelain --untracked-files=all)
  [ -z "$NON_DATA_DIRTY" ] || { bad "runtime checkout has unexpected changes:$NON_DATA_DIRTY"; exit 1; }
  if [ -n "$(git -C "$RUNTIME_SITE" status --porcelain --untracked-files=all -- worldsindex/data)" ]; then
    git -C "$RUNTIME_SITE" stash push --include-untracked -m "WorldsIndex installer recovery $(date -u '+%Y%m%dT%H%M%SZ')" -- worldsindex/data >/dev/null \
      || { bad "could not preserve existing generated data"; exit 1; }
    ok "preserved unfinished generated data"
  fi
  GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $HOME/.ssh/id_ed25519" \
    git -C "$RUNTIME_SITE" fetch --quiet origin main \
    || { bad "could not refresh runtime checkout"; exit 1; }
  git -C "$RUNTIME_SITE" merge --quiet --ff-only origin/main \
    || { bad "runtime checkout is not a clean fast-forward"; exit 1; }
  ok "runtime checkout current"
fi

echo
echo "Checking unattended GitHub access"
PUSHTEST=$(cd "$RUNTIME_SITE" && GIT_TERMINAL_PROMPT=0 \
  GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $HOME/.ssh/id_ed25519" \
  git push --dry-run origin main 2>&1)
if [ $? -eq 0 ]; then
  ok "unattended SSH push works"
else
  bad "unattended push failed"
  printf '%s\n' "$PUSHTEST" | sed 's/^/        /'
  exit 1
fi

echo
case "$SOURCE_MODE" in copy|direct) ;; *) bad "WORLDSINDEX_SOURCE_MODE must be copy or direct"; exit 1 ;; esac
if [ "$SOURCE_MODE" = "direct" ]; then
  case "$AUTHORING_SOURCE" in
    "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
      bad "direct mode needs the source checkout outside ~/Documents, ~/Desktop and ~/Downloads (launchd cannot read them): $AUTHORING_SOURCE"
      info "move the checkout (for example to ~/Projects/) or use WORLDSINDEX_SOURCE_MODE=copy"
      exit 1 ;;
  esac
  [ -d "$AUTHORING_SOURCE/.git" ] || { bad "direct mode expects a git checkout at $AUTHORING_SOURCE"; exit 1; }
  RUNTIME_SOURCE="$AUTHORING_SOURCE"
  echo "Using the editable checkout directly (no operational copy)"
  ok "runtime source $RUNTIME_SOURCE"
else
echo "Refreshing the launchd-readable ExoNexus mirror"
SOURCE_WAS_PRESENT=0
[ -f "$RUNTIME_SOURCE/package.json" ] && SOURCE_WAS_PRESENT=1
RSYNC_EXCLUDES=(
  --exclude='/.git/'
  --exclude='/.next/'
  --exclude='/.vinext/'
  --exclude='/.wrangler/'
  --exclude='/dist/'
  --exclude='* 2.*'
)
if [ "$SOURCE_WAS_PRESENT" -eq 1 ]; then
  # Preserve the operational receipt chain that launchd advances between
  # installer runs; all code, contracts, and frozen inputs still mirror the
  # editable source exactly.
  RSYNC_EXCLUDES+=(--exclude='outputs/sync/' --exclude='public/data/sync/')
fi
mkdir -p "$RUNTIME_SOURCE"
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$AUTHORING_SOURCE/" "$RUNTIME_SOURCE/" \
  || { bad "could not refresh $RUNTIME_SOURCE"; exit 1; }
[ -f "$RUNTIME_SOURCE/package.json" ] || { bad "operational source mirror is incomplete"; exit 1; }
[ -f "$RUNTIME_SOURCE/node_modules/tsx/dist/loader.mjs" ] || { bad "operational Node dependencies are incomplete"; exit 1; }
if [ -f "$RUNTIME_SOURCE/.env.local" ]; then
  chmod 600 "$RUNTIME_SOURCE/.env.local" || { bad "could not protect the local credential file"; exit 1; }
fi
ok "operational source $RUNTIME_SOURCE"
info "note: in copy mode the agent's promotions and receipts stay in this copy; rerun the installer to refresh code, or use direct mode"
fi

python3 - "$TEMPLATE" "$DEST" "$HOME" "$RUNTIME_SOURCE" "$NODE_BIN" <<'PY'
from pathlib import Path
import sys

template, destination, home, source, node_bin = sys.argv[1:]
text = Path(template).read_text()
text = (text.replace("REPLACE_WITH_HOME", home)
            .replace("REPLACE_WITH_SOURCE", source)
            .replace("REPLACE_WITH_NODE_BIN", node_bin))
Path(destination).write_text(text)
PY
[ $? -eq 0 ] || { bad "could not write $DEST"; exit 1; }
plutil -lint "$DEST" >/dev/null 2>&1 \
  && ok "agent written   ~/Library/LaunchAgents/$LABEL.plist" \
  || { bad "generated agent is malformed"; exit 1; }

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
rmdir "$LOG_DIR/.runner.lock.d" "$LOG_DIR/.publish.lock.d" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST" 2>/dev/null \
  || { bad "launchctl bootstrap failed"; exit 1; }
ok "agent loaded"

echo
echo "Starting the first supervised run"
AGENT_STATE=""
for _ in {1..10}; do
  AGENT_STATE=$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null || true)
  if printf '%s\n' "$AGENT_STATE" | grep -q 'state = running' \
     || printf '%s\n' "$AGENT_STATE" | grep -q 'last exit code = 0'; then
    break
  fi
  sleep 1
done
if printf '%s\n' "$AGENT_STATE" | grep -q 'state = running'; then
  ok "first launchd run is active"
elif printf '%s\n' "$AGENT_STATE" | grep -q 'last exit code = 0'; then
  ok "first launchd run exited successfully"
else
  bad "first launchd run did not start cleanly"
  info "See $LOG_DIR/launchd.err.log and $LOG_DIR/runner.log"
  exit 1
fi

cat <<EOF

Done. WorldsIndex now checks locally and publishes without ChatGPT or Codex scheduling.

  Schedule       every 2 minutes: follow the local source files and publish when they changed
                 and every static gate passed; every hour: provider monitor, promotion gates,
                 test suite, build, atlas regeneration — while this Mac is awake, online, logged in
  Source mode    $SOURCE_MODE
  Editable source $AUTHORING_SOURCE
  Runtime source  $RUNTIME_SOURCE
  Runtime        $RUNTIME_SITE
  Public page    https://jackmcguireastro.github.io/worldsindex/
  Logs           $LOG_DIR
  Diagnose       $AUTHORING_SITE/scripts/diagnose_worldsindex_mirror.sh
  Turn it off    $AUTHORING_SITE/scripts/install_worldsindex_mirror.sh --uninstall

Provider receipts may update automatically. Catalog measurements remain on the
last reconciled snapshot until the applicable source-specific promotion gate passes.
EOF
