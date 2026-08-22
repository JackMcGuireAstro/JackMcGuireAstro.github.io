#!/usr/bin/env python3
"""
update_ctas.py, run CTAS headlessly and export sanitized public data.

This is the bridge between the private CTAS processing code and the public
GitHub Pages site. It runs inside a GitHub Actions job, calls CTAS's own
single-pass connector logic, and writes a small allowlisted JSON payload that
the browser-side CTAS interface reads.

It reuses the real CTAS implementation. It does not reimplement ingestion,
rights screening, normalization or scoring, it calls
``connector.poll_once()``, which is exactly what CTAS's own long-running
worker calls on each cycle, then drains CTAS's durable queue and reads the
result through CTAS's own QueryService.

    python scripts/update_ctas.py --output-dir ctas/data

Exit codes
    0  a usable public dataset was written
    1  the run failed and nothing safe could be published (skip deployment)

Sources are declared in SOURCES below. Each names the environment variables
it requires; a source whose variables are absent is reported honestly as
disabled rather than failing the run. Enabling TNS or GCN later is therefore
a matter of adding repository secrets, not of changing this file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime, timezone

UTC = timezone.utc  # datetime.UTC is 3.11+; this works on 3.9+
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = 1
CADENCE_TEXT = "about every 2 minutes"
TNS_OBJECT = re.compile(r"^(?:AT|SN)?(\d{4}[a-z]+)$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Source registry
#
# ``requires`` lists the environment variables a source needs. Empty means the
# source is credential-free and always runs. ``factory`` is resolved lazily so
# that importing this module never requires CTAS to be installed.
# ---------------------------------------------------------------------------
SOURCES: list[dict[str, Any]] = [
    {
        "key": "fink-lsst",
        "label": "Fink / Rubin LSST public broker",
        "requires": [],
        "import_path": ("supernova_watch.connectors.fink_lsst", "FinkLSSTPublicConnector"),
        "note": "Rubin alert packets are world-public with no proprietary period.",
    },
    {
        "key": "tns-astronotes",
        "label": "TNS AstroNotes (released public notes)",
        "requires": [],
        "import_path": ("supernova_watch.connectors.astronotes", "TNSAstroNotesConnector"),
        "note": "Only notes explicitly marked note-public are retained.",
    },
    {
        "key": "tns-public-objects",
        "label": "TNS public object deltas",
        "requires": ["TNS_BOT_ID", "TNS_BOT_NAME", "TNS_API_KEY"],
        "import_path": ("supernova_watch.connectors.tns", "TNSPublicConnector"),
        "note": "Requires TNS bot credentials.",
    },
    {
        "key": "gcn",
        "label": "NASA GCN public Kafka stream",
        "requires": ["GCN_CLIENT_ID", "GCN_CLIENT_SECRET"],
        "import_path": ("supernova_watch.connectors.gcn", "GCNPublicConnector"),
        "note": "Requires NASA GCN Kafka client credentials.",
    },
]

# Fields copied verbatim from CTAS's own event summary into public output.
# This is an allowlist: anything not named here never reaches the website.
# Deliberately excluded: internal ids, workflow status, simulation flags and
# internal record-accounting counters.
PUBLIC_EVENT_FIELDS = {
    "preferred_name": "name",
    "ra_deg": "ra_deg",
    "dec_deg": "dec_deg",
    "discovery_time": "discovery_time",
    "discovery_magnitude": "discovery_magnitude",
    "event_type": "event_type",
    "primary_messenger": "primary_messenger",
    "consensus_class": "classification",
    "consensus_probability": "classification_probability",
    "priority_score": "ctas_score",
    "observation_count": "observation_count",
    "spectrum_count": "spectrum_count",
    "signal_count": "signal_count",
    "latest_observation": "latest_observation",
    "updated_at": "updated_at",
}

PUBLIC_SCORE_FACTORS = {
    "recency_points", "brightness_points", "classification_gap_points",
    "classification_conflict_points", "spectroscopy_gap_points", "coverage_reduction",
    "observation_gap_points", "multimessenger_points", "status",
}

# Public catalogue services we are willing to deep-link to, keyed by the
# alias provider CTAS records. Anything else becomes a plain designation.
PUBLIC_LINKS = {
    "tns": ("TNS", "https://www.wis-tns.org/object/{id}"),
    "fink": ("Fink", "https://api.lsst.fink-portal.org/{id}"),
    "fink-lsst": ("Fink", "https://api.lsst.fink-portal.org/{id}"),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# sanitisation
# ---------------------------------------------------------------------------
def sanitize_event(summary: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a CTAS event summary to allowlisted public fields."""
    if summary.get("simulation"):
        return None  # never publish simulated records
    name = summary.get("preferred_name")
    if not name:
        return None

    out: dict[str, Any] = {}
    for src, dest in PUBLIC_EVENT_FIELDS.items():
        value = summary.get(src)
        if value is None or value == "":
            continue
        if isinstance(value, float) and value != value:  # NaN
            continue
        out[dest] = value

    raw_factors = summary.get("priority_factors")
    if isinstance(raw_factors, dict):
        factors = {
            key: value for key, value in raw_factors.items()
            if key in PUBLIC_SCORE_FACTORS
            and isinstance(value, (int, float, str, bool))
        }
        if factors:
            out["score_factors"] = factors

    links = []
    for alias in summary.get("aliases") or []:
        provider = str(alias.get("provider", "")).lower()
        external = alias.get("external_id")
        if not external:
            continue
        # Allowlist: only designations from known public catalogues are
        # published. An unrecognised provider may be an internal reference,
        # so it is dropped rather than guessed at.
        if provider not in PUBLIC_LINKS:
            continue
        label, template = PUBLIC_LINKS[provider]
        linked_id = str(external)
        if provider == "tns":
            match = TNS_OBJECT.fullmatch(linked_id.strip())
            if not match:
                continue
            linked_id = match.group(1)
        links.append({"label": label, "designation": str(external),
                      "url": template.format(id=linked_id)})
    if links:
        out["links"] = links[:6]

    return out


def validate_payload(payload: dict[str, Any]) -> list[str]:
    """Structural checks run before anything is allowed to deploy."""
    problems: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version mismatch")
    if not isinstance(payload.get("candidates"), list):
        problems.append("candidates is not a list")
        return problems
    banned = {"id", "status", "simulation", "token", "api_key", "secret", "password"}
    for i, c in enumerate(payload["candidates"]):
        if not isinstance(c, dict):
            problems.append(f"candidate {i} is not an object")
            continue
        if not c.get("name"):
            problems.append(f"candidate {i} has no name")
        leaked = banned & set(c)
        if leaked:
            problems.append(f"candidate {i} leaks internal fields: {sorted(leaked)}")
        blob = json.dumps(c)
        if "/Users/" in blob or ".codex" in blob:
            problems.append(f"candidate {i} contains a local filesystem path")
    return problems


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
async def run_ctas(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (public candidates, per-source status)."""
    from supernova_watch.config import Settings           # noqa: PLC0415
    from supernova_watch.database import Database         # noqa: PLC0415
    from supernova_watch.ingestion import IngestionService  # noqa: PLC0415
    from supernova_watch.query import QueryService        # noqa: PLC0415
    from supernova_watch.queue import QueuedIngestion, WorkQueue  # noqa: PLC0415

    settings = Settings()
    database = Database(settings)
    await database.initialize()

    ingestion = IngestionService(database, settings)
    queue = WorkQueue(database, settings, ingestion)
    queued = QueuedIngestion(queue, ingestion)

    statuses: list[dict[str, Any]] = []

    for spec in SOURCES:
        missing = [v for v in spec["requires"] if not os.environ.get(v)]
        if missing:
            statuses.append({
                "source": spec["key"], "label": spec["label"], "state": "disabled",
                "detail": f"not configured, requires {', '.join(missing)}",
                "note": spec["note"],
            })
            continue

        module_name, class_name = spec["import_path"]
        try:
            module = __import__(module_name, fromlist=[class_name])
            connector = getattr(module, class_name)(database, settings, queued)
        except Exception as exc:  # pragma: no cover - defensive
            statuses.append({"source": spec["key"], "label": spec["label"],
                             "state": "error", "detail": f"could not start: {exc.__class__.__name__}"})
            continue

        try:
            # poll_once() is CTAS's own single-pass cycle, the same call its
            # long-running worker makes. No logic is duplicated here.
            result = await asyncio.wait_for(connector.poll_once(), timeout=args.source_timeout)
            detail = ""
            if isinstance(result, dict):
                detail = f"{result.get('unique_alerts', '?')} unique public alerts in bounded lookback"
            elif isinstance(result, tuple) and result:
                detail = f"{result[0]} public note(s)"
            statuses.append({"source": spec["key"], "label": spec["label"],
                             "state": "ok", "detail": detail, "note": spec["note"]})
        except TimeoutError:
            statuses.append({"source": spec["key"], "label": spec["label"],
                             "state": "timeout",
                             "detail": f"no response within {args.source_timeout}s"})
        except Exception as exc:
            statuses.append({"source": spec["key"], "label": spec["label"],
                             "state": "unavailable",
                             "detail": f"{exc.__class__.__name__}: {str(exc)[:160]}"})

    # Drain CTAS's durable ingest queue so everything the connectors enqueued
    # is actually normalized, rights-screened and stored.
    drained = 0
    try:
        while drained < args.max_queue_items:
            if not await queue.process_one():
                break
            drained += 1
    except Exception as exc:
        statuses.append({"source": "ingest-queue", "label": "CTAS ingest queue",
                         "state": "degraded",
                         "detail": f"{exc.__class__.__name__}: {str(exc)[:160]}"})

    # Read back through CTAS's own query layer.
    candidates: list[dict[str, Any]] = []
    try:
        query = QueryService(database)
        page = await query.events(simulation=False, age_hours=args.lookback_hours,
                                  limit=args.max_candidates)
        for summary in page.get("items", page.get("events", [])) or []:
            public = sanitize_event(summary)
            if public:
                candidates.append(public)
    except Exception as exc:
        statuses.append({"source": "query", "label": "CTAS query layer", "state": "error",
                         "detail": f"{exc.__class__.__name__}: {str(exc)[:160]}"})

    candidates.sort(key=lambda c: (-(c.get("ctas_score") or 0.0), c.get("name", "")))
    await database.close()
    return candidates, statuses


def main() -> int:
    ap = argparse.ArgumentParser(description="Run CTAS headlessly and export public JSON.")
    ap.add_argument("--output-dir", default=str(ROOT / "ctas" / "data"))
    ap.add_argument("--lookback-hours", type=float, default=72.0)
    ap.add_argument("--max-candidates", type=int, default=200)
    ap.add_argument("--max-queue-items", type=int, default=5000)
    ap.add_argument("--source-timeout", type=float, default=180.0)
    ap.add_argument("--allow-empty", action="store_true",
                    help="write an empty dataset instead of failing when no source succeeded")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = utcnow()

    # Keep CTAS's own state ephemeral: a throwaway SQLite file per run. The
    # public dataset is the only thing that persists between runs.
    tmp = tempfile.mkdtemp(prefix="ctas-run-")
    os.environ.setdefault("SOC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}/run.db")
    os.environ.setdefault("SOC_ENVIRONMENT", "ci")
    os.environ.setdefault("ARTIFACT_STORE_DIR", f"{tmp}/artifacts")

    failure: str | None = None
    candidates: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    try:
        candidates, statuses = asyncio.run(run_ctas(args))
    except Exception as exc:
        failure = f"{exc.__class__.__name__}: {exc}"
        print("CTAS run failed:", failure, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    ok_sources = [s for s in statuses if s["state"] == "ok"]
    enabled = [s for s in statuses if s["state"] != "disabled"]

    candidates_path = out_dir / "candidates.json"
    status_path = out_dir / "status.json"

    degraded = bool(failure) or (enabled and not ok_sources)

    # If this run produced nothing usable, prefer keeping the last good
    # dataset over publishing an empty or broken one. --allow-empty overrides
    # this and publishes an honest empty dataset instead (used for the very
    # first deployment, before any source has ever succeeded).
    if degraded and not args.allow_empty:
        if candidates_path.exists():
            try:
                previous = json.loads(candidates_path.read_text())
                prev_n = len(previous.get("candidates", []))
            except Exception:
                previous, prev_n = None, 0
            if previous is not None:
                status_path.write_text(json.dumps({
                    "schema_version": SCHEMA_VERSION,
                    "pipeline_status": "degraded",
                    "last_successful_update": previous.get("generated_at"),
                    "last_attempt": iso(started),
                    "candidate_count": prev_n,
                    "cadence": CADENCE_TEXT,
                    "sources": statuses,
                    "detail": "No alert source responded on this run; the previous dataset is retained.",
                }, indent=2) + "\n")
                print(f"degraded: retained previous dataset ({prev_n} candidates)")
                return 0
        print("no usable data and no previous dataset to retain", file=sys.stderr)
        return 1

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(utcnow()),
        "cadence": CADENCE_TEXT,
        "lookback_hours": args.lookback_hours,
        "candidate_count": len(candidates),
        "degraded": degraded,
        "candidates": candidates,
    }

    problems = validate_payload(payload)
    if problems:
        print("public payload failed validation:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1

    candidates_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    status_path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "pipeline_status": "ok" if ok_sources else ("degraded" if degraded else "idle"),
        "last_successful_update": payload["generated_at"],
        "last_attempt": iso(started),
        "candidate_count": len(candidates),
        "cadence": CADENCE_TEXT,
        "runtime_seconds": round((utcnow() - started).total_seconds(), 1),
        "sources": statuses,
    }, indent=2) + "\n")

    print(f"wrote {candidates_path} ({len(candidates)} candidates)")
    print(f"wrote {status_path}")
    for s in statuses:
        print(f"  [{s['state']:11s}] {s['label']}: {s.get('detail','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
