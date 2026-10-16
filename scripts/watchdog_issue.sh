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
#
# Optional: when WATCHDOG_NOTIFY_WEBHOOK is set (repository secret, see
# CTAS-AUTOMATION.md), the report's short summary is also POSTed there when the issue
# is opened, when the set of alerts changes, and when it closes: plain text with a
# Title header for ntfy.sh, JSON {"text", "content"} for Slack and Discord incoming
# webhooks. WATCHDOG_NOTIFY_FORMAT=text|json overrides the guess made from the URL.
# The issue remains the record; a failed notification is reported, never fatal.
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
if any(code.startswith("state-token") for code in codes): names.append("state key")
if "deploy-failed" in codes: names.append("deployment")
print(", ".join(names) or "ok")
' "$REPORT")
TEXT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("summary", ""))' "$REPORT")

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
    notify "recovered" "https://github.com/$REPO/issues/$OPEN"
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
    --title "Publisher alert: $SUMMARY" --body-file body.md)
  echo "opened $URL ($FINGERPRINT)"
  notify "opened" "$URL"
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
  [ "$LAST_FINGERPRINT" = "$FINGERPRINT" ] || notify "changed" "https://github.com/$REPO/issues/$OPEN"
else
  echo "issue #$OPEN unchanged ($FINGERPRINT, last update ${AGE_HOURS}h ago); no new comment"
fi
