"""Multimessenger event pages for CTAS dossiers (Framework v2 Track 3d).

Every public event whose primary messenger is a gravitational wave, a neutrino, a fast radio
burst or an X-ray localisation gets a ``multimessenger`` block (``ctas.multimessenger@1``),
absent for every other event (their dossier bytes do not change):

* gravitational waves (LVK public alerts): the alert's own fields first-class -- LVK's
  significance flag, the false-alarm rate, the source classification, HasNS / HasRemnant /
  HasMassGap / HasSSM, group, search and pipeline, the external-coincidence summary (with a
  combined sky map when one was published), the notice history and the retraction state;
  then the collector's analysis runs on the newest retained sky map: Multi-Order Coverage
  maps (IVOA MOC 2.0) of the 10/50/90 % regions, the 90 % Space-Time MOC over the 14-day
  follow-up window, the galaxies inside the 90 % region ranked by volume density times a
  stated weight, and every revision of those runs (the collector re-runs them hourly for 14
  days); the counterpart registry joins the Track 3a optical ranking with each candidate's
  public state, prior activity and an implied absolute magnitude at the map's distance.
* neutrinos (IceCube), FRBs (CHIME/FRB, DSA-110) and X-ray localisations (Einstein Probe,
  SVOM, MAXI, Swift/XRT): the published circular region (centre, radius, containment as the
  provider states it), its area, and the public CTAS optical events inside it discovered
  within a stated window, computed by this exporter at every release.

The WIRO / Red Buttes observation log that will draw coverage is not imported yet (Track 4)
and the block says so. Treasure Map's public candidates are not read: every endpoint of its
API needs a personal token, and none is configured. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

CANDIDATE_KEY = "multimessenger"
SCHEMA = "ctas.multimessenger@1"
VERSION = "1.0.0"
RUN_TYPES = ("gw-localization-moc", "gw-galaxy-ranking", "gw-counterpart-ranking")
RUNS_SQL = """
SELECT ar.*
FROM analysis_runs ar
WHERE ar.event_id IN ({ids})
  AND ar.analysis_type IN ('gw-localization-moc', 'gw-galaxy-ranking', 'gw-counterpart-ranking')
  AND ar.data_rights IN ('public', 'open')
ORDER BY ar.event_id, COALESCE(ar.completed_at, ar.created_at), ar.id
"""
CIRCLE_MESSENGERS = {"neutrino", "radio", "x-ray"}
# Events listed inside a region are optical / electromagnetic transients, not other notices.
OPTICAL_MESSENGERS = {"electromagnetic", "optical", "multimessenger", "", "unknown"}
WINDOW_BEFORE_DAYS = 1.0
WINDOW_AFTER_DAYS = 14.0
GALAXY_ROWS = 100
REGISTRY_ROWS = 200
REVISION_ROWS = 50
SECONDS_PER_YEAR = 365.25 * 86400
LVK_PROPERTY_KEYS = ("HasNS", "HasRemnant", "HasMassGap", "HasSSM")
LVK_CLASSES = ("BNS", "NSBH", "BBH", "Terrestrial")
CLAIM_CIRCLE = (
    "The published localisation region of one notice and the public CTAS optical events that "
    "fall inside it in a stated time window; a position inside the region is not an "
    "association, and the region's containment is the provider's statement."
)
CLAIM_REGISTRY = (
    "Public optical events inside the newest retained sky map, with their CTAS state; listing "
    "is not a counterpart claim. The implied absolute magnitude uses the map's mean distance "
    "and no extinction or K-correction, and only says how bright the event would be at that "
    "distance."
)
TREASURE_MAP_NOTE = ("Treasure Map public candidates not read: every endpoint of its API needs a "
                     "personal API token, and none is configured.")
COVERAGE_NOTE = ("WIRO and Red Buttes observation log not imported yet (Framework v2 Track 4); "
                 "no coverage is drawn.")


# ----------------------------------------------------------------------------- helpers
def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


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


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(f"{value:.{digits}g}")


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    moment = _time(value)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    value = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, value))))


def cap_area_sq_deg(radius_deg: float) -> float:
    return 2 * math.pi * (1 - math.cos(math.radians(radius_deg))) * (180 / math.pi) ** 2


def _run(kind: str, method: str, *, inputs: dict[str, Any], parameters: dict[str, Any],
         result: dict[str, Any] | None, reason: str | None = None, warnings: list[str] | None = None,
         claim: str) -> dict[str, Any]:
    run: dict[str, Any] = {
        "analysisType": kind, "methodName": method, "methodVersion": VERSION,
        "status": "complete" if result is not None else "not-computed",
        "inputs": inputs, "inputChecksumSha256": _sha256(inputs), "parameters": parameters,
        "warnings": warnings or [], "claimBoundary": claim,
    }
    if result is not None:
        run["result"] = result
        run["resultChecksumSha256"] = _sha256(result)
    else:
        run["notComputedReason"] = reason or "not computed"
    return run


def _signals(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (candidate.get("follow_up") or {}).get("messenger_signals") or [] if isinstance(row, dict)]


def _collector_run(row: dict[str, Any]) -> dict[str, Any]:
    """A collector analysis run as published (method, version, checksums, result)."""

    manifest = _json(row.get("input_manifest"), {})
    result = _json(row.get("result"), {})
    parameters = _json(row.get("parameters"), {})
    return {
        "analysisRunId": str(row.get("id") or ""),
        "analysisType": str(row.get("analysis_type") or ""),
        "methodName": str(row.get("method_name") or ""),
        "methodVersion": str(row.get("method_version") or ""),
        "status": str(row.get("status") or "").lower(),
        "inputs": manifest,
        "inputChecksumSha256": str(row.get("input_checksum") or "") or _sha256(manifest),
        "parameters": parameters,
        "softwareVersions": _json(row.get("software_versions"), {}),
        "result": result,
        "resultChecksumSha256": str(row.get("result_checksum") or "") or _sha256(result),
        "warnings": [str(value) for value in _json(row.get("warnings"), [])],
        "completedAt": _iso(row.get("completed_at") or row.get("created_at")),
        "claimBoundary": str(parameters.get("claim_boundary") or ""),
    }


# ----------------------------------------------------------------------------- GW
def lvk_facts(candidate: dict[str, Any], signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    gw = [row for row in signals if row.get("messenger") == "gravitational-wave"]
    if not gw:
        return None
    gw.sort(key=lambda row: (str(row.get("observed_at") or ""), str(row.get("ctas_received_at") or "")))
    latest = max(gw, key=lambda row: str(row.get("ctas_received_at") or row.get("observed_at") or ""))
    properties = _json(latest.get("properties"), {})
    classification = _json(latest.get("classification"), {})
    far_hz = _float(latest.get("false_alarm_rate_hz"))
    coinc = {key[len("external_coinc_"):]: value for key, value in sorted(properties.items())
             if key.startswith("external_coinc_")}
    facts = {
        "significant": properties.get("significant") if isinstance(properties.get("significant"), bool) else None,
        "significantBasis": "LVK alert flag" if isinstance(properties.get("significant"), bool) else "not in the retained alert",
        "falseAlarmRateHz": _round(far_hz),
        "falseAlarmRatePerYear": _round(far_hz * SECONDS_PER_YEAR) if far_hz is not None else None,
        "classification": {key: _round(_float(classification.get(key)), 4) for key in LVK_CLASSES if key in classification},
        "properties": {key: (_round(_float(properties.get(key)), 4) if key in properties else None) for key in LVK_PROPERTY_KEYS},
        "group": properties.get("group"), "search": properties.get("search"), "pipeline": properties.get("pipeline"),
        "instruments": [str(value) for value in _json(latest.get("detector_network"), [])],
        "externalCoincidence": coinc or None,
        "combinedSkyMapPublished": bool(coinc.get("combined_skymap")) if coinc else False,
        "chirpMassBins": None,
        "chirpMassNote": "not in the public alert (LVK publishes chirp-mass bins only for sub-solar-mass searches)",
        "gracedbUrl": properties.get("gracedb_url") or latest.get("source_url"),
        "noticeHistory": [
            {"alertType": row.get("alert_type"), "issuedAt": _iso(row.get("source_published_at") or row.get("ctas_received_at")),
             "falseAlarmRateHz": _round(_float(row.get("false_alarm_rate_hz")))}
            for row in gw
        ],
        "retracted": str(candidate.get("status") or "").lower() == "retracted",
    }
    return facts


def _latest(runs: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    complete = [run for run in runs if run["analysisType"] == kind and run["status"] in {"complete", "insufficient-data"}]
    return complete[-1] if complete else None


def localization_block(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    latest = _latest(runs, "gw-localization-moc")
    if latest is None:
        return None
    reference = str(latest["inputs"].get("skymap_source_reference") or "")
    revisions = [
        {"completedAt": run["completedAt"], "skymapChecksumSha256": run["inputs"].get("skymap_checksum_sha256"),
         "alertType": run["inputs"].get("alert_type"),
         "combinedMap": str(run["inputs"].get("skymap_source_reference") or "").startswith("gcn-embedded-combined:"),
         "area90SqDeg": (run["result"].get("credible_area_sq_deg") or {}).get("90")}
        for run in runs if run["analysisType"] == "gw-localization-moc"
    ][-REVISION_ROWS:]
    return {**latest, "combinedMap": reference.startswith("gcn-embedded-combined:"), "revisions": revisions}


def galaxy_block(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    latest = _latest(runs, "gw-galaxy-ranking")
    if latest is None:
        return None
    result = dict(latest["result"])
    rows = list(result.get("galaxies") or [])
    result["galaxies"] = rows[:GALAXY_ROWS]
    result["publishedRows"] = len(result["galaxies"])
    revisions = [
        {"completedAt": run["completedAt"], "galaxiesInRegion": run["result"].get("galaxies_in_region"),
         "catalogue": (run["result"].get("catalogue") or {}).get("name"),
         "catalogueChecksumSha256": (run["result"].get("catalogue") or {}).get("checksum_sha256")}
        for run in runs if run["analysisType"] == "gw-galaxy-ranking"
    ][-REVISION_ROWS:]
    published = {**latest, "result": result, "revisions": revisions}
    published["resultChecksumSha256"] = latest["resultChecksumSha256"]  # the collector's, over all rows
    return published


def counterpart_registry(candidate: dict[str, Any], runs: list[dict[str, Any]],
                         index: dict[str, dict[str, Any]], distance_mpc: float | None) -> dict[str, Any]:
    ranking = _latest(runs, "gw-counterpart-ranking")
    rows = []
    for row in ((ranking or {}).get("result") or {}).get("rankings") or []:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or "")
        public = index.get(event_id)
        state = ("retracted" if public and public["status"] in {"retracted", "bogus"}
                 else "public" if public else "not-in-public-catalog")
        magnitude = public.get("peak_magnitude") if public else None
        absolute = None
        if magnitude is not None and distance_mpc and distance_mpc > 0:
            absolute = round(magnitude - 5 * math.log10(distance_mpc * 1e6 / 10), 2)
        rows.append({
            "eventId": event_id, "name": (public or {}).get("name") or row.get("preferred_name"),
            "state": state, "rank2d": row.get("rank_2d"), "rank3d": row.get("rank_3d"),
            "credibleLevel": row.get("credible_level"),
            "probabilityDensityPerSr": row.get("probability_density_per_sr"),
            "discoveryTime": (public or {}).get("discovery_time"),
            "peakMagnitude": magnitude, "impliedAbsoluteMagnitude": absolute,
            "priorActivity": (public or {}).get("prior_activity"),
        })
    inputs = {"rankingRunId": (ranking or {}).get("analysisRunId"),
              "rankingResultChecksumSha256": (ranking or {}).get("resultChecksumSha256"),
              "mapMeanDistanceMpc": distance_mpc}
    result = {"rows": rows[:REGISTRY_ROWS], "rowCount": len(rows), "treasureMap": TREASURE_MAP_NOTE}
    if ranking is None:
        return _run("counterpart-registry", "public-catalog-state-join", inputs=inputs,
                    parameters={"rows": REGISTRY_ROWS}, result=None,
                    reason="no counterpart ranking has been computed for a retained sky map", claim=CLAIM_REGISTRY)
    return _run("counterpart-registry", "public-catalog-state-join", inputs=inputs,
                parameters={"rows": REGISTRY_ROWS, "absoluteMagnitude": "m - 5 log10(d / 10 pc), map mean distance, no extinction or K-correction"},
                result=result, claim=CLAIM_REGISTRY,
                warnings=[] if distance_mpc else ["the map has no mean distance: no implied absolute magnitudes"])


# ----------------------------------------------------------------------------- circles
def circle_region(signals: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
    rows = [row for row in signals if row.get("messenger") in CIRCLE_MESSENGERS]
    if not rows:
        return None
    rows.sort(key=lambda row: str(row.get("ctas_received_at") or row.get("observed_at") or ""))
    for row in reversed(rows):  # the newest notice that carries a region
        props = _json(row.get("properties"), {})
        ra = _float(props.get("source_ra_deg"))
        dec = _float(props.get("source_dec_deg"))
        if ra is None or dec is None:
            ra, dec = _float(candidate.get("ra_deg")), _float(candidate.get("dec_deg"))
        radius, basis, ellipse = None, None, None
        for key in ("position_error_90_deg", "ra_dec_error", "position_error_radius_deg"):
            value = props.get(key)
            if isinstance(value, list) and len(value) >= 2:
                # GCN unified schema: [semi-major, semi-minor, position angle] in degrees; the
                # circle drawn is the one that contains the ellipse.
                axes = [_float(v) for v in value[:2]]
                if all(a is not None and a > 0 for a in axes):
                    ellipse = {"semiMajorDeg": _round(max(axes), 6), "semiMinorDeg": _round(min(axes), 6),
                               "positionAngleDeg": _round(_float(value[2]), 6) if len(value) > 2 else None}
                    radius, basis = max(axes), f"{key} (ellipse; circumscribing circle)"
                    break
                continue
            radius = _float(value)
            if radius is not None and radius > 0:
                basis = key
                break
        if ra is None or dec is None or radius is None:
            continue
        containment = _float(props.get("containment_probability"))
        return {
            "messenger": row.get("messenger"), "instrument": row.get("instrument"),
            "noticeId": row.get("assertion_id"), "alertType": row.get("alert_type"),
            "observedAt": _iso(row.get("observed_at")),
            "centerRaDeg": _round(ra, 9), "centerDecDeg": _round(dec, 9),
            "radiusDeg": _round(radius, 6), "radiusField": basis, "ellipse": ellipse,
            "radius50Deg": _round(_float(props.get("position_error_50_deg")), 6),
            "containment": containment,
            "containmentBasis": "provider containment_probability" if containment is not None else "as published; containment not stated",
            "areaSqDeg": _round(cap_area_sq_deg(radius), 6),
        }
    return None


def circle_candidates(region: dict[str, Any], event_id: str, index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    t0 = _time(region.get("observedAt"))
    rows = []
    if t0 is not None:
        start, end = t0 - timedelta(days=WINDOW_BEFORE_DAYS), t0 + timedelta(days=WINDOW_AFTER_DAYS)
        for other_id, public in index.items():
            if other_id == event_id or public.get("ra") is None or not public.get("optical", True):
                continue
            discovered = _time(public.get("discovery_time"))
            if discovered is None or not (start <= discovered <= end):
                continue
            distance = separation_deg(region["centerRaDeg"], region["centerDecDeg"], public["ra"], public["dec"])
            if distance > region["radiusDeg"]:
                continue
            rows.append({
                "eventId": other_id, "name": public["name"], "state": public["status"],
                "separationDeg": _round(distance, 6),
                "timeOffsetDays": _round((discovered - t0).total_seconds() / 86400, 6),
                "discoveryTime": public.get("discovery_time"), "peakMagnitude": public.get("peak_magnitude"),
            })
    rows.sort(key=lambda row: (row["separationDeg"], row["name"] or ""))
    return {"rows": rows[:REGISTRY_ROWS], "rowCount": len(rows)}


# ----------------------------------------------------------------------------- assembly
def public_index(candidates: Iterable[dict[str, Any]],
                 host_blocks: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Public events with a usable position, for the region and registry joins."""

    index = {}
    for candidate in candidates:
        ra, dec = _float(candidate.get("ra_deg")), _float(candidate.get("dec_deg"))
        prior = None
        host = (host_blocks or {}).get(str(candidate.get("event_id"))) or candidate.get("host_association") or {}
        for run in host.get("runs") or []:
            if run.get("analysisType") == "prior-activity" and run.get("status") == "complete":
                prior = bool((run.get("result") or {}).get("flagged"))
        magnitudes = [_float(row.get("magnitude")) for row in (candidate.get("follow_up") or {}).get("observations") or []
                      if isinstance(row, dict) and row.get("detection") not in (False, 0)]
        magnitudes = [value for value in magnitudes if value is not None]
        index[str(candidate.get("event_id"))] = {
            "name": candidate.get("name"), "status": str(candidate.get("status") or "").lower(),
            "ra": ra, "dec": dec, "discovery_time": _iso(candidate.get("discovery_time")),
            "peak_magnitude": _round(min(magnitudes), 5) if magnitudes else None,
            "prior_activity": prior,
            "optical": str(candidate.get("primary_messenger") or "").lower() in OPTICAL_MESSENGERS,
        }
    return index


def build_multimessenger(candidate: dict[str, Any], run_rows: Iterable[dict[str, Any]],
                         index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    signals = _signals(candidate)
    if not signals:
        return None
    event_id = str(candidate.get("event_id"))
    runs = [_collector_run(row) for row in run_rows if str(row.get("analysis_type")) in RUN_TYPES]
    facts = lvk_facts(candidate, signals)
    if facts is not None:
        localization = localization_block(runs)
        distance = _float(((localization or {}).get("result") or {}).get("map_distance_mean_mpc"))
        return {
            "schema": SCHEMA, "kind": "gravitational-wave", "lvk": facts,
            "localization": localization,
            "galaxies": galaxy_block(runs),
            "counterparts": counterpart_registry(candidate, runs, index, distance),
            "coverage": COVERAGE_NOTE,
            "refreshPolicy": ("the collector re-runs the counterpart and galaxy rankings hourly for 14 days "
                              "after a significant event; each re-run is listed under revisions"),
        }
    region = circle_region(signals, candidate)
    if region is None:
        return None
    inputs = {"noticeId": region["noticeId"], "center": [region["centerRaDeg"], region["centerDecDeg"]],
              "radiusDeg": region["radiusDeg"], "publicEventCount": len(index)}
    found = circle_candidates(region, event_id, index)
    run = _run("region-candidates", "published-circle-cone-and-window", inputs=inputs,
               parameters={"windowBeforeDays": WINDOW_BEFORE_DAYS, "windowAfterDays": WINDOW_AFTER_DAYS,
                           "separation": "great-circle distance from the published centre"},
               result=found, claim=CLAIM_CIRCLE,
               warnings=["galaxy lists are made for gravitational-wave sky maps only"])
    return {"schema": SCHEMA, "kind": str(region["messenger"]), "region": region, "candidates": run,
            "coverage": COVERAGE_NOTE}


# ----------------------------------------------------------------------------- gate
def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def multimessenger_problems(candidates: Iterable[dict[str, Any]]) -> list[str]:
    """Certificate gate: every run names its method version and inputs with a checksum; the
    MOC areas nest; galaxy lists name their catalogue; registry and region rows reference
    events of this release; region rows lie inside their circle."""

    candidates = list(candidates)
    ids = {str(c.get("event_id")) for c in candidates}
    problems: list[str] = []
    for candidate in candidates:
        block = candidate.get(CANDIDATE_KEY)
        if block is None:
            continue
        name = candidate.get("name") or candidate.get("event_id")
        if block.get("schema") != SCHEMA:
            problems.append(f"{name}: multimessenger block has schema {block.get('schema')!r}")
            continue
        runs = [block.get(key) for key in ("localization", "galaxies", "counterparts", "candidates") if block.get(key)]
        for run in runs:
            if not run.get("methodName") or not run.get("methodVersion"):
                problems.append(f"{name}: a multimessenger run lacks its method or version")
            if not _valid_sha256(run.get("inputChecksumSha256")) or not isinstance(run.get("inputs"), dict):
                problems.append(f"{name}: a multimessenger run lacks inputs with a checksum")
            if run.get("status") == "complete" and not _valid_sha256(run.get("resultChecksumSha256")):
                problems.append(f"{name}: a complete multimessenger run lacks its result checksum")
        localization = block.get("localization")
        if localization:
            areas = [r.get("covering_area_sq_deg") for r in (localization.get("result") or {}).get("regions") or []
                     if r.get("covering_area_sq_deg") is not None]
            if areas != sorted(areas):
                problems.append(f"{name}: credible-region MOC areas do not nest")
            if not localization["inputs"].get("skymap_checksum_sha256"):
                problems.append(f"{name}: localization does not name its sky-map checksum")
        galaxies = block.get("galaxies")
        if galaxies and galaxies.get("status") == "complete" and not (galaxies["result"].get("catalogue") or {}).get("name"):
            problems.append(f"{name}: galaxy ranking does not name its catalogue")
        for row in ((block.get("counterparts") or {}).get("result") or {}).get("rows") or []:
            if row.get("state") != "not-in-public-catalog" and row.get("eventId") not in ids:
                problems.append(f"{name}: registry row {row.get('name')} is not an event of this release")
        region = block.get("region")
        for row in ((block.get("candidates") or {}).get("result") or {}).get("rows") or []:
            if row.get("eventId") not in ids:
                problems.append(f"{name}: region row {row.get('name')} is not an event of this release")
            if region and (row.get("separationDeg") or 0) > (region.get("radiusDeg") or 0) + 1e-9:
                problems.append(f"{name}: region row {row.get('name')} lies outside the published circle")
    return problems
