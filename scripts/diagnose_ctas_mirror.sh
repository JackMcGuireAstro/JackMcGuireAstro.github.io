#!/bin/bash
# Dump everything needed to work out why the CTAS mirror is not publishing.
# Prints no secrets. Paste the whole output back.
#   bash ~/Documents/GitHub/JackMcGuireAstro.github.io/scripts/diagnose_ctas_mirror.sh

LABEL="io.github.jackmcguireastro.ctas-mirror"
SITE="$HOME/Documents/GitHub/JackMcGuireAstro.github.io"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DB="$HOME/.codex/.chatgpt-projects/g-p-6a5d91be2e688191b7333527fcd488b3/data/soc.db"
LOGD="$HOME/Library/Logs/ctas-mirror"
DOMAIN="gui/$(id -u)"
h() { printf '\n===== %s =====\n' "$*"; }

h "1. environment"
sw_vers 2>&1 | tr '\n' ' '; echo
echo "shell: $SHELL   whoami: $(whoami)   uid: $(id -u)"
echo "python3: $(command -v python3 || echo MISSING)  $(python3 -V 2>&1)"
echo "git:     $(command -v git || echo MISSING)  $(git --version 2>&1)"

h "2. is the agent file installed?"
if [ -f "$DEST" ]; then
  echo "present: $DEST"
  plutil -lint "$DEST" 2>&1
  echo "-- does it still contain the placeholder? (it must NOT) --"
  grep -c 'REPLACE_WITH_HOME' "$DEST" | sed 's/^/REPLACE_WITH_HOME occurrences: /'
  echo "-- ProgramArguments / WatchPaths --"
  plutil -extract ProgramArguments xml1 -o - "$DEST" 2>&1 | grep '<string>'
  plutil -extract WatchPaths xml1 -o - "$DEST" 2>&1 | grep '<string>'
else
  echo "MISSING: $DEST   <-- the installer never wrote it"
fi

h "3. is launchd actually running it?"
launchctl print "$DOMAIN/$LABEL" 2>&1 | \
  egrep -i 'state|program|last exit|pid |runs|path =|error|not find' | head -30

h "4. logs"
for f in "$LOGD/publish.log" "$LOGD/launchd.err.log" "$LOGD/launchd.out.log"; do
  echo "--- $f ---"
  if [ -f "$f" ]; then tail -n 25 "$f"; else echo "(does not exist)"; fi
done

h "5. can a plain shell read the CTAS database?"
if [ -r "$DB" ]; then
  echo "readable, $(du -h "$DB" | cut -f1)"
else
  echo "NOT READABLE: $DB"
fi

h "6. can a plain shell read the repo? (macOS TCC blocks ~/Documents for some jobs)"
if [ -r "$SITE/.git/HEAD" ]; then echo "repo readable"; else echo "NOT READABLE: $SITE"; fi

h "7. git state"
cd "$SITE" 2>/dev/null && {
  git --no-optional-locks log --oneline -3
  echo "-- status --"; git --no-optional-locks status --porcelain | head
  echo "-- stale locks --"; find .git -name '*.lock' 2>/dev/null; echo "(blank = none)"
}

h "8. can git push without a prompt?"
GIT_TERMINAL_PROMPT=0 git -C "$SITE" ls-remote origin -h refs/heads/main >/dev/null 2>&1 \
  && echo "yes, credential available" \
  || echo "NO - git would ask for a password, so an unattended push cannot work"

h "9. run the publisher right now, in the foreground"
bash "$SITE/scripts/publish_ctas.sh" --force 2>&1 | tail -n 20
echo "exit status: $?"

h "done"
