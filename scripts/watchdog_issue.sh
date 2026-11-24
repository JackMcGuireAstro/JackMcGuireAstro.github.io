#!/bin/bash
# Turn a freshness-watchdog report into exactly one GitHub issue, quietly.
#
#   watchdog_issue.sh <report.json> <report.md>
#
# Every issue comment is an e-mail to the owner, so (2026-11-24) the issue is EDITED in
# place and commented on only when the picture got worse:
#
# Stale:     open a labelled issue if none is open (one e-mail). While it stays open, each
#            check rewrites the body (silent). A comment (e-mail) is added only when a more
#            severe alert code appeared than any seen so far on this issue.
# Current:   the issue is not closed at once. The body says "recovering since <time>"; it
#            closes (one e-mail) once the condition has stayed current for COOLDOWN_HOURS
#            (default 6), so a condition that comes and goes produces one open and one
#            close, not a burst. A relapse inside the cooldown just clears the recovery time.
#
# Runs inside GitHub Actions with GH_TOKEN set to the workflow token, which may read and
# write issues in this repository and nothing else. The body carries a hidden marker
#   <!-- watchdog-fingerprint: <codes> top: <most severe code> recovered: <iso|none> -->
# so the next run can tell what changed without keeping state of its own. Issues from
# before 2026-11-24 carry the old marker (fingerprint only); they are read the same way.
#
# Optional: when WATCHDOG_NOTIFY_WEBHOOK is set (repository secret, see CTAS-AUTOMATION.md),
# the report's short summary is also POSTed there when the issue opens, worsens and closes:
# plain text with a Title header for ntfy.sh, JSON {"text", "content"} for Slack and
# Discord incoming webhooks. WATCHDOG_NOTIFY_FORMAT=text|json overrides the guess made from
# the URL. The issue remains the record; a failed notification is reported, never fatal.
set -euo pipefail

REPORT=${1:?report.json}
MARKDOWN=${2:?report.md}
LABEL=${WATCHDOG_LABEL:-freshness-watchdog}
COOLDOWN_HOURS=${WATCHDOG_COOLDOWN_HOURS:-6}
TITLE_PREFIX=${WATCHDOG_TITLE_PREFIX:-Publisher alert}
RECOVERED_TEXT=${WATCHDOG_RECOVERED_TEXT:-both publishers are current again}
LABEL_TEXT=${WATCHDOG_LABEL_TEXT:-Opened automatically when a publisher stops updating the live site}
REPO=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPORT_ABS=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")

OK=$(python3 -c 'import json,sys; print("1" if json.load(open(sys.argv[1]))["ok"] else "0")' "$REPORT")
FINGERPRINT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$REPORT")
# The most severe alert code (scripts/watchdog_freshness.py SEVERITY) and a code's rank.
TOP=$(cd "$HERE" && python3 -c '
import json, sys, watchdog_freshness as w
codes = [row["code"] for row in json.load(open(sys.argv[1]))["alerts"]]
print(min(codes, key=lambda c: (w.severity(c), c)) if codes else "none")' "$REPORT_ABS" 2>/dev/null || echo none)
rank() { (cd "$HERE" && python3 -c 'import sys, watchdog_freshness as w; print(w.severity(sys.argv[1]) if sys.argv[1] != "none" else 99)' "$1"); }
SUMMARY=$(python3 -c '
import json, sys
report = json.load(open(sys.argv[1]))
codes = [row["code"] for row in report["alerts"]]
names = []
if any(code.startswith("ctas") for code in codes): names.append("CTAS")
if any(code.startswith("worldsindex") for code in codes): names.append("WorldsIndex")
if any(code.startswith("state-token") for code in codes): names.append("state key")
if "deploy-failed" in codes: names.append("deployment")
if "deploy-stale" in codes: names.append("public site")
print(", ".join(names) or "ok")
' "$REPORT")
# The live-site check (scripts/watchdog_freshness.py, item 7) gets the plain title.
TITLE=$(python3 -c '
import json, sys
report = json.load(open(sys.argv[1]))
codes = [row["code"] for row in report["alerts"]]
others = sys.argv[2].replace("public site", "").replace(", ,", ",").strip(" ,")
if "deploy-stale" in codes:
    print("Public site is not deploying" + (f" (also: {others})" if others else ""))
else:
    print(f"{sys.argv[3]}: {sys.argv[2]}")
' "$REPORT" "$SUMMARY" "$TITLE_PREFIX")
TEXT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("summary", ""))' "$REPORT")
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# notify <event> <issue url>: POST the summary to WATCHDOG_NOTIFY_WEBHOOK, if set.
notify() {
  [ -n "${WATCHDOG_NOTIFY_WEBHOOK:-}" ] || return 0
  local title="Publisher watchdog: $1 ($SUMMARY)" body="$TEXT"$'\n'"$2" format=${WATCHDOG_NOTIFY_FORMAT:-}
  if [ -z "$format" ]; then case "$WATCHDOG_NOTIFY_WEBHOOK" in *ntfy*) format=text ;; *) format=json ;; esac; fi
  if [ "$format" = "text" ]; then
    curl -fsS -m 20 -H "Title: $title" -H "Priority: high" --data-binary "$body" "$WATCHDOG_NOTIFY_WEBHOOK" >/dev/null
  else
    python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1], "content": sys.argv[1][:1900]}))' "$title"$'\n'"$body" \
      | curl -fsS -m 20 -H 'Content-Type: application/json' --data-binary @- "$WATCHDOG_NOTIFY_WEBHOOK" >/dev/null
  fi && echo "notified the webhook ($1)" || echo "webhook notification failed ($1); the issue is the record"
}

# body <fingerprint> <top> <recovered-at|none> [<lead line>]: the issue body from the report.
body() {
  {
    [ -z "${4:-}" ] || printf '%s\n\n' "$4"
    cat "$MARKDOWN"
    printf '\n_Last checked %s. This issue is updated in place; a comment is added only when the picture gets worse._\n' "$NOW"
    printf '\n<!-- watchdog-fingerprint: %s top: %s recovered: %s -->\n' "$1" "$2" "$3"
  } > body.md
}

gh label create "$LABEL" --repo "$REPO" --color D93F0B \
  --description "$LABEL_TEXT" >/dev/null 2>&1 || true

OPEN=$(gh issue list --repo "$REPO" --label "$LABEL" --state open --limit 1 --json number --jq '.[0].number // empty')
LAST_FINGERPRINT=none; LAST_TOP=none; LAST_RECOVERED=none
if [ -n "$OPEN" ]; then
  # The newest marker: the body (edited in place since 2026-11-24), else the last comment.
  read -r LAST_FINGERPRINT LAST_TOP LAST_RECOVERED < <(gh issue view "$OPEN" --repo "$REPO" --json body,comments --jq '
    def mark: if test("<!-- watchdog-fingerprint: ") then
        (capture("<!-- watchdog-fingerprint: (?<f>[^ ]+)(?: top: (?<t>[^ ]+))?(?: recovered: (?<r>[^ ]+))? -->") | "\(.f) \(.t // "none") \(.r // "none")")
      else empty end;
    ([.body] + [.comments[].body] | map(mark) | .[0]) // "none none none"')
fi

if [ "$OK" = "1" ]; then
  if [ -z "$OPEN" ]; then
    echo "both publishers current; no open alert issue"
    exit 0
  fi
  if [ "$LAST_RECOVERED" != "none" ]; then
    AGE_HOURS=$(python3 -c '
import datetime, sys
try:
    then = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    print(int((datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() // 3600))
except ValueError:
    print(0)
' "$LAST_RECOVERED")
    if [ "$AGE_HOURS" -ge "$COOLDOWN_HOURS" ]; then
      {
        printf 'Recovered: %s, current for %s h.\n\n' "$RECOVERED_TEXT" "$AGE_HOURS"
        cat "$MARKDOWN"
        printf '\n<!-- watchdog-fingerprint: ok top: none recovered: %s -->\n' "$LAST_RECOVERED"
      } > recovery.md
      gh issue close "$OPEN" --repo "$REPO" --comment "$(cat recovery.md)"
      echo "closed issue #$OPEN (recovered ${AGE_HOURS}h ago)"
      notify "recovered" "https://github.com/$REPO/issues/$OPEN"
    else
      body "$LAST_FINGERPRINT" "$LAST_TOP" "$LAST_RECOVERED" "**Recovering** since $LAST_RECOVERED: $RECOVERED_TEXT. This issue closes by itself after $COOLDOWN_HOURS h without a relapse."
      gh issue edit "$OPEN" --repo "$REPO" --body-file body.md >/dev/null
      echo "issue #$OPEN recovering since $LAST_RECOVERED (${AGE_HOURS}h); closes after ${COOLDOWN_HOURS}h"
    fi
  else
    body "$LAST_FINGERPRINT" "$LAST_TOP" "$NOW" "**Recovering** since $NOW: $RECOVERED_TEXT. This issue closes by itself after $COOLDOWN_HOURS h without a relapse."
    gh issue edit "$OPEN" --repo "$REPO" --body-file body.md >/dev/null
    echo "issue #$OPEN marked recovering at $NOW"
  fi
  exit 0
fi

body "$FINGERPRINT" "$TOP" none
if [ -z "$OPEN" ]; then
  URL=$(gh issue create --repo "$REPO" --label "$LABEL" --title "$TITLE" --body-file body.md)
  echo "opened $URL ($FINGERPRINT)"
  notify "opened" "$URL"
  exit 0
fi

# Open already: edit in place; a comment (e-mail) only when a more severe code appeared.
WORSE=0
if [ "$(rank "$TOP")" -lt "$(rank "$LAST_TOP")" ]; then WORSE=1; fi
gh issue edit "$OPEN" --repo "$REPO" --title "$TITLE" --body-file body.md >/dev/null
if [ "$WORSE" = 1 ]; then
  {
    printf 'Worse: **%s** joined the alerts (was %s).\n\n' "$TOP" "$LAST_TOP"
    printf '%s\n' "$TEXT"
  } > worse.md
  gh issue comment "$OPEN" --repo "$REPO" --body-file worse.md >/dev/null
  echo "issue #$OPEN got worse ($LAST_TOP -> $TOP); commented"
  notify "worse" "https://github.com/$REPO/issues/$OPEN"
else
  if [ "$LAST_RECOVERED" != "none" ]; then echo "issue #$OPEN relapsed; recovery time cleared ($FINGERPRINT)"
  else echo "issue #$OPEN updated in place ($LAST_FINGERPRINT -> $FINGERPRINT)"; fi
fi
