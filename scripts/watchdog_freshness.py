#!/usr/bin/env python3
"""Check that the live CTAS and WorldsIndex releases are still being published.

Both publishers run in GitHub Actions (ctas-cloud.yml, worldsindex-cloud.yml; until
October 2026 on one Mac) and fail closed: when either refuses a release it
keeps refusing, silently, until someone looks. GitHub Pages keeps serving the last
snapshot, so nothing on the site says "the publisher stopped". This check runs in
GitHub Actions on a schedule (see .github/workflows/freshness-watchdog.yml), reads
the public status files, and decides whether either publisher has stalled.

Three signals, in order of certainty:

1. Cross-check. The two publishers run under the same conditions (GitHub Actions
   available). If WorldsIndex published within the last couple of hours but CTAS has
   not published for several, Actions is evidently working and the CTAS publisher
   itself is stuck; the reverse holds for WorldsIndex. This catches the
   2026-09-07 and 2026-09-24 CTAS outages within hours instead of days.
2. Absolute age. A release older than the configured maximum means either GitHub
   Actions has not run them for that long or both publishers are stuck. Either is
   worth knowing about.
3. Deployment. If the last "Validate and deploy site" run failed, pushes are
   landing but the live site is frozen at the previous green commit.
4. Run results. The cloud workflows can fail *after* publishing (2026-10-14: every
   WorldsIndex run published, then failed saving its working state, so the site
   stayed fresh while every run restored the same stale state). The last few
   completed runs of ctas-cloud.yml and worldsindex-cloud.yml (`gh run list`) are
   therefore read too: a streak of failures, or a high failure rate over a day,
   is an alert, classified as "published but failed afterwards" when the release
   is newer than the failures, otherwise as "not publishing".
5. State saves. Each cloud run publishes a small marker (`ctas/data/cloud-run.json`,
   `worldsindex/data/cloud-run.json`, written by scripts/cloud_run_marker.py) that
   names the state copy it restored and when that copy was saved. A fresh marker
   whose saved-at time is old means the runs publish but their saves fail.
6. The state key. The same marker records whether GitHub accepted the
   CTAS_STATE_TOKEN key and, for keys that expire, the expiry date; a rejected key
   or one expiring within ten days is an alert before every run starts failing.

The script never fails the workflow on a stale release; it writes a JSON report
and a Markdown summary, and the workflow turns those into one GitHub issue that is
opened, updated when the situation changes, and closed on recovery. Only genuine
errors (bad arguments, unwritable report) exit non-zero.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

UTC = dt.timezone.utc
CTAS_STATUS_PATH = "ctas/data/status.json"
WORLDSINDEX_MANIFEST_PATH = "worldsindex/data/manifest.json"
CTAS_MARKER_PATH = "ctas/data/cloud-run.json"
WORLDSINDEX_MARKER_PATH = "worldsindex/data/cloud-run.json"
DEPLOY_WORKFLOW_NAME = "Validate and deploy site"

DEFAULT_THRESHOLDS = {
    # Absolute maxima. The laptop is open about ten hours a day, so a closed
    # stretch of ~14 h is normal; these sit above that so an ordinary day does not
    # raise an alert on its own. The cross-check below does not depend on sleep.
    "ctas_max_age_hours": 18.0,
    "worldsindex_max_age_hours": 30.0,
    # Cross-check: one publisher is "evidently running" if it published within
    # cross_check_fresh_hours; the other is "stalled" if it has not published for
    # cross_check_stale_hours while the first one is running. CTAS normally
    # publishes every ~18 minutes and WorldsIndex roughly hourly while awake.
    "cross_check_fresh_hours": 2.0,
    "cross_check_stale_hours": 3.0,
    # Run results: an alert when this many completed runs in a row failed, or when
    # more than failure_rate_percent of at least four completed runs in the last
    # failure_window_hours failed.
    "consecutive_failures": 3.0,
    "failure_rate_percent": 50.0,
    "failure_window_hours": 24.0,
    # State saves: the marker a run publishes names the state copy it restored. CTAS
    # runs every ~30-45 min and WorldsIndex hourly, so the restored copy is normally
    # under two hours old when the marker is read; older than this, the saves fail.
    "ctas_state_max_age_hours": 3.0,
    "worldsindex_state_max_age_hours": 6.0,
    # The state key: warn this many days before the expiry date the marker records.
    "token_warn_days": 10.0,
}

# The cloud workflows whose runs are read, keyed by the alert-code prefix.
CLOUD_WORKFLOWS = {
    "ctas": {"file": "ctas-cloud.yml", "title": "CTAS cloud collector and publisher",
             "artifact": "ctas-publish-log"},
    "worldsindex": {"file": "worldsindex-cloud.yml", "title": "WorldsIndex cloud builder and publisher",
                    "artifact": "worldsindex-publish-log"},
}
# Run conclusions that say nothing about the publisher and are left out of the streak.
UNINFORMATIVE_CONCLUSIONS = {"cancelled", "skipped", None}
# Most urgent first: the first alert's action becomes the first line of the issue.
SEVERITY = ("state-token-rejected", "-runs-failing", "-stalled-while-", "-stale", "-unavailable",
            "-runs-failing-after-publish", "-state-not-saved", "deploy-failed", "state-token-expiring")
RENEW = "run `Renew cloud key.command` on the Mac (it copies a fresh `gh auth token` into the CTAS_STATE_TOKEN secret)"


def parse_utc(value):
    """Parse an ISO-8601 timestamp (Z or offset, optional fraction) to aware UTC."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso(moment):
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if moment else None


def hours_between(later, earlier):
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds() / 3600.0, 2)


def fetch_json(url, attempts=3, timeout=30):
    """Fetch a JSON document, bypassing CDN caches; return (document, error)."""
    error = None
    for attempt in range(1, attempts + 1):
        try:
            separator = "&" if "?" in url else "?"
            request = urllib.request.Request(
                f"{url}{separator}watchdog={int(time.time())}",
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache",
                         "User-Agent": "jackmcguireastro-freshness-watchdog"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response), None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                time.sleep(5 * attempt)
    return None, error


def describe_ctas(status, now):
    """Reduce the CTAS status document to the freshness facts this check uses."""
    if not isinstance(status, dict):
        return {"available": False, "error": "status document is not a JSON object"}
    last_update = parse_utc(status.get("last_successful_update") or status.get("generated_at"))
    valid_until = parse_utc(status.get("valid_until"))
    return {
        "available": True,
        "last_update": iso(last_update),
        "age_hours": hours_between(now, last_update),
        "valid_until": iso(valid_until),
        "certificate_expired": bool(valid_until and valid_until < now),
        "candidate_count": status.get("candidate_count"),
        "pipeline_status": status.get("pipeline_status"),
        "degraded_source_count": status.get("degraded_source_count"),
        "latest_record_update": status.get("latest_record_update"),
    }


def describe_worldsindex(manifest, now):
    if not isinstance(manifest, dict):
        return {"available": False, "error": "manifest document is not a JSON object"}
    generated = parse_utc(manifest.get("generatedAt"))
    return {
        "available": True,
        "last_update": iso(generated),
        "age_hours": hours_between(now, generated),
        "object_count": manifest.get("objectCount"),
        "detail_record_count": manifest.get("detailRecordCount"),
        "atlas_generated_at": manifest.get("atlasGeneratedAt"),
    }


def describe_marker(marker, error, now):
    """Reduce a cloud-run marker (scripts/cloud_run_marker.py) to the facts this check uses."""
    if error or not isinstance(marker, dict):
        return {"available": False, "error": error or "marker is not a JSON object"}
    generated = parse_utc(marker.get("generated_at"))
    saved = parse_utc(marker.get("state_saved_at"))
    expires = parse_utc(marker.get("token_expires_at"))  # a date; midnight UTC
    return {
        "available": True,
        "generated_at": iso(generated),
        "age_hours": hours_between(now, generated),
        "run_id": marker.get("run_id"),
        "run_url": marker.get("run_url"),
        "state_asset": marker.get("state_asset"),
        "state_saved_at": iso(saved),
        "state_age_hours": hours_between(now, saved),
        "token_status": marker.get("token_status") or "not-checked",
        "token_checked_at": marker.get("token_checked_at"),
        "token_expires_at": expires.date().isoformat() if expires else None,
        "token_days_left": (expires.date() - now.date()).days if expires else None,
    }


def describe_deploy(deploy):
    """Normalize `gh run list --json ...` output (newest first) or a single run object.

    Reports the newest *completed* run and when the last successful one started, so a
    failure that a later run has already fixed, or one still inside the expected
    window after a CTAS code change (CI stays red until the next CTAS release
    re-certifies the new code), is not mistaken for a frozen site.
    """
    runs = deploy if isinstance(deploy, list) else [deploy] if isinstance(deploy, dict) else []
    runs = [run for run in runs if isinstance(run, dict)]
    completed = [run for run in runs if run.get("status") == "completed"]
    if not completed:
        return {"known": False}
    run = completed[0]
    last_success = next((item.get("createdAt") for item in completed if item.get("conclusion") == "success"), None)
    return {
        "known": True,
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("createdAt"),
        "head_sha": (run.get("headSha") or "")[:10],
        "title": run.get("displayTitle"),
        "url": run.get("url"),
        "last_success_at": last_success,
    }


def failed_step(run_jobs):
    """Name of the first failed step in `gh run view --json jobs` output, or None."""
    jobs = run_jobs.get("jobs") if isinstance(run_jobs, dict) else None
    for job in jobs or []:
        if not isinstance(job, dict) or job.get("conclusion") == "success":
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("conclusion") not in ("success", "skipped", None):
                return step.get("name")
    return None


def describe_runs(runs, run_jobs, now, window_hours):
    """Reduce `gh run list --json conclusion,status,createdAt,url,databaseId` output for one
    cloud workflow (newest first) to the facts the run-result check uses.

    Only completed runs with an informative conclusion count; cancelled and skipped runs
    (concurrency, a disabled schedule) say nothing about the publisher. `run_jobs` maps a
    run's databaseId (as a string) to its `gh run view --json jobs` document, when fetched.
    """
    rows = runs if isinstance(runs, list) else []
    completed = [row for row in rows if isinstance(row, dict) and row.get("status") == "completed"
                 and row.get("conclusion") not in UNINFORMATIVE_CONCLUSIONS]
    completed.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    if not completed:
        return {"known": False}
    streak = 0
    for row in completed:
        if row.get("conclusion") == "success":
            break
        streak += 1
    since = now - dt.timedelta(hours=window_hours)
    in_window = [row for row in completed if (parse_utc(row.get("createdAt")) or since) >= since]
    failed_in_window = [row for row in in_window if row.get("conclusion") != "success"]
    newest_failed = next((row for row in completed if row.get("conclusion") != "success"), None)
    streak_started = completed[streak - 1].get("createdAt") if streak else None
    jobs = (run_jobs or {}).get(str(newest_failed.get("databaseId"))) if newest_failed else None
    return {
        "known": True,
        "newest_conclusion": completed[0].get("conclusion"),
        "newest_created_at": completed[0].get("createdAt"),
        "consecutive_failures": streak,
        "streak_started_at": streak_started,
        "window_completed": len(in_window),
        "window_failed": len(failed_in_window),
        "window_failure_percent": round(100.0 * len(failed_in_window) / len(in_window), 1) if in_window else 0.0,
        "last_success_at": next((row.get("createdAt") for row in completed if row.get("conclusion") == "success"), None),
        "newest_failed_url": newest_failed.get("url") if newest_failed else None,
        "newest_failed_at": newest_failed.get("createdAt") if newest_failed else None,
        "failed_step": failed_step(jobs) if jobs else None,
    }


def severity(code):
    """Rank of an alert code in SEVERITY (lower is more urgent): exact, suffix or infix match."""
    for rank, pattern in enumerate(SEVERITY):
        if code == pattern or (pattern.endswith("-") and pattern in code) or \
                (pattern.startswith("-") and code.endswith(pattern)):
            return rank
    return len(SEVERITY)


def describe_action(code, report):
    """One sentence saying what to do about an alert, from the report's facts."""
    name = code.split("-")[0]
    workflow = CLOUD_WORKFLOWS.get(name)
    runs = report["runs"].get(name) or {}
    newest = f" ({runs['newest_failed_url']})" if runs.get("newest_failed_url") else ""
    step = f"step '{runs['failed_step']}'" if runs.get("failed_step") else "the failed step"
    if code == "state-token-rejected":
        return f"GitHub rejected the state key: {RENEW}, then start each cloud workflow by hand (Actions -> Run workflow)."
    if code == "state-token-expiring":
        return f"Before {report['token'].get('token_expires_at')}, {RENEW}."
    if code.endswith("-runs-failing-after-publish"):
        return (f"Open the newest failed run of {workflow['file']}{newest}: it published and then failed in {step}, "
                f"most likely saving the state. Save the log and the `{workflow['artifact']}` artifact, then check "
                "the state repository's `state` release and the key's write access.")
    if code.endswith("-runs-failing"):
        return (f"Open the newest failed run of {workflow['file']}{newest} and read {step}; save the log and the "
                f"`{workflow['artifact']}` artifact. If it fails at the private checkout or the restore, {RENEW}.")
    if code.endswith("-state-not-saved"):
        marker = report["markers"].get(name) or {}
        run = f" ({marker['run_url']})" if marker.get("run_url") else ""
        return (f"The {workflow['file']} runs keep restoring a state saved {marker.get('state_saved_at')}, so the "
                f"save at the end of each run fails: open the newest run{run}, read the lines after "
                "\"saving ... to the state repository\" and save the log.")
    if "-stalled-while-" in code or code.endswith("-stale"):
        return (f"Open the newest run of {workflow['file']} (Actions -> \"{workflow['title']}\"), save its log and "
                f"`{workflow['artifact']}` artifact, and start the workflow by hand; if every run fails at the "
                f"first checkout, {RENEW}.")
    if code.endswith("-unavailable"):
        path = CTAS_STATUS_PATH if name == "ctas" else WORLDSINDEX_MANIFEST_PATH
        return f"Check that {path} is served by the site (a broken deploy or a GitHub Pages outage); see the last deploy run."
    if code == "deploy-failed":
        url = f" ({report['deploy']['url']})" if report["deploy"].get("url") else ""
        return f"Open the failed '{DEPLOY_WORKFLOW_NAME}' run{url}; fix or revert the commit, or re-run the last green one."
    return "See the runbook (CTAS-AUTOMATION.md, \"When an alert fires\")."


def evaluate(ctas_status, ctas_error, worldsindex_manifest, worldsindex_error, deploy, now=None,
             ctas_runs=None, worldsindex_runs=None, run_jobs=None,
             ctas_marker=None, ctas_marker_error=None, worldsindex_marker=None, worldsindex_marker_error=None,
             **thresholds):
    """Return the full report. Pure function: everything it needs is passed in."""
    now = now or dt.datetime.now(UTC)
    limits = dict(DEFAULT_THRESHOLDS)
    limits.update({key: float(value) for key, value in thresholds.items() if value is not None})
    alerts = []
    notes = []

    def alert(code, message):
        alerts.append({"code": code, "message": message})

    ctas = {"available": False, "error": ctas_error} if ctas_error else describe_ctas(ctas_status, now)
    worlds = ({"available": False, "error": worldsindex_error} if worldsindex_error
              else describe_worldsindex(worldsindex_manifest, now))
    deployment = describe_deploy(deploy)

    if not ctas["available"]:
        alert("ctas-unavailable", f"CTAS status could not be read: {ctas.get('error')}")
    elif ctas["age_hours"] is None:
        alert("ctas-unavailable", "CTAS status has no readable last_successful_update timestamp")
    if not worlds["available"]:
        alert("worldsindex-unavailable", f"WorldsIndex manifest could not be read: {worlds.get('error')}")
    elif worlds["age_hours"] is None:
        alert("worldsindex-unavailable", "WorldsIndex manifest has no readable generatedAt timestamp")

    ctas_age = ctas.get("age_hours")
    worlds_age = worlds.get("age_hours")

    # 1. cross-check: one publisher running proves the runners are working.
    if ctas_age is not None and worlds_age is not None:
        if worlds_age <= limits["cross_check_fresh_hours"] and ctas_age > limits["cross_check_stale_hours"]:
            alert("ctas-stalled-while-worldsindex-publishes",
                  f"CTAS has not published for {ctas_age:g} h while WorldsIndex published "
                  f"{worlds_age:g} h ago, so the cloud runs are working and the CTAS publisher itself is stuck.")
        if ctas_age <= limits["cross_check_fresh_hours"] and worlds_age > limits["cross_check_stale_hours"]:
            alert("worldsindex-stalled-while-ctas-publishes",
                  f"WorldsIndex has not published for {worlds_age:g} h while CTAS published "
                  f"{ctas_age:g} h ago, so the cloud runs are working and the WorldsIndex publisher itself is stuck.")

    # 2. absolute age.
    if ctas_age is not None and ctas_age > limits["ctas_max_age_hours"]:
        alert("ctas-stale", f"CTAS last published {ctas['last_update']} ({ctas_age:g} h ago; "
                            f"limit {limits['ctas_max_age_hours']:g} h).")
    if worlds_age is not None and worlds_age > limits["worldsindex_max_age_hours"]:
        alert("worldsindex-stale", f"WorldsIndex last published {worlds['last_update']} ({worlds_age:g} h ago; "
                                   f"limit {limits['worldsindex_max_age_hours']:g} h).")

    # 3. deployment: the newest completed run failed and nothing has deployed for a while.
    if deployment.get("known") and deployment.get("conclusion") not in ("success", None):
        success_age = hours_between(now, parse_utc(deployment.get("last_success_at")))
        if success_age is None or success_age > limits["cross_check_stale_hours"]:
            since = (f"nothing has deployed for {success_age:g} h" if success_age is not None
                     else "no recent successful deployment")
            alert("deploy-failed", f"The last '{DEPLOY_WORKFLOW_NAME}' run ({deployment.get('head_sha')}, "
                                   f"{deployment.get('title')}) ended with {deployment.get('conclusion')} and "
                                   f"{since}; the live site is frozen at the previous green deployment.")
        else:
            notes.append(f"The last deploy run failed, but a deployment succeeded {success_age:g} h ago; "
                         "a failure right after a CTAS code change clears on the next CTAS release.")

    # 4. run results: a streak of failed runs, or a high failure rate over the window.
    runs = {}
    for name, workflow in CLOUD_WORKFLOWS.items():
        summary = describe_runs(ctas_runs if name == "ctas" else worldsindex_runs, run_jobs, now,
                                limits["failure_window_hours"])
        runs[name] = summary
        if not summary["known"]:
            continue
        streak = summary["consecutive_failures"]
        rate = summary["window_failure_percent"]
        if streak >= limits["consecutive_failures"]:
            how = f"the last {streak} completed runs of {workflow['file']} failed"
            failing_since = summary["streak_started_at"]
        elif summary["window_completed"] >= 4 and rate > limits["failure_rate_percent"]:
            how = (f"{summary['window_failed']} of the last {summary['window_completed']} completed runs of "
                   f"{workflow['file']} ({rate:g}%) failed in {limits['failure_window_hours']:g} h")
            failing_since = summary["newest_failed_at"]
        else:
            continue
        step = f" in step '{summary['failed_step']}'" if summary.get("failed_step") else ""
        newest = f" (newest: {summary['newest_failed_url']})" if summary.get("newest_failed_url") else ""
        component = ctas if name == "ctas" else worlds
        last_update = parse_utc(component.get("last_update"))
        since = parse_utc(failing_since)
        if last_update and since and last_update > since:
            alert(f"{name}-runs-failing-after-publish",
                  f"{how[0].upper()}{how[1:]}{step}{newest}, yet the release is current (published "
                  f"{component['age_hours']:g} h ago, after the failures began): the run fails after "
                  "publishing, so the working state is not being saved and every run restores an old one.")
        else:
            alert(f"{name}-runs-failing",
                  f"{how[0].upper()}{how[1:]}{step}{newest} and nothing was published since they began: "
                  "the publisher is not publishing at all.")

    # 5. state saves: the runs publish (a recent marker) but restore an old state copy.
    markers = {"ctas": describe_marker(ctas_marker, ctas_marker_error, now),
               "worldsindex": describe_marker(worldsindex_marker, worldsindex_marker_error, now)}
    for name, workflow in CLOUD_WORKFLOWS.items():
        marker = markers[name]
        label = "CTAS" if name == "ctas" else "WorldsIndex"
        if not marker["available"]:
            notes.append(f"No cloud-run marker for {label} yet ({marker.get('error')}); the state-save check "
                         "starts with the first release published by the updated workflow.")
            continue
        limit = limits[f"{name}_state_max_age_hours"]
        state_age, marker_age = marker.get("state_age_hours"), marker.get("age_hours")
        if state_age is None:
            notes.append(f"The {label} cloud-run marker has no readable state_saved_at.")
        elif state_age > limit and marker_age is not None and marker_age <= limit:
            alert(f"{name}-state-not-saved",
                  f"The {label} run that published {marker_age:g} h ago restored a working state saved "
                  f"{state_age:g} h ago ({marker.get('state_asset')}; limit {limit:g} h): the runs publish, "
                  "but saving the state at the end of each run fails, so every run restores an old state.")

    # 6. the state key, as the most recently published marker saw it (one secret, two users).
    checked = [m for m in markers.values() if m["available"] and m["token_status"] not in ("not-checked", "unknown")]
    checked.sort(key=lambda m: m.get("token_checked_at") or "", reverse=True)
    key = checked[0] if checked else None
    if key and key["token_status"] == "unauthorized":
        alert("state-token-rejected",
              f"GitHub rejected the CTAS_STATE_TOKEN key (HTTP 401) when the run that published at "
              f"{key['generated_at']} tried it; the cloud runs will fail at the private checkout until it is renewed.")
    elif key and key["token_days_left"] is not None and key["token_days_left"] <= limits["token_warn_days"]:
        days = key["token_days_left"]
        when = (f"expires on {key['token_expires_at']} ({days} day{'s' if days != 1 else ''} left)" if days >= 0
                else f"expired on {key['token_expires_at']} ({-days} day{'s' if days != -1 else ''} ago)")
        alert("state-token-expiring", f"The CTAS_STATE_TOKEN key {when}; every cloud run fails once it has expired.")

    if ctas.get("available") and ctas.get("certificate_expired") and not any(
            row["code"].startswith("ctas") for row in alerts):
        notes.append("The CTAS certificate has expired (90-minute validity) but the release is within limits; "
                     "the next cloud run renews it.")
    if ctas.get("pipeline_status") == "degraded":
        notes.append(f"CTAS reports pipeline_status=degraded ({ctas.get('degraded_source_count')} upstream "
                     "sources); that concerns providers, not publication.")

    codes = sorted(row["code"] for row in alerts)
    report = {
        "checked_at": iso(now),
        "ok": not alerts,
        "alerts": sorted(alerts, key=lambda row: (severity(row["code"]), row["code"])),
        "notes": notes,
        "fingerprint": "|".join(codes) if codes else "ok",
        "thresholds": limits,
        "ctas": ctas,
        "worldsindex": worlds,
        "deploy": deployment,
        "runs": runs,
        "markers": markers,
        "token": key or {"token_status": "not-checked"},
    }
    for row in report["alerts"]:
        row["action"] = describe_action(row["code"], report)
    # A few lines for a push notification or chat message (scripts/watchdog_issue.sh).
    if alerts:
        report["summary"] = (f"Do this first: {report['alerts'][0]['action']}\n"
                             + "\n".join(f"- {row['code']}: {row['message']}" for row in report["alerts"]))
    else:
        report["summary"] = f"Both publishers are current as of {report['checked_at']}."
    return report


def render_markdown(report, site_url, mention=""):
    """Markdown for the job summary and the GitHub issue body."""
    def age(value):
        return "—" if value is None else f"{value:g} h"

    ctas, worlds, deploy = report["ctas"], report["worldsindex"], report["deploy"]
    lines = []
    if report["ok"]:
        lines.append(f"**Both publishers are current** as of {report['checked_at']}.")
    else:
        lines.append(f"**Do this first:** {report['alerts'][0]['action']}")
        lines.append("")
        lines.append(f"**Publisher freshness alert** as of {report['checked_at']}"
                     + (f" — {mention}" if mention else ""))
        lines.append("")
        for row in report["alerts"]:
            lines.append(f"- `{row['code']}`: {row['message']}")
            if row["action"] != report["alerts"][0]["action"]:
                lines.append(f"  - To do: {row['action']}")
    lines.append("")
    lines.append("| Component | Last release (UTC) | Age | Alert limit | Detail |")
    lines.append("|---|---|---|---|---|")
    ctas_detail = (f"{ctas.get('candidate_count')} candidates; certificate "
                   f"{'expired at' if ctas.get('certificate_expired') else 'valid until'} {ctas.get('valid_until')}"
                   if ctas.get("available") else f"unavailable: {ctas.get('error')}")
    worlds_detail = (f"{worlds.get('object_count')} objects, {worlds.get('detail_record_count')} rows"
                     if worlds.get("available") else f"unavailable: {worlds.get('error')}")
    lines.append(f"| [CTAS]({site_url}/ctas.html) | {ctas.get('last_update') or '—'} | {age(ctas.get('age_hours'))} "
                 f"| {report['thresholds']['ctas_max_age_hours']:g} h | {ctas_detail} |")
    lines.append(f"| [WorldsIndex]({site_url}/worldsindex/) | {worlds.get('last_update') or '—'} | "
                 f"{age(worlds.get('age_hours'))} | {report['thresholds']['worldsindex_max_age_hours']:g} h | {worlds_detail} |")
    if deploy.get("known"):
        state = deploy.get("conclusion") or deploy.get("status")
        link = f"[{state}]({deploy['url']})" if deploy.get("url") else state
        lines.append(f"| Last deploy | {deploy.get('created_at') or '—'} | — | — | {link}: {deploy.get('title')} |")
    for name in CLOUD_WORKFLOWS:
        marker = report.get("markers", {}).get(name) or {}
        if marker.get("available"):
            label = "CTAS" if name == "ctas" else "WorldsIndex"
            run = f"[run {marker['run_id']}]({marker['run_url']})" if marker.get("run_url") else "run"
            lines.append(f"| {label} state save | {marker.get('state_saved_at') or '—'} | "
                         f"{age(marker.get('state_age_hours'))} | {report['thresholds'][name + '_state_max_age_hours']:g} h "
                         f"| {marker.get('state_asset') or '—'}, restored by {run} {age(marker.get('age_hours'))} ago |")
    key = report.get("token") or {}
    if key.get("token_status") not in (None, "not-checked"):
        expiry = (f"expires {key['token_expires_at']} ({key['token_days_left']} days)" if key.get("token_expires_at")
                  else "no expiry date")
        lines.append(f"| State key (CTAS_STATE_TOKEN) | {key.get('token_checked_at') or '—'} | — | "
                     f"{report['thresholds']['token_warn_days']:g} days | {key['token_status']}; {expiry} |")
    for name, workflow in CLOUD_WORKFLOWS.items():
        runs = report.get("runs", {}).get(name) or {}
        if not runs.get("known"):
            continue
        state = runs["newest_conclusion"]
        if runs.get("newest_failed_url") and state != "success":
            state = f"[{state}]({runs['newest_failed_url']})"
        detail = (f"{state}; {runs['consecutive_failures']} failed in a row; "
                  f"{runs['window_failed']}/{runs['window_completed']} failed in "
                  f"{report['thresholds']['failure_window_hours']:g} h")
        if runs.get("failed_step"):
            detail += f"; failed step: {runs['failed_step']}"
        lines.append(f"| Runs of {workflow['file']} | {runs.get('newest_created_at') or '—'} | — | "
                     f"{report['thresholds']['consecutive_failures']:g} in a row | {detail} |")
    if report["notes"]:
        lines.append("")
        for note in report["notes"]:
            lines.append(f"_{note}_")
    if not report["ok"]:
        lines.append("")
        lines.append("**Where to look**")
        lines.append("")
        lines.append("- CTAS: Actions -> \"CTAS cloud collector and publisher\": the newest run's log and its "
                     "`ctas-publish-log` artifact. Runs chain themselves; the relay's 20-minute keeper restarts them. "
                     "Run it by hand with \"Run workflow\".")
        lines.append("- WorldsIndex: Actions -> \"WorldsIndex cloud builder and publisher\" and its "
                     "`worldsindex-publish-log` artifact.")
        lines.append("- Every run failing at the first checkout or state step: the CTAS_STATE_TOKEN key has stopped "
                     "working (gh signed out or revoked); run `Renew cloud key.command`. A failed deploy: re-run the "
                     "last green workflow or revert the commit.")
        lines.append("- The runbook for every alert code: CTAS-AUTOMATION.md, \"When an alert fires\".")
        lines.append("")
        lines.append("This issue is maintained by the freshness watchdog workflow: it comments when the situation "
                     "changes (and every 12 hours while it persists) and closes itself when both publishers are current again.")
    return "\n".join(lines) + "\n"


def load_json_file(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="https://jackmcguireastro.github.io", help="site origin")
    parser.add_argument("--ctas-file", help="read the CTAS status from this file instead of the site")
    parser.add_argument("--worldsindex-file", help="read the WorldsIndex manifest from this file instead of the site")
    parser.add_argument("--deploy-json", help="output of `gh run list --json ...` for the deploy workflow")
    parser.add_argument("--ctas-runs-json", help="output of `gh run list --workflow ctas-cloud.yml --json ...`")
    parser.add_argument("--worldsindex-runs-json",
                        help="output of `gh run list --workflow worldsindex-cloud.yml --json ...`")
    parser.add_argument("--run-jobs-dir", help="folder of <databaseId>.json files from `gh run view --json jobs`")
    parser.add_argument("--ctas-marker-file", help="read the CTAS cloud-run marker from this file instead of the site")
    parser.add_argument("--worldsindex-marker-file",
                        help="read the WorldsIndex cloud-run marker from this file instead of the site")
    parser.add_argument("--now", help="ISO-8601 override of the current time (tests)")
    parser.add_argument("--mention", default="", help="GitHub @mention to put in alert text")
    parser.add_argument("--report", help="write the JSON report here")
    parser.add_argument("--markdown", help="write the Markdown summary here")
    for key, value in DEFAULT_THRESHOLDS.items():
        parser.add_argument("--" + key.replace("_", "-"), type=float, default=None,
                            help=f"default {value:g}")
    args = parser.parse_args(argv)

    now = parse_utc(args.now) if args.now else dt.datetime.now(UTC)
    if args.now and now is None:
        parser.error("--now must be an ISO-8601 timestamp")
    base = args.base.rstrip("/")

    if args.ctas_file:
        ctas_status, ctas_error = load_json_file(args.ctas_file), None
    else:
        ctas_status, ctas_error = fetch_json(f"{base}/{CTAS_STATUS_PATH}")
    if args.worldsindex_file:
        worlds_manifest, worlds_error = load_json_file(args.worldsindex_file), None
    else:
        worlds_manifest, worlds_error = fetch_json(f"{base}/{WORLDSINDEX_MANIFEST_PATH}")
    if args.ctas_marker_file:
        ctas_marker, ctas_marker_error = load_json_file(args.ctas_marker_file), None
    else:
        ctas_marker, ctas_marker_error = fetch_json(f"{base}/{CTAS_MARKER_PATH}", attempts=1)
    if args.worldsindex_marker_file:
        worlds_marker, worlds_marker_error = load_json_file(args.worldsindex_marker_file), None
    else:
        worlds_marker, worlds_marker_error = fetch_json(f"{base}/{WORLDSINDEX_MARKER_PATH}", attempts=1)

    def optional_json(path, label):
        if not path:
            return None
        try:
            return load_json_file(path)
        except (OSError, ValueError) as exc:
            print(f"{label} unavailable: {exc}", file=sys.stderr)
            return None

    deploy = optional_json(args.deploy_json, "deploy status")
    ctas_runs = optional_json(args.ctas_runs_json, "CTAS run list")
    worlds_runs = optional_json(args.worldsindex_runs_json, "WorldsIndex run list")
    run_jobs = {}
    if args.run_jobs_dir and os.path.isdir(args.run_jobs_dir):
        for entry in sorted(os.listdir(args.run_jobs_dir)):
            if entry.endswith(".json"):
                document = optional_json(os.path.join(args.run_jobs_dir, entry), f"run {entry}")
                if document is not None:
                    run_jobs[entry[:-5]] = document

    report = evaluate(ctas_status, ctas_error, worlds_manifest, worlds_error, deploy, now=now,
                      ctas_runs=ctas_runs, worldsindex_runs=worlds_runs, run_jobs=run_jobs,
                      ctas_marker=ctas_marker, ctas_marker_error=ctas_marker_error,
                      worldsindex_marker=worlds_marker, worldsindex_marker_error=worlds_marker_error,
                      **{key: getattr(args, key) for key in DEFAULT_THRESHOLDS})
    markdown = render_markdown(report, base, args.mention)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
