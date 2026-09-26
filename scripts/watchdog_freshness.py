#!/usr/bin/env python3
"""Check that the live CTAS and WorldsIndex releases are still being published.

Both publishers run on one Mac and fail closed: when either refuses a release it
keeps refusing, silently, until someone looks. GitHub Pages keeps serving the last
snapshot, so nothing on the site says "the publisher stopped". This check runs in
GitHub Actions on a schedule (see .github/workflows/freshness-watchdog.yml), reads
the public status files, and decides whether either publisher has stalled.

Three signals, in order of certainty:

1. Cross-check. The two launchd agents run under the same conditions (Mac awake,
   logged in, online). If WorldsIndex published within the last couple of hours but
   CTAS has not published for several, the Mac is evidently up and the CTAS
   publisher itself is stuck; the reverse holds for WorldsIndex. This catches the
   2026-09-07 and 2026-09-24 CTAS outages within hours instead of days.
2. Absolute age. A release older than the configured maximum means either the Mac
   has been asleep or offline for that long or both publishers are stuck. Either is
   worth knowing about.
3. Deployment. If the last "Validate and deploy site" run failed, pushes are
   landing but the live site is frozen at the previous green commit.

The script never fails the workflow on a stale release; it writes a JSON report
and a Markdown summary, and the workflow turns those into one GitHub issue that is
opened, updated when the situation changes, and closed on recovery. Only genuine
errors (bad arguments, unwritable report) exit non-zero.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request

UTC = dt.timezone.utc
CTAS_STATUS_PATH = "ctas/data/status.json"
WORLDSINDEX_MANIFEST_PATH = "worldsindex/data/manifest.json"
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
}


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


def evaluate(ctas_status, ctas_error, worldsindex_manifest, worldsindex_error, deploy, now=None, **thresholds):
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

    # 1. cross-check: one publisher running proves the Mac is up.
    if ctas_age is not None and worlds_age is not None:
        if worlds_age <= limits["cross_check_fresh_hours"] and ctas_age > limits["cross_check_stale_hours"]:
            alert("ctas-stalled-while-worldsindex-publishes",
                  f"CTAS has not published for {ctas_age:g} h while WorldsIndex published "
                  f"{worlds_age:g} h ago, so the Mac is up and the CTAS publisher itself is stuck.")
        if ctas_age <= limits["cross_check_fresh_hours"] and worlds_age > limits["cross_check_stale_hours"]:
            alert("worldsindex-stalled-while-ctas-publishes",
                  f"WorldsIndex has not published for {worlds_age:g} h while CTAS published "
                  f"{ctas_age:g} h ago, so the Mac is up and the WorldsIndex publisher itself is stuck.")

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

    if ctas.get("available") and ctas.get("certificate_expired") and not any(
            row["code"].startswith("ctas") for row in alerts):
        notes.append("The CTAS certificate has expired (30-minute validity) but the release is within limits; "
                     "this is the normal state while the Mac sleeps.")
    if ctas.get("pipeline_status") == "degraded":
        notes.append(f"CTAS reports pipeline_status=degraded ({ctas.get('degraded_source_count')} upstream "
                     "sources); that concerns providers, not publication.")

    codes = sorted(row["code"] for row in alerts)
    return {
        "checked_at": iso(now),
        "ok": not alerts,
        "alerts": alerts,
        "notes": notes,
        "fingerprint": "|".join(codes) if codes else "ok",
        "thresholds": limits,
        "ctas": ctas,
        "worldsindex": worlds,
        "deploy": deployment,
    }


def render_markdown(report, site_url, mention=""):
    """Markdown for the job summary and the GitHub issue body."""
    def age(value):
        return "—" if value is None else f"{value:g} h"

    ctas, worlds, deploy = report["ctas"], report["worldsindex"], report["deploy"]
    lines = []
    if report["ok"]:
        lines.append(f"**Both publishers are current** as of {report['checked_at']}.")
    else:
        lines.append(f"**Publisher freshness alert** as of {report['checked_at']}"
                     + (f" — {mention}" if mention else ""))
        lines.append("")
        for row in report["alerts"]:
            lines.append(f"- `{row['code']}`: {row['message']}")
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
    if report["notes"]:
        lines.append("")
        for note in report["notes"]:
            lines.append(f"_{note}_")
    if not report["ok"]:
        lines.append("")
        lines.append("**Where to look on the Mac**")
        lines.append("")
        lines.append("- CTAS: `tail -n 20 ~/Library/Logs/ctas-mirror/runner.log` and `publish.log`; "
                     "`bash scripts/diagnose_ctas_mirror.sh`. A `FAIL` line repeating every minute is a stuck "
                     "publisher, not a sleeping Mac — see CTAS-AUTOMATION.md, \"Recover an interrupted "
                     "generated-data publication\".")
        lines.append("- WorldsIndex: `tail -n 20 ~/Library/Logs/worldsindex-mirror/runner.log`; "
                     "`./scripts/diagnose_worldsindex_mirror.sh`.")
        lines.append("- Both silent with the Mac awake: `launchctl print gui/$(id -u)/io.github.jackmcguireastro.ctas-mirror` "
                     "and the WorldsIndex label; a failed deploy: re-run the last green workflow or revert the commit.")
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
    deploy = None
    if args.deploy_json:
        try:
            deploy = load_json_file(args.deploy_json)
        except (OSError, ValueError) as exc:
            print(f"deploy status unavailable: {exc}", file=sys.stderr)

    report = evaluate(ctas_status, ctas_error, worlds_manifest, worlds_error, deploy, now=now,
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
