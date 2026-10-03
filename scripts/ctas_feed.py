#!/usr/bin/env python3
"""Atom feed of strong new CTAS follow-up targets, written with each release.

Reads the release's first-screen summary (ctas/data/live-summary.json) and writes
ctas/data/feed.xml: follow-up target candidates discovered in the last FEED_DAYS days
(relative to the newest discovery in the release, so the file only changes when the
targets do) with a CTAS score of at least FEED_MIN_SCORE, newest first, at most
FEED_LIMIT entries. Each entry links to the object's dossier. No network, deterministic.

Usage: ctas_feed.py [--summary ctas/data/live-summary.json] [--output ctas/data/feed.xml]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

SITE = "https://jackmcguireastro.github.io"
FEED_URL = f"{SITE}/ctas/data/feed.xml"
FEED_DAYS = 30
FEED_MIN_SCORE = 60.0
FEED_LIMIT = 50
TARGET_ROLE = "follow-up-target-candidate"


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def select_targets(summary: dict) -> list[dict]:
    columns = summary.get("candidate_columns") or []
    at = {name: index for index, name in enumerate(columns)}
    rows = []
    for raw in summary.get("candidate_rows") or []:
        row = {name: raw[index] if index < len(raw) else None for name, index in at.items()}
        discovered = _when(row.get("discovery_time"))
        score = row.get("ctas_score")
        if row.get("record_role") != TARGET_ROLE or discovered is None or not isinstance(score, (int, float)):
            continue
        row["_discovered"] = discovered
        rows.append(row)
    if not rows:
        return []
    newest = max(row["_discovered"] for row in rows)
    cutoff = newest - timedelta(days=FEED_DAYS)
    chosen = [row for row in rows if row["_discovered"] >= cutoff and row["ctas_score"] >= FEED_MIN_SCORE]
    chosen.sort(key=lambda row: (row["_discovered"], str(row.get("event_id"))), reverse=True)
    return chosen[:FEED_LIMIT]


def render(summary: dict) -> str:
    targets = select_targets(summary)
    updated = _stamp(max((row["_discovered"] for row in targets), default=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        "  <title>CTAS — strong new follow-up targets</title>",
        f"  <subtitle>Follow-up target candidates from the Cowboy Transient Alert System discovered in the last {FEED_DAYS} days with a CTAS score of at least {FEED_MIN_SCORE:g}. Scores are triage aids, not classifications.</subtitle>",
        f'  <link rel="self" type="application/atom+xml" href="{FEED_URL}"/>',
        f'  <link rel="alternate" type="text/html" href="{SITE}/ctas.html"/>',
        f"  <id>{FEED_URL}</id>",
        f"  <updated>{updated}</updated>",
        "  <author><name>Jack McGuire (CTAS)</name></author>",
    ]
    for row in targets:
        event_id = str(row.get("event_id"))
        name = str(row.get("name") or event_id)
        label = row.get("classification") or "Unclassified"
        magnitude = row.get("discovery_magnitude")
        survey = row.get("discovery_survey")
        ra, dec = row.get("ra_deg"), row.get("dec_deg")
        bits = [str(label)]
        if isinstance(magnitude, (int, float)):
            bits.append(f"{magnitude:.1f} mag")
        if survey:
            bits.append(str(survey))
        title = f"{name} — {', '.join(bits)} (score {row['ctas_score']:.0f})"
        href = f"{SITE}/ctas.html?event={quote(event_id, safe='')}#dossier"
        details = [f"Discovered {_stamp(row['_discovered'])}", f"CTAS score {row['ctas_score']:.1f}"]
        if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
            details.append(f"RA {ra:.5f}°, Dec {dec:+.5f}°")
        if row.get("redshift") is not None:
            details.append(f"z = {row['redshift']}")
        out += [
            "  <entry>",
            f"    <title>{escape(title)}</title>",
            f'    <link rel="alternate" type="text/html" href="{escape(href)}"/>',
            f"    <id>urn:ctas:event:{escape(event_id)}</id>",
            f"    <updated>{_stamp(row['_discovered'])}</updated>",
            f"    <summary>{escape('; '.join(details))}.</summary>",
            "  </entry>",
        ]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", default="ctas/data/live-summary.json")
    parser.add_argument("--output", default="ctas/data/feed.xml")
    args = parser.parse_args(argv)
    text = render(json.loads(Path(args.summary).read_text(encoding="utf-8")))
    output = Path(args.output)
    temporary = output.with_suffix(".xml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"feed: {text.count('<entry>')} entries -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
