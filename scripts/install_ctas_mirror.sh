#!/bin/bash
# =============================================================================
# install_ctas_mirror.sh - make the CTAS mirror run by itself.
#
# Run this ONCE, in Terminal:
#     bash ~/Documents/GitHub/JackMcGuireAstro.github.io/scripts/install_ctas_mirror.sh
#
# It installs a launchd agent that watches the CTAS database and publishes to
# GitHub whenever the data actually changes. After this, nothing is manual.
#
# To uninstall:  bash scripts/install_ctas_mirror.sh --uninstall
# =============================================================================
set -uo pipefail

LABEL="io.github.jackmcguireastro.ctas-mirror"
SITE="$HOME/Documents/GitHub/JackMcGuireAstro.github.io"
SRC="$SITE/scripts/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DB="$HOME/.codex/.chatgpt-projects/g-p-6a5d91be2e688191b7333527fcd488b3/data/soc.db"
DOMAIN="gui/$(id -u)"

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }
info() { printf '        %s\n' "$*"; }

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
  rm -f "$DEST"
  echo "Removed $LABEL. The mirror will no longer run on its own."
  exit 0
fi

echo
echo "Installing the CTAS mirror"
echo "=========================="

# ---------------------------------------------------------- preflight
fail=0
[ -d "$SITE" ]   && ok "website repo    $SITE"          || { bad "website repo not found: $SITE"; fail=1; }
[ -f "$SRC" ]    && ok "agent template  scripts/$LABEL.plist" || { bad "missing $SRC"; fail=1; }
[ -f "$DB" ]     && ok "CTAS database   $(du -h "$DB" | cut -f1)" || { bad "CTAS database not found: $DB"; fail=1; }
command -v python3 >/dev/null && ok "python3         $(python3 -V 2>&1)" || { bad "python3 not found"; fail=1; }
[ "$fail" -eq 0 ] || { echo; echo "Fix the above and run this again."; exit 1; }

# Can we push without a human typing a password? GitHub Desktop normally puts
# the credential in the keychain, which a launchd job can read.
echo
echo "Checking that an unattended push will work"
if GIT_TERMINAL_PROMPT=0 git -C "$SITE" ls-remote origin -h refs/heads/main >/dev/null 2>&1; then
  ok "GitHub credentials are available without a prompt"
else
  bad "git cannot reach GitHub without asking for a credential"
  info "Do one manual push from GitHub Desktop first so the credential is"
  info "stored in your keychain, then run this installer again."
  exit 1
fi

# ---------------------------------------------------------- install
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/ctas-mirror"
sed "s|REPLACE_WITH_HOME|$HOME|g" "$SRC" > "$DEST" || { bad "could not write $DEST"; exit 1; }
plutil -lint "$DEST" >/dev/null 2>&1 && ok "agent written   ~/Library/LaunchAgents/$LABEL.plist" \
  || { bad "the generated plist is malformed"; exit 1; }

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null      # replace any earlier copy
if launchctl bootstrap "$DOMAIN" "$DEST" 2>/dev/null; then
  ok "agent loaded"
else
  bad "launchctl bootstrap failed"
  info "Try: launchctl bootout $DOMAIN/$LABEL   then run this again."
  exit 1
fi
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null

# ---------------------------------------------------------- first run
echo
echo "Running once now so the site is current"
launchctl kickstart "$DOMAIN/$LABEL" 2>/dev/null
sleep 12
LOG="$HOME/Library/Logs/ctas-mirror/publish.log"
if [ -f "$LOG" ]; then
  echo
  tail -n 12 "$LOG" | sed 's/^/        /'
else
  info "no log yet; give it a minute, then: tail -f $LOG"
fi

cat <<EOF

Done. From here on this is automatic.

  What it does   watches the CTAS database; whenever the candidate data
                 actually changes it exports, commits ctas/data, and pushes.
                 No schedule, no 30-minute wait, nothing to click.
  Watch it       tail -f ~/Library/Logs/ctas-mirror/publish.log
  Run it by hand $SITE/scripts/publish_ctas.sh --force
  Turn it off    bash $SITE/scripts/install_ctas_mirror.sh --uninstall

EOF
