#!/usr/bin/env python3
"""Write the small public marker a cloud run publishes beside its release.

The cloud runs (ctas-cloud.yml, worldsindex-cloud.yml) restore their working state
from a private repository, publish, then save the state back. A failed save leaves
the public data fresh and the next run restoring an old state, which nothing on the
site showed until 2026-10-14. Each run therefore publishes this marker
(``ctas/data/cloud-run.json``, ``worldsindex/data/cloud-run.json``) and the freshness
watchdog reads it:

    cloud_run_marker.py --workflow ctas-cloud.yml --receipt state-restored.json --output cloud-run.json [--probe-token]

``state_saved_at`` is the time the restored state copy was saved (the stamp in its
name, from the restore receipt the state script writes under STATE_RECEIPT), so it is
the time of the last save that worked. GITHUB_RUN_ID, GITHUB_SERVER_URL and
GITHUB_REPOSITORY (set by Actions) identify the run.

With ``--probe-token`` the key in GH_TOKEN (the CTAS_STATE_TOKEN secret, a copy of the
owner's ``gh auth token``) is tried against https://api.github.com/user: the marker
records whether GitHub accepted it and, when the response carries the
``github-authentication-token-expiration`` header (keys that expire), the expiry
date, date only. Nothing secret is written: never the key, nor the account it names.

``site_commit`` is the website commit whose code builds the release (``--site-commit``,
or HEAD of ``--site``); the next run reads it back as the known-good code. When a release
check blocked the current code and the cloud runner rebuilt the release with the code that
produced the previously published release (RESILIENCE.md), ``--fallback DECISION.json``
adds ``code_fallback: {failed_commit, failed_check, detail, used_commit}`` (plus the full
list of failed checks), so the published marker says which code built the release and why.
The marker is written by the runner (the current code) even when the release is built by
the fallback code, and it is outside the checksum-bound release, so the key can be added
after the checks without touching certified bytes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "cloud-run-marker@1"
TOKEN_PROBE_URL = "https://api.github.com/user"
TOKEN_EXPIRY_HEADER = "github-authentication-token-expiration"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_receipt(path: str | None) -> dict:
    if not path:
        return {}
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def parse_expiry(header: str | None) -> str | None:
    """The date in GitHub's expiry header ("2026-11-15 10:00:00 UTC" or "... +0000")."""
    match = re.match(r"\s*(\d{4}-\d{2}-\d{2})", header or "")
    return match.group(1) if match else None


def probe_token(token: str, opener=None, timeout: float = 20) -> dict:
    """Try the key against the API; report acceptance and expiry date, never the key."""
    checked = {"token_checked_at": now_iso(), "token_expires_at": None}
    if not token:
        return {**checked, "token_status": "not-checked"}
    request = urllib.request.Request(TOKEN_PROBE_URL, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "jackmcguireastro-cloud-run-marker"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            status, headers = response.status, response.headers
    except urllib.error.HTTPError as error:
        status, headers = error.code, error.headers
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {**checked, "token_status": "unknown", "token_probe_error": type(error).__name__}
    if status == 200:
        verdict = "ok"
    elif status == 401:
        verdict = "unauthorized"
    else:
        verdict = "unknown"
    return {**checked, "token_status": verdict, "token_http_status": status,
            "token_expires_at": parse_expiry(headers.get(TOKEN_EXPIRY_HEADER) if headers else None)}


def site_head(site: str | None) -> str | None:
    if not site:
        return None
    completed = subprocess.run(["git", "-C", site, "rev-parse", "HEAD"], capture_output=True, text=True)
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit) else None


def fallback_fields(decision: dict | None) -> dict:
    """The ``code_fallback`` key of a release built by the fallback code after the current
    code was blocked (decision of scripts/release_checks.py decide)."""
    if not isinstance(decision, dict) or not decision.get("release_commit"):
        return {}
    blocking = list(decision.get("blocking") or [])
    return {"code_fallback": {
        "failed_commit": decision.get("main_commit"),
        "failed_check": blocking[0] if blocking else None,
        "failed_checks": blocking,
        "detail": str(decision.get("blocking_detail") or decision.get("explanation") or decision.get("reason") or "")[:300],
        "used_commit": decision.get("release_commit"),
    }}


def build_marker(workflow: str, receipt: dict, environ: dict, generated_at: str | None = None,
                 token: dict | None = None, site_commit: str | None = None, decision: dict | None = None) -> dict:
    run_id = environ.get("GITHUB_RUN_ID") or None
    server = environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = environ.get("GITHUB_REPOSITORY")
    return {
        "schema": SCHEMA,
        "generated_at": generated_at or now_iso(),
        "workflow": workflow,
        "run_id": run_id,
        "run_url": f"{server}/{repository}/actions/runs/{run_id}" if run_id and repository else None,
        "state_asset": receipt.get("asset"),
        "state_saved_at": receipt.get("saved_at"),
        "state_restored_at": receipt.get("restored_at"),
        **(token or {"token_status": "not-checked", "token_expires_at": None}),
        "site_commit": site_commit,
        **fallback_fields(decision),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", required=True, help="workflow file name, e.g. ctas-cloud.yml")
    parser.add_argument("--receipt", help="restore receipt written by the state script (STATE_RECEIPT)")
    parser.add_argument("--output", required=True, help="where to write the marker")
    parser.add_argument("--probe-token", action="store_true", help="record whether GH_TOKEN works and when it expires")
    parser.add_argument("--site-commit", help="the website commit whose code builds this release")
    parser.add_argument("--site", help="the website checkout; its HEAD is the site commit when --site-commit is not given")
    parser.add_argument("--fallback", help="decision JSON (scripts/release_checks.py decide) of a fallback-code retry")
    args = parser.parse_args(argv)
    token = probe_token(os.environ.get("GH_TOKEN", "")) if args.probe_token else None
    decision = read_receipt(args.fallback) if args.fallback else None
    marker = build_marker(args.workflow, read_receipt(args.receipt), os.environ, token=token,
                          site_commit=args.site_commit or site_head(args.site), decision=decision)
    Path(args.output).write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"cloud-run marker: state saved {marker['state_saved_at'] or 'unknown'} ({marker['state_asset'] or 'no receipt'}); "
          f"key {marker['token_status']}, expires {marker['token_expires_at'] or 'never or unknown'}; "
          f"site code {(marker['site_commit'] or 'unknown')[:10]}" + (" (code fallback)" if marker.get("code_fallback") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
