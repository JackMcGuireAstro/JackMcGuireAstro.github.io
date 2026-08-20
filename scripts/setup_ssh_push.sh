#!/bin/bash
# =============================================================================
# setup_ssh_push.sh - let scripts push to GitHub without a password prompt.
#
#   bash ~/Documents/GitHub/JackMcGuireAstro.github.io/scripts/setup_ssh_push.sh
#
# Generates an SSH key if you do not have one, stores it in the macOS keychain
# so unattended pushes work after login, shows you the PUBLIC key to paste into
# GitHub, then switches this repo's remote to SSH and pushes.
#
# It never prints your private key. It never force-pushes.
# =============================================================================
set -uo pipefail

SITE="$HOME/Documents/GitHub/JackMcGuireAstro.github.io"
KEY="$HOME/.ssh/id_ed25519"
SSH_URL="git@github.com:JackMcGuireAstro/JackMcGuireAstro.github.io.git"

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "1. SSH key"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
if [ -f "$KEY" ]; then
  ok "already have one at ~/.ssh/id_ed25519"
else
  echo "  Creating one. You may set a passphrase or press Enter twice for none;"
  echo "  either works, because it gets stored in your keychain."
  ssh-keygen -t ed25519 -C "machoslothman@gmail.com" -f "$KEY" || { bad "keygen failed"; exit 1; }
  ok "created ~/.ssh/id_ed25519"
fi

step "2. keychain, so pushes work unattended"
CFG="$HOME/.ssh/config"
if ! grep -qs 'Host github.com' "$CFG" 2>/dev/null; then
  printf '\nHost github.com\n  AddKeysToAgent yes\n  UseKeychain yes\n  IdentityFile %s\n' "$KEY" >> "$CFG"
  chmod 600 "$CFG"
  ok "added a github.com block to ~/.ssh/config"
else
  ok "~/.ssh/config already has a github.com block"
fi
ssh-add --apple-use-keychain "$KEY" 2>/dev/null && ok "key loaded into the keychain" \
  || ssh-add -K "$KEY" 2>/dev/null && ok "key loaded into the keychain" \
  || echo "  (could not preload the key; it will prompt once on first use)"

step "3. add this PUBLIC key to GitHub"
echo
echo "------------------------------------------------------------------"
cat "$KEY.pub"
echo "------------------------------------------------------------------"
command -v pbcopy >/dev/null && pbcopy < "$KEY.pub" && echo "  (copied to your clipboard)"
cat <<EOF

  Open:  https://github.com/settings/ssh/new
  Title: anything, e.g. "MacBook Pro"
  Key:   paste the block above
  Then click "Add SSH key".

  This is a PUBLIC key. It is safe to paste. Your private key never leaves
  this machine and is not shown here.

EOF
read -r -p "  Press Enter once you have added it... " _

step "4. verify GitHub accepts the key"
OUT=$(ssh -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1)
if printf '%s' "$OUT" | grep -q 'successfully authenticated'; then
  ok "$(printf '%s' "$OUT" | head -1)"
else
  bad "GitHub did not accept the key"
  printf '%s\n' "$OUT" | sed 's/^/        /'
  echo
  echo "  Add the key at https://github.com/settings/ssh/new and run this again."
  exit 1
fi

step "5. point this repo at SSH and push"
cd "$SITE" || { bad "cannot find $SITE"; exit 1; }
git remote set-url origin "$SSH_URL" || { bad "could not set the remote"; exit 1; }
ok "remote is now $SSH_URL"

PENDING=$(git log --oneline origin/main..HEAD 2>/dev/null | wc -l | tr -d ' ')
echo "  pushing $PENDING pending commit(s)..."
if git push origin main; then
  ok "pushed"
else
  bad "push still failing, see the message above"
  exit 1
fi

cat <<EOF

Done. Command-line git can now push on its own.

Restart the mirror and it will stay in sync by itself:

    bash $SITE/scripts/mirror_loop.sh

EOF
