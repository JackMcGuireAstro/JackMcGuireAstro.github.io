#!/bin/bash
# Place the latest published data releases into this checkout's working tree.
#
# Generated data is not committed to main; each publisher keeps its latest release as
# the only commit of its own branch. CI runs this before validating and deploying, and
# you can run it locally to preview the complete site:
#
#   bash scripts/overlay_published_data.sh            # both branches
#   bash scripts/overlay_published_data.sh ctas-data  # one branch
#
# The files land in ignored paths (see .gitignore), so main's status stays clean.
# Prints "<branch> <commit>" for each overlaid release.
set -euo pipefail
BRANCHES=("$@")
[ "${#BRANCHES[@]}" -gt 0 ] || BRANCHES=(ctas-data worldsindex-data)
for branch in "${BRANCHES[@]}"; do
  git fetch --quiet --no-tags --depth=1 origin "+refs/heads/$branch:refs/remotes/origin/$branch"
  commit=$(git rev-parse "refs/remotes/origin/$branch")
  case "$branch" in
    ctas-data) prefix=ctas/data ;;
    worldsindex-data) prefix=worldsindex/data ;;
    *) echo "unknown data branch: $branch" >&2; exit 2 ;;
  esac
  # Remove what a previous overlay left, keeping tracked files (observatories.json).
  git clean -fdqX -- "$prefix"
  git archive --format=tar "$commit" -- "$prefix" | tar -x -f -
  echo "$branch $commit"
done
