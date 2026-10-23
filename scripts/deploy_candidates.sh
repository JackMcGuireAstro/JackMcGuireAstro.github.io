#!/bin/bash
# "Validate and deploy site": choose the site code to deploy with the current data releases.
#
#   deploy_candidates.sh      (run from main's checkout, after main's check groups ran)
#
# Candidates, in order, at most three, a commit already tried is skipped:
#   main            this checkout (its checks already ran, results in $CHECK_RESULTS)
#   ctas-release    the commit that produced the current CTAS release (the HEAD whose code the
#                   CTAS certificate binds; scripts/release_commit.py read --product ctas)
#   last-deployment the commit of the last successful github-pages deployment (deployments API)
#
# Each fallback candidate is checked out as a worktree (fetched explicitly: the checkout is
# shallow), the very same data commits are extracted into it, and every check group runs
# there with main's runner and classification (scripts/deploy_checks.sh all <tree>), skipping
# advisory checks, which cannot change the choice. The first candidate with no blocking
# failure is deployed (scripts/release_checks.py choose); advisory failures are warnings. A
# candidate that is not an ancestor of main is never used. When no candidate passes, nothing
# is deployed and the step fails, as before.
#
# Writes $RUNNER_TEMP/choice.json and $RUNNER_TEMP/decision.json (for the issues and the run
# report) and, to $GITHUB_OUTPUT: action (proceed | use-fallback | block), tree, frontend
# (the candidate label or none), commit.
#
# Environment: CHECK_RESULTS (main's results), CHECK_HISTORY, CTAS_DATA_COMMIT,
# WORLDSINDEX_DATA_COMMIT, GH_TOKEN (deployments: read; compare), GITHUB_REPOSITORY,
# RUNNER_TEMP, GITHUB_OUTPUT. LANDING_WINDOW_HOURS (default 3): see below.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SITE=$(cd "$HERE/.." && pwd)
TEMP=${RUNNER_TEMP:-/tmp}
REPO=${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}
OUT=${GITHUB_OUTPUT:-/dev/null}
MAIN=$(git -C "$SITE" rev-parse HEAD)
CHOICE="$TEMP/choice.json"
DECISION="$TEMP/decision.json"
CTAS_DATA_COMMIT=${CTAS_DATA_COMMIT:?CTAS_DATA_COMMIT}
WORLDSINDEX_DATA_COMMIT=${WORLDSINDEX_DATA_COMMIT:?WORLDSINDEX_DATA_COMMIT}
RC="$HERE/release_checks.py"

candidates=("main=$MAIN=$CHECK_RESULTS")
choose() { python3 "$RC" choose "${candidates[@]/#/--candidate=}" --out "$CHOICE"; }
chosen=$(choose)
echo "with main's code: ${chosen%% *}"

if [ "$chosen" = none ]; then
  CTAS_RELEASE=$(python3 "$HERE/release_commit.py" read --product ctas --data-dir "$SITE/ctas/data")
  LAST_DEPLOY=$(python3 "$HERE/release_commit.py" last-deployment --repo "$REPO")
  echo "fallback candidates: ctas-release ${CTAS_RELEASE:-none}, last-deployment ${LAST_DEPLOY:-none}"
  tried=" $MAIN "
  for pair in "ctas-release=$CTAS_RELEASE" "last-deployment=$LAST_DEPLOY"; do
    label=${pair%%=*}
    commit=${pair#*=}
    [ -n "$commit" ] || { candidates+=("$label=="); continue; }
    case "$tried" in *" $commit "*) candidates+=("$label=$commit="); continue ;; esac
    tried="$tried$commit "
    on_main=$(python3 "$HERE/release_commit.py" on-main --repo "$REPO" --commit "$commit" --main "$MAIN")
    if [ "$on_main" = no ]; then
      echo "candidate $label ${commit:0:10} is not an ancestor of main; not used"
      candidates+=("$label=$commit=")
      continue
    fi
    tree="$(dirname "$SITE")/candidate-$label"
    rm -rf -- "$tree"
    git -C "$SITE" worktree prune
    if ! { git -C "$SITE" cat-file -e "$commit^{commit}" 2>/dev/null \
             || git -C "$SITE" fetch --quiet --no-tags --depth=1 origin "$commit"; } \
       || ! git -C "$SITE" worktree add --quiet --detach "$tree" "$commit"; then
      echo "candidate $label ${commit:0:10} could not be checked out"
      candidates+=("$label=$commit=")
      continue
    fi
    # The same data the main candidate was validated with.
    git -C "$SITE" archive "$CTAS_DATA_COMMIT" -- ctas/data | tar -x -f - -C "$tree"
    git -C "$SITE" archive "$WORLDSINDEX_DATA_COMMIT" -- worldsindex/data | tar -x -f - -C "$tree"
    results="$TEMP/checks-$label.jsonl"
    rm -f -- "$results"
    echo "::group::checks of candidate $label ${commit:0:10}"
    started=$(date +%s)
    CHECK_RESULTS="$results" CHECK_DIR="$TEMP/deploy-checks-$label" CHECK_SKIP_MISSING=1 CHECK_ONLY_BLOCKING=1 \
      CHECK_ECHO_LINES=40 bash "$HERE/deploy_checks.sh" all "$tree"
    echo "::endgroup::"
    echo "candidate $label ${commit:0:10} validated in $(( $(date +%s) - started ))s"
    candidates+=("$label=$commit=$results")
    chosen=$(choose)
    [ "$chosen" = none ] || break
  done
fi

label=${chosen%% *}
commit=${chosen#* }
case "$label" in
  main) action=proceed; tree="$SITE" ;;
  none) action=block; tree=""; commit="" ;;
  *) action=use-fallback; tree="$(dirname "$SITE")/candidate-$label" ;;
esac
# Landing window: right after a CTAS code landing, main's bound code differs from the code
# that certified the live CTAS release until the next CTAS release re-certifies it (30 to 60
# minutes), so main fails the binding check and the ctas-release candidate is deployed. That
# is expected; the fallback issue is only opened when it lasts longer than
# LANDING_WINDOW_HOURS after main's commit.
main_age_hours=$(python3 -c 'import sys,time; print(round((time.time() - int(sys.argv[1])) / 3600, 2))' \
  "$(git -C "$SITE" log -1 --format=%ct "$MAIN")")
python3 - "$CHOICE" "$DECISION" "$action" "$MAIN" "$main_age_hours" "${LANDING_WINDOW_HOURS:-3}" <<'PY'
import datetime, json, sys
choice_path, decision_path, action, main, age, window = sys.argv[1:7]
choice = json.load(open(choice_path))
chosen = choice.get("chosen") or {}
first = choice["tried"][0] if choice.get("tried") else {}
blocking = first.get("blocking", []) if first.get("label") == "main" else []
landing = action == "use-fallback" and chosen.get("label") == "ctas-release" and float(age) < float(window)
tried = "; ".join(f"{row['label']} {(row.get('commit') or '-')[:10]} {row['status']}" for row in choice.get("tried", []))
explanation = {
    "proceed": "main's code passed every blocking check with the current data",
    "use-fallback": f"main's code failed {', '.join(blocking) or 'a blocking check'}; deployed {chosen.get('label')} "
                    f"{(chosen.get('commit') or '')[:10]} with the same data" + (" (landing window: expected until the next CTAS release)" if landing else ""),
    "block": f"no candidate passed every blocking check ({tried}); nothing deployed, the previous deployment stays live",
}[action]
decision = {
    "schema": "release-decision@1", "scope": "deploy", "decided_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "action": action, "reason": {"proceed": "no-blocking-failure", "use-fallback": "fallback-candidate-passed", "block": "no-candidate-passed"}[action],
    "explanation": explanation, "main_commit": main, "release_commit": chosen.get("commit") if action == "use-fallback" else None,
    "fallback_label": chosen.get("label") if action == "use-fallback" else None, "blocking": blocking,
    "fallback": action == "use-fallback", "landing_window": landing, "candidates": choice.get("tried", []),
}
json.dump(decision, open(decision_path, "w"), indent=2, sort_keys=True)
print(f"decision: {action}: {explanation}")
PY
{
  echo "action=$action"
  echo "tree=$tree"
  echo "frontend=$label"
  echo "commit=$commit"
} >>"$OUT"
[ "$action" != block ]
