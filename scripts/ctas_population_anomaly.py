"""Population anomaly and "objects like this" for CTAS (Framework v2 Track 3c, part 2).

One release-level file, ``ctas/data/population-anomaly.json`` (``ctas.population-anomaly@1``),
built each release from the light-curve features of Track 3c part 1:

* the feature table itself (one row per event with every feature present), so the run is
  reproducible from the file alone;
* an isolation-forest anomaly score per event (coniferest, Korolev et al.; seeded, stated
  parameters), its rank and percentile, and the features that drive it (coniferest's
  per-feature signature: the most negative contributions);
* the five nearest neighbours of every event in the robustly scaled feature space
  ("objects like this"), with their names and reported classifications.

It lives in its own file, not in the dossiers, because a population score changes whenever
the population does; the dossiers stay byte-identical. The Track 3a collector score
(analysis_screens) is unchanged and shown beside it.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

PATH = "ctas/data/population-anomaly.json"
SCHEMA = "ctas.population-anomaly@1"
VERSION = "1.0.0"
N_TREES = 500
N_SUBSAMPLES = 256
RANDOM_SEED = 20261029
NEIGHBOURS = 5
DRIVERS = 2
MIN_POPULATION = 50
FEATURES = (
    ("log10HalfMaxRiseDays", "log10 of the half-maximum rise time (days)"),
    ("log10HalfMaxDurationDays", "log10 of the half-maximum duration (days)"),
    ("asinhDecline15", "asinh(15-day decline rate / 0.01 mag per day)"),
    ("peakGMinusR", "ZTF g - r at the fitted peaks (mag, not extinction-corrected)"),
)
CLAIM = (
    "A score of how unusual an event's light-curve shape is among the CTAS events with the "
    "same features in this release; it points at events worth a look and is not a "
    "classification, and it changes as the population changes."
)


def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _round(value: float | None, digits: int = 5) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if value == 0:
        return 0.0
    return float(f"{value:.{digits}g}")


def feature_row(candidate: dict[str, Any]) -> tuple[dict[str, float] | None, str | None]:
    block = candidate.get("lightcurve_science") or {}
    run = next((r for r in block.get("runs") or [] if r.get("analysisType") == "lightcurve-features"), None)
    if not run or run.get("status") != "complete":
        return None, "no complete light-curve features"
    result = run.get("result") or {}
    rise, duration, decline = result.get("halfMaxRiseDays"), result.get("halfMaxDurationDays"), result.get("declineMagPerDay15")
    bands = result.get("bands") or {}
    g, r = (bands.get("ztfg") or {}).get("peakMagnitude"), (bands.get("ztfr") or {}).get("peakMagnitude")
    if rise is None or duration is None or decline is None or not rise > 0 or not duration > 0:
        return None, "rise, duration or decline missing"
    if g is None or r is None:
        return None, "no ZTF g and r peak magnitudes"
    row = {"log10HalfMaxRiseDays": math.log10(rise), "log10HalfMaxDurationDays": math.log10(duration),
           "asinhDecline15": math.asinh(decline / 0.01), "peakGMinusR": g - r}
    if not all(math.isfinite(v) for v in row.values()):
        return None, "a feature is not finite"
    return {k: float(f"{v:.6g}") for k, v in row.items()}, None


def _robust_scale(columns):
    import numpy as np
    med = np.median(columns, axis=0)
    mad = np.median(np.abs(columns - med), axis=0) * 1.4826
    mad[mad == 0] = 1.0
    return (columns - med) / mad, med, mad


def build(candidates: Iterable[dict[str, Any]], release_label: str | None = None) -> dict[str, Any]:
    """The release-level document (always produced; 'not computed' with a reason when the
    population is too small or coniferest is unavailable)."""

    rows, skipped = [], {}
    for candidate in candidates:
        values, reason = feature_row(candidate)
        if values is None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        lightcurve = candidate.get("lightcurve_science") or {}
        rows.append({"eventId": str(candidate.get("event_id")), "name": candidate.get("name"),
                     "classification": candidate.get("classification"),
                     "lightcurveInputChecksumSha256": (lightcurve.get("inputs") or {}).get("inputChecksumSha256"),
                     "features": values})
    rows.sort(key=lambda r: (str(r["name"]), r["eventId"]))
    table = [{"eventId": r["eventId"], "features": r["features"]} for r in rows]
    parameters = {"nTrees": N_TREES, "nSubsamples": N_SUBSAMPLES, "randomSeed": RANDOM_SEED, "neighbours": NEIGHBOURS,
                  "drivers": DRIVERS, "minPopulation": MIN_POPULATION,
                  "features": [{"name": n, "definition": d} for n, d in FEATURES],
                  "scaling": "each feature minus its population median, divided by 1.4826 x its median absolute deviation",
                  "score": "coniferest IsolationForest.score_samples (lower = more unusual); rank 1 = most unusual",
                  "drivers": "the features with the most negative coniferest feature_signature contributions",
                  "neighbourMetric": "Euclidean distance in the robustly scaled feature space"}
    document: dict[str, Any] = {
        "schema": SCHEMA, "methodName": "coniferest-isolation-forest-and-neighbours", "methodVersion": VERSION,
        "release": release_label, "parameters": parameters, "claimBoundary": CLAIM,
        "references": ["2020A&C....3200388I", "2008ICDM....413L"],
        "featureTable": table, "featureTableChecksumSha256": _sha256(table),
        "population": len(rows), "excludedByReason": dict(sorted(skipped.items())),
    }
    if len(rows) < MIN_POPULATION:
        document.update(status="not-computed", notComputedReason=f"only {len(rows)} events have every feature (minimum {MIN_POPULATION})", events=[])
        document["resultChecksumSha256"] = None
        return document
    try:
        import numpy as np
        import coniferest
        from coniferest.isoforest import IsolationForest
    except ImportError as error:
        document.update(status="not-computed", notComputedReason=f"coniferest is not installed ({error})", events=[])
        document["resultChecksumSha256"] = None
        return document
    names = [n for n, _ in FEATURES]
    raw = np.array([[r["features"][n] for n in names] for r in rows], dtype=float)
    scaled, med, mad = _robust_scale(raw)
    forest = IsolationForest(n_trees=N_TREES, n_subsamples=N_SUBSAMPLES, random_seed=RANDOM_SEED, n_jobs=1).fit(scaled)
    scores = forest.score_samples(scaled)
    signature = forest.feature_signature(scaled)
    order = sorted(range(len(rows)), key=lambda i: (scores[i], rows[i]["name"] or "", rows[i]["eventId"]))
    rank_of = {i: rank for rank, i in enumerate(order, start=1)}
    events = []
    for i, row in enumerate(rows):
        contributions = sorted(((float(signature[i][j]), names[j]) for j in range(len(names))), key=lambda item: (item[0], item[1]))
        drivers = [{"feature": name, "value": row["features"][name], "robustZ": _round(float(scaled[i][names.index(name)]), 4),
                    "contribution": _round(value, 4)} for value, name in contributions[:DRIVERS] if value < 0]
        distances = np.sqrt(((scaled - scaled[i]) ** 2).sum(axis=1))
        near = sorted((float(distances[j]), rows[j]["name"] or "", j) for j in range(len(rows)) if j != i)[:NEIGHBOURS]
        events.append({
            "eventId": row["eventId"], "name": row["name"], "score": _round(float(scores[i]), 6),
            "rank": rank_of[i], "percentile": _round(100.0 * (len(rows) - rank_of[i]) / (len(rows) - 1), 4),
            "drivers": drivers,
            "neighbours": [{"eventId": rows[j]["eventId"], "name": rows[j]["name"], "classification": rows[j]["classification"],
                            "distance": _round(d, 4)} for d, _, j in near],
        })
    events.sort(key=lambda e: e["rank"])
    document.update(status="complete", events=events,
                    scaling={"median": [_round(float(v), 6) for v in med], "mad": [_round(float(v), 6) for v in mad]},
                    software={"coniferest": getattr(coniferest, "__version__", "unknown"), "numpy": np.__version__})
    document["resultChecksumSha256"] = _sha256(events)
    return document


def problems(document: dict[str, Any] | None, candidates: Iterable[dict[str, Any]]) -> list[str]:
    """Certificate gate: the document names its method and version, its feature table
    matches its checksum and the dossiers' light-curve features, and every scored event and
    neighbour is a public CTAS event."""

    out: list[str] = []
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        return ["population-anomaly.json is missing or not a " + SCHEMA + " document"]
    if not document.get("methodName") or not document.get("methodVersion"):
        out.append("method name or version missing")
    table = document.get("featureTable")
    if not isinstance(table, list) or document.get("featureTableChecksumSha256") != _sha256(table):
        out.append("feature table checksum missing or wrong")
        return out
    by_id = {str(c.get("event_id")): c for c in candidates}
    for row in table:
        candidate = by_id.get(str(row.get("eventId")))
        if candidate is None:
            out.append(f"{row.get('eventId')}: not a public event")
            continue
        expected, _ = feature_row(candidate)
        if expected != row.get("features"):
            out.append(f"{candidate.get('name')}: features do not match the dossier's light-curve features")
    if document.get("status") == "complete":
        events = document.get("events") or []
        if document.get("resultChecksumSha256") != _sha256(events):
            out.append("result checksum missing or wrong")
        ids = {str(r.get("eventId")) for r in table}
        for event in events:
            if str(event.get("eventId")) not in ids:
                out.append(f"{event.get('name')}: scored but not in the feature table")
            for neighbour in event.get("neighbours") or []:
                if str(neighbour.get("eventId")) not in ids:
                    out.append(f"{event.get('name')}: neighbour {neighbour.get('name')} not in the feature table")
    elif document.get("status") == "not-computed":
        if not document.get("notComputedReason"):
            out.append("not computed without a reason")
    else:
        out.append("unknown status")
    return out[:50]
