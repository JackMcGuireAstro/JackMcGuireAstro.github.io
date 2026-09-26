#!/bin/bash
# One-time move of generated release data from main to the ctas-data and
# worldsindex-data branches (see scripts/data_branch.sh). Run from an authoring clone
# whose HEAD contains the data-branch code, while both publishers are paused (the
# landing script holds their locks), then push main.
#
#   1. For each data branch that does not exist yet on origin, create it as one
#      parentless commit holding exactly the generated files of origin/main (plus the
#      deploy dispatcher from HEAD). An existing branch is left alone.
#   2. Commit, on top of HEAD, the removal of those generated files from main's index.
#      The files stay in the working tree (now ignored), so nothing local disappears.
#
# Idempotent: re-running after a partial run only does what is still missing.
# Prints what it did; exit status non-zero means nothing further should be pushed.
set -euo pipefail

DISPATCHER=.github/workflows/data-branch-deploy.yml
git cat-file -e "HEAD:$DISPATCHER" 2>/dev/null || { echo "HEAD does not contain $DISPATCHER; apply the data-branch code first" >&2; exit 1; }
grep -qx '/ctas/data/\*' .gitignore || { echo ".gitignore does not ignore generated data yet; apply the data-branch code first" >&2; exit 1; }
git fetch --quiet origin main
BASE=$(git rev-parse origin/main)

# generated paths on origin/main: every ctas/data file except the tracked observatories
# list, and every worldsindex/data file
ctas_filter() { grep -v '	ctas/data/observatories\.json$'; }
wi_filter() { cat; }

seed() {
  local branch=$1 prefix=$2 filter=$3 label=$4
  if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    echo "$branch already exists on origin; left as is"
    return 0
  fi
  local index tree commit blob count
  index=$(mktemp); rm -f "$index"
  count=$(git ls-tree -r "$BASE" -- "$prefix" | $filter | wc -l | tr -d ' ')
  [ "$count" -gt 0 ] || { echo "origin/main has no generated files under $prefix" >&2; return 1; }
  git ls-tree -r "$BASE" -- "$prefix" | $filter | GIT_INDEX_FILE="$index" git update-index --index-info
  blob=$(git rev-parse "HEAD:$DISPATCHER")
  GIT_INDEX_FILE="$index" git update-index --add --cacheinfo "100644,$blob,$DISPATCHER"
  tree=$(GIT_INDEX_FILE="$index" git write-tree)
  rm -f "$index"
  commit=$(printf '%s data: moved from main %s (%s files)\n' "$label" "$(git rev-parse --short "$BASE")" "$count" | git commit-tree "$tree")
  git push --quiet origin "$commit:refs/heads/$branch"
  echo "created $branch at $(git rev-parse --short "$commit") with $count files from main $(git rev-parse --short "$BASE")"
}

seed ctas-data ctas/data ctas_filter CTAS
seed worldsindex-data worldsindex/data wi_filter WorldsIndex

TRACKED=$( { git ls-files -- ctas/data | grep -v '^ctas/data/observatories\.json$'; git ls-files -- worldsindex/data; } || true)
if [ -z "$TRACKED" ]; then
  echo "main no longer tracks generated data; no migration commit needed"
  exit 0
fi
printf '%s\n' "$TRACKED" | git rm -q --cached --pathspec-from-file=-
git commit -q -m "Move generated release data off main onto the ctas-data and worldsindex-data branches

Each publisher now replaces its own data branch with one commit per release
(scripts/data_branch.sh) and the deploy workflow combines main with the latest
commit of both branches. main keeps only code, so the site's history stops
growing with every data release. The files remain in working trees as ignored
build output; scripts/overlay_published_data.sh fetches the published releases."
echo "committed the removal of $(printf '%s\n' "$TRACKED" | wc -l | tr -d ' ') generated files from main as $(git rev-parse --short HEAD)"
