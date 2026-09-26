#!/bin/bash
# Turn a freshness-watchdog report into exactly one GitHub issue.
#
#   watchdog_issue.sh <report.json> <report.md>
#
# Stale:   open a labelled issue if none is open; otherwise add a comment only when
#          the set of alerts changed or the last update is older than REMIND_HOURS,
#          so a persisting outage produces two reminders a day, not one an hour.
# Current: close the open issue (if any) with a recovery comment.
#
# Runs inside GitHub Actions with GH_TOKEN set to the workflow token, which may
# read and write issues in this repository and nothing else. Every body carries a
# hidden `watchdog-fingerprint` marker so the next run can tell whether anything
# changed without keeping state of its own.
set -euo pipefail

REPORT=${1:?report.json}
MARKDOWN=${2:?report.md}
LABEL=${WATCHDOG_LABEL:-freshness-watchdog}
REMIND_HOURS=${WATCHDOG_REMIND_HOURS:-12}
REPO=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}

OK=$(python3 -c 'import json,sys; print("1" if json.load(open(sys.argv[1]))["ok"] else "0")' "$REPORT")
FINGERPRINT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$REPORT")
SUMMARY=$(python3 -c '
import json, sys
report = json.load(open(sys.argv[1]))
codes = [row["code"] for row in report["alerts"]]
names = []
if any(code.startswith("ctas") for code in codes): names.append("CTAS")
if any(code.startswith("worldsindex") for code in codes): names.append("WorldsIndex")
if "deploy-failed" in codes: names.append("deployment")
print(", ".join(names) or "ok")
' "$REPORT")

gh label create "$LABEL" --repo "$REPO" --color D93F0B \
  --description "Opened automatically when a publisher stops updating the live site" >/dev/null 2>&1 || true

OPEN=$(gh issue list --repo "$REPO" --label "$LABEL" --state open --limit 1 --json number --jq '.[0].number // empty')

if [ "$OK" = "1" ]; then
  if [ -n "$OPEN" ]; then
    {
      printf 'Recovered: both publishers are current again.\n\n'
      cat "$MARKDOWN"
      printf '\n<!-- watchdog-fingerprint: ok -->\n'
    } > recovery.md
    gh issue close "$OPEN" --repo "$REPO" --comment "$(cat recovery.md)"
    echo "closed issue #$OPEN (recovered)"
  else
    echo "both publishers current; no open alert issue"
  fi
  exit 0
fi

{
  cat "$MARKDOWN"
  printf '\n<!-- watchdog-fingerprint: %s -->\n' "$FINGERPRINT"
} > body.md

if [ -z "$OPEN" ]; then
  URL=$(gh issue create --repo "$REPO" --label "$LABEL" \
    --title "Publisher freshness alert: $SUMMARY" --body-file body.md)
  echo "opened $URL ($FINGERPRINT)"
  exit 0
fi

# Compare with the newest fingerprint on the open issue (last comment, else body).
read -r LAST_FINGERPRINT LAST_AT < <(gh issue view "$OPEN" --repo "$REPO" --json body,createdAt,comments --jq '
  def mark: (capture("<!-- watchdog-fingerprint: (?<f>[^ ]+) -->") // {f: "none"}).f;
  if (.comments | length) > 0 then (.comments[-1] | "\(.body | mark) \(.createdAt)")
  else "\(.body | mark) \(.createdAt)" end')
NOW_EPOCH=$(date -u +%s)
LAST_EPOCH=$(python3 -c '
import datetime, sys
text = sys.argv[1].replace("Z", "+00:00")
try:
    print(int(datetime.datetime.fromisoformat(text).timestamp()))
except ValueError:
    print(0)
' "$LAST_AT")
AGE_HOURS=$(( (NOW_EPOCH - LAST_EPOCH) / 3600 ))

if [ "$LAST_FINGERPRINT" != "$FINGERPRINT" ] || [ "$AGE_HOURS" -ge "$REMIND_HOURS" ]; then
  gh issue comment "$OPEN" --repo "$REPO" --body-file body.md >/dev/null
  echo "updated issue #$OPEN ($LAST_FINGERPRINT -> $FINGERPRINT, last update ${AGE_HOURS}h ago)"
else
  echo "issue #$OPEN unchanged ($FINGERPRINT, last update ${AGE_HOURS}h ago); no new comment"
fi
