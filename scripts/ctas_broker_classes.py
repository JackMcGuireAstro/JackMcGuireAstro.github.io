"""Broker classifications side by side (Framework v2 Track 3c, part 1).

The collector asks ALeRCE, Fink and ANTARES about an event's exact retained ZTF object and
keeps each complete response as checksum-replayable query evidence (collector module
``broker_classifications``). This module re-reads those exact bytes, validates them with
the same contracts, and publishes one ``broker_classes`` block per event
(``ctas.broker-classes@1``):

* per broker: the current query outcome (the latest receipt, with the same outcome states
  as every other source), and from the latest receipt that returned data: every classifier
  with its version and class probabilities (ALeRCE), the newest alert's science-module
  scores (Fink), or the locus's filter tags and classifier properties (ANTARES);
* a "reading" per broker: one coarse class under a stated rule, so the brokers can be put
  next to each other. The reading is CTAS's, never the broker's own word, and it never
  replaces the broker's numbers;
* a consensus panel: which coarse classes the brokers' readings and the TNS label give,
  whether they agree, and nothing averaged.

Events without any broker receipt get no key, so their dossiers are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

CANDIDATE_KEY = "broker_classes"
SCHEMA = "ctas.broker-classes@1"
SOURCES = {
    "alerce-classifiers": "ALeRCE",
    "fink-science-scores": "Fink",
    "antares-ztf-loci": "ANTARES",
}
PARSER_CONTRACTS = {
    "alerce-classifiers": "alerce-ztf-probabilities-json@1.0.0",
    "fink-science-scores": "fink-ztf-science-scores-json-rows@1.0.0",
    "antares-ztf-loci": "antares-locus-listing-json@1.0.0",
}
ATTRIBUTION = {
    "alerce-classifiers": "ALeRCE broker classifiers (Sanchez-Saez et al. 2021; Carrasco-Davis et al. 2021); cite Forster et al. 2021 and acknowledge ALeRCE and ZTF.",
    "fink-science-scores": "Fink broker science modules; cite Moller et al. 2021 and include the current Fink acknowledgment.",
    "antares-ztf-loci": "ANTARES (NSF NOIRLab); cite Matheson et al. 2021 and acknowledge NSF NOIRLab.",
}
PUBLIC_OUTCOMES = {
    "data": "searched-with-data", "no-match": "searched-no-match", "ambiguous": "ambiguous",
    "unavailable": "temporarily-unavailable", "failed": "provider-failure",
}
COARSE_CLASSES = (
    "SN Ia", "SN core-collapse", "SN (type unknown)", "SLSN", "TDE", "kilonova", "AGN / QSO / blazar",
    "variable star", "microlensing", "asteroid", "bogus or artefact", "other",
)
FINK_SCORES = {
    "d:snn_snia_vs_nonia": "SuperNNova SN Ia vs non-Ia",
    "d:snn_sn_vs_all": "SuperNNova SN vs all",
    "d:rf_snia_vs_nonia": "Random forest SN Ia vs non-Ia (early)",
    "d:rf_kn_vs_nonkn": "Kilonova vs non-kilonova",
    "d:mulens": "Microlensing (LIA)",
    "d:slsn_score": "SLSN score",
    "d:anomaly_score": "Anomaly score (lower is more anomalous)",
}
READING_RULES = {
    "alerce-classifiers": ("rank-1 class of the light-curve classifier (lc_classifier) when published, "
                           "otherwise rank-1 class of the newest stamp classifier"),
    "fink-science-scores": ("newest alert: SN Ia if SuperNNova SN-vs-all > 0.5 and SN-Ia-vs-non-Ia > 0.5; SN (type "
                            "unknown) if SN-vs-all > 0.5; kilonova if kilonova score > 0.5; microlensing if "
                            "microlensing score > 0.5; otherwise no reading"),
    "antares-ztf-loci": "the locus's classifier property (superphot_plus_class) when present; filter tags alone give no reading",
}
CONSENSUS_RULE = ("Coarse classes from each broker reading and from the TNS label are compared as labels; "
                  "agreement means every available label is the same coarse class. Nothing is averaged.")
CLAIM_BOUNDARY = ("Broker machine-learning outputs and CTAS's stated reading of them; not spectroscopic "
                  "classifications, and the reading never replaces the broker's own numbers.")

_ALERCE_COARSE = {
    "snia": "SN Ia", "snibc": "SN core-collapse", "snii": "SN core-collapse", "sniin": "SN core-collapse",
    "slsn": "SLSN", "tde": "TDE", "sn": "SN (type unknown)", "kn": "kilonova",
    "qso": "AGN / QSO / blazar", "agn": "AGN / QSO / blazar", "blazar": "AGN / QSO / blazar",
    "cv/nova": "variable star", "yso": "variable star", "lpv": "variable star", "e": "variable star",
    "dsct": "variable star", "rrl": "variable star", "cep": "variable star", "periodic-other": "variable star",
    "vs": "variable star", "asteroid": "asteroid", "bogus": "bogus or artefact", "satellite": "bogus or artefact",
}


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _round(value: Any, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not math.isfinite(number):
        return None
    return float(f"{number:.{digits}g}") if number else 0.0


def coarse_class(label: str | None) -> str | None:
    """Map a broker class or a TNS label onto the shared coarse vocabulary."""

    if not label:
        return None
    text = str(label).strip()
    key = text.lower().replace(" ", "")
    if key in _ALERCE_COARSE:
        return _ALERCE_COARSE[key]
    lowered = text.lower()
    if lowered.startswith("sn ia") or lowered.startswith("snia") or lowered in {"ia", "sn ia-pec"}:
        return "SN Ia"
    if lowered.startswith("slsn"):
        return "SLSN"
    if lowered.startswith("sn ii") or lowered.startswith("sn ib") or lowered.startswith("sn ic") or lowered.startswith("snii") or lowered.startswith("snib"):
        return "SN core-collapse"
    if lowered in {"sn", "supernova"} or lowered.startswith("sn "):
        return "SN (type unknown)"
    if lowered.startswith("tde"):
        return "TDE"
    if "kilonova" in lowered or lowered == "kn":
        return "kilonova"
    if lowered in {"agn", "qso", "blazar"} or lowered.startswith("agn"):
        return "AGN / QSO / blazar"
    if lowered in {"cv", "nova", "varstar", "lbv", "m dwarf", "galactic nuclei"} or "variable" in lowered or lowered.startswith("cv"):
        return "variable star"
    return None


def _decode(body: Any) -> Any:
    if body is None:
        raise ValueError("no response body was kept")
    if isinstance(body, memoryview):
        body = body.tobytes()
    if isinstance(body, str):
        body = body.encode()

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON object key {key}")
            out[key] = value
        return out

    return json.loads(body, parse_constant=lambda _v: None, object_pairs_hook=reject_duplicate_keys), hashlib.sha256(body).hexdigest()


def _alerce(document: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(document, list):
        raise ValueError("ALeRCE probabilities are not a list")
    classifiers: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in document:
        if not isinstance(row, dict):
            raise ValueError("ALeRCE probability row is not an object")
        name, version, label = row.get("classifier_name"), row.get("classifier_version"), row.get("class_name")
        probability, rank = _round(row.get("probability"), 6), row.get("ranking")
        if not all(isinstance(value, str) and value for value in (name, version, label)) or probability is None \
                or not 0 <= probability <= 1 or isinstance(rank, bool) or not isinstance(rank, int):
            raise ValueError("ALeRCE probability row violates the contract")
        classifiers.setdefault((name, version), []).append({"class": label, "probability": probability, "rank": rank})
    published = [{"classifier": name, "version": version,
                  "classes": sorted(rows, key=lambda item: (item["rank"], item["class"]))}
                 for (name, version), rows in sorted(classifiers.items())]
    reading: dict[str, Any] = {"rule": READING_RULES["alerce-classifiers"], "coarseClass": None}
    chosen = next((item for item in published if item["classifier"] == "lc_classifier"), None)
    if chosen is None:
        stamps = [item for item in published if item["classifier"].startswith("stamp_classifier")]
        chosen = sorted(stamps, key=lambda item: (item["classifier"] != "stamp_classifier", item["version"]))[-1] if stamps else None
    if chosen and chosen["classes"]:
        top = chosen["classes"][0]
        reading.update({"classifier": chosen["classifier"], "version": chosen["version"], "brokerClass": top["class"],
                        "probability": top["probability"], "coarseClass": coarse_class(top["class"])})
    return {"classifiers": published}, reading


def _fink(document: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(document, list) or not document:
        raise ValueError("Fink scores are not a non-empty row list")
    for row in document:
        if not isinstance(row, dict) or not isinstance(row.get("i:jd"), (int, float)):
            raise ValueError("Fink score row violates the contract")
    newest = max(document, key=lambda row: (float(row["i:jd"]), str(row.get("i:candid"))))
    scores = {column: _round(newest.get(column), 6) for column in FINK_SCORES}
    published = {
        "alerts": len(document),
        "newestAlert": {"jd": _round(newest.get("i:jd"), 13), "candid": str(newest.get("i:candid")) if newest.get("i:candid") is not None else None},
        "scores": [{"column": column, "label": FINK_SCORES[column], "value": scores[column]} for column in FINK_SCORES],
        "cdsCrossmatch": newest.get("d:cdsxmatch") if isinstance(newest.get("d:cdsxmatch"), str) else None,
    }
    reading: dict[str, Any] = {"rule": READING_RULES["fink-science-scores"], "coarseClass": None}
    sn_all = scores["d:snn_sn_vs_all"] or 0
    snia = scores["d:snn_snia_vs_nonia"] or 0
    if sn_all > 0.5 and snia > 0.5:
        reading["coarseClass"] = "SN Ia"
    elif sn_all > 0.5:
        reading["coarseClass"] = "SN (type unknown)"
    elif (scores["d:rf_kn_vs_nonkn"] or 0) > 0.5:
        reading["coarseClass"] = "kilonova"
    elif (scores["d:mulens"] or 0) > 0.5:
        reading["coarseClass"] = "microlensing"
    return published, reading


def _antares(document: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("data"), list) or len(document["data"]) != 1:
        raise ValueError("ANTARES listing does not hold exactly one locus")
    locus = document["data"][0]
    attributes = locus.get("attributes") or {}
    properties = attributes.get("properties") or {}
    tags = attributes.get("tags") or []
    if not isinstance(locus.get("id"), str) or not isinstance(properties, dict) or not isinstance(tags, list):
        raise ValueError("ANTARES locus violates the contract")
    classifier = {key: (_round(value, 6) if isinstance(value, (int, float)) and not isinstance(value, bool) else value)
                  for key, value in sorted(properties.items())
                  if not key.startswith("feature_") and any(token in key.lower() for token in ("class", "prob", "superphot", "anomaly"))
                  and isinstance(value, (str, int, float)) and not isinstance(value, bool)}
    published = {"locusId": locus["id"], "tags": sorted(str(tag) for tag in tags), "classifierProperties": classifier,
                 "alerts": properties.get("num_alerts") if isinstance(properties.get("num_alerts"), int) else None,
                 "catalogs": sorted(str(item) for item in (attributes.get("catalogs") or []) if isinstance(item, str))}
    reading: dict[str, Any] = {"rule": READING_RULES["antares-ztf-loci"], "coarseClass": None}
    label = properties.get("superphot_plus_class")
    if isinstance(label, str) and label:
        reading.update({"brokerClass": label, "coarseClass": coarse_class(label),
                        "probability": _round(properties.get("superphot_plus_prob"), 6)})
    return published, reading


PARSERS = {"alerce-classifiers": _alerce, "fink-science-scores": _fink, "antares-ztf-loci": _antares}


def broker_rows_sql() -> str:
    sources = ", ".join(f"'{source}'" for source in SOURCES)
    return f"""
        SELECT a.event_id, a.id AS attempt_id, a.source_id, a.query_kind, a.terminal_state, a.detail,
               a.error_code, a.checked_at, a.response_checksum,
               e.response_body, e.response_checksum AS evidence_checksum, e.parser_contract,
               e.request_url, e.received_at
        FROM source_query_attempts a
        LEFT JOIN source_query_response_evidence e ON e.attempt_id = a.id AND e.sequence = 0
        WHERE a.event_id IN ({{ids}})
          AND a.source_id IN ({sources})
        ORDER BY a.event_id, a.checked_at, a.id
    """


def build_broker_classes(candidate: dict[str, Any], rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    rows = list(rows)
    if not rows:
        return None
    brokers: list[dict[str, Any]] = []
    for source, name in SOURCES.items():
        source_rows = [row for row in rows if row.get("source_id") == source]
        if not source_rows:
            continue
        latest = source_rows[-1]
        entry: dict[str, Any] = {
            "broker": name, "source": source,
            "currentOutcome": PUBLIC_OUTCOMES.get(str(latest.get("terminal_state")), str(latest.get("terminal_state"))),
            "currentReceipt": {"attemptId": str(latest["attempt_id"]), "checkedAt": str(latest.get("checked_at")),
                               "detail": str(latest.get("detail") or "")[:400]},
            "attribution": ATTRIBUTION[source],
        }
        data_rows = [row for row in source_rows if row.get("terminal_state") == "data" and row.get("response_body") is not None]
        if data_rows:
            data = data_rows[-1]
            try:
                document, digest = _decode(data["response_body"])
                if data.get("evidence_checksum") and digest != data["evidence_checksum"]:
                    raise ValueError("the kept response does not match its checksum")
                if data.get("parser_contract") != PARSER_CONTRACTS[source]:
                    raise ValueError("the kept response names another parser contract")
                published, reading = PARSERS[source](document)
            except ValueError as exc:
                entry["dataReceipt"] = {"attemptId": str(data["attempt_id"]), "unreadable": str(exc)[:200]}
            else:
                entry["dataReceipt"] = {"attemptId": str(data["attempt_id"]), "checkedAt": str(data.get("checked_at")),
                                        "responseChecksumSha256": digest, "parserContract": PARSER_CONTRACTS[source],
                                        "requestUrl": str(data.get("request_url") or "")}
                entry["output"] = published
                entry["reading"] = reading
        brokers.append(entry)
    tns_label = candidate.get("classification")
    labels = {entry["broker"]: (entry.get("reading") or {}).get("coarseClass") for entry in brokers}
    voters = {broker: label for broker, label in labels.items() if label}
    tns_coarse = coarse_class(tns_label) if tns_label and str(tns_label).lower() not in {"unclassified", "none", "unknown"} else None
    distinct = set(voters.values()) | ({tns_coarse} if tns_coarse else set())
    if not voters:
        state = "no-broker-reading"
    elif len(distinct) == 1:
        state = "agree" if len(voters) + (1 if tns_coarse else 0) > 1 else "single-reading"
    else:
        state = "disagree"
    consensus = {
        "rule": CONSENSUS_RULE,
        "readings": [{"broker": broker, "coarseClass": label} for broker, label in sorted(labels.items())],
        "tnsLabel": tns_label if tns_label else None,
        "tnsCoarseClass": tns_coarse,
        "state": state,
        "coarseClassesSeen": sorted(distinct),
    }
    block = {"schema": SCHEMA, "brokers": brokers, "consensus": consensus, "claimBoundary": CLAIM_BOUNDARY,
             "vocabulary": list(COARSE_CLASSES)}
    block["blockChecksumSha256"] = _sha256({key: block[key] for key in ("brokers", "consensus")})
    return block


def broker_problems(candidates: Iterable[dict[str, Any]]) -> list[str]:
    """Certificate gate: every broker entry traces to a receipt; every published output
    traces to a kept response checksum and contract; readings use the vocabulary."""

    problems: list[str] = []
    for candidate in candidates:
        block = candidate.get(CANDIDATE_KEY)
        if block is None:
            continue
        name = candidate.get("name") or candidate.get("event_id")
        if not isinstance(block, dict) or block.get("schema") != SCHEMA or not isinstance(block.get("brokers"), list):
            problems.append(f"{name}: {CANDIDATE_KEY} is not a {SCHEMA} block")
            continue
        for entry in block["brokers"]:
            label = f"{name} {entry.get('broker')}"
            if entry.get("source") not in SOURCES or not (entry.get("currentReceipt") or {}).get("attemptId"):
                problems.append(f"{label}: no receipt")
            if "output" in entry:
                receipt = entry.get("dataReceipt") or {}
                digest = receipt.get("responseChecksumSha256")
                if not (isinstance(digest, str) and len(digest) == 64) or receipt.get("parserContract") != PARSER_CONTRACTS.get(entry.get("source")):
                    problems.append(f"{label}: output without a response checksum and contract")
            reading = entry.get("reading")
            if reading is not None:
                if not reading.get("rule"):
                    problems.append(f"{label}: reading without a rule")
                if reading.get("coarseClass") is not None and reading["coarseClass"] not in COARSE_CLASSES:
                    problems.append(f"{label}: reading outside the vocabulary")
        expected = _sha256({key: block.get(key) for key in ("brokers", "consensus")})
        if block.get("blockChecksumSha256") != expected:
            problems.append(f"{name}: block checksum does not match")
    return problems
