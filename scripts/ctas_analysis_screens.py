"""Collector analysis screens published beside each dossier (Framework v2 Track 3a).

The CTAS collector already computes four products that never reached the public export:

* ``population-anomaly`` (robust-population-feature-deviation): how far an event's retained
  features sit from the current CTAS population, with the features that drive it;
* ``multiband-light-curve`` (shared-shape multiband Bazin plus an AB-SED blackbody screen):
  the joint light-curve shape, peak colours and a single-temperature colour screen;
* ``gw-counterpart-ranking`` (healpix-credible-level-distance-ranking): every localized
  optical candidate in a gravitational-wave sky map's window, ranked in 2-D and 3-D;
* ``gw-tiling-plan`` (greedy-healpix-probability-coverage): a draft tiling of that map for
  the WIRO and Red Buttes fields (placeholder fields of view, Framework v2 section 9).

Each is published as an analysis run: method and version, an input summary with the
collector's input checksum, parameters, software versions, the result (large diagnostic
arrays omitted and named), the collector's result checksum, quality, warnings, review state
and a claim boundary. Only the latest ``complete`` run of each kind (each field, for tiling)
is published, under the top-level candidate key ``analysis_screens``; an event with none
has no key at all, so its dossier bytes do not change. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA = "ctas.analysis-screens@1"
CANDIDATE_KEY = "analysis_screens"
SCREEN_TYPES = (
    "population-anomaly",
    "multiband-light-curve",
    "gw-counterpart-ranking",
    "gw-tiling-plan",
)
# The badge rule, stated where it is applied: the collector's score is the root mean square
# of capped absolute robust z scores, so 2.0 means the retained features sit, on average,
# about two robust standard deviations from the CTAS population median.
UNUSUAL_SCORE_THRESHOLD = 2.0
# Diagnostic arrays the dossier does not draw; they stay in the collector's run (its result
# checksum, published beside the trimmed result, covers them).
OMITTED_RESULT_FIELDS = {
    "multiband-light-curve": ("residuals", "parameter_covariance"),
}
RANKING_ROW_LIMIT = 500
CLAIM_BOUNDARIES = {
    "population-anomaly": (
        "A deviation from the current retained CTAS population on the listed features, which "
        "reflects the CTAS selection function and missing data. It is not a statement that the "
        "object is astrophysically rare or of any class."
    ),
    "multiband-light-curve": (
        "An empirical shared-shape Bazin description of the retained difference photometry and an "
        "observer-frame single-temperature colour screen. No extinction, K-correction, redshift or "
        "explosion model is fitted; the temperature is not a physical photospheric temperature claim."
    ),
    "gw-counterpart-ranking": (
        "An ordering of retained, localized optical records by sky-map probability density (2-D) and "
        "by density times the map's distance likelihood (3-D, only records with a retained distance). "
        "It is not an association, a counterpart claim or a completeness statement."
    ),
    "gw-tiling-plan": (
        "A draft sky-probability tiling with circular fields and no chip gaps, rotation, weather or "
        "schedule constraints. It requests no telescope time; the WIRO and Red Buttes fields of view "
        "are placeholders until the real instrument values are supplied."
    ),
}


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    if value in (None, ""):
        return fallback
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _valid_sha256(value: Any) -> str | None:
    rendered = str(value or "").strip().lower()
    return rendered if re.fullmatch(r"[0-9a-f]{64}", rendered) else None


def _iso(value: Any) -> str | None:
    rendered = str(value or "").strip()
    if not rendered:
        return None
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _field_id(row: dict[str, Any]) -> str:
    field = _json(row.get("parameters"), {}).get("field")
    if isinstance(field, dict) and field.get("id"):
        return str(field["id"])
    fov = _json(row.get("parameters"), {}).get("field_of_view_sq_deg")
    return f"fov-{fov}" if fov is not None else ""


def latest_complete_runs(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The latest complete, rights-cleared run of each screen type (each field for tiling)."""

    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        kind = str(row.get("analysis_type") or "")
        if kind not in SCREEN_TYPES or str(row.get("status") or "").lower() != "complete":
            continue
        if str(row.get("data_rights") or "").lower() not in {"public", "open"}:
            continue
        key = (kind, _field_id(row) if kind == "gw-tiling-plan" else "")
        clock = (str(row.get("completed_at") or row.get("created_at") or ""), str(row.get("id") or ""))
        current = latest.get(key)
        if current is None or clock > (str(current.get("completed_at") or current.get("created_at") or ""), str(current.get("id") or "")):
            latest[key] = row
    return [latest[key] for key in sorted(latest, key=lambda item: (SCREEN_TYPES.index(item[0]), item[1]))]


def _inputs(kind: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if kind == "population-anomaly":
        return {
            "populationChecksumSha256": _valid_sha256(manifest.get("population_checksum_sha256")),
            "populationSize": manifest.get("population_size"),
            "targetFeatures": {
                key: value for key, value in sorted((manifest.get("target_features") or {}).items())
                if key != "event_id"
            },
        }
    if kind == "multiband-light-curve":
        records = [row for row in manifest.get("records") or [] if isinstance(row, dict)]
        observed = sorted(str(row.get("observed_at")) for row in records if row.get("observed_at"))
        return {
            "recordCount": len(records),
            "providers": sorted({str(row.get("provider")) for row in records if row.get("provider")}),
            "bands": sorted({str(row.get("band")) for row in records if row.get("band")}),
            "observedFrom": _iso(observed[0]) if observed else None,
            "observedTo": _iso(observed[-1]) if observed else None,
        }
    summary = {
        "signalId": manifest.get("signal_id"),
        "skymapRevisionId": manifest.get("skymap_revision_id"),
        "skymapChecksumSha256": _valid_sha256(manifest.get("skymap_checksum_sha256")),
    }
    if kind == "gw-counterpart-ranking":
        summary["candidateSetChecksumSha256"] = _valid_sha256(manifest.get("candidate_set_checksum_sha256"))
        summary["candidateCount"] = len(manifest.get("candidates") or [])
    return summary


def _result(kind: str, result: dict[str, Any], public_events: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    omitted = [key for key in OMITTED_RESULT_FIELDS.get(kind, ()) if key in result]
    published = {key: value for key, value in result.items() if key not in omitted}
    if kind == "gw-counterpart-ranking":
        rows = []
        for row in (result.get("rankings") or [])[:RANKING_ROW_LIMIT]:
            if not isinstance(row, dict):
                continue
            event_id = str(row.get("event_id") or "")
            public = public_events.get(event_id)
            status = str((public or {}).get("status") or "").lower()
            rows.append({
                "eventId": event_id,
                "name": (public or {}).get("name") or row.get("preferred_name"),
                "candidateState": (
                    "retracted" if status in {"retracted", "bogus"}
                    else "public" if public else "not-in-public-catalog"
                ),
                "rank2d": row.get("rank_2d"),
                "rank3d": row.get("rank_3d"),
                "probabilityDensityPerSr": row.get("probability_density_per_sr"),
                "cellProbabilityMass": row.get("cell_probability_mass"),
                "credibleLevel": row.get("credible_level"),
                "distanceLikelihood": row.get("distance_likelihood"),
                "jointScore": row.get("joint_score"),
            })
        published["rankings"] = rows
    return published, omitted


def build_screen(row: dict[str, Any], public_events: dict[str, dict[str, Any]], *, retracted_event: bool = False) -> dict[str, Any]:
    kind = str(row.get("analysis_type"))
    manifest = _json(row.get("input_manifest"), {})
    result = _json(row.get("result"), {})
    published, omitted = _result(kind, result, public_events)
    screen = {
        "analysisRunId": str(row.get("id") or ""),
        "analysisType": kind,
        "methodName": str(row.get("method_name") or ""),
        "methodVersion": str(row.get("method_version") or ""),
        "status": "COMPLETE",
        "inputs": _inputs(kind, manifest),
        "inputChecksumSha256": _valid_sha256(row.get("input_checksum")) or _sha256(manifest),
        "parameters": _json(row.get("parameters"), {}),
        "softwareVersions": _json(row.get("software_versions"), {}),
        "result": published,
        "resultChecksumSha256": _valid_sha256(row.get("result_checksum")) or _sha256(result),
        "omittedResultFields": omitted,
        "quality": _json(row.get("quality"), {}),
        "warnings": [str(value) for value in _json(row.get("warnings"), [])],
        "reviewState": {"machine": "MACHINE", "human-reviewed": "HUMAN_REVIEWED", "human-adopted": "HUMAN_ADOPTED",
                        "rejected": "REJECTED"}.get(str(row.get("review_state") or "machine").lower(), "MACHINE"),
        "completedAt": _iso(row.get("completed_at") or row.get("created_at")),
        "claimBoundary": CLAIM_BOUNDARIES[kind],
    }
    if kind == "population-anomaly":
        score = published.get("robust_deviation_score")
        screen["badge"] = {
            "rule": f"unusual when the robust deviation score is at least {UNUSUAL_SCORE_THRESHOLD}",
            "unusual": isinstance(score, (int, float)) and score >= UNUSUAL_SCORE_THRESHOLD,
            "score": score,
            "drivingFeatures": [
                {"feature": item.get("feature"), "robustZ": item.get("robust_z")}
                for item in sorted(
                    (item for item in published.get("feature_deviations") or [] if isinstance(item, dict)),
                    key=lambda item: -abs(float(item.get("robust_z") or 0)),
                )[:3]
            ],
        }
    if kind in {"gw-counterpart-ranking", "gw-tiling-plan"}:
        screen["eventRetracted"] = bool(retracted_event)
    return screen


def build_analysis_screens(candidate: dict[str, Any], rows: Iterable[dict[str, Any]],
                           public_events: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    runs = latest_complete_runs(rows)
    if not runs:
        return None
    retracted = str(candidate.get("status") or "").lower() in {"retracted", "bogus"}
    return {"schema": SCHEMA, "runs": [build_screen(row, public_events, retracted_event=retracted) for row in runs]}


def screen_problems(candidates: Iterable[dict[str, Any]]) -> list[str]:
    """The certificate gate: every published screen names its inputs and method version."""

    problems: list[str] = []
    for candidate in candidates:
        block = candidate.get(CANDIDATE_KEY)
        if block is None:
            continue
        name = candidate.get("name") or candidate.get("event_id")
        if not isinstance(block, dict) or block.get("schema") != SCHEMA or not block.get("runs"):
            problems.append(f"{name}: analysis_screens is not a {SCHEMA} block with runs")
            continue
        for run in block["runs"]:
            kind = run.get("analysisType")
            label = f"{name} {kind}"
            if kind not in SCREEN_TYPES:
                problems.append(f"{label}: unknown screen type")
            if not run.get("methodName") or not run.get("methodVersion"):
                problems.append(f"{label}: method name or version missing")
            if not _valid_sha256(run.get("inputChecksumSha256")) or not isinstance(run.get("inputs"), dict) or not run["inputs"]:
                problems.append(f"{label}: inputs or input checksum missing")
            if not _valid_sha256(run.get("resultChecksumSha256")):
                problems.append(f"{label}: result checksum missing")
            if run.get("status") != "COMPLETE" or not run.get("claimBoundary"):
                problems.append(f"{label}: not a complete run with a claim boundary")
    return problems
