#!/bin/bash
# Read the WorldsIndex publisher state without printing credentials.
set -uo pipefail

LABEL="io.github.jackmcguireastro.worldsindex-mirror"
RUNTIME_SITE="$HOME/Library/Application Support/WorldsIndexPublisher/site"
SOURCE="${WORLDSINDEX_SOURCE:-$HOME/Documents/Codex/CTAS and WorldsIndex/WorldsIndex Development/work/worldsindex}"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/worldsindex-mirror"
DOMAIN="gui/$(id -u)"
h() { printf '\n===== %s =====\n' "$*"; }

h "1. environment"
sw_vers 2>&1 | tr '\n' ' '; echo
echo "shell: $SHELL   user: $(whoami)   uid: $(id -u)"
echo "node:    $(command -v node || echo MISSING)  $(node --version 2>&1)"
echo "npm:     $(command -v npm || echo MISSING)  $(npm --version 2>&1)"
echo "python3: $(command -v python3 || echo MISSING)  $(python3 -V 2>&1)"
echo "git:     $(command -v git || echo MISSING)  $(git --version 2>&1)"

h "2. installed six-hour agent"
if [ -f "$DEST" ]; then
  echo "present: $DEST"
  plutil -lint "$DEST" 2>&1
  echo "unresolved placeholders: $(grep -Ec 'REPLACE_WITH_(HOME|SOURCE)' "$DEST" || true)"
  echo "program: $(plutil -extract ProgramArguments.1 raw -o - "$DEST" 2>/dev/null)"
  echo "source: $(plutil -extract EnvironmentVariables.WORLDSINDEX_SOURCE raw -o - "$DEST" 2>/dev/null)"
  echo "interval: $(plutil -extract StartInterval raw -o - "$DEST" 2>/dev/null) seconds"
else
  echo "MISSING: $DEST"
fi

h "3. launchd state"
launchctl print "$DOMAIN/$LABEL" 2>&1 | \
  egrep -i 'state|program|last exit|pid |runs|path =|error|not find|interval' | head -40

h "4. operational inputs"
[ -r "$SOURCE/package.json" ] && echo "source readable: $SOURCE" || echo "source NOT READABLE: $SOURCE"
[ -r "$SOURCE/outputs/sync/latest-attempt.json" ] && \
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("last provider state:",d.get("state"),"at",d.get("completedAt",d.get("startedAt","unknown")))' "$SOURCE/outputs/sync/latest-attempt.json" \
  || echo "last provider receipt unavailable"
[ -r "$RUNTIME_SITE/.git/HEAD" ] && echo "runtime repo readable: $RUNTIME_SITE" || echo "runtime repo NOT READABLE"

h "5. runtime git state"
if [ -d "$RUNTIME_SITE/.git" ]; then
  git -C "$RUNTIME_SITE" --no-optional-locks log --oneline -3
  echo "origin: $(git -C "$RUNTIME_SITE" remote get-url origin 2>/dev/null)"
  echo "sync: $(git -C "$RUNTIME_SITE" rev-list --left-right --count origin/main...HEAD 2>/dev/null)"
  echo "working tree:"; git -C "$RUNTIME_SITE" --no-optional-locks status --porcelain
  if [ -f "$RUNTIME_SITE/worldsindex/data/manifest.json" ]; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("release:",d.get("generatedAt"),d.get("objectCount"),"objects",d.get("detailRecordCount"),"native rows")' "$RUNTIME_SITE/worldsindex/data/manifest.json"
  fi
fi

h "6. logs"
for file in "$LOG_DIR/runner.log" "$LOG_DIR/publish.log" "$LOG_DIR/launchd.err.log" "$LOG_DIR/launchd.out.log"; do
  echo "--- $file ---"
  [ -f "$file" ] && tail -n 20 "$file" || echo "(does not exist)"
done

h "7. unattended push path"
GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -i $HOME/.ssh/id_ed25519" \
  git -C "$RUNTIME_SITE" push --dry-run origin main >/dev/null 2>&1 \
  && echo "push dry-run passed" || echo "push dry-run FAILED"

h "done"
