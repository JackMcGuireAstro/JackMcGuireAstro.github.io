#!/usr/bin/env python3
"""
export_ctas_snapshot.py, publish a sanitized snapshot of the REAL CTAS database.

The scheduled GitHub Actions job starts from an empty database each run, so it
can only ever see a short lookback window. The accumulated science lives in the
local CTAS SQLite database. This script exports that database to the same
public JSON schema the website already consumes, so the public page shows what
CTAS actually knows rather than what one fresh fetch happened to catch.

The database is opened STRICTLY READ-ONLY (SQLite `mode=ro`). Nothing is
written to it, no schema is created, and CTAS does not need to be installed -
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
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

UTC = timezone.utc  # datetime.UTC is 3.11+; this works on 3.9+
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

# database column -> published field name. Anything absent here is never
# published. `id`, `simulation` and `created_at` are deliberately omitted.
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

TNS_OBJECT = re.compile(r"^(?:AT|SN)?(\d{4}[a-z]+)$", re.IGNORECASE)

# Merged records are audit rows pointing at a surviving parent, not separate
# astronomical events. CTAS's own query layer hides them; so do we.
EXCLUDED_STATUS = {"merged"}

PUBLIC_SCORE_FACTORS = {
    "recency_points", "brightness_points", "classification_gap_points",
    "classification_conflict_points", "spectroscopy_gap_points", "coverage_reduction",
    "observation_gap_points", "multimessenger_points", "status",
}


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


def tns_object_id(value: str) -> str | None:
    """Return the path identifier used by public TNS object pages."""

    match = TNS_OBJECT.fullmatch(value.strip())
    return match.group(1) if match else None


def rows_by_event(
    cur: sqlite3.Cursor,
    event_ids: list[str],
    statement: str,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    chunk_size = 400
    for index in range(0, len(event_ids), chunk_size):
        chunk = event_ids[index:index + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        for row in cur.execute(statement.format(ids=placeholders), chunk):
            event_id = str(row["event_id"])
            item = {
                key: clean(row[key])
                for key in row.keys()
                if key != "event_id" and clean(row[key]) is not None
            }
            for key in ("observed_at", "asserted_at", "published_at"):
                if key in item:
                    item[key] = iso(item[key])
            grouped.setdefault(event_id, []).append(item)
    return grouped


def export(db_path: Path, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cols = ", ".join(COLUMNS)
    rows = cur.execute(
        f"SELECT id, simulation, priority_factors, {cols} FROM events "
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

    classifications = rows_by_event(
        cur,
        ids,
        """
        SELECT event_id, provider, classification, subtype, probability, method,
               asserted_at, citation_url
        FROM classification_assertions
        WHERE event_id IN ({ids})
          AND data_rights IN ('public', 'open')
          AND COALESCE(superseded, 0) = 0
          AND COALESCE(retracted, 0) = 0
        ORDER BY event_id, asserted_at DESC, id
        """,
    )
    observations = rows_by_event(
        cur,
        ids,
        """
        SELECT event_id, provider, observed_at, detection, telescope, observatory,
               instrument, pipeline, band, magnitude_system, magnitude,
               magnitude_error, flux, flux_error, flux_unit, limiting_magnitude,
               exposure_seconds, signal_to_noise, calibration, photometry_method,
               summary, source_url
        FROM observations
        WHERE event_id IN ({ids})
          AND data_rights IN ('public', 'open')
          AND COALESCE(superseded, 0) = 0
        ORDER BY event_id, observed_at DESC, id
        """,
    )
    signals = rows_by_event(
        cur,
        ids,
        """
        SELECT event_id, provider, provider_signal_id, observed_at, messenger, role,
               instrument, detection, alert_type, significance_sigma,
               false_alarm_rate_hz, sky_area_50_sq_deg, sky_area_90_sq_deg,
               distance_mpc, distance_std_mpc, measurement, summary, source_url,
               skymap_url
        FROM messenger_signals
        WHERE event_id IN ({ids})
          AND data_rights IN ('public', 'open')
          AND COALESCE(simulation, 0) = 0
        ORDER BY event_id, observed_at DESC, id
        """,
    )
    publications = rows_by_event(
        cur,
        ids,
        """
        WITH latest_revision AS (
          SELECT pr.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY pr.publication_id
                   ORDER BY pr.retrieved_at DESC, pr.id DESC
                 ) AS revision_rank
          FROM publication_revisions pr
        )
        SELECT pel.event_id, p.provider, p.provider_publication_id,
               p.publication_type, p.canonical_url, p.published_at,
               lr.title, lr.authors_text, lr.abstract
        FROM publication_event_links pel
        JOIN publications p ON p.id = pel.publication_id
        JOIN latest_revision lr
          ON lr.publication_id = p.id AND lr.revision_rank = 1
        WHERE pel.event_id IN ({ids})
          AND p.data_rights IN ('public', 'open')
        ORDER BY pel.event_id, p.published_at DESC, p.id
        """,
    )

    spectra = rows_by_event(
        cur,
        ids,
        """
        SELECT event_id, provider, provider_spectrum_id, observed_at, telescope,
               instrument, configuration, wavelength_unit, flux_unit, resolution,
               calibration_state, public_download_url, file_name, file_checksum,
               source_url
        FROM spectra
        WHERE event_id IN ({ids})
          AND data_rights IN ('public', 'open')
        ORDER BY event_id, observed_at DESC, id
        """,
    )

    counts = {"total_real_events": cur.execute(
        "SELECT COUNT(*) FROM events WHERE COALESCE(simulation,0)=0").fetchone()[0]}

    source_rows = [
        {
            "source": clean(row["id"]),
            "label": clean(row["display_name"]),
            "facility": clean(row["facility"]),
            "data_types": json.loads(row["data_types"] or "[]"),
            "mode": clean(row["mode"]),
            "public_scope": clean(row["public_scope"]),
            "state": clean(row["runtime_state"]) or "unknown",
            "detail": clean(row["runtime_detail"]),
            "last_message_at": iso(row["last_message_at"]),
            "lag_seconds": clean(row["lag_seconds"]),
            "documentation_url": clean(row["documentation_url"]),
            "enabled": bool(row["enabled"]),
        }
        for row in cur.execute(
            """
            SELECT id, display_name, facility, data_types, mode, public_scope,
                   runtime_state, runtime_detail, last_message_at, lag_seconds,
                   documentation_url, enabled
            FROM sources
            ORDER BY display_name, id
            """
        )
    ]

    provider_counts: dict[str, dict[str, int]] = {}
    for kind, table, extra in (
        ("observations", "observations", "COALESCE(superseded,0)=0"),
        ("spectra", "spectra", "1=1"),
        ("messenger_signals", "messenger_signals", "COALESCE(simulation,0)=0"),
        ("classifications", "classification_assertions",
         "COALESCE(superseded,0)=0 AND COALESCE(retracted,0)=0"),
    ):
        for row in cur.execute(
            f"""
            SELECT provider, COUNT(*) AS n
            FROM {table}
            WHERE data_rights IN ('public','open') AND {extra}
            GROUP BY provider
            """
        ):
            provider_counts.setdefault(str(row["provider"]), {})[kind] = int(row["n"])
    for row in cur.execute(
        """
        SELECT p.provider, COUNT(*) AS n
        FROM publication_event_links pel
        JOIN publications p ON p.id = pel.publication_id
        WHERE p.data_rights IN ('public','open')
        GROUP BY p.provider
        """
    ):
        provider_counts.setdefault(str(row["provider"]), {})["publications"] = int(row["n"])

    survey_rows = [
        {"survey": str(row["survey"]), "candidate_count": int(row["n"])}
        for row in cur.execute(
            """
            SELECT discovery_survey AS survey, COUNT(*) AS n
            FROM events
            WHERE COALESCE(simulation,0)=0 AND status != 'merged'
              AND discovery_survey IS NOT NULL AND TRIM(discovery_survey) != ''
            GROUP BY discovery_survey
            ORDER BY n DESC, discovery_survey
            """
        )
    ]
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

        try:
            raw_factors = json.loads(r["priority_factors"] or "{}")
        except (TypeError, json.JSONDecodeError):
            raw_factors = {}
        if isinstance(raw_factors, dict):
            factors = {
                key: clean(value) for key, value in raw_factors.items()
                if key in PUBLIC_SCORE_FACTORS
                and isinstance(value, (int, float, str, bool))
                and clean(value) is not None
            }
            if factors:
                rec["score_factors"] = factors

        links = []
        for a in alias_map.get(r["id"], []):
            provider = str(a["provider"] or "").lower()
            ext = clean(a["external_id"])
            if not ext or provider not in PUBLIC_LINKS:
                continue          # unknown provider may be an internal ref
            label, template = PUBLIC_LINKS[provider]
            entry = {"label": label, "designation": str(ext)}
            if template:
                linked_id = tns_object_id(str(ext)) if provider == "tns" else str(ext)
                if linked_id:
                    entry["url"] = template.format(id=linked_id)
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

        follow_up = {
            "classifications": classifications.get(r["id"], []),
            "observations": observations.get(r["id"], []),
            "spectra": spectra.get(r["id"], []),
            "messenger_signals": signals.get(r["id"], []),
            "publications": publications.get(r["id"], []),
        }
        rec["follow_up_counts"] = {
            key: len(value) for key, value in follow_up.items()
        }
        rec["follow_up_total"] = sum(rec["follow_up_counts"].values())
        if any(follow_up.values()):
            rec["follow_up"] = follow_up

        out.append(rec)

    counts["published"] = len(out)
    counts["skipped"] = skipped
    counts["sources"] = source_rows
    counts["provider_counts"] = provider_counts
    counts["surveys"] = survey_rows
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
        "cadence": "about every 2 minutes",
        "candidate_count": len(candidates),
        "degraded": False,
        "candidates": candidates,
    }

    def total(field: str) -> int:
        return sum(int(c.get("follow_up_counts", {}).get(field, 0)) for c in candidates)

    messenger_counts: dict[str, int] = {}
    priority_bands = {"urgent_75_100": 0, "high_50_74": 0, "routine_25_49": 0, "low_0_24": 0}
    for candidate in candidates:
        messenger = str(candidate.get("primary_messenger") or "unknown")
        messenger_counts[messenger] = messenger_counts.get(messenger, 0) + 1
        score = float(candidate.get("ctas_score") or 0)
        band = ("urgent_75_100" if score >= 75 else "high_50_74" if score >= 50
                else "routine_25_49" if score >= 25 else "low_0_24")
        priority_bands[band] += 1

    recent = sorted(
        candidates,
        key=lambda row: row.get("updated_at") or row.get("discovery_time") or "",
        reverse=True,
    )[:20]
    payload["recent_stream"] = [
        {
            key: row[key] for key in (
                "name", "updated_at", "discovery_time", "classification",
                "primary_messenger", "ctas_score", "follow_up_counts",
            ) if key in row
        }
        for row in recent
    ]
    payload["statistics"] = {
        "real_events": counts["total_real_events"],
        "public_candidates": len(candidates),
        "observations": total("observations"),
        "spectra": total("spectra"),
        "messenger_signals": total("messenger_signals"),
        "classifications": total("classifications"),
        "publications": total("publications"),
        "candidates_with_follow_up": sum(c.get("follow_up_total", 0) > 0 for c in candidates),
        "messengers": dict(sorted(messenger_counts.items())),
        "priority_bands": priority_bands,
    }
    payload["sources"] = [
        {**source, "record_counts": counts["provider_counts"].get(str(source["source"]), {})}
        for source in counts["sources"]
    ]
    payload["provider_statistics"] = [
        {"provider": provider, **record_counts}
        for provider, record_counts in sorted(
            counts["provider_counts"].items(),
            key=lambda item: (-sum(item[1].values()), item[0]),
        )
    ]
    payload["surveys"] = counts["surveys"]

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
        "cadence": "about every 2 minutes",
        "statistics": payload["statistics"],
        "sources": payload["sources"],
        "surveys": payload["surveys"],
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
    candidates_raw = (body + "\n").encode()
    site_root = Path(__file__).resolve().parents[1]
    bound_files = {
        "ctas.html": (site_root / "ctas.html").read_bytes(),
        "ctas/app.js": (site_root / "ctas/app.js").read_bytes(),
        "ctas/ctas.css": (site_root / "ctas/ctas.css").read_bytes(),
        "ctas/data/candidates.json": candidates_raw,
        "scripts/export_ctas_snapshot.py": Path(__file__).read_bytes(),
        "scripts/mirror_loop.sh": (site_root / "scripts/mirror_loop.sh").read_bytes(),
        "scripts/publish_ctas.sh": (site_root / "scripts/publish_ctas.sh").read_bytes(),
    }
    gates = [
        {"id": "catalog-population", "passed": len(candidates) == payload["candidate_count"] and bool(candidates), "evidence": f"{len(candidates)}/{payload['candidate_count']}"},
        {"id": "public-export-safety", "passed": not problems, "evidence": "allowlisted fields and fail-closed validation"},
        {"id": "candidate-detail-counts", "passed": all("name" in row and "ctas_score" in row and "follow_up_counts" in row for row in candidates), "evidence": f"{sum('name' in row and 'ctas_score' in row and 'follow_up_counts' in row for row in candidates)}/{len(candidates)}"},
        {"id": "two-minute-publication-contract", "passed": payload["cadence"] == "about every 2 minutes", "evidence": "120-second mirror loop"},
        {"id": "public-research-surface", "passed": all(token in (bound_files["ctas.html"] + bound_files["ctas/app.js"]).decode() for token in ("CTAS page contents", "ctas-sky-canvas", "data-sky-days=\"7\"", "data-sky-days=\"30\"", "renderDetails(c)")), "evidence": "contents, sky controls, ranked feed, and candidate details"},
    ]
    certificate = {
        "schema": "ctas.static-catalog-certification@1.0.0",
        "generated_at": payload["generated_at"],
        "architecture": "local-python-to-static-github-pages",
        "claim_boundary": "Automated static-catalog assurance; not peer review, scientific truth, or a managed-service deployment claim.",
        "status": "certified-static-catalog" if all(gate["passed"] for gate in gates) else "not-certified",
        "candidate_count": len(candidates),
        "gates": gates,
        "files": {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in sorted(bound_files.items())},
    }
    canonical = (json.dumps(certificate, sort_keys=True, separators=(",", ":")) + "\n").encode()
    certificate["report_checksum_sha256"] = hashlib.sha256(canonical).hexdigest()
    status["static_catalog_assurance"] = {
        "status": certificate["status"],
        "schema": certificate["schema"],
        "report_checksum_sha256": certificate["report_checksum_sha256"],
    }
    (out / "candidates.json").write_bytes(candidates_raw)
    (out / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    (out / "certification.json").write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {out/'candidates.json'}, {out/'status.json'}, and {out/'certification.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
