#!/usr/bin/env python3
"""Per-source health of the CTAS collector over the last day, from its database.

Run by scripts/ctas_cloud_run.sh after each cycle; the table goes to the GitHub job
summary (GITHUB_STEP_SUMMARY) and the log, so a source that quietly fails on every
request is visible without reading collector logs. Only counts and the collector's own
error codes are shown: no request details, URLs or credentials. Read-only.

Usage: ctas_source_health.py --db soc.db [--hours 24] [--now ISO]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

AUTH_HINTS = ("AUTH", "TOKEN", "CREDENTIAL", "NOT_CONFIGURED")


def _stamp(value: datetime) -> str:
    # The collector stores naive UTC datetimes as "YYYY-MM-DD HH:MM:SS.ffffff".
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def health(db: str, *, hours: float = 24, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    since = _stamp(now - timedelta(hours=hours))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        sources: dict[str, dict] = {}
        for source_id, state, code, count, last in con.execute(
            "SELECT source_id, terminal_state, COALESCE(error_code, ''), COUNT(*), MAX(checked_at) "
            "FROM source_query_attempts WHERE checked_at >= ? GROUP BY 1, 2, 3",
            (since,),
        ):
            row = sources.setdefault(source_id, {"attempts": 0, "states": {}, "codes": {}, "last_data": None})
            row["attempts"] += count
            row["states"][state] = row["states"].get(state, 0) + count
            if code and state in {"failed", "unavailable", "not-configured"}:
                row["codes"][code] = row["codes"].get(code, 0) + count
            if state == "data":
                row["last_data"] = max(filter(None, (row["last_data"], last)))
        intake = dict(con.execute(
            "SELECT provider, COUNT(*) FROM alert_envelopes WHERE received_at >= ? GROUP BY 1",
            (since,),
        ).fetchall())
    finally:
        con.close()
    problems = []
    for source_id, row in sorted(sources.items()):
        failed = row["states"].get("failed", 0)
        auth = sum(n for code, n in row["codes"].items() if any(h in code for h in AUTH_HINTS))
        if auth:
            problems.append(f"{source_id}: {auth} attempt(s) refused for credentials")
        elif row["attempts"] >= 10 and failed == row["attempts"]:
            problems.append(f"{source_id}: every attempt failed ({failed})")
        elif row["attempts"] >= 10 and failed / row["attempts"] >= 0.5:
            problems.append(f"{source_id}: {failed} of {row['attempts']} attempts failed")
    return {"hours": hours, "sources": sources, "intake": intake, "problems": problems}


def markdown(report: dict) -> str:
    lines = [f"### CTAS sources, last {report['hours']:g} h", ""]
    if report["problems"]:
        lines += ["**Needs attention:** " + "; ".join(report["problems"]), ""]
    else:
        lines += ["No source is failing on most of its requests.", ""]
    lines += ["| Source | Attempts | Data | No match | Failed | Skipped | Most common problem |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for source_id, row in sorted(report["sources"].items()):
        s = row["states"]
        skipped = s.get("unavailable", 0) + s.get("not-configured", 0) + s.get("ambiguous", 0)
        top = max(row["codes"].items(), key=lambda kv: kv[1], default=("", 0))
        lines.append(f"| {source_id} | {row['attempts']} | {s.get('data', 0)} | {s.get('no-match', 0)} | "
                     f"{s.get('failed', 0)} | {skipped} | {f'{top[0]} ({top[1]})' if top[1] else ''} |")
    if report["intake"]:
        lines += ["", "Alerts received: " + ", ".join(f"{k} {v}" for k, v in sorted(report["intake"].items()))]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
    text = markdown(health(args.db, hours=args.hours, now=now))
    sys.stdout.write(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
