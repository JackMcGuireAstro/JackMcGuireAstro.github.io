#!/usr/bin/env python3
"""Keep the release-check GitHub issues in step with what a run found.

Four labels, each issue keyed by a hidden marker so every run can find its own issue
without keeping state. Issues are edited in place, never commented: every comment is an
e-mail to the owner (2026-11-24: 81 of 100 issues in two weeks were one advisory browser
check opening and closing itself, each with its comments).

* ``check-warning``   one issue per check id (``<!-- check-warning: <id> scope=<scope> -->``):
                      opened when an INTEGRITY check still in watch mode fails (it will
                      block once promoted), edited when what fails changes, closed
                      automatically when the same check passes again in the same scope.
                      An ADVISORY failure never opens an issue: it goes to the daily digest.
* ``daily-digest``    at most one issue per UTC day ("Daily digest <date>"), created by the
                      first informational finding of the day and edited by the rest:
                      advisory check failures, lost runners, fallbacks inside a landing
                      window, early warnings, notes. Its items are kept in a JSON marker in
                      the body, so every run can merge its own in without state. The
                      previous day's digest is closed (without a comment) when a new one
                      opens or when a run finds it older than a day.
* ``fallback-active`` one issue per scope (ctas-publish, worldsindex-publish, deploy): "CTAS
                      published with fallback code", "WorldsIndex published with fallback
                      code", "Site deployed with fallback code": the run published or
                      deployed with older code (the commit that produced the previous
                      release, or the deploy's next candidate) because the current code
                      failed a blocking check; it names the commit range and the checks.
                      Closed when a run of that scope succeeds with the current code.
* ``release-blocked`` one issue per scope: nothing was published or deployed (the data is
                      bad, no fallback was possible, or the fallback failed too). Closed on
                      the next run of that scope that publishes or deploys anything.

Every body is sanitized (scripts/run_report.py) before it is sent. The planner is a pure
function (``plan``); ``apply`` runs the GitHub CLI with the workflow token in GH_TOKEN.

    release_issues.py sync --scope ctas-publish --results checks.jsonl [--decision decision.json]
                           [--repo OWNER/NAME] [--run-url URL] [--mention @name] [--dry-run]
    release_issues.py digest-add --kind lost-runner --key <id> --title "..." [--detail "..."]
                           [--scope ctas-publish] [--run-url URL] [--repo OWNER/NAME] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_report import collect_secrets, leaks, read_jsonl, sanitize  # noqa: E402

LABELS = {
    "check-warning": ("FBCA04", "A release check failed without blocking (advisory or watch mode); closes when it passes again"),
    "fallback-active": ("D93F0B", "A publisher or the deploy is running older code because the current code failed a blocking check"),
    "release-blocked": ("B60205", "Nothing was published or deployed: a blocking failure that the fallback code could not get past"),
    "daily-digest": ("0E8A16", "One issue a day for what is worth knowing but not acting on: advisory failures, lost runners"),
}
DIGEST_LABEL = "daily-digest"
DIGEST_MARKER = re.compile(r"<!-- daily-digest: (\{.*?\}) -->", re.DOTALL)
DIGEST_KINDS = ("advisory", "lost-runner", "fallback", "early", "note")
# GitHub refuses a label whose description is longer than 100 characters (HTTP 422), and then
# every "gh issue create --label" for it fails with "could not add label: ... not found".
LABEL_DESCRIPTION_MAX = 100
SCOPE_TITLES = {"ctas-publish": "CTAS publisher", "worldsindex-publish": "WorldsIndex publisher", "deploy": "site deploy"}
FALLBACK_TITLES = {"ctas-publish": "CTAS published with fallback code", "worldsindex-publish": "WorldsIndex published with fallback code",
                   "deploy": "Site deployed with fallback code"}
# Failures outside the classified checks (a push or a save that failed, a cancelled step):
# no new release-blocked issue, because one transient failure would open and close one
# every time; the freshness watchdog alerts on a streak of failed runs instead.
QUIET_REASONS = {"error-outside-checks", "run-failed", "deploy-failed"}
# Sub-check rows (one per schema file) are reported through their family's issue.
MARKER = re.compile(r"<!-- (check-warning|fallback-active|release-blocked): (\S+)(?: scope=(\S+))?(?: fp=(\S+))? -->")


def fingerprint(*items) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:12]


def marker(kind: str, key: str, scope: str | None, fp: str) -> str:
    return f"<!-- {kind}: {key}" + (f" scope={scope}" if scope else "") + f" fp={fp} -->"


def parse_marker(text: str | None) -> dict | None:
    """The newest marker in a body or comment."""
    found = list(MARKER.finditer(text or ""))
    if not found:
        return None
    match = found[-1]
    return {"kind": match.group(1), "key": match.group(2), "scope": match.group(3), "fp": match.group(4)}


def tail_block(result: dict, lines: int = 60) -> str:
    tail = (result.get("output_tail") or [])[-lines:]
    if not tail:
        return ""
    return "<details><summary>last lines of output</summary>\n\n```text\n" + "\n".join(tail) + "\n```\n</details>\n"


def parts_text(result: dict) -> str:
    parts = result.get("parts") or []
    if not parts:
        return ""
    return "\n".join(f"- `{row['part']}` ({row['class']})" for row in parts[:20]) + "\n"


def compare_url(repo: str | None, base: str | None, head: str | None) -> str | None:
    if repo and base and head and base != head:
        return f"https://github.com/{repo}/compare/{base}...{head}"
    return None


def plan(scope: str, results: list[dict], decision: dict | None, open_issues: list[dict], *,
         repo: str | None = None, run_url: str | None = None, mention: str = "") -> list[dict]:
    """Issue actions for one run. Pure function.

    ``open_issues``: [{number, title, body, last_marker}] with the label in ``labels``;
    ``last_marker`` is parse_marker() of the newest comment or of the body.
    Returns [{op: create|comment|close, label, number?, title?, body}].
    """
    actions = []
    digest_items = []
    run = f" ([run]({run_url}))" if run_url else ""
    by_key = {}
    for issue in open_issues:
        mark = issue.get("last_marker") or parse_marker(issue.get("body"))
        if mark:
            by_key[(mark["kind"], mark["key"])] = {**issue, "marker": mark}

    # check-warning: one per check id
    seen = set()
    for result in results:
        if result.get("family"):
            continue
        check_id, outcome = result.get("id"), result.get("outcome")
        if not check_id or check_id in seen:
            continue
        seen.add(check_id)
        issue = by_key.get(("check-warning", check_id))
        if outcome == "warn" and result.get("class") == "advisory":
            # Never an issue of its own: one line in the day's digest (and an old-style
            # advisory issue that is still open is left to close when the check passes).
            tail = [line for line in (result.get("output_tail") or []) if line.strip()][-3:]
            digest_items.append(digest_item("advisory", f"{scope}:{check_id}", f"`{check_id}` failed in the {SCOPE_TITLES.get(scope, scope)} (advisory)",
                                            detail="; ".join(parts_text(result).splitlines()[:5]) or " / ".join(tail)[:300],
                                            scope=scope, run_url=run_url))
        elif outcome == "warn":
            fp = fingerprint(check_id, result.get("class"), sorted(p["part"] for p in result.get("parts") or []))
            why = f"{result.get('class')}, still in watch mode ({result.get('reason')}); it will block once promoted"
            body = (f"Release check `{check_id}` failed in the {SCOPE_TITLES.get(scope, scope)}{run} and did not block: {why}.\n\n"
                    + (f"Failing parts:\n\n{parts_text(result)}\n" if result.get("parts") else "")
                    + tail_block(result)
                    + f"\nFix the check or the code; this issue closes itself when `{check_id}` passes again. "
                      "See RESILIENCE.md and scripts/check-classes.json.\n\n" + marker("check-warning", check_id, scope, fp) + "\n")
            if issue is None:
                actions.append({"op": "create", "label": "check-warning", "title": f"Check warning: {check_id}", "body": body})
            elif issue["marker"].get("fp") != fp:
                actions.append({"op": "edit", "label": "check-warning", "number": issue["number"], "body": "What fails changed.\n\n" + body})
        elif outcome == "pass" and issue is not None and (issue["marker"].get("scope") in (None, scope)):
            actions.append({"op": "close", "label": "check-warning", "number": issue["number"],
                            "body": f"`{check_id}` passes again in the {SCOPE_TITLES.get(scope, scope)}{run}. Closing.\n\n"
                                    + marker("check-warning", check_id, scope, "closed") + "\n"})

    if decision is None:
        return actions + digest_actions(digest_items, open_issues)
    action = decision.get("action")
    fallback_issue = by_key.get(("fallback-active", scope))
    blocked_issue = by_key.get(("release-blocked", scope))
    blocking = [row for row in results if row.get("outcome") == "block"]
    details = "".join(f"\n**`{row['id']}`** ({row.get('class')}, {row.get('mode')}): {row.get('reason')}\n\n{parts_text(row)}{tail_block(row)}"
                      for row in blocking[:5])
    main, good = decision.get("main_commit"), decision.get("release_commit")
    compare = compare_url(repo, good, main)
    who = f" {mention}" if mention else ""
    if action == "use-fallback" and decision.get("landing_window"):
        # right after a CTAS code landing: expected until the next CTAS release (RESILIENCE.md)
        digest_items.append(digest_item("fallback", f"{scope}:landing-window", f"The {SCOPE_TITLES.get(scope, scope)} used the previous release's code (landing window)",
                                        detail=f"current `{(main or '')[:12]}`, used `{(good or '')[:12]}`; expected until the next CTAS release re-certifies main",
                                        scope=scope, run_url=run_url))
    elif action == "use-fallback":
        fp = fingerprint(main, good, decision.get("blocking"))
        verb = "deployed" if scope == "deploy" else "published"
        body = (f"The {SCOPE_TITLES.get(scope, scope)}{run} {verb} with fallback code{who}.\n\n"
                f"- current code `{(main or '')[:12]}` failed blocking check(s): {', '.join(f'`{c}`' for c in decision.get('blocking', []))}\n"
                + (f"- detail: {decision.get('blocking_detail')}\n" if decision.get("blocking_detail") else "")
                + f"- {verb} with `{(good or '')[:12]}` ({decision.get('fallback_label') or 'the code that produced the published release'}) on the same data\n"
                + (f"- commit range to look at: {compare}\n" if compare else "")
                + f"\n{decision.get('explanation')}\n{details}\n"
                  "Fix the current code (or revert the commit); the next run that succeeds with it closes this issue.\n\n"
                + marker("fallback-active", scope, None, fp) + "\n")
        if fallback_issue is None:
            actions.append({"op": "create", "label": "fallback-active",
                            "title": f"{FALLBACK_TITLES.get(scope, scope)} ({(good or '')[:7]})", "body": body})
        elif fallback_issue["marker"].get("fp") != fp:
            actions.append({"op": "edit", "label": "fallback-active", "number": fallback_issue["number"], "body": body})
    elif action == "proceed" and fallback_issue is not None:
        actions.append({"op": "close", "label": "fallback-active", "number": fallback_issue["number"],
                        "body": f"The {SCOPE_TITLES.get(scope, scope)} succeeded with main's code `{(main or '')[:12]}`{run}. Closing.\n\n"
                                + marker("fallback-active", scope, None, "closed") + "\n"})
    if action == "block":
        fp = fingerprint(decision.get("reason"), decision.get("blocking"), main)
        body = (f"Nothing was {'deployed' if scope == 'deploy' else 'published'} by the {SCOPE_TITLES.get(scope, scope)}{run}{who}: "
                f"**{decision.get('reason')}**. {decision.get('explanation')}\n\n"
                f"- current code: `{(main or '')[:12]}`; fallback code: `{(good or 'none')[:12]}`\n"
                + (f"- commit range: {compare}\n" if compare else "")
                + f"{details}\nThe live site keeps the previous release. Closes itself on the next run that "
                  f"{'deploys' if scope == 'deploy' else 'publishes'}.\n\n" + marker("release-blocked", scope, None, fp) + "\n")
        if blocked_issue is None and decision.get("reason") in QUIET_REASONS:
            pass  # a failure outside the classified checks: the freshness watchdog's run-result alerts cover it
        elif blocked_issue is None:
            ids = ", ".join(decision.get("blocking", [])) or decision.get("reason")
            actions.append({"op": "create", "label": "release-blocked",
                            "title": f"Release blocked: {SCOPE_TITLES.get(scope, scope)}: {ids}"[:200], "body": body})
        elif blocked_issue["marker"].get("fp") != fp:
            actions.append({"op": "edit", "label": "release-blocked", "number": blocked_issue["number"], "body": body})
    elif action in ("proceed", "use-fallback") and blocked_issue is not None:
        actions.append({"op": "close", "label": "release-blocked", "number": blocked_issue["number"],
                        "body": f"The {SCOPE_TITLES.get(scope, scope)} {'deployed' if scope == 'deploy' else 'published'} again{run}"
                                + (" (with fallback code)" if action == "use-fallback" else "") + ". Closing.\n\n"
                                + marker("release-blocked", scope, None, "closed") + "\n"})
    return actions + digest_actions(digest_items, open_issues)


# ----------------------------------------------------------------------------- daily digest
def today(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%d")


def digest_item(kind: str, key: str, title: str, *, detail: str = "", scope: str | None = None,
                run_url: str | None = None, now: dt.datetime | None = None) -> dict:
    """One informational finding. ``key`` identifies the finding across runs (repeats are counted)."""
    when = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"kind": kind if kind in DIGEST_KINDS else "note", "key": key, "title": title[:200], "detail": (detail or "")[:400],
            "scope": scope, "run_url": run_url, "first_seen": when, "last_seen": when, "count": 1}


def parse_digest(body: str | None) -> dict | None:
    found = list(DIGEST_MARKER.finditer(body or ""))
    if not found:
        return None
    try:
        data = json.loads(found[-1].group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None


def merge_digest_items(existing: list[dict], new: list[dict]) -> list[dict]:
    merged = {row["key"]: dict(row) for row in existing if isinstance(row, dict) and row.get("key")}
    for row in new:
        old = merged.get(row["key"])
        if old is None:
            merged[row["key"]] = dict(row)
        else:
            old["count"] = int(old.get("count") or 1) + 1
            old["last_seen"] = row["last_seen"]
            old["run_url"] = row.get("run_url") or old.get("run_url")
            old["detail"] = row.get("detail") or old.get("detail")
    return sorted(merged.values(), key=lambda r: (DIGEST_KINDS.index(r.get("kind", "note")) if r.get("kind") in DIGEST_KINDS else 9, r["first_seen"]))


def render_digest(date: str, items: list[dict]) -> str:
    names = {"advisory": "Advisory checks that failed (never blocking)", "lost-runner": "Runs that died with a lost runner",
             "fallback": "Fallbacks", "early": "Early warnings", "note": "Notes"}
    out = [f"What happened on {date} (UTC) that is worth knowing but needs no action. Updated in place by each run; "
           "nothing here blocked a release or a deploy. Hard problems open their own issue.\n"]
    for kind in DIGEST_KINDS:
        rows = [r for r in items if r.get("kind") == kind]
        if not rows:
            continue
        out.append(f"### {names[kind]}\n")
        for r in rows:
            times = f"{r['count']}× between {r['first_seen'][11:16]} and {r['last_seen'][11:16]}" if int(r.get("count") or 1) > 1 else f"at {r['first_seen'][11:16]}"
            link = f" ([run]({r['run_url']}))" if r.get("run_url") else ""
            out.append(f"- {r['title']} — {times}{link}" + (f"\n  - {r['detail']}" if r.get("detail") else ""))
        out.append("")
    out.append(f"<!-- daily-digest: {json.dumps({'date': date, 'items': items}, sort_keys=True)} -->\n")
    return "\n".join(out)


def digest_actions(items: list[dict], open_issues: list[dict], now: dt.datetime | None = None) -> list[dict]:
    """Create today's digest with ``items``, or merge them into it; close older digests."""
    date = today(now)
    digests = [issue for issue in open_issues if issue.get("label") == DIGEST_LABEL or parse_digest(issue.get("body"))]
    actions = []
    current = None
    for issue in digests:
        data = parse_digest(issue.get("body")) or {}
        if data.get("date") == date and current is None:
            current = (issue, data)
        else:
            actions.append({"op": "close", "label": DIGEST_LABEL, "number": issue["number"], "body": ""})
    if not items:
        return actions
    if current is None:
        return actions + [{"op": "create", "label": DIGEST_LABEL, "title": f"Daily digest {date}", "body": render_digest(date, merge_digest_items([], items))}]
    issue, data = current
    merged = merge_digest_items(data.get("items") or [], items)
    return actions + [{"op": "edit", "label": DIGEST_LABEL, "number": issue["number"], "body": render_digest(date, merged)}]


# ----------------------------------------------------------------------------- GitHub CLI
def gh(args: list[str], runner: Callable = subprocess.run) -> str:
    completed = runner(["gh", *args], capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {sanitize(completed.stderr.strip())[:300]}")
    return completed.stdout


def open_issues(repo: str, runner: Callable = subprocess.run) -> list[dict]:
    issues = []
    for label in LABELS:
        listing = json.loads(gh(["issue", "list", "--repo", repo, "--label", label, "--state", "open",
                                 "--limit", "100", "--json", "number,title,body"], runner) or "[]")
        for issue in listing:
            # Issues are edited in place, so the body carries the newest marker; the comments
            # are read only for issues from before 2026-11-24, whose updates were comments.
            mark = parse_marker(issue.get("body"))
            if mark and label != DIGEST_LABEL:
                try:
                    view = json.loads(gh(["issue", "view", str(issue["number"]), "--repo", repo, "--json", "comments"], runner) or "{}")
                    for comment in reversed(view.get("comments") or []):
                        newer = parse_marker(comment.get("body"))
                        if newer:
                            mark = newer
                            break
                except (RuntimeError, ValueError):
                    pass
            issues.append({**issue, "label": label, "last_marker": mark})
    return issues


WITHHELD = ("The details of this update were withheld: after redaction a credential-shaped string was still in "
            "them, so they are not posted to this public issue. Read them in the workflow run's log.")


def public_body(body: str, secrets: list[str]) -> str:
    """The body as posted: sanitized, then scanned; when a credential shape survives, a fixed
    notice that keeps the issue's hidden marker (so the next run still finds its issue)."""
    clean = sanitize(body, secrets)[:60000]
    if not leaks(clean, secrets):
        return clean
    data = parse_digest(body)
    if data is not None:
        # A digest keeps its state in the marker: post it again with every detail dropped and
        # the titles sanitized; if a credential shape still survives, the day's list is lost.
        bare = {"date": data.get("date"), "items": [{**row, "detail": "", "title": sanitize(str(row.get("title", "")), secrets)}
                                                    for row in data["items"]]}
        marker_text = f"<!-- daily-digest: {json.dumps(bare, sort_keys=True)} -->\n"
        return WITHHELD + ("\n\n" + marker_text if not leaks(marker_text, secrets) else "\n")
    found = list(MARKER.finditer(body or ""))
    return WITHHELD + ("\n\n" + found[-1].group(0) + "\n" if found else "\n")


def public_title(title: str, secrets: list[str]) -> str:
    clean = sanitize(title, secrets)
    return "Release check issue (title withheld: it contained a credential shape)" if leaks(clean, secrets) else clean


def apply(actions: list[dict], repo: str, secrets: list[str], runner: Callable = subprocess.run) -> None:
    """Ensure the labels exist (create or update, --force), then run every action. A label or
    action that fails is reported and the rest still run; the failures are raised together at
    the end so the caller prints one WARN naming each of them."""
    failures = []
    for label, (color, description) in LABELS.items():
        try:
            gh(["label", "create", label, "--repo", repo, "--color", color,
                "--description", description[:LABEL_DESCRIPTION_MAX], "--force"], runner)
        except RuntimeError as error:
            failures.append(f"label {label}: {error}")
    for action in actions:
        body = public_body(action["body"], secrets)
        if action.get("title") is not None:
            action = {**action, "title": public_title(action["title"], secrets)}
        try:
            if action["op"] == "create":
                url = gh(["issue", "create", "--repo", repo, "--label", action["label"], "--title", action["title"], "--body", body], runner)
                print(f"opened {url.strip()} ({action['label']})")
            elif action["op"] == "comment":
                gh(["issue", "comment", str(action["number"]), "--repo", repo, "--body", body], runner)
                print(f"commented on #{action['number']} ({action['label']})")
            elif action["op"] == "edit":
                gh(["issue", "edit", str(action["number"]), "--repo", repo, "--body", body], runner)
                print(f"updated #{action['number']} in place ({action['label']})")
            elif action["op"] == "close":
                gh(["issue", "close", str(action["number"]), "--repo", repo] + (["--comment", body] if body.strip() else []), runner)
                print(f"closed #{action['number']} ({action['label']})")
        except RuntimeError as error:
            failures.append(f"{action['op']} {action.get('number') or action['label']}: {error}")
    if failures:
        raise RuntimeError("; ".join(failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sync")
    p.add_argument("--scope", required=True, choices=sorted(SCOPE_TITLES))
    p.add_argument("--results", action="append", default=[])
    p.add_argument("--decision")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    p.add_argument("--run-url")
    p.add_argument("--mention", default=os.environ.get("RELEASE_ISSUE_MENTION", ""))
    p.add_argument("--secret-env", default="")
    p.add_argument("--secret-file", action="append", default=[])
    p.add_argument("--dry-run", action="store_true", help="print the planned actions; call nothing")
    d = sub.add_parser("digest-add", help="add one informational finding to today's digest issue")
    d.add_argument("--kind", required=True, choices=DIGEST_KINDS)
    d.add_argument("--key", required=True, help="identifies the finding across runs (repeats are counted, not repeated)")
    d.add_argument("--title", required=True)
    d.add_argument("--detail", default="")
    d.add_argument("--scope", default=None)
    d.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    d.add_argument("--run-url")
    d.add_argument("--secret-env", default="")
    d.add_argument("--secret-file", action="append", default=[])
    d.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "digest-add":
        return main_digest_add(args)
    results = [row for path in args.results for row in read_jsonl(path)]
    decision = None
    if args.decision and Path(args.decision).is_file():
        decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    run_url = args.run_url
    if not run_url and os.environ.get("GITHUB_RUN_ID") and args.repo:
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{args.repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    secrets = collect_secrets(args.secret_env, args.secret_file)   # every SECRET_ENV_NAMES variable, plus these
    if args.dry_run:
        for action in plan(args.scope, results, decision, [], repo=args.repo, run_url=run_url, mention=args.mention):
            print(json.dumps({**action, "body": public_body(action["body"], secrets)[:400]}, sort_keys=True))
        return 0
    if not args.repo:
        print("no repository named (GITHUB_REPOSITORY); issues not synced", file=sys.stderr)
        return 0
    try:
        actions = plan(args.scope, results, decision, open_issues(args.repo), repo=args.repo, run_url=run_url, mention=args.mention)
        apply(actions, args.repo, secrets)
    except (RuntimeError, OSError, ValueError) as error:
        # Issues are the notification, never the release: a failure here is reported, not fatal.
        print(f"WARN  could not sync the release-check issues: {sanitize(str(error), secrets)}", file=sys.stderr)
        return 0
    if not actions:
        print("release-check issues: nothing to change")
    return 0


def main_digest_add(args) -> int:
    run_url = args.run_url
    if not run_url and os.environ.get("GITHUB_RUN_ID") and args.repo:
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{args.repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    secrets = collect_secrets(args.secret_env, args.secret_file)
    item = digest_item(args.kind, args.key, args.title, detail=args.detail, scope=args.scope, run_url=run_url)
    if args.dry_run:
        for action in digest_actions([item], []):
            print(json.dumps({**action, "body": public_body(action["body"], secrets)[:400]}, sort_keys=True))
        return 0
    if not args.repo:
        print("no repository named (GITHUB_REPOSITORY); digest not updated", file=sys.stderr)
        return 0
    try:
        listing = json.loads(gh(["issue", "list", "--repo", args.repo, "--label", DIGEST_LABEL, "--state", "open",
                                 "--limit", "20", "--json", "number,title,body"]) or "[]")
        issues = [{**issue, "label": DIGEST_LABEL} for issue in listing]
        apply(digest_actions([item], issues), args.repo, secrets)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"WARN  could not update the daily digest: {sanitize(str(error), secrets)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
