"""Property-centric source comparison for CTAS dossiers (Framework v2, Track 2b).

For each public event the exporter collects every retained source assertion of five
properties and publishes them side by side under the top-level candidate key ``comparison``
(``ctas.comparison@1``), using the record shape of the WorldsIndex conflict sets (Track 2a):

* ``position``        TNS hourly-feed positions (each distinct value; earlier values are
                      superseded revisions), the TNS public-detail position, TNS AstroNote
                      reported positions and GCN notice localizations with their error radii;
* ``classification``  CTAS classification assertions (TNS, TNS public reports, GCN) with their
                      superseded and retracted history, and AstroNote reported object types;
* ``redshift``        TNS-reported transient redshifts, TNS public-detail transient and host
                      redshifts, AstroNote reported redshifts and NED host redshifts;
* ``host``            proposed host galaxies (TNS public detail, AstroNotes, NED);
* ``photometry``      only the exactly compatible photometry groups (same epoch, band, system,
                      method, calibration and direct/difference state, two or more providers)
                      that the AstroEvidence screen marks statistically inconsistent.

Every row keeps the source's original value; nothing is averaged, voted or overwritten. Each
pair of rows is assessed with a stated rule and labelled in the Track 2a vocabulary; a
property is "in conflict" when a pair is statistically inconsistent, a status conflict or a
revision/retraction. Three headline values (position, classification, redshift) get a
selection record with a written rule, labelled "selection, not a measurement", beside the
source rows; the displayed CTAS summary value is compared with it, never replaced.

Every row names a receipt: the retained alert envelope (stream intake) or the provider
response checksum (queried sources). The certificate gate ``comparison-receipt-trace``
checks that every row of every property in conflict traces to one. An event with no
property carrying two or more rows has no ``comparison`` key, so its dossier bytes do not
change. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable

SCHEMA = "ctas.comparison@1"
CANDIDATE_KEY = "comparison"
POLICY_VERSION = "ctas-comparison-policy.v1"
SELECTION_RULE_ID = "ctas-headline"
SELECTION_RULE_VERSION = "v1"
SELECTION_LABEL = "selection, not a measurement"

RELATION_VOCABULARY = (
    "statistically-inconsistent", "method-incompatible", "definition-incompatible", "epoch-dependent",
    "identity-ambiguity", "status-conflict", "revision-or-retraction", "not-yet-assessed",
)
CONFLICT_RELATIONS = frozenset({"statistically-inconsistent", "status-conflict", "revision-or-retraction"})
# Most severe first: the set-level relation is the most severe pair relation (Track 2a rule).
RELATION_SEVERITY = (
    "statistically-inconsistent", "status-conflict", "revision-or-retraction", "identity-ambiguity",
    "definition-incompatible", "method-incompatible", "epoch-dependent", "not-yet-assessed",
)
VERDICTS = ("consistent", "inconsistent", "not-comparable")

# The comparison rules, stated where they are applied (and published in every block).
SIGMA_THRESHOLD = 3.0
POSITION_TOLERANCE_ARCSEC = 2.0
REDSHIFT_TOLERANCE = 0.005
PHOTOMETRY_GROUP_LIMIT = 10
ROW_LIMIT_PER_PROPERTY = 60
RULES = {
    "sigma": (
        "Two values that both state an uncertainty are inconsistent when they differ by more than "
        f"{SIGMA_THRESHOLD:g} combined standard deviations (uncertainties added in quadrature; a stated "
        "position error radius is treated as one standard deviation)."
    ),
    "position": (
        "Two positions with no stated uncertainty are inconsistent when they are more than "
        f"{POSITION_TOLERANCE_ARCSEC:g} arcsec apart; when one states an error radius, the separation is "
        f"compared with {SIGMA_THRESHOLD:g} times that radius."
    ),
    "redshift": (
        "Two redshifts with no stated uncertainty are inconsistent when they differ by more than "
        f"{REDSHIFT_TOLERANCE:g} (about 1,500 km/s); transient and host redshifts are different "
        "definitions and are listed, not compared; host redshifts of differently named hosts are an "
        "identity ambiguity."
    ),
    "classification": (
        "Spectroscopic classes and alert or messenger labels are different definitions and are not "
        "compared; two active classes differ when neither label refines the other (for example 'SN Ia' "
        "and 'SN Ia-91T' agree); an earlier value from the same provider is a revision."
    ),
    "host": (
        "Two proposed hosts agree when their names match after case and spacing are ignored or one name "
        "is among the other's NED cross-identifications; otherwise they are an identity ambiguity. "
        "Association probabilities are not computed."
    ),
    "photometry": (
        "Only exactly compatible photometry (same epoch, band, system, method, calibration and "
        "direct/difference state, two or more providers) is compared. The limiting magnitude or flux "
        "that a provider reports alongside a detection is that epoch's detection threshold, not a "
        "non-detection, and is not compared with the detection; a detection brighter than another "
        "provider's non-detection limit at the same epoch is a status conflict. A group is listed only when it is still "
        f"inconsistent under these rules; at most {PHOTOMETRY_GROUP_LIMIT} groups per event, most "
        "significant first."
    ),
    "revision": (
        "A retracted row, or a superseded row of the same provider whose value differs beyond the "
        "property's rule, is a revision or retraction; a revision within the rule is consistent; the "
        "earlier value stays visible."
    ),
}
SELECTION_RULES = {
    "position": (
        "The most recent TNS hourly-feed position (the IAU Transient Name Server is the naming "
        "authority); otherwise the GCN localization with the smallest stated error radius; otherwise none."
    ),
    "classification": (
        "The most recent active TNS spectroscopic classification; otherwise none (alert and messenger "
        "labels are not promoted to a class)."
    ),
    "redshift": (
        "The most recent TNS-reported transient redshift; otherwise a NED host redshift with a stated "
        "uncertainty when exactly one host is proposed; otherwise none."
    ),
}
CLAIM_BOUNDARY = (
    "Source assertions shown side by side with stated comparison rules. A consistent label is not "
    "proof that the values are scientifically equivalent, an inconsistent label is not a judgement "
    "of which source is right, and a selection is a display rule, not a measurement or a review."
)
PROPERTIES = {
    "position": {"label": "Position", "unit": "deg"},
    "classification": {"label": "Classification", "unit": None},
    "redshift": {"label": "Redshift", "unit": "1"},
    "host": {"label": "Proposed host galaxy", "unit": None},
    "photometry": {"label": "Photometry (compatible epochs)", "unit": "mag"},
}
PROPERTY_ORDER = tuple(PROPERTIES)
RECEIPT_KINDS = ("alert-envelope", "provider-response", "query-receipt")
STREAM_PROVIDERS = ("tns", "tns-public-reports", "tns-astronotes", "gcn")

# The exporter's queries (scripts/export_ctas_snapshot.py runs them with rows_by_event).
ENVELOPE_SQL = """
        SELECT ae.event_id, ae.id AS envelope_id, ae.provider, ae.topic, ae.checksum,
               ae.received_at, ae.source_publication_time, ae.event_time,
               json_extract(ae.raw_payload, '$.name') AS name,
               json_extract(ae.raw_payload, '$.ra') AS ra,
               json_extract(ae.raw_payload, '$.dec') AS dec,
               json_extract(ae.raw_payload, '$.coordinate_error_arcsec') AS coordinate_error_arcsec,
               json_extract(ae.raw_payload, '$.coordinate_frame') AS coordinate_frame,
               json_extract(ae.raw_payload, '$.coordinate_epoch') AS coordinate_epoch,
               json_extract(ae.raw_payload, '$.transient_redshift') AS transient_redshift,
               json_extract(ae.raw_payload, '$.host_redshift') AS host_redshift,
               json_extract(ae.raw_payload, '$.host_name') AS host_name,
               json_extract(ae.raw_payload, '$.source_url') AS source_url,
               json_extract(ae.raw_payload, '$.discovery_survey') AS discovery_survey,
               json_extract(ae.raw_payload, '$.tns_public_detail.ra_deg') AS detail_ra,
               json_extract(ae.raw_payload, '$.tns_public_detail.dec_deg') AS detail_dec,
               json_extract(ae.raw_payload, '$.tns_public_detail.host_name') AS detail_host_name,
               json_extract(ae.raw_payload, '$.tns_public_detail.host_redshift') AS detail_host_redshift,
               json_extract(ae.raw_payload, '$.tns_public_detail.transient_redshift') AS detail_transient_redshift,
               json_extract(ae.raw_payload, '$.tns_public_detail.discovery_time') AS detail_discovery_time,
               json_extract(ae.raw_payload, '$.source_payload.tns_public_objects_hourly_row.Discovery_ADS_bibcode') AS discovery_bibcode,
               json_extract(ae.raw_payload, '$.source_payload.tns_public_objects_hourly_row.Class_ADS_bibcodes') AS class_bibcodes,
               json_extract(ae.raw_payload, '$.source_payload.tns_public_objects_hourly_row.lastmodified') AS tns_lastmodified,
               json_extract(ae.raw_payload, '$.source_payload.astronote_id') AS astronote_id,
               json_extract(ae.raw_payload, '$.source_payload.related_object.reported_assertions') AS reported_assertions
        FROM alert_envelopes ae
        WHERE ae.event_id IN ({ids})
          AND ae.provider IN ('tns', 'tns-public-reports', 'tns-astronotes', 'gcn')
          AND ae.rights_decision = 'public'
          AND json_valid(ae.raw_payload)
        ORDER BY ae.event_id, ae.received_at, ae.id
"""
CLASSIFICATION_SQL = """
        SELECT ca.event_id, ca.id AS assertion_id, ca.provider, ca.classification, ca.subtype,
               ca.probability, ca.method, ca.model_name, ca.model_version, ca.asserted_at,
               ca.citation_url, ca.superseded, ca.retracted,
               ae.id AS envelope_id, ae.checksum AS envelope_checksum, ae.topic AS envelope_topic,
               ae.received_at AS envelope_received_at,
               CASE WHEN json_valid(ae.raw_payload) THEN json_extract(ae.raw_payload,
                    '$.source_payload.tns_public_objects_hourly_row.Class_ADS_bibcodes') END AS class_bibcodes
        FROM classification_assertions ca
        LEFT JOIN alert_envelopes ae ON ae.id = ca.envelope_id
        WHERE ca.event_id IN ({ids})
          AND ca.data_rights IN ('public', 'open')
        ORDER BY ca.event_id, ca.asserted_at, ca.id
"""
HOST_SQL = """
        SELECT h.event_id, h.id AS assertion_id, h.provider, h.queried_name, h.canonical_name,
               h.cross_identifications, h.transient_offset_arcsec, h.redshift, h.redshift_error,
               h.redshift_reference, h.response_checksum, h.source_url, h.queried_at,
               ae.id AS envelope_id, ae.checksum AS envelope_checksum
        FROM host_context_assertions h
        LEFT JOIN alert_envelopes ae ON ae.id = h.envelope_id
        WHERE h.event_id IN ({ids})
          AND h.data_rights IN ('public', 'open')
        ORDER BY h.event_id, h.queried_at, h.id
"""
ENVELOPE_CHECKSUM_SQL = "SELECT id, checksum, topic, received_at FROM alert_envelopes WHERE id IN ({ids})"

SHA256 = re.compile(r"[0-9a-f]{64}")
BIBCODE = re.compile(r"^\d{4}[A-Za-z&.]{1,5}[A-Za-z0-9.&]{9}[A-Z.]$")
SUBTYPE_SEPARATORS = re.compile(r"[\s\-_/]+")


# ----------------------------------------------------------------------------- helpers
def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def stable_id(*parts: Any) -> str:
    material = "\x1f".join("" if part is None else str(part) for part in parts)
    return "cmp:" + hashlib.sha256(material.encode()).hexdigest()[:24]


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def text(value: Any) -> str | None:
    rendered = " ".join(str(value).split()) if value is not None else ""
    return rendered or None


def iso(value: Any) -> str | None:
    """A UTC ISO-8601 string from the stored timestamp forms, or None."""
    raw = text(value)
    if not raw:
        return None
    raw = raw.replace("T", " ").replace("Z", "").split("+")[0].strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}(?::\d{2})?)(\.\d+)?)?$", raw)
    if not match:
        return None
    clock = match.group(2) or "00:00:00"
    if len(clock) == 5:
        clock += ":00"
    return f"{match.group(1)}T{clock}Z"


def sexagesimal(value: Any, *, hours: bool) -> float | None:
    """Degrees from a sexagesimal ('12:21:50.479' / '+04:28:54.14') or decimal string."""
    plain = number(value)
    if plain is not None:
        return plain
    raw = text(value)
    if not raw:
        return None
    match = re.fullmatch(r"([+-]?)(\d{1,3})[:\s](\d{1,2})[:\s](\d{1,2}(?:\.\d+)?)", raw)
    if not match:
        return None
    sign = -1.0 if match.group(1) == "-" else 1.0
    degrees = float(match.group(2)) + float(match.group(3)) / 60 + float(match.group(4)) / 3600
    degrees *= 15.0 if hours else 1.0
    return sign * degrees


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation (Vincenty formula, stable at all separations)."""
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    delta = a2 - a1
    numerator = math.hypot(math.cos(d2) * math.sin(delta),
                           math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(delta))
    denominator = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(delta)
    return math.degrees(math.atan2(numerator, denominator)) * 3600.0


def bibcodes(value: Any) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,;\s]+", str(value or "")):
        part = part.strip()
        if part and BIBCODE.match(part) and part not in out:
            out.append(part)
    return out


def ads_url(bibcode: str | None) -> str | None:
    return f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract" if bibcode else None


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(str(value)) if value not in (None, "") else fallback
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def class_key(value: Any) -> str:
    return SUBTYPE_SEPARATORS.sub(" ", str(value or "").casefold()).strip()


def name_key(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").casefold())


SUBTYPE_CONTINUATION = re.compile(r"^(?: .+|[abclnpx][a-z0-9]{0,3}(?: .*)?)$")


def class_refines(left: str, right: str) -> bool:
    """True when one class label refines the other ('SN Ia' / 'SN Ia-91T', 'SN II' / 'SN IIP').

    A continuation of a type letter must be a subtype letter (a, b, c, l, n, p, x), so 'SN I'
    and 'SN II' do not refine each other."""
    a, b = class_key(left), class_key(right)
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return bool(short) and long_.startswith(short) and bool(SUBTYPE_CONTINUATION.match(long_[len(short):]))


def _receipt_envelope(envelope_id: Any, checksum: Any, topic: Any = None, received_at: Any = None) -> dict[str, Any] | None:
    digest = str(checksum or "").strip().lower()
    if not envelope_id or not SHA256.fullmatch(digest):
        return None
    return {"kind": "alert-envelope", "envelopeId": str(envelope_id), "checksumSha256": digest,
            "topic": text(topic), "receivedAt": iso(received_at)}


def _receipt_response(record_id: Any, checksum: Any, queried_at: Any = None) -> dict[str, Any] | None:
    digest = str(checksum or "").strip().lower()
    if not record_id or not SHA256.fullmatch(digest):
        return None
    return {"kind": "provider-response", "envelopeId": None, "checksumSha256": digest,
            "topic": None, "receivedAt": iso(queried_at)}


def _row(*, source_id: str, source_record_id: str, value: str, normalized: Any, unit: str | None,
         err: float | None, definition: str, time: str | None, time_kind: str, frame: str | None,
         method: str | None, instrument: str | None, reference: dict[str, Any], state: str,
         receipt: dict[str, Any] | None, probability: float | None = None, assertion_id: str | None = None,
         seen: int = 1, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "id": "",
        "sourceId": source_id,
        "sourceRecordId": source_record_id,
        "assertionId": assertion_id,
        "value": value,
        "normalized": normalized,
        "unit": unit,
        "errPlus": err,
        "errMinus": err,
        "limitType": "NONE",
        "probability": probability,
        "definitionCode": definition,
        "epoch": {"time": time, "kind": time_kind, "frame": frame},
        "method": method,
        "instrument": instrument,
        "reference": reference,
        "state": state,
        "seenInEnvelopes": seen,
        "receipt": receipt,
        "compat": None,
    }
    if extra:
        row.update(extra)
    row["id"] = stable_id("row", source_id, source_record_id, definition, json.dumps(normalized, sort_keys=True), state)
    return row


def _reference(label: str, url: Any = None, bibcode_list: Iterable[str] = ()) -> dict[str, Any]:
    codes = [code for code in bibcode_list if code]
    single = codes[0] if len(codes) == 1 else None
    return {"label": label, "url": text(url) or ads_url(single), "bibcode": single, "bibcodes": codes}


# ----------------------------------------------------------------------------- rows
def _collapse(rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """One row per (source, definition, value): the latest receipt wins; repeats are counted."""
    out: dict[Any, dict[str, Any]] = {}
    for row in rows:
        k = (row["sourceId"], row["definitionCode"], key(row))
        if k in out:
            kept = out[k]
            kept["seenInEnvelopes"] += row["seenInEnvelopes"]
            if (row["epoch"]["time"] or "") >= (kept["epoch"]["time"] or ""):
                row["seenInEnvelopes"] = kept["seenInEnvelopes"]
                out[k] = row
        else:
            out[k] = row
    return list(out.values())


def _mark_revisions(rows: list[dict[str, Any]], providers: Iterable[str]) -> None:
    """Within a provider's stream rows, the value in the latest envelope is active; earlier distinct values are superseded."""
    for provider in providers:
        own = [row for row in rows if row["sourceId"] == provider and row["state"] == "active"]
        if len(own) < 2:
            continue
        latest = max(own, key=lambda row: (row["epoch"]["time"] or "", row["sourceRecordId"]))
        for row in own:
            if row is not latest:
                row["state"] = "superseded"
                row["id"] = stable_id("row", row["sourceId"], row["sourceRecordId"], row["definitionCode"],
                                      json.dumps(row["normalized"], sort_keys=True), row["state"])


def position_rows(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for env in envelopes:
        provider = str(env.get("provider") or "")
        receipt = _receipt_envelope(env.get("envelope_id"), env.get("checksum"), env.get("topic"), env.get("received_at"))
        frame = " ".join(filter(None, (text(env.get("coordinate_frame")), text(env.get("coordinate_epoch"))))) or None
        if provider == "tns":
            ra, dec = number(env.get("ra")), number(env.get("dec"))
            if ra is None or dec is None:
                continue
            codes = bibcodes(env.get("discovery_bibcode"))
            rows.append(_row(
                source_id="tns", source_record_id=f"tns-hourly:{text(env.get('name')) or env.get('envelope_id')}",
                value=f"{env.get('ra')}, {env.get('dec')}", normalized=[round(ra, 7), round(dec, 7)], unit="deg",
                err=number(env.get("coordinate_error_arcsec")), definition="reported-position",
                time=iso(env.get("tns_lastmodified")) or iso(env.get("received_at")), time_kind="provider-modified",
                frame=frame, method="TNS public objects (hourly)", instrument=text(env.get("discovery_survey")),
                reference=_reference("TNS object page; discovery report", env.get("source_url") if not codes else None, codes),
                state="active", receipt=receipt,
            ))
        elif provider == "tns-public-reports":
            ra, dec = number(env.get("detail_ra")), number(env.get("detail_dec"))
            if ra is None or dec is None:
                continue
            rows.append(_row(
                source_id="tns-public-reports", source_record_id=f"tns-detail:{text(env.get('name')) or env.get('envelope_id')}",
                value=f"{env.get('detail_ra')}, {env.get('detail_dec')}", normalized=[round(ra, 7), round(dec, 7)], unit="deg",
                err=None, definition="reported-position", time=iso(env.get("received_at")), time_kind="retrieved",
                frame=frame, method="TNS 2.0 public object detail", instrument=None,
                reference=_reference("TNS public object detail", env.get("source_url")), state="active", receipt=receipt,
            ))
        elif provider == "tns-astronotes":
            ra, dec = sexagesimal(env.get("ra"), hours=True), sexagesimal(env.get("dec"), hours=False)
            if ra is None or dec is None:
                continue
            note = text(env.get("astronote_id"))
            rows.append(_row(
                source_id="tns-astronotes", source_record_id=f"astronote:{note}:{text(env.get('name'))}",
                value=f"{env.get('ra')}, {env.get('dec')}", normalized=[round(ra, 7), round(dec, 7)], unit="deg",
                err=None, definition="reported-position", time=iso(env.get("source_publication_time")), time_kind="published",
                frame=frame, method="TNS AstroNote reported position", instrument=None,
                reference=_reference(f"TNS AstroNote {note}" if note else "TNS AstroNote",
                                     f"https://www.wis-tns.org/astronotes/astronote/{note}" if note else None),
                state="active", receipt=receipt, extra={"astronoteId": note},
            ))
        elif provider == "gcn":
            ra, dec = number(env.get("ra")), number(env.get("dec"))
            if ra is None or dec is None:
                continue
            err = number(env.get("coordinate_error_arcsec"))
            rows.append(_row(
                source_id="gcn", source_record_id=f"gcn:{text(env.get('topic'))}:{text(env.get('name'))}",
                value=f"{env.get('ra')}, {env.get('dec')}" + (f" ± {err:g} arcsec" if err is not None else ""),
                normalized=[round(ra, 7), round(dec, 7)], unit="deg", err=err,
                definition="localization",
                time=iso(env.get("event_time")) or iso(env.get("received_at")), time_kind="trigger",
                frame=frame, method=f"GCN notice ({text(env.get('topic'))})", instrument=text(env.get("discovery_survey")),
                reference=_reference("GCN notice", "https://gcn.nasa.gov/notices"), state="active", receipt=receipt,
            ))
    rows = _collapse(rows, lambda row: tuple(row["normalized"]) + (row["errPlus"],))
    _mark_revisions([row for row in rows if row["sourceId"] in ("tns", "tns-public-reports")], ("tns", "tns-public-reports"))
    return rows


def _note_assertions(env: dict[str, Any]) -> dict[str, Any]:
    return _json(env.get("reported_assertions"), {})


def redshift_rows(envelopes: list[dict[str, Any]], hosts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for env in envelopes:
        provider = str(env.get("provider") or "")
        receipt = _receipt_envelope(env.get("envelope_id"), env.get("checksum"), env.get("topic"), env.get("received_at"))
        name = text(env.get("name")) or str(env.get("envelope_id"))
        candidates: list[tuple[str, Any, str | None, str, str | None, str]] = []
        if provider == "tns":
            candidates.append(("transient", env.get("transient_redshift"), None, "tns-hourly", iso(env.get("tns_lastmodified")) or iso(env.get("received_at")), "provider-modified"))
        elif provider == "tns-public-reports":
            candidates.append(("transient", env.get("detail_transient_redshift"), None, "tns-detail", iso(env.get("received_at")), "retrieved"))
            candidates.append(("host", env.get("detail_host_redshift"), text(env.get("detail_host_name")), "tns-detail", iso(env.get("received_at")), "retrieved"))
        elif provider == "tns-astronotes":
            notes = _note_assertions(env)
            note = text(env.get("astronote_id"))
            candidates.append(("transient", notes.get("reported_redshift"), None, f"astronote:{note}", iso(env.get("source_publication_time")), "published"))
            candidates.append(("host", notes.get("host_redshift"), text(notes.get("host_name")), f"astronote:{note}", iso(env.get("source_publication_time")), "published"))
        for definition, raw, host, record, when, when_kind in candidates:
            value = number(raw)
            if value is None:
                continue
            reference = (_reference("TNS object page", env.get("source_url")) if provider != "tns-astronotes" else
                         _reference(f"TNS AstroNote {text(env.get('astronote_id'))}",
                                    f"https://www.wis-tns.org/astronotes/astronote/{text(env.get('astronote_id'))}"))
            rows.append(_row(
                source_id=provider, source_record_id=f"{record}:{name}", value=str(raw), normalized=value, unit="1",
                err=None, definition=f"{definition}-redshift", time=when, time_kind=when_kind, frame=None,
                method={"tns": "TNS public objects (hourly)", "tns-public-reports": "TNS 2.0 public object detail",
                        "tns-astronotes": "TNS AstroNote reported value"}[provider],
                instrument=None, reference=reference, state="active", receipt=receipt,
                extra={"host": host} if definition == "host" else None,
            ))
    for host in hosts:
        value = number(host.get("redshift"))
        if value is None:
            continue
        receipt = (_receipt_envelope(host.get("envelope_id"), host.get("envelope_checksum"))
                   or _receipt_response(host.get("assertion_id"), host.get("response_checksum"), host.get("queried_at")))
        err = number(host.get("redshift_error"))
        rows.append(_row(
            source_id=str(host.get("provider") or "ned"), source_record_id=f"ned:{text(host.get('canonical_name'))}",
            value=str(host.get("redshift")), normalized=value, unit="1", err=abs(err) if err is not None else None,
            definition="host-redshift", time=iso(host.get("queried_at")), time_kind="retrieved", frame=None,
            method="NED host-context query", instrument=None,
            reference=_reference("NED" + (f" ({text(host.get('redshift_reference'))})" if text(host.get("redshift_reference")) else ""),
                                 host.get("source_url"), bibcodes(host.get("redshift_reference"))),
            state="active", receipt=receipt, assertion_id=str(host.get("assertion_id") or "") or None,
            extra={"host": text(host.get("canonical_name"))},
        ))
    rows = _collapse(rows, lambda row: (row["normalized"], row.get("host")))
    _mark_revisions([row for row in rows if row["definitionCode"] == "transient-redshift"], ("tns", "tns-public-reports"))
    return rows


def classification_rows(assertions: list[dict[str, Any]], envelopes: list[dict[str, Any]],
                        classify: Callable[[Any], str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in assertions:
        provider = str(item.get("provider") or "").lower()
        label = " ".join(filter(None, (text(item.get("classification")), text(item.get("subtype"))))) or None
        if not label:
            continue
        kind = classify(label)
        definition = "spectroscopic-class" if kind == "transient.classification" else "alert-label"
        state = "retracted" if item.get("retracted") else "superseded" if item.get("superseded") else "active"
        receipt = _receipt_envelope(item.get("envelope_id"), item.get("envelope_checksum"), item.get("envelope_topic"), item.get("envelope_received_at"))
        codes = bibcodes(item.get("class_bibcodes")) if provider == "tns" else []
        method = " · ".join(filter(None, (text(item.get("method")) if text(item.get("method")) != "unknown" else None,
                                          text(item.get("model_name")), text(item.get("model_version"))))) or None
        rows.append(_row(
            source_id=provider, source_record_id=str(item.get("envelope_id") or item.get("assertion_id")),
            value=label, normalized=class_key(label), unit=None, err=None, definition=definition,
            time=iso(item.get("asserted_at")), time_kind="asserted", frame=None, method=method, instrument=None,
            reference=_reference("TNS classification report" if codes else provider.upper() if provider == "gcn" else "TNS object page",
                                 item.get("citation_url") if not codes else None, codes),
            state=state, receipt=receipt, probability=number(item.get("probability")),
            assertion_id=str(item.get("assertion_id") or "") or None,
        ))
    for env in envelopes:
        if str(env.get("provider")) != "tns-astronotes":
            continue
        label = text(_note_assertions(env).get("reported_object_type"))
        if not label:
            continue
        note = text(env.get("astronote_id"))
        kind = classify(label)
        rows.append(_row(
            source_id="tns-astronotes", source_record_id=f"astronote:{note}:{text(env.get('name'))}",
            value=label, normalized=class_key(label), unit=None, err=None,
            definition="spectroscopic-class" if kind == "transient.classification" else "alert-label",
            time=iso(env.get("source_publication_time")), time_kind="published", frame=None,
            method="TNS AstroNote reported object type", instrument=None,
            reference=_reference(f"TNS AstroNote {note}" if note else "TNS AstroNote",
                                 f"https://www.wis-tns.org/astronotes/astronote/{note}" if note else None),
            state="active", receipt=_receipt_envelope(env.get("envelope_id"), env.get("checksum"), env.get("topic"), env.get("received_at")),
            extra={"astronoteId": note},
        ))
    return _collapse(rows, lambda row: (row["normalized"], row["state"], row.get("astronoteId")))


def host_rows(envelopes: list[dict[str, Any]], hosts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for env in envelopes:
        provider = str(env.get("provider") or "")
        receipt = _receipt_envelope(env.get("envelope_id"), env.get("checksum"), env.get("topic"), env.get("received_at"))
        if provider == "tns-public-reports" and text(env.get("detail_host_name")):
            name = text(env.get("detail_host_name"))
            rows.append(_row(
                source_id=provider, source_record_id=f"tns-detail:{text(env.get('name'))}", value=name, normalized=name_key(name),
                unit=None, err=None, definition="proposed-host", time=iso(env.get("received_at")), time_kind="retrieved",
                frame=None, method="TNS 2.0 public object detail", instrument=None,
                reference=_reference("TNS public object detail", env.get("source_url")), state="active", receipt=receipt,
                extra={"crossIdentifications": [], "offsetArcsec": None, "associationProbability": None},
            ))
        elif provider == "tns-astronotes":
            name = text(_note_assertions(env).get("host_name"))
            if not name:
                continue
            note = text(env.get("astronote_id"))
            rows.append(_row(
                source_id=provider, source_record_id=f"astronote:{note}:{text(env.get('name'))}", value=name, normalized=name_key(name),
                unit=None, err=None, definition="proposed-host", time=iso(env.get("source_publication_time")), time_kind="published",
                frame=None, method="TNS AstroNote reported host", instrument=None,
                reference=_reference(f"TNS AstroNote {note}" if note else "TNS AstroNote",
                                     f"https://www.wis-tns.org/astronotes/astronote/{note}" if note else None),
                state="active", receipt=receipt,
                extra={"crossIdentifications": [], "offsetArcsec": None, "associationProbability": None, "astronoteId": note},
            ))
    for host in hosts:
        name = text(host.get("canonical_name")) or text(host.get("queried_name"))
        if not name:
            continue
        cross = [text(value) for value in _json(host.get("cross_identifications"), []) if text(value)]
        receipt = (_receipt_envelope(host.get("envelope_id"), host.get("envelope_checksum"))
                   or _receipt_response(host.get("assertion_id"), host.get("response_checksum"), host.get("queried_at")))
        rows.append(_row(
            source_id=str(host.get("provider") or "ned"), source_record_id=f"ned:{name}", value=name, normalized=name_key(name),
            unit=None, err=None, definition="proposed-host", time=iso(host.get("queried_at")), time_kind="retrieved",
            frame=None, method="NED host-context query" + (f" (queried as {text(host.get('queried_name'))})" if text(host.get("queried_name")) and text(host.get("queried_name")) != name else ""),
            instrument=None, reference=_reference("NED", host.get("source_url")), state="active", receipt=receipt,
            assertion_id=str(host.get("assertion_id") or "") or None,
            extra={"crossIdentifications": cross[:12], "offsetArcsec": number(host.get("transient_offset_arcsec")),
                   "associationProbability": None},
        ))
    return _collapse(rows, lambda row: row["normalized"])


def photometry_groups(candidate: dict[str, Any], measurements: list[dict[str, Any]],
                      envelope_receipts: dict[str, dict[str, Any]]) -> list[tuple[float | None, list[dict[str, Any]]]]:
    """The AstroEvidence screen's photometry conflict groups as comparison rows.

    Since 2026-10-27 the screen itself sets aside the limit a provider reports with its own
    detection and labels a detection that violates another provider's non-detection limit
    STATUS_CONFLICT; releases made before then label every group STATISTICALLY_INCONSISTENT.
    Both are read, and the detection thresholds are still set aside below for older releases.
    """
    relations = {"STATISTICALLY_INCONSISTENT", "STATUS_CONFLICT"}
    conflicts = [row for row in ((candidate.get("astro_evidence") or {}).get("conflictSets") or [])
                 if str(row.get("propertyCode", "")).startswith("phot.") and relations & set(row.get("relations") or [])]
    if not conflicts:
        return []
    by_id = {row["measurementId"]: row for row in measurements}
    observation_by_measurement: dict[str, dict[str, Any]] = {}
    threshold_of_detection: set[str] = set()
    for obs in (candidate.get("follow_up") or {}).get("observations") or []:
        assertion = str(obs.get("assertion_id") or "")
        detected = obs.get("detection") in (1, True, "1", "true")
        for kind in ("magnitude", "flux", "limiting-magnitude", "limiting-flux"):
            mid = "ae:measurement:" + hashlib.sha256("\x1f".join((kind, assertion)).encode()).hexdigest()[:24]
            observation_by_measurement[mid] = obs
            if detected and kind.startswith("limiting-"):
                # The limit reported with a detection is the epoch's detection threshold: the
                # AstroEvidence screen compares it as a non-detection, which it is not.
                threshold_of_detection.add(mid)
    groups: list[tuple[float | None, list[dict[str, Any]]]] = []
    for conflict in sorted(conflicts, key=lambda row: (-(row.get("significanceSigma") or 0), row.get("conflictSetId") or "")):
        rows = []
        for mid in conflict.get("measurementIds") or []:
            item, obs = by_id.get(mid), observation_by_measurement.get(mid)
            if not item or not obs or mid in threshold_of_detection:
                continue
            value = number(item.get("normalizedValue"))
            limit = item.get("valueKind") if item.get("valueKind") in ("UPPER_LIMIT", "LOWER_LIMIT") else "NONE"
            row = _row(
                source_id=str(item.get("sourceContractId")), source_record_id=str(item.get("sourceRecordId")),
                value=f"{item.get('originalValue')} {item.get('originalUnit') or ''}".strip(), normalized=value,
                unit=item.get("normalizedUnit"), err=number(item.get("uncertaintyPositive")),
                definition=f"{item.get('propertyCode')}|{item.get('bandpass')}|{item.get('referenceFrame') or ''}",
                time=iso(obs.get("observed_at")), time_kind="observed",
                frame=item.get("referenceFrame"), method=item.get("method"),
                instrument=" · ".join(filter(None, (text(item.get("facility")), text(item.get("instrument"))))) or None,
                reference=_reference(str(item.get("sourceContractId")), (item.get("citation") or {}).get("url")),
                state="active", receipt=envelope_receipts.get(str(obs.get("source_record_id") or "")),
                assertion_id=mid,
                extra={"band": item.get("bandpass"), "groupId": conflict.get("conflictSetId"),
                       "mjd": (item.get("time") or {}).get("normalizedMjd")},
            )
            row["limitType"] = limit
            rows.append(row)
        if len(rows) >= 2 and len({row["sourceId"] for row in rows}) >= 2:
            groups.append((number(conflict.get("significanceSigma")), rows))
    return groups


# ----------------------------------------------------------------------------- comparison
def _pair(property_code: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Relation, verdict, sigma and reason for one pair of rows (the stated rules)."""
    def result(relation: str | None, verdict: str, sigma: float | None, reason: str) -> dict[str, Any]:
        return {"relation": relation, "verdict": verdict, "sigma": None if sigma is None else round(sigma, 4), "reason": reason}

    states = {left["state"], right["state"]}
    if states != {"active"}:
        if "retracted" in states and left["sourceId"] == right["sourceId"]:
            return result("revision-or-retraction", "not-comparable", None,
                          "A retracted value from the same provider; it stays visible.")
        if left["sourceId"] == right["sourceId"] and "active" in states and left["definitionCode"] == right["definitionCode"]:
            # A revision is assessed with the property's own rule, then labelled.
            within = _pair(property_code, {**left, "state": "active"}, {**right, "state": "active"})
            if within["verdict"] == "consistent":
                return result(None, "consistent", within["sigma"], "A later revision from the same provider within the stated rule: " + within["reason"])
            if within["verdict"] == "not-comparable":
                return result(within["relation"], "not-comparable", None,
                              "An earlier value from the same provider; " + within["reason"][0].lower() + within["reason"][1:])
            return result("revision-or-retraction", "not-comparable", within["sigma"],
                          "The provider revised this value beyond the stated rule (" + within["reason"].rstrip(".") + "); the earlier value stays visible.")
        # Not a gap in the assessment: an earlier value is compared only with its own provider's later one.
        return result(None, "not-comparable", None, "Superseded and retracted rows of different providers are not compared.")
    if left["definitionCode"] != right["definitionCode"]:
        if property_code == "position" and {left["definitionCode"], right["definitionCode"]} == {"localization", "reported-position"}:
            region, point = (left, right) if left["definitionCode"] == "localization" else (right, left)
            sep = separation_arcsec(*region["normalized"], *point["normalized"])
            radius = region["errPlus"] or 0.0
            sigma = sep / radius if radius > 0 else None
            if sigma is not None and sigma > SIGMA_THRESHOLD:
                return result("statistically-inconsistent", "inconsistent", sigma,
                              f"The reported position lies {sep:.1f} arcsec from the localization centre, more than {SIGMA_THRESHOLD:g} times its {radius:g} arcsec error radius.")
            return result(None, "consistent", sigma, f"The reported position lies {sep:.1f} arcsec from the localization centre (error radius {radius:g} arcsec).")
        return result("definition-incompatible", "not-comparable", None,
                      f"Different definitions ({left['definitionCode']} vs {right['definitionCode']}) are listed, not compared.")
    if property_code == "position":
        sep = separation_arcsec(*left["normalized"], *right["normalized"])
        errors = [value for value in (left["errPlus"], right["errPlus"]) if value]
        if left["definitionCode"] == "localization" and len(errors) < 2:
            return result("not-yet-assessed", "not-comparable", None,
                          f"Localizations {sep:.0f} arcsec apart; without both error radii they are listed, not compared.")
        if errors:
            combined = math.sqrt(sum(value * value for value in errors))
            sigma = sep / combined
            verdict = "inconsistent" if sigma > SIGMA_THRESHOLD else "consistent"
            return result("statistically-inconsistent" if verdict == "inconsistent" else None, verdict, sigma,
                          f"{sep:.2f} arcsec apart with a combined error of {combined:.2f} arcsec.")
        if sep > POSITION_TOLERANCE_ARCSEC:
            return result("statistically-inconsistent", "inconsistent", None,
                          f"{sep:.2f} arcsec apart with no stated uncertainty (tolerance {POSITION_TOLERANCE_ARCSEC:g} arcsec).")
        return result(None, "consistent", None, f"{sep:.2f} arcsec apart (within the {POSITION_TOLERANCE_ARCSEC:g} arcsec tolerance).")
    if property_code == "redshift":
        if left["definitionCode"] == "host-redshift" and left.get("host") and right.get("host") and name_key(left["host"]) != name_key(right["host"]):
            return result("identity-ambiguity", "not-comparable", None,
                          f"Host redshifts of differently named hosts ({left['host']} vs {right['host']}).")
        delta = abs(left["normalized"] - right["normalized"])
        if left["errPlus"] and right["errPlus"]:
            combined = math.hypot(left["errPlus"], right["errPlus"])
            sigma = delta / combined
            verdict = "inconsistent" if sigma > SIGMA_THRESHOLD else "consistent"
            return result("statistically-inconsistent" if verdict == "inconsistent" else None, verdict, sigma,
                          f"Differ by {delta:.5f} with a combined uncertainty of {combined:.5f}.")
        if delta > REDSHIFT_TOLERANCE:
            return result("statistically-inconsistent", "inconsistent", None,
                          f"Differ by {delta:.5f} with no stated uncertainty on both (tolerance {REDSHIFT_TOLERANCE:g}).")
        return result(None, "consistent", None, f"Differ by {delta:.5f} (within the {REDSHIFT_TOLERANCE:g} tolerance).")
    if property_code == "classification":
        if class_refines(left["value"], right["value"]):
            same = class_key(left["value"]) == class_key(right["value"])
            return result(None, "consistent", None, "Same label." if same else "One label refines the other.")
        if left["definitionCode"] == "alert-label":
            return result("method-incompatible", "not-comparable", None,
                          "Different alert or messenger labels describe different notices or methods; they are listed, not compared.")
        return result("status-conflict", "inconsistent", None, f"Active classes differ ('{left['value']}' vs '{right['value']}').")
    if property_code == "host":
        def known(row: dict[str, Any]) -> set[str]:
            return {row["normalized"]} | {name_key(value) for value in row.get("crossIdentifications") or []}
        if known(left) & known(right):
            return result(None, "consistent", None, "The same host (name or NED cross-identification).")
        return result("identity-ambiguity", "not-comparable", None,
                      f"Differently named hosts ({left['value']} vs {right['value']}); they may be aliases or different galaxies.")
    if property_code == "photometry":
        lv, rv = left["normalized"], right["normalized"]
        if lv is None or rv is None:
            return result("not-yet-assessed", "not-comparable", None, "A value is missing.")
        if left["limitType"] == right["limitType"] == "NONE":
            if left["errPlus"] and right["errPlus"]:
                combined = math.hypot(left["errPlus"], right["errPlus"])
                sigma = abs(lv - rv) / combined
                verdict = "inconsistent" if sigma > SIGMA_THRESHOLD else "consistent"
                return result("statistically-inconsistent" if verdict == "inconsistent" else None, verdict, sigma,
                              f"Differ by {abs(lv - rv):.3f} with a combined uncertainty of {combined:.3f}.")
            return result("not-yet-assessed", "not-comparable", None, "An uncertainty is missing.")
        measurement = left if left["limitType"] == "NONE" else right if right["limitType"] == "NONE" else None
        limit = right if measurement is left else left if measurement is right else None
        if measurement is None or limit is None:
            return result("not-yet-assessed", "not-comparable", None, "Two limits are not compared.")
        mv, lim = measurement["normalized"], limit["normalized"]
        broken = (limit["limitType"] == "UPPER_LIMIT" and mv > lim) or (limit["limitType"] == "LOWER_LIMIT" and mv < lim)
        if broken:
            # Same epoch and band means the same exposure: one record says detected, the other not.
            return result("status-conflict", "inconsistent", None,
                          f"The same epoch and band is reported as a detection ({mv:.3f}) by {measurement['sourceId']} "
                          f"and as a non-detection fainter than {lim:.3f} by {limit['sourceId']}.")
        return result(None, "consistent", None, "The detection respects the limit.")
    return result("not-yet-assessed", "not-comparable", None, "No rule for this property.")


def assess(property_code: str, rows: list[dict[str, Any]], set_key: str) -> dict[str, Any]:
    """Pairwise assessment of one property's rows: per-row compatibility and the set summary."""
    by_relation: dict[str, int] = {}
    by_verdict = {verdict: 0 for verdict in VERDICTS}
    notable: list[list[Any]] = []
    relations: set[str] = set()
    sigmas: list[float] = []
    for i, left in enumerate(rows):
        for j in range(i + 1, len(rows)):
            pair = _pair(property_code, left, rows[j])
            by_verdict[pair["verdict"]] += 1
            key = pair["relation"] or "none"
            by_relation[key] = by_relation.get(key, 0) + 1
            if pair["relation"]:
                relations.add(pair["relation"])
            if pair["sigma"] is not None:
                sigmas.append(pair["sigma"])
            if pair["relation"] in CONFLICT_RELATIONS:
                notable.append([i, j, pair["sigma"], pair["relation"], pair["reason"]])
    # Per-row compatibility against the reference row (the first active row, else the first row).
    reference = next((index for index, row in enumerate(rows) if row["state"] == "active"), 0)
    for index, row in enumerate(rows):
        counts = {"consistent": 0, "inconsistent": 0, "not-comparable": 0}
        for other_index, other in enumerate(rows):
            if other_index != index:
                counts[_pair(property_code, row, other)["verdict"]] += 1
        if index == reference:
            row["compat"] = {"against": None, "relation": None, "verdict": "reference", "sigma": None,
                             "reason": "The reference row for this table (the first active row).", **counts}
        else:
            pair = _pair(property_code, rows[reference], row)
            row["compat"] = {"against": reference, **pair, **counts}
    relation = next((name for name in RELATION_SEVERITY if name in relations), None)
    conflict = any(name in CONFLICT_RELATIONS for name in relations)
    notable.sort(key=lambda item: (RELATION_SEVERITY.index(item[3]), -(item[2] or 0)))
    explanation = (
        "The sources disagree under the stated rule: " + notable[0][4] if conflict and notable else
        "Every assessed pair is consistent under the stated rules." if by_verdict["consistent"] and not relations else
        "Rows are listed; " + (", ".join(sorted(relations)) if relations else "no pair could be compared") + "."
    )
    return {
        "id": stable_id("set", set_key, property_code, *(row["id"] for row in rows)),
        "relationLabel": relation,
        "conflict": conflict,
        "rowCount": len(rows),
        "sourceIds": sorted({row["sourceId"] for row in rows}),
        "definitionCodes": sorted({row["definitionCode"] for row in rows}),
        "sigmaThreshold": SIGMA_THRESHOLD,
        "maxPairwiseSigma": max(sigmas) if sigmas else None,
        "pairCounts": {"total": sum(by_verdict.values()), "byRelation": dict(sorted(by_relation.items())), "byVerdict": by_verdict},
        "notablePairs": [item[:4] for item in notable[:10]],
        "explanation": explanation,
        "comparisonPolicyVersion": POLICY_VERSION,
    }


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"active": 0, "superseded": 1, "retracted": 2}
    return sorted(rows, key=lambda row: (order.get(row["state"], 3), row["definitionCode"], row["sourceId"],
                                         row["epoch"]["time"] or "", row["id"]))


def _selection(property_code: str, rows: list[dict[str, Any]], candidate: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    active = [(index, row) for index, row in enumerate(rows) if row["state"] == "active"]
    chosen: tuple[int, dict[str, Any]] | None = None
    basis = "not-available"
    if property_code == "position":
        tns = [item for item in active if item[1]["sourceId"] == "tns"]
        if tns:
            chosen, basis = max(tns, key=lambda item: item[1]["epoch"]["time"] or ""), "naming-authority"
        else:
            gcn = [item for item in active if item[1]["sourceId"] == "gcn" and item[1]["errPlus"]]
            if gcn:
                chosen, basis = min(gcn, key=lambda item: item[1]["errPlus"]), "smallest-localization"
        display = (number(candidate.get("ra_deg")), number(candidate.get("dec_deg")))
        matches = (None if chosen is None or None in display else
                   separation_arcsec(*chosen[1]["normalized"], *display) < 0.05)
    elif property_code == "classification":
        tns = [item for item in active if item[1]["sourceId"] == "tns" and item[1]["definitionCode"] == "spectroscopic-class"]
        if tns:
            chosen, basis = max(tns, key=lambda item: item[1]["epoch"]["time"] or ""), "naming-authority"
        shown = text(candidate.get("classification"))
        matches = None if chosen is None or not shown else class_key(shown) == chosen[1]["normalized"]
    elif property_code == "redshift":
        tns = [item for item in active if item[1]["sourceId"] == "tns" and item[1]["definitionCode"] == "transient-redshift"]
        if tns:
            chosen, basis = max(tns, key=lambda item: item[1]["epoch"]["time"] or ""), "naming-authority"
        else:
            ned = [item for item in active if item[1]["definitionCode"] == "host-redshift" and item[1]["sourceId"] == "ned" and item[1]["errPlus"]]
            if len({name_key(item[1].get("host")) for item in ned}) == 1 and ned:
                chosen, basis = max(ned, key=lambda item: item[1]["epoch"]["time"] or ""), "host-redshift-with-uncertainty"
        shown = number(candidate.get("redshift"))
        matches = None if chosen is None or shown is None else abs(shown - chosen[1]["normalized"]) < 1e-6
    else:
        return None
    if chosen is None:
        return None
    index, row = chosen
    return {
        "id": stable_id("selection", event_id, property_code, row["id"]),
        "ruleId": SELECTION_RULE_ID,
        "ruleVersion": SELECTION_RULE_VERSION,
        "rule": SELECTION_RULES[property_code],
        "selector": "exporter",
        "reviewState": "unreviewed",
        "propertyCode": property_code,
        "definitionCode": row["definitionCode"],
        "basis": basis,
        "label": SELECTION_LABEL,
        "value": row["normalized"],
        "displayValue": row["value"],
        "unit": row["unit"],
        "errPlus": row["errPlus"],
        "errMinus": row["errMinus"],
        "chosenRow": index,
        "chosenSourceRecordIds": [row["sourceRecordId"]],
        "rejectedRowCount": sum(1 for other_index, other in enumerate(rows)
                                if other_index != index and other["definitionCode"] == row["definitionCode"]),
        "matchesDisplayedValue": matches,
        "reason": f"Chosen by {SELECTION_RULE_ID}.{SELECTION_RULE_VERSION} ({basis}); the source rows are unchanged.",
    }


def _query_receipts(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per source contract, the latest persisted query receipt that returned data (with a response checksum)."""
    out: dict[str, dict[str, Any]] = {}
    for receipt in (candidate.get("astro_evidence") or {}).get("persistedQueryReceipts") or []:
        digest = str(receipt.get("responseChecksumSha256") or "").lower()
        if receipt.get("outcome") not in ("DATA_RETURNED", "PARTIAL_RESULT") or not SHA256.fullmatch(digest):
            continue
        source = str(receipt.get("sourceContractId") or "")
        when = str(receipt.get("completedAt") or receipt.get("startedAt") or "")
        if source and (source not in out or when >= (out[source]["receivedAt"] or "")):
            out[source] = {"kind": "query-receipt", "envelopeId": None, "receiptId": receipt.get("receiptId"),
                           "checksumSha256": digest, "topic": None, "receivedAt": when or None}
    return out


def _with_query_receipts(rows: list[dict[str, Any]], receipts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        if row["receipt"] is None and row["sourceId"] in receipts:
            row["receipt"] = dict(receipts[row["sourceId"]])
    return rows


def build_comparison(candidate: dict[str, Any], *, envelopes: list[dict[str, Any]], classifications: list[dict[str, Any]],
                     hosts: list[dict[str, Any]], measurements: list[dict[str, Any]] | None = None,
                     envelope_receipts: dict[str, dict[str, Any]] | None = None,
                     classify: Callable[[Any], str] | None = None) -> dict[str, Any] | None:
    """The comparison block of one candidate, or None when no property has two or more rows."""
    event_id = str(candidate.get("event_id") or "")
    if classify is None:
        try:
            from ctas_astro_evidence import classification_property as classify  # type: ignore[no-redef]
        except ModuleNotFoundError:  # imported as scripts.ctas_comparison in tests
            from scripts.ctas_astro_evidence import classification_property as classify  # type: ignore[no-redef]
    built: dict[str, list[dict[str, Any]]] = {
        "position": position_rows(envelopes),
        "classification": classification_rows(classifications, envelopes, classify),
        "redshift": redshift_rows(envelopes, hosts),
        "host": host_rows(envelopes, hosts),
    }
    query_receipts = _query_receipts(candidate)
    properties: list[dict[str, Any]] = []
    for code in ("position", "classification", "redshift", "host"):
        rows = _sort_rows(_with_query_receipts(built[code], query_receipts))
        truncated = max(0, len(rows) - ROW_LIMIT_PER_PROPERTY)
        rows = rows[:ROW_LIMIT_PER_PROPERTY]
        if len(rows) < 2:
            continue
        block = {"code": code, "label": PROPERTIES[code]["label"], "unit": PROPERTIES[code]["unit"],
                 "rule": RULES[code], "rows": rows, "rowsOmitted": truncated}
        block["set"] = assess(code, rows, event_id)
        selection = _selection(code, rows, candidate, event_id)
        block["selections"] = [selection] if selection else []
        properties.append(block)
    listed = 0
    for sigma, rows in photometry_groups(candidate, measurements or [], envelope_receipts or {}):
        if listed >= PHOTOMETRY_GROUP_LIMIT:
            break
        rows = _sort_rows(_with_query_receipts(rows, query_receipts))
        entry = assess("photometry", rows, rows[0].get("groupId") or event_id)
        if not entry["conflict"]:
            continue  # consistent once detection thresholds are set aside: not a conflict to list
        block = {"code": "photometry", "label": PROPERTIES["photometry"]["label"], "unit": rows[0]["unit"],
                 "rule": RULES["photometry"], "rows": rows, "rowsOmitted": 0,
                 "group": {"band": rows[0].get("band"), "epoch": rows[0]["epoch"]["time"], "screenSigma": sigma}}
        block["set"] = entry
        block["selections"] = []
        properties.append(block)
        listed += 1
    if not properties:
        return None
    conflicts = [block for block in properties if block["set"]["conflict"]]
    return {
        "schema": SCHEMA,
        "policyVersion": POLICY_VERSION,
        "sigmaThreshold": SIGMA_THRESHOLD,
        "rules": dict(RULES),
        "claimBoundary": CLAIM_BOUNDARY,
        "summary": {
            "properties": len(properties),
            "rows": sum(len(block["rows"]) for block in properties),
            "propertiesInConflict": len(conflicts),
            "conflictCodes": sorted({block["code"] for block in conflicts}),
            "selections": sum(len(block["selections"]) for block in properties),
            "selectionsDifferingFromDisplay": sum(1 for block in properties for selection in block["selections"]
                                                  if selection["matchesDisplayedValue"] is False),
        },
        "properties": properties,
    }


def envelope_ids_for_photometry(candidate: dict[str, Any]) -> list[str]:
    """Envelope IDs of the observations in the candidate's inconsistent photometry groups."""
    sets = [row for row in ((candidate.get("astro_evidence") or {}).get("conflictSets") or [])
            if str(row.get("propertyCode", "")).startswith("phot.")]
    if not sets:
        return []
    return sorted({str(obs.get("source_record_id")) for obs in (candidate.get("follow_up") or {}).get("observations") or []
                   if obs.get("source_record_id")})


# ----------------------------------------------------------------------------- the gate
def comparison_problems(candidates: Iterable[dict[str, Any]]) -> list[str]:
    """Certificate gate comparison-receipt-trace: structure, vocabulary and receipt tracing."""
    problems: list[str] = []
    for candidate in candidates:
        block = candidate.get(CANDIDATE_KEY)
        if block is None:
            continue
        name = str(candidate.get("name") or candidate.get("event_id"))
        if not isinstance(block, dict) or block.get("schema") != SCHEMA or block.get("policyVersion") != POLICY_VERSION:
            problems.append(f"{name}: comparison block has the wrong schema or policy version")
            continue
        properties = block.get("properties") or []
        if not properties:
            problems.append(f"{name}: comparison block without properties")
        for prop in properties:
            code = prop.get("code")
            rows = prop.get("rows") or []
            entry = prop.get("set") or {}
            if code not in PROPERTIES:
                problems.append(f"{name}: unknown comparison property {code}")
            if len(rows) < 2:
                problems.append(f"{name}: {code} lists fewer than two rows")
            if entry.get("relationLabel") not in (None, *RELATION_VOCABULARY):
                problems.append(f"{name}: {code} relation {entry.get('relationLabel')} is outside the vocabulary")
            if entry.get("rowCount") != len(rows):
                problems.append(f"{name}: {code} row count does not match its rows")
            if any(key in prop for key in ("mean", "average", "weightedMean")):
                problems.append(f"{name}: {code} carries an averaged value")
            for row in rows:
                compat = row.get("compat") or {}
                if compat.get("relation") not in (None, *RELATION_VOCABULARY):
                    problems.append(f"{name}: {code} row relation outside the vocabulary")
                if entry.get("conflict"):
                    receipt = row.get("receipt") or {}
                    if (receipt.get("kind") not in RECEIPT_KINDS or not SHA256.fullmatch(str(receipt.get("checksumSha256") or ""))
                            or not row.get("sourceRecordId")):
                        problems.append(f"{name}: {code} row {row.get('sourceId')}:{row.get('sourceRecordId')} does not trace to a receipt")
            for selection in prop.get("selections") or []:
                index = selection.get("chosenRow")
                if not isinstance(index, int) or not 0 <= index < len(rows):
                    problems.append(f"{name}: {code} selection points outside its rows")
                elif selection.get("label") != SELECTION_LABEL or rows[index]["sourceRecordId"] not in (selection.get("chosenSourceRecordIds") or []):
                    problems.append(f"{name}: {code} selection is not labelled or does not name its source row")
        summary = block.get("summary") or {}
        if summary.get("propertiesInConflict") != sum(1 for prop in properties if (prop.get("set") or {}).get("conflict")):
            problems.append(f"{name}: comparison summary does not count its conflicts")
    return problems
