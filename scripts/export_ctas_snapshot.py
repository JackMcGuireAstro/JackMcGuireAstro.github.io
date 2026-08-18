#!/usr/bin/env python3
"""
export_ctas_snapshot.py — publish a sanitized snapshot of the REAL CTAS database.

The scheduled GitHub Actions job starts from an empty database each run, so it
can only ever see a short lookback window. The accumulated science lives in the
local CTAS SQLite database. This script exports that database to the same
public JSON schema the website already consumes, so the public page shows what
CTAS actually knows rather than what one fresh fetch happened to catch.

The database is opened STRICTLY READ-ONLY (SQLite `mode=ro`). Nothing is
written to it, no schema is created, and CTAS does not need to be installed —
only the standard library is used, so it runs on a stock macOS python3 while
CTAS itself is running.

    python scripts/export_ctas_snapshot.py \
        --database ~/path/to/soc.db \
        --output-dir ctas/data

Publication is an allowlist: only the columns named in COLUMNS below can ever
reach the website. Internal identifiers, scoring internals and simulated
records are excluded by construction.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

UTC = timezone.utc  # datetime.UTC is 3.11+; this works on 3.9+
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# database column -> published field name. Anything absent here is never
# published. `id`, `simulation`, `priority_factors` and `created_at` are
# deliberately omitted.
COLUMNS: dict[str, str] = {
    "preferred_name": "name",
    "ra_deg": "ra_deg",
    "dec_deg": "dec_deg",
    "coordinate_error_arcsec": "coordinate_error_arcsec",
    "discovery_time": "discovery_time",
    "first_detection_time": "first_detection_time",
    "discovery_survey": "discovery_survey",
    "discovery_instrument": "discovery_instrument",
    "discovery_magnitude": "discovery_magnitude",
    "status": "status",
    "host_name": "host_name",
    "host_redshift": "host_redshift",
    "transient_redshift": "redshift",
    "distance_mpc": "distance_mpc",
    "consensus_class": "classification",
    "consensus_probability": "classification_probability",
    "priority_score": "ctas_score",
    "event_type": "event_type",
    "primary_messenger": "primary_messenger",
    "updated_at": "updated_at",
}

# Alias providers we are willing to publish, and how to link them.
PUBLIC_LINKS = {
    "tns":                ("TNS",  "https://www.wis-tns.org/object/{id}"),
    "tns-astronotes":     ("TNS AstroNote", None),
    "tns-public-reports": ("TNS report", None),
    "gcn":                ("GCN", None),
}

# Merged records are audit rows pointing at a surviving parent, not separate
# astronomical events. CTAS's own query layer hides them; so do we.
EXCLUDED_STATUS = {"merged"}


def iso(value: Any) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(" ", "T", 1)
    if "." in s:
        s = s.split(".", 1)[0]
    if not s.endswith("Z"):
        s += "Z"
    return s


def utcstamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return round(value, 6)
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return value


def export(db_path: Path, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cols = ", ".join(COLUMNS)
    rows = cur.execute(
        f"SELECT id, simulation, {cols} FROM events "
        f"WHERE COALESCE(simulation, 0) = 0 "
        f"ORDER BY COALESCE(updated_at, discovery_time) DESC "
        f"LIMIT ?",
        (limit,),
    ).fetchall()

    # Aliases for exactly the events we are publishing.
    ids = [r["id"] for r in rows]
    alias_map: dict[str, list[sqlite3.Row]] = {}
    CHUNK = 400
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        q = ",".join("?" * len(chunk))
        for a in cur.execute(
            f"SELECT event_id, provider, external_id FROM aliases WHERE event_id IN ({q})",
            chunk,
        ):
            alias_map.setdefault(a["event_id"], []).append(a)

    counts = {"total_real_events": cur.execute(
        "SELECT COUNT(*) FROM events WHERE COALESCE(simulation,0)=0").fetchone()[0]}
    con.close()

    out: list[dict[str, Any]] = []
    skipped = 0
    for r in rows:
        if str(r["status"] or "").lower() in EXCLUDED_STATUS:
            skipped += 1
            continue
        name = clean(r["preferred_name"])
        if not name:
            skipped += 1
            continue

        rec: dict[str, Any] = {}
        for src, dest in COLUMNS.items():
            v = clean(r[src])
            if v is None:
                continue
            if src in ("discovery_time", "first_detection_time", "updated_at"):
                v = iso(v)
                if v is None:
                    continue
            rec[dest] = v

        links = []
        for a in alias_map.get(r["id"], []):
            provider = str(a["provider"] or "").lower()
            ext = clean(a["external_id"])
            if not ext or provider not in PUBLIC_LINKS:
                continue          # unknown provider may be an internal ref
            label, template = PUBLIC_LINKS[provider]
            entry = {"label": label, "designation": str(ext)}
            if template:
                entry["url"] = template.format(id=str(ext))
            links.append(entry)
        if links:
            # de-duplicate on (label, designation)
            seen, uniq = set(), []
            for l in links:
                k = (l["label"], l["designation"])
                if k not in seen:
                    seen.add(k)
                    uniq.append(l)
            rec["links"] = uniq[:6]

        out.append(rec)

    counts["published"] = len(out)
    counts["skipped"] = skipped
    return out, counts


BANNED_KEYS = {"id", "simulation", "priority_factors", "created_at",
               "token", "api_key", "secret", "password"}


def validate(payload: dict[str, Any]) -> list[str]:
    problems = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version mismatch")
    cands = payload.get("candidates")
    if not isinstance(cands, list):
        return ["candidates is not a list"]
    for i, c in enumerate(cands):
        if not c.get("name"):
            problems.append(f"candidate {i} has no name")
        leaked = BANNED_KEYS & set(c)
        if leaked:
            problems.append(f"candidate {i} leaks {sorted(leaked)}")
    blob = json.dumps(cands)
    for bad in ("/Users/", ".codex", "BEGIN PRIVATE KEY"):
        if bad in blob:
            problems.append(f"payload contains forbidden content: {bad}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", required=True,
                    help="path to the CTAS SQLite database (opened read-only)")
    ap.add_argument("--output-dir", default="ctas/data")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--dry-run", action="store_true",
                    help="print a summary and sample; write nothing")
    args = ap.parse_args()

    db = Path(os.path.expanduser(args.database)).resolve()
    if not db.exists():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 2

    try:
        candidates, counts = export(db, args.limit)
    except sqlite3.Error as exc:
        print(f"error: could not read the database read-only: {exc}", file=sys.stderr)
        return 1

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcstamp(),
        "origin": "local-snapshot",
        "cadence": "approximately every 30 minutes",
        "candidate_count": len(candidates),
        "degraded": False,
        "candidates": candidates,
    }

    problems = validate(payload)
    if problems:
        print("export failed validation:", file=sys.stderr)
        for p in problems[:20]:
            print("  -", p, file=sys.stderr)
        return 1

    status = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_status": "ok",
        "origin": "local-snapshot",
        "last_successful_update": payload["generated_at"],
        "candidate_count": len(candidates),
        "cadence": "approximately every 30 minutes",
        "sources": [{
            "source": "ctas-local",
            "label": "CTAS accumulated event database",
            "state": "ok",
            "detail": f"{counts['published']} public events "
                      f"of {counts['total_real_events']} real events retained",
        }],
    }

    body = json.dumps(payload, indent=2)
    print(f"database        : {db.name}")
    print(f"real events     : {counts['total_real_events']:,}")
    print(f"published       : {counts['published']:,}   (skipped {counts['skipped']})")
    print(f"payload size    : {len(body)/1024:.0f} KB")
    if candidates:
        print("\nsample record:")
        print(json.dumps(candidates[0], indent=2)[:900])

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(body + "\n")
    (out / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    print(f"\nwrote {out/'candidates.json'} and {out/'status.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
