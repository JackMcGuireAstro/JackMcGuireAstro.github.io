# shellcheck shell=bash
# The last-good code for the cloud runners' one fallback attempt (RESILIENCE.md). Sourced by
# scripts/ctas_cloud_run.sh and scripts/worldsindex_cloud_run.sh.
#
# The known-good code is the website commit that produced the currently published release,
# read back from that release on its data branch (scripts/release_commit.py). When a
# publisher's release check blocks with the current code, the runner asks
# scripts/release_checks.py decide whether that code may retry (fallback-eligible checks
# only, a different commit, an ancestor of main, knowing every blocking check) and runs the
# release step once more from a checkout of that commit, on the same data.

# release_commit_of <site> <ctas|worldsindex>: the commit that produced the release on the
# data branch, or nothing. Read from the publisher's data store (which the publisher synced
# to the published release before it was blocked, so nothing is downloaded); without a
# store, the branch tip is fetched into the site checkout.
release_commit_of() {
  local site=$1 product=$2 branch store ref
  branch="$product-data"
  store="$site/.git/$product-data-store"
  if [ -d "$store/objects" ] && git --git-dir="$store" rev-parse --verify --quiet "refs/remotes/origin/$branch" >/dev/null; then
    python3 "$site/scripts/release_commit.py" read --product "$product" --git-dir "$store" --ref "refs/remotes/origin/$branch" 2>/dev/null || true
    return 0
  fi
  ref="refs/code-fallback/$branch"
  git -C "$site" fetch --quiet --no-tags --depth=1 origin "+refs/heads/$branch:$ref" 2>/dev/null || return 0
  python3 "$site/scripts/release_commit.py" read --product "$product" \
    --git-dir "$(git -C "$site" rev-parse --absolute-git-dir)" --ref "$ref" 2>/dev/null || true
}

# commit_on_main <site> <commit> <main commit>: yes | no | unknown (GitHub API compare; GH_TOKEN).
commit_on_main() {
  local site=$1 commit=$2 main=$3
  python3 "$site/scripts/release_commit.py" on-main --repo "${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}" \
    --commit "$commit" --main "$main" 2>/dev/null || echo unknown
}

# fallback_checkout <site> <commit> <dir>: a standalone checkout of <commit> at <dir>.
# Actions checkouts are shallow, so the commit is fetched explicitly (depth 1). The checkout
# borrows the site's objects (alternates; nothing is copied), its origin is the site's
# origin (the publishers push their data branch there), and its origin/main is <commit>:
# the CTAS exporter's code-alignment gates then certify the release against the code that
# actually built it, which is what the certificate must say. (A `git worktree` would share
# refs/remotes/origin/main with the site checkout, which is main, so those gates would fail.)
fallback_checkout() {
  local site=$1 commit=$2 dir=$3 objects url
  git -C "$site" cat-file -e "$commit^{commit}" 2>/dev/null \
    || git -C "$site" fetch --quiet --no-tags --depth=1 origin "$commit" || return 1
  objects=$(cd "$site" && cd "$(git rev-parse --git-path objects)" && pwd) || return 1
  url=$(git -C "$site" remote get-url origin) || return 1
  rm -rf -- "$dir"
  git init -q "$dir" || return 1
  printf '%s\n' "$objects" >"$dir/.git/objects/info/alternates"
  # the commit is a shallow boundary: git must never look for its parents
  printf '%s\n' "$commit" >"$dir/.git/shallow"
  git -C "$dir" remote add origin "$url" || return 1
  git -C "$dir" update-ref refs/remotes/origin/main "$commit" || return 1
  git -C "$dir" -c advice.detachedHead=false checkout -q --detach "$commit" || return 1
}

# ---------------------------------------------------------------------- the one attempt
# The runner defines, before calling code_fallback_run:
#   FALLBACK_SCOPE   ctas-publish | worldsindex-publish
#   SITE WORK LOGS MAIN_COMMIT RELEASE_COMMIT CHECKS_MAIN CHECKS_FALLBACK DECISION MARKER
#   DEPLOY_TOKEN (GitHub API compare), FALLBACK_WORKFLOW (the marker's workflow file name)
#   publish_with <site> <log dir> <results file> <marker>: runs that checkout's publisher on
#                    the same data (prints its output, returns its exit code)
#   fallback_ready (optional): returns non-zero when the same data is not available
#   say: the runner's logger
# and reads afterwards: ACTION (proceed | try-fallback | use-fallback | block),
# FALLBACK_USED (1 when the fallback code's run passed), FALLBACK_STATUS, FALLBACK_OUT.
RELEASE_ON_MAIN=${RELEASE_ON_MAIN:-unknown}

fallback_decide() {
  python3 "$SITE/scripts/release_checks.py" decide --scope "$FALLBACK_SCOPE" --results "$CHECKS_MAIN" \
    --main-commit "$MAIN_COMMIT" --release-commit "$RELEASE_COMMIT" --release-commit-on-main "$RELEASE_ON_MAIN" \
    --out "$DECISION" "$@" 2>>"$LOGS/runner.log" || echo block
}
fallback_text() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["reason"]+": "+d["explanation"])' "$DECISION" 2>/dev/null; }

# code_fallback_run <product> <exit status of the publish with the current code>
# At most one fallback attempt: this function never calls itself and the runner calls it once.
code_fallback_run() {
  local product=$1 status=$2 fallback_site fallback_marker classes
  FALLBACK_USED=0
  FALLBACK_STATUS=""
  FALLBACK_OUT=""
  if [ "$status" -eq 3 ] && [ -n "$RELEASE_COMMIT" ] && [ "$RELEASE_COMMIT" != "$MAIN_COMMIT" ]; then
    RELEASE_ON_MAIN=$(GH_TOKEN="${DEPLOY_TOKEN:-${GH_TOKEN:-}}" commit_on_main "$SITE" "$RELEASE_COMMIT" "$MAIN_COMMIT")
  fi
  ACTION=$(fallback_decide --main-exit "$status")
  [ "$status" -eq 3 ] || return 0
  if [ "$ACTION" != "try-fallback" ]; then
    say "a release check blocked the release and no fallback applies: $(fallback_text)"
    return 0
  fi
  if declare -F fallback_ready >/dev/null && ! fallback_ready; then
    say "a release check blocked the release; the same data is not available for a fallback attempt, nothing published"
    return 0
  fi
  say "a release check blocked $(printf '%.10s' "$MAIN_COMMIT"); retrying once with $(printf '%.10s' "$RELEASE_COMMIT"), the code that produced the published release, on the same data"
  fallback_site="$WORK/fallback-site"
  if ! fallback_checkout "$SITE" "$RELEASE_COMMIT" "$fallback_site"; then
    say "could not prepare a checkout of $(printf '%.10s' "$RELEASE_COMMIT"); nothing published"
    rm -rf -- "$fallback_site"
    return 0
  fi
  # Reuse the data store this run already synced instead of downloading the data branch
  # again into the fallback checkout (CTAS: one release is ~1.6 GB).
  [ -d "$SITE/.git/$product-data-store" ] && ln -s "$SITE/.git/$product-data-store" "$fallback_site/.git/$product-data-store"
  # The marker of the fallback release: this run's marker (one key probe per run) with the
  # fallback code's site_commit and code_fallback.
  fallback_marker="$WORK/cloud-run-fallback.json"
  if [ -n "${MARKER:-}" ] && python3 "$SITE/scripts/cloud_run_marker.py" --workflow "${FALLBACK_WORKFLOW:-unknown}" \
       --receipt "$WORK/state-restored.json" --output "$fallback_marker.new" --site-commit "$RELEASE_COMMIT" --fallback "$DECISION" >/dev/null \
     && python3 - "$MARKER" "$fallback_marker.new" "$fallback_marker" <<'PYMARK'
import json, sys
main, fallback = (json.load(open(path)) for path in sys.argv[1:3])
main.update({key: fallback[key] for key in ("site_commit", "code_fallback") if key in fallback})
json.dump(main, open(sys.argv[3], "w"), indent=2, sort_keys=True)
PYMARK
  then :; else fallback_marker="${MARKER:-}"; fi
  rm -f -- "$fallback_marker.new" "$CHECKS_FALLBACK"
  FALLBACK_OUT=$(publish_with "$fallback_site" "$LOGS/publish-fallback" "$CHECKS_FALLBACK" "$fallback_marker" 2>&1)
  FALLBACK_STATUS=$?
  printf '%s\n' "$FALLBACK_OUT" | tail -25 | sed 's/^/fallback: /'
  printf '%s\n' "$FALLBACK_OUT" | grep -E '^(FAIL|WARN)  ' | sed 's/^/fallback: /' >>"$LOGS/runner.log" || true
  classes="$fallback_site/scripts/check-classes.json"
  [ -f "$classes" ] || classes=/dev/null   # code older than the classification: it knows the baseline checks
  ACTION=$(fallback_decide --main-exit "$status" --fallback-results "$CHECKS_FALLBACK" --fallback-exit "$FALLBACK_STATUS" \
    --fallback-classes "$classes")
  if [ "$ACTION" = "use-fallback" ]; then
    FALLBACK_USED=1
    say "published with fallback code $(printf '%.10s' "$RELEASE_COMMIT") (marker code_fallback; fallback issue opened or updated)"
  else
    say "the fallback code could not publish either ($(fallback_text))"
  fi
  rm -rf -- "$fallback_site"
  return 0
}
