#!/usr/bin/env python3
"""Deterministic CTAS -> AstroEvidence 0.1.0 compatibility projection.

The operational database remains source-native.  This module only projects
rights-cleared public rows into the framework schema and a small amount of
explicit UI metadata.  Missing legacy receipt fields stay null; they are never
reconstructed from guesses.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ASTRO_EVIDENCE_SCHEMA_NAME = "astro-evidence-core"
ASTRO_EVIDENCE_SCHEMA_VERSION = "0.1.0"
PROJECTION_VERSION = "ctas-static-astro-evidence@1.0.0"
IDENTITY_POLICY_VERSION = "ctas-provider-scoped-alias@1.0.0"
RECONCILIATION_POLICY_VERSION = "ctas-assertion-first@1.0.0"
APPLICABILITY_RULE_VERSION = "ctas-source-applicability@1.0.0"

OUTCOME_MAP = {
    "data": "DATA_RETURNED",
    "data-returned": "DATA_RETURNED",
    "searched-with-data": "DATA_RETURNED",
    "no-match": "SEARCHED_NO_MATCH",
    "searched-no-match": "SEARCHED_NO_MATCH",
    "partial": "PARTIAL_RESULT",
    "partial-result": "PARTIAL_RESULT",
    "indeterminate": "PARTIAL_RESULT",
    "failed": "QUERY_FAILED",
    "query-failed": "QUERY_FAILED",
    "unavailable": "NOT_QUERIED",
    "blocked": "QUERY_BLOCKED",
    "blocked-rights": "QUERY_BLOCKED",
    "query-blocked": "QUERY_BLOCKED",
    "not-configured": "NOT_CONFIGURED",
    "not-applicable": "NOT_APPLICABLE",
    "ambiguous": "AMBIGUOUS",
    "ambiguous-target": "AMBIGUOUS",
    "stale": "STALE_LAST_GOOD_RETAINED",
    "link-only": "LINK_ONLY_NOT_QUERIED",
    "not-queried": "NOT_QUERIED",
}

EXECUTED_OUTCOMES = {
    "DATA_RETURNED", "SEARCHED_NO_MATCH", "PARTIAL_RESULT", "QUERY_FAILED",
}

SENSITIVE_REQUEST_KEY_TOKENS = frozenset({
    "accesstoken", "apikey", "authentication", "authorization", "clientid",
    "clientsecret", "cookie", "credential", "password", "passwd", "resulturl",
    "secret", "signature", "signedurl", "taskurl", "token",
})
SENSITIVE_LITERAL = re.compile(
    r"(?i)(?:\bbearer\s+\S+|\b(?:access[_-]?token|api[_-]?key|authorization|"
    r"client[_-]?secret|password|passwd|secret|signature|token)\s*[=:]\s*[^\s&,;]+)"
)
REDACTED = "[REDACTED]"

NON_OPTICAL_TOKENS = (
    "neutrino", "gamma", "gravitational", "x-ray", "radio", "messenger",
    "gcn", "lvk", "icecube", "fermi", "swift", "hawc", "snews",
)

OPERATIONAL_CLASSIFICATION_LABELS = {
    "below horizon", "bogus", "distant particles", "high-importance", "retracted",
    "unreliable location",
}
PHYSICAL_TRANSIENT_CLASSIFICATION_LABELS = frozenset({
    "active galactic nucleus", "agn", "cataclysmic variable", "cv", "kilonova",
    "luminous red nova", "nova", "tde", "tidal disruption", "tidal disruption event",
})
SUPERNOVA_CLASSIFICATION = re.compile(
    r"^(?:(?:sn|type)\s+(?:i(?:a|ax|b|bc|bn|c|cn)?|ii(?:b|n|p|l)?)|"
    r"slsn(?:[-\s](?:i|ii))?|sn[-\s]like|supernova)(?:[-\s].*)?$",
    re.IGNORECASE,
)
NON_CLASSIFICATION_MARKER = re.compile(
    r"(?:^|[\s_-])(?:classifier|probability|prob|score|versus|vs)(?:$|[\s_-])",
    re.IGNORECASE,
)


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def stable_id(kind: str, *parts: Any) -> str:
    material = "\x1f".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(material.encode()).hexdigest()[:24]
    return f"ae:{kind}:{digest}"


def valid_sha256(value: Any) -> str | None:
    rendered = str(value or "").strip().lower()
    return rendered if re.fullmatch(r"[0-9a-f]{64}", rendered) else None


def iso_datetime(value: Any, fallback: str | None = None) -> str | None:
    rendered = str(value or fallback or "").strip()
    if not rendered:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", rendered):
        rendered += "T00:00:00"
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError:
        return iso_datetime(fallback) if fallback and str(fallback) != rendered else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    if value in (None, ""):
        return fallback
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _is_sensitive_key(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    return any(token in normalized for token in SENSITIVE_REQUEST_KEY_TOKENS)


def sanitize_public_text(value: Any) -> str | None:
    """Remove credentials from public free text and URLs without guessing secrets."""

    if value is None:
        return None
    rendered = str(value).strip()
    if not rendered:
        return rendered
    if any(marker in rendered for marker in ("/Users/", ".codex")):
        return REDACTED
    try:
        parsed = urlsplit(rendered)
    except ValueError:
        return REDACTED if SENSITIVE_LITERAL.search(rendered) else rendered
    if parsed.scheme and parsed.netloc:
        netloc = parsed.netloc
        if parsed.username is not None or parsed.password is not None:
            hostname = parsed.hostname
            if hostname is None:
                return REDACTED
            host = f"[{hostname}]" if ":" in hostname else hostname
            try:
                port = parsed.port
            except ValueError:
                return REDACTED
            netloc = f"{host}:{port}" if port is not None else host
        query = urlencode([
            (
                key,
                REDACTED if _is_sensitive_key(key) or SENSITIVE_LITERAL.search(item) else item,
            )
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        fragment = REDACTED if SENSITIVE_LITERAL.search(parsed.fragment) else parsed.fragment
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
    return REDACTED if SENSITIVE_LITERAL.search(rendered) else rendered


def sanitized_public_value(value: Any) -> Any:
    """Recursively redact public JSON and record every affected field path."""

    redacted: list[str] = []

    def scrub(item: Any, path: str) -> Any:
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            existing_redactions = item.get("redactedFields")
            for key in sorted(item):
                child_path = f"{path}.{key}" if path else str(key)
                if _is_sensitive_key(key):
                    result[str(key)] = REDACTED
                    redacted.append(child_path)
                    continue
                result[str(key)] = scrub(item[key], child_path)
            if not path and isinstance(existing_redactions, list):
                redacted.extend(str(row) for row in existing_redactions if row)
            return result
        if isinstance(item, list):
            return [scrub(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if isinstance(item, str):
            sanitized = sanitize_public_text(item)
            if sanitized != item:
                redacted.append(path)
            return sanitized
        return item

    public = scrub(value, "")
    if isinstance(public, dict) and redacted:
        public["redactedFields"] = sorted(set(redacted))
    return public


def public_url(value: Any) -> str | None:
    rendered = sanitize_public_text(value)
    if not rendered or rendered == REDACTED:
        return None
    try:
        parsed = urlsplit(rendered)
    except ValueError:
        return None
    return rendered if parsed.scheme.casefold() == "https" and bool(parsed.netloc) else None


def public_artifact_reference(value: Any) -> str | None:
    rendered = str(value or "").strip()
    if re.fullmatch(r"sha256:[a-fA-F0-9]{64}", rendered):
        return rendered.lower()
    digest = valid_sha256(rendered)
    if digest:
        return f"sha256:{digest}"
    return public_url(rendered)


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def quality_flags(value: Any) -> list[str]:
    if isinstance(value, str):
        parsed = json_value(value, [])
        if parsed:
            value = parsed
        elif value.strip():
            value = [value.strip()]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    rendered: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = "; ".join(f"{key}={item[key]}" for key in sorted(item))
        else:
            text = str(item).strip()
        if text and text not in rendered:
            rendered.append(text)
    return rendered


def classification_property(value: Any) -> str:
    normalized = " ".join(str(value or "").casefold().split())
    if normalized in {"", "n/a", "na", "none", "unknown", "unclassified"}:
        return "transient.classification.status"
    if normalized in OPERATIONAL_CLASSIFICATION_LABELS:
        return "alert.operational_label"
    if NON_CLASSIFICATION_MARKER.search(normalized):
        return "alert.event_label"
    base = normalized.removesuffix(" candidate")
    if base in PHYSICAL_TRANSIENT_CLASSIFICATION_LABELS or SUPERNOVA_CLASSIFICATION.fullmatch(normalized):
        return "transient.classification"
    return "alert.event_label"


def sanitized_request(value: Any) -> dict[str, Any] | None:
    """Return a deterministic public request description with credentials removed."""

    parsed = json_value(value, {})
    if not parsed:
        return None
    public = sanitized_public_value(parsed)
    return public if isinstance(public, dict) and public else None


def _access_mode(row: dict[str, Any]) -> str:
    raw = " ".join(str(row.get(key) or "") for key in ("access_mode", "protocol", "query_scope")).lower()
    if "stream" in raw or "live" in raw:
        return "STREAM"
    if "bulk" in raw or "snapshot" in raw or "delta" in raw:
        return "BULK_SNAPSHOT"
    if "manual" in raw:
        return "MANUAL"
    if "link" in raw or "represented" in raw or "broker-mediated" in raw:
        return "LINK_ONLY"
    if "hybrid" in raw:
        return "HYBRID"
    return "PER_TARGET_QUERY"


def _implementation_state(row: dict[str, Any]) -> str:
    raw = str(row.get("implementation_state") or row.get("connector_implementation_state") or "").lower()
    if "represented" in raw:
        return "REPRESENTED_THROUGH_PROVIDER"
    if "credential" in raw:
        return "CREDENTIAL_REQUIRED"
    if "authorization" in raw or "user-author" in raw or "topic-author" in raw:
        return "AUTHORIZATION_REQUIRED"
    if "blocked-policy" in raw:
        return "BLOCKED_POLICY"
    if "not-implemented" in raw or "review-required" in raw:
        return "NOT_IMPLEMENTED"
    if "manual" in raw or "archival" in raw or "link-only" in raw:
        return "LINK_ONLY"
    if raw == "implemented" or raw.startswith("implemented-"):
        return "QUERYABLE"
    return "NOT_IMPLEMENTED"


def _rights_state(row: dict[str, Any]) -> str:
    raw = str(row.get("rights_or_public_access_basis") or "").lower()
    auth = str(row.get("authentication_requirement") or "").lower()
    if "blocked" in raw:
        return "BLOCKED"
    public_basis = any(token in raw for token in ("public", "open"))
    if not public_basis and any(token in raw for token in ("authorized", "user-owned", "private")):
        return "AUTHORIZED_PRIVATE"
    if public_basis and any(token in auth for token in ("token", "key", "oauth", "registered", "account")):
        return "CREDENTIALLED_PUBLIC"
    if public_basis and any(token in raw for token in ("attribution", "acknowledg", "credit", "citation")):
        return "PUBLIC_WITH_ATTRIBUTION"
    if public_basis:
        return "PUBLIC"
    return "UNRESOLVED"


def _applicability(candidate: dict[str, Any], row: dict[str, Any], referenced: set[str]) -> tuple[bool, str]:
    source_id = str(row.get("source_key") or "")
    if source_id in referenced:
        return True, "Retained evidence, an alias, or an append-only receipt references this source."
    if source_id.startswith("authorized-") or source_id == "private-tom-skyportal":
        return False, "Authorized user-supplied sources apply only when an explicit event binding exists."
    family = str(row.get("source_family") or row.get("primary_family") or "")
    event_text = " ".join(str(candidate.get(key) or "") for key in ("event_type", "primary_messenger")).lower()
    if family == "multimessenger-and-high-energy":
        relevant = any(token in event_text for token in NON_OPTICAL_TOKENS)
        return relevant, (
            "The event is represented as a non-optical or multimessenger notice."
            if relevant else "This event is not represented as a high-energy or multimessenger notice."
        )
    has_position = number(candidate.get("ra_deg")) is not None and number(candidate.get("dec_deg")) is not None
    if family in {
        "optical-and-time-domain-surveys", "photometric-follow-up", "archives",
        "host-counterpart-and-catalog-context", "spectroscopy",
    }:
        return has_position, (
            "A valid ICRS position permits the bounded source contract."
            if has_position else "The bounded source contract requires a valid ICRS position."
        )
    if family in {"discovery-and-alert-brokers", "reports-and-literature", "other-declared-sources"}:
        return True, "The retained event identity supports an exact-name or broker-record evaluation."
    return False, "No executable applicability rule currently maps this source family to the event."


def source_contract(row: dict[str, Any], generated_at: str, applicability_rule: str) -> dict[str, Any]:
    source_id = str(row.get("source_key") or "unknown-source")
    documentation = public_url(row.get("documentation_url")) or "https://jackmcguireastro.github.io/ctas.html#active-sources"
    products = [str(value) for value in (row.get("data_types") or row.get("product_contracts") or []) if str(value).strip()]
    limitations_value = row.get("known_limitations")
    limitations = [str(value) for value in limitations_value if str(value).strip()] if isinstance(limitations_value, list) else [
        str(limitations_value or "No additional limitation recorded.")
    ]
    return {
        "sourceContractId": source_id,
        "sourceName": str(row.get("name") or source_id),
        "authority": str(row.get("organization_or_facility") or row.get("name") or source_id),
        "documentationUrl": documentation,
        "scientificRole": str(row.get("source_family") or row.get("primary_family") or "declared public evidence source"),
        "productTypes": products or ["provider-defined public metadata"],
        "accessMode": _access_mode(row),
        "implementationState": _implementation_state(row),
        "rightsState": _rights_state(row),
        "requiredAttribution": str(row.get("rights_or_public_access_basis") or "Provider terms and attribution apply."),
        "queryScope": str(row.get("query_scope") or row.get("access_mode") or "Declared source-contract scope"),
        "applicabilityRule": f"{APPLICABILITY_RULE_VERSION}: {applicability_rule}",
        "freshnessSloSeconds": None,
        "rateLimitPolicy": str(row.get("rate_or_cadence_limit") or "Provider documentation does not state a numeric limit."),
        "authenticationRequirement": str(row.get("authentication_requirement") or "Not documented"),
        "parserVersion": PROJECTION_VERSION,
        "schemaVersion": str(row.get("contract_version") or "1.0.0"),
        "identityPolicyVersion": IDENTITY_POLICY_VERSION,
        "reconciliationPolicyVersion": RECONCILIATION_POLICY_VERSION,
        "knownLimitations": limitations,
        "lastVerifiedAt": iso_datetime(row.get("last_verified"), generated_at) or generated_at,
    }


def canonical_outcome(attempt: dict[str, Any]) -> str:
    raw = str(attempt.get("outcome") or attempt.get("terminal_state") or "").strip().lower().replace("_", "-")
    error = str(attempt.get("error_code") or attempt.get("error_category") or "").strip().lower().replace("_", "-")
    if any(token in error for token in ("partial", "pending", "row-cap-exceeded")):
        return "PARTIAL_RESULT"
    if "ambiguous" in error or "identity-unavailable" in error:
        return "AMBIGUOUS"
    if any(token in error for token in (
        "lacks-retained-", "target-name-unavailable", "target-discovery-time-unavailable",
        "target-identifier-unavailable",
    )):
        return "NOT_QUERIED"
    if any(token in error for token in ("right", "credential", "authorization", "rate", "secure-automation-withheld", "auth-failed")):
        return "QUERY_BLOCKED"
    if raw not in OUTCOME_MAP:
        raise ValueError(f"unknown source-query terminal state: {raw or '<empty>'}")
    return OUTCOME_MAP[raw]


def _query_kind(attempt: dict[str, Any], contract: dict[str, Any]) -> str:
    raw = str(attempt.get("query_kind") or "").lower()
    if "literature" in raw or "bibliographic" in raw:
        return "LITERATURE_QUERY"
    if "product" in raw or "archive" in raw or "spectrum" in raw:
        return "PRODUCT_DISCOVERY"
    if contract["accessMode"] == "STREAM":
        return "STREAM_RECEIPT"
    if contract["accessMode"] == "BULK_SNAPSHOT":
        return "SNAPSHOT_LOOKUP"
    if contract["accessMode"] in {"MANUAL", "LINK_ONLY"}:
        return "LINK_HANDOFF"
    return "PER_TARGET_QUERY"


def _int_or_none(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _record_cap(detail: dict[str, Any]) -> Any:
    caps = detail.get("caps")
    if isinstance(caps, dict):
        return _first_present(caps, "recordCap", "record_cap")
    return None


def _is_finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _is_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int)


def _parsed_datetime(value: Any) -> datetime | None:
    rendered = str(value or "").strip()
    if not rendered:
        return None
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def receipt_completeness(receipt: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """Evaluate receipt metadata without fabricating missing legacy evidence."""

    required: dict[str, Any] = {
        "executionState": detail.get("executionState"),
        "targetIdentity": detail.get("targetIdentity"),
        "startedAt": receipt.get("startedAt"),
        "completedAt": receipt.get("completedAt"),
    }
    outcome = receipt.get("outcome")
    execution_state = detail.get("executionState")
    requires_execution_metadata = execution_state == "EXECUTED" or (
        execution_state is None and outcome in EXECUTED_OUTCOMES
    )
    if execution_state == "NOT_EXECUTED" and outcome in EXECUTED_OUTCOMES:
        required["requestExecutionOutcomeConsistency"] = None
    non_execution_outcomes = {
        "LINK_ONLY_NOT_QUERIED", "NOT_APPLICABLE", "NOT_CONFIGURED", "NOT_QUERIED",
    }
    if execution_state == "EXECUTED" and outcome in non_execution_outcomes:
        required["requestExecutionOutcomeConsistency"] = None
    if requires_execution_metadata:
        parser = receipt.get("parserVersion")
        schema = receipt.get("schemaVersion")
        required.update({
            "providerRelease": detail.get("providerRelease"),
            "normalizedRequest": detail.get("normalizedRequest"),
            "requestFingerprintSha256": valid_sha256(receipt.get("requestFingerprintSha256")),
            "responseStatus": detail.get("responseStatus"),
            "paginationOrRecordCap": (
                detail.get("pagination") if detail.get("pagination") is not None else _record_cap(detail)
            ),
            "recordsSeen": receipt.get("recordsSeen"),
            "recordsRetained": receipt.get("recordsRetained"),
            "recordsRejected": receipt.get("recordsRejected"),
            "parserVersion": None if parser == "legacy-receipt:not-recorded" else parser,
            "schemaVersion": None if schema == "legacy-receipt:not-recorded" else schema,
            "latencyMs": detail.get("latencyMs"),
            "retryCount": detail.get("retryCount"),
            "paginationComplete": receipt.get("paginationComplete"),
        })

    started = _parsed_datetime(receipt.get("startedAt"))
    completed = _parsed_datetime(receipt.get("completedAt"))
    if started is not None and completed is not None and completed < started:
        required["timeOrdering"] = None

    latency = detail.get("latencyMs")
    if latency is not None and (not _is_finite_number(latency) or latency < 0):
        required["latencyFiniteNonNegative"] = None
    retries = detail.get("retryCount")
    if retries is not None and (not _is_integer(retries) or retries < 0):
        required["retryCountNonNegative"] = None

    counts = (
        receipt.get("recordsSeen"), receipt.get("recordsRetained"), receipt.get("recordsRejected"),
    )
    if any(value is not None and (not _is_integer(value) or value < 0) for value in counts):
        required["recordCountsNonNegative"] = None
    if all(_is_integer(value) for value in counts) and counts[1] + counts[2] != counts[0]:
        required["recordCountClosure"] = None

    cap = _record_cap(detail)
    if cap is not None and (not _is_integer(cap) or cap <= 0):
        required["recordCapPositive"] = None
    if _is_integer(cap) and _is_integer(counts[0]) and counts[0] > cap:
        required["recordCapConsistency"] = None
    if receipt.get("paginationComplete") is False and outcome in {"DATA_RETURNED", "SEARCHED_NO_MATCH"}:
        required["paginationOutcomeConsistency"] = None
    if outcome == "DATA_RETURNED" and receipt.get("recordsRetained") == 0:
        required["dataOutcomeRecordConsistency"] = None
    if outcome == "SEARCHED_NO_MATCH" and receipt.get("recordsRetained") not in {None, 0}:
        required["dataOutcomeRecordConsistency"] = None
    if outcome == "STALE_LAST_GOOD_RETAINED" and not receipt.get("staleReceiptId"):
        required["staleReceiptLinkage"] = None
    if requires_execution_metadata and outcome in {
        "DATA_RETURNED", "PARTIAL_RESULT", "SEARCHED_NO_MATCH",
    }:
        required["responseChecksumOrImmutableArtifactReference"] = (
            valid_sha256(receipt.get("responseChecksumSha256"))
            or detail.get("immutableArtifactReference")
        )
    if outcome in {"QUERY_FAILED", "QUERY_BLOCKED", "AMBIGUOUS"}:
        required["errorCategory"] = detail.get("errorCategory")

    missing = sorted(key for key, value in required.items() if value is None)
    return {"complete": not missing, "missingFields": missing}


def sanitized_receipt_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Return the allowlisted, credential-safe public receipt extension."""

    return {
        "receiptId": str(detail.get("receiptId") or ""),
        "sourceContractId": str(detail.get("sourceContractId") or ""),
        "targetIdentity": sanitized_public_value(detail.get("targetIdentity")),
        "providerRelease": sanitize_public_text(detail.get("providerRelease")),
        "normalizedRequest": sanitized_public_value(detail.get("normalizedRequest")),
        "responseStatus": sanitize_public_text(detail.get("responseStatus")),
        "pagination": sanitized_public_value(detail.get("pagination")),
        "caps": sanitized_public_value(detail.get("caps")),
        "immutableArtifactReference": public_artifact_reference(
            detail.get("immutableArtifactReference")
        ),
        "latencyMs": detail.get("latencyMs"),
        "retryCount": detail.get("retryCount"),
        "errorCategory": sanitize_public_text(detail.get("errorCategory")),
        "metadataCompleteness": str(detail.get("metadataCompleteness") or "LEGACY_NULLABLE"),
        "executionState": detail.get("executionState"),
    }


def _attempt_receipt(
    attempt: dict[str, Any], target_id: str, contract: dict[str, Any], generated_at: str,
    current: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_id = contract["sourceContractId"]
    started = iso_datetime(attempt.get("started_at") or attempt.get("request_time") or attempt.get("checked_at") or attempt.get("created_at"), generated_at) or generated_at
    completed = iso_datetime(attempt.get("completed_at") or attempt.get("checked_at"), started)
    normalized_request = sanitized_request(attempt.get("normalized_request"))
    request_checksum = valid_sha256(
        attempt.get("request_fingerprint_sha256") or attempt.get("request_fingerprint")
    )
    if request_checksum is None and normalized_request is not None:
        request_checksum = hashlib.sha256(canonical_json_bytes(normalized_request)).hexdigest()
    receipt_id = str(attempt.get("id") or stable_id("receipt", target_id, source_id, started, attempt.get("query_kind")))
    outcome = canonical_outcome(attempt)
    receipt = {
        "receiptId": receipt_id,
        "targetId": target_id,
        "sourceContractId": source_id,
        "queryKind": _query_kind(attempt, contract),
        "applicabilityState": (
            "NOT_APPLICABLE" if outcome == "NOT_APPLICABLE"
            else "UNRESOLVED" if outcome in {"AMBIGUOUS", "NOT_QUERIED"}
            else "APPLICABLE"
        ),
        "outcome": outcome,
        "scope": sanitize_public_text(attempt.get("scope") or attempt.get("policy_basis") or attempt.get("query_kind") or "Legacy bounded event query; unavailable fields remain null."),
        "startedAt": started,
        "completedAt": completed,
        "requestFingerprintSha256": request_checksum,
        "responseChecksumSha256": valid_sha256(attempt.get("response_checksum") or attempt.get("response_checksum_sha256")),
        "recordsSeen": _int_or_none(_first_present(attempt, "records_seen", "row_count")),
        "recordsRetained": _int_or_none(_first_present(attempt, "records_retained", "retained_count")),
        "recordsRejected": _int_or_none(_first_present(attempt, "records_rejected", "rejected_count")),
        "paginationComplete": (
            bool(attempt.get("pagination_complete"))
            if attempt.get("pagination_complete") is not None else None
        ),
        "parserVersion": str(attempt.get("parser_version") or "legacy-receipt:not-recorded"),
        "schemaVersion": str(attempt.get("provider_schema_version") or attempt.get("response_schema_version") or attempt.get("schema_version") or "legacy-receipt:not-recorded"),
        "isCurrent": bool(current),
        "evidenceUrl": public_url(attempt.get("evidence_url") or attempt.get("immutable_artifact_reference")),
        "errorCode": sanitize_public_text(attempt.get("error_code")),
        "errorDetail": None,
        "nextEligibleAt": iso_datetime(attempt.get("next_eligible_at")),
        "staleReceiptId": str(attempt.get("stale_receipt_id") or "") or None,
    }
    optional_present = any(attempt.get(key) is not None for key in (
        "provider_release", "provider_version", "provider_release_version", "normalized_request", "response_status",
        "pagination", "caps", "records_seen", "records_retained", "records_rejected",
        "record_cap", "parser_version", "latency_ms", "retry_count", "error_category",
    ))
    explicit_execution = attempt.get("request_executed")
    if explicit_execution is None:
        raw_terminal = str(attempt.get("terminal_state") or attempt.get("outcome") or "").strip().lower().replace("_", "-")
        execution_state = "EXECUTED" if raw_terminal in {"data", "data-returned", "searched-with-data", "no-match", "searched-no-match", "partial", "partial-result", "indeterminate", "failed", "query-failed"} else "NOT_EXECUTED"
    else:
        execution_state = "EXECUTED" if bool(explicit_execution) else "NOT_EXECUTED"
    detail = {
        "receiptId": receipt_id,
        "sourceContractId": source_id,
        "targetIdentity": sanitized_public_value(
            json_value(attempt.get("target_identity"), {}) or {"targetId": target_id}
        ),
        "providerRelease": sanitize_public_text(
            attempt.get("provider_release") or attempt.get("provider_version") or attempt.get("provider_release_version")
        ),
        "normalizedRequest": normalized_request,
        "responseStatus": sanitize_public_text(_first_present(attempt, "response_status", "http_status")),
        "pagination": sanitized_public_value(json_value(attempt.get("pagination"), {}) or None),
        "caps": sanitized_public_value(
            json_value(attempt.get("caps"), {})
            or ({"recordCap": _int_or_none(attempt.get("record_cap"))} if attempt.get("record_cap") is not None else None)
        ),
        "immutableArtifactReference": public_artifact_reference(attempt.get("immutable_artifact_reference")),
        "latencyMs": number(attempt.get("latency_ms")),
        "retryCount": _int_or_none(attempt.get("retry_count")),
        "errorCategory": sanitize_public_text(attempt.get("error_category") or attempt.get("error_code")),
        "metadataCompleteness": "RECORDED" if optional_present else "LEGACY_NULLABLE",
        "executionState": execution_state,
    }
    detail = sanitized_receipt_detail(detail)
    detail["completeness"] = receipt_completeness(receipt, detail)
    return receipt, detail


def _time(
    row: dict[str, Any], primary_key: str, *, mjd_key: str = "mjd", jd_key: str = "jd",
) -> dict[str, Any]:
    """Project a source time without inventing its scale or reference position.

    ``observed_at`` is a normalized CTAS database clock, while ``original_time``,
    ``jd`` and ``mjd`` can preserve the provider representation.  Prefer those
    source-native values when available.  JD -> MJD is the only numerical time
    conversion performed here and its offset is definitionally exact.
    """

    original_time = _first_present(row, "original_time", "source_original_time")
    mjd = number(row.get(mjd_key)) if mjd_key else None
    jd = number(row.get(jd_key)) if jd_key else None
    primary = row.get(primary_key)
    explicit_format = _first_present(row, "time_format", "original_time_format")

    if original_time is not None:
        original_value = original_time
        time_format = str(explicit_format) if explicit_format is not None else "SOURCE_NATIVE"
    elif jd is not None:
        original_value = jd
        time_format = "JD"
    elif mjd is not None:
        original_value = mjd
        time_format = "MJD"
    elif primary is not None:
        original_value = primary
        time_format = str(explicit_format) if explicit_format is not None else "ISO 8601"
    else:
        original_value = None
        time_format = str(explicit_format) if explicit_format is not None else None

    normalized_mjd = mjd if mjd is not None else (jd - 2_400_000.5 if jd is not None else None)
    scale = _first_present(row, "time_scale", "timescale", "original_time_scale")
    reference_position = _first_present(
        row, "time_reference_position", "reference_position", "time_refposition",
    )
    uncertainty = number(_first_present(row, "time_uncertainty", "time_uncertainty_seconds"))
    return {
        "originalValue": original_value,
        "format": time_format,
        "scale": str(scale) if scale is not None else None,
        "referencePosition": str(reference_position) if reference_position is not None else None,
        "uncertainty": abs(uncertainty) if uncertainty is not None else None,
        "normalizedMjd": normalized_mjd,
    }


def _time_compatibility_key(row: dict[str, Any], primary_key: str) -> tuple[Any, ...]:
    projected = _time(row, primary_key)
    if projected["normalizedMjd"] is not None:
        return ("MJD", projected["normalizedMjd"])
    return (projected["format"], projected["originalValue"])


def _messenger_revision(row: dict[str, Any]) -> tuple[str, int | None]:
    """Return a provider-scoped revision group and explicit revision number."""

    provider_signal_id = str(row.get("provider_signal_id") or row.get("source_record_id") or "")
    properties = json_value(row.get("properties"), {})
    raw_revision = _first_present(row, "messenger_revision", "revision")
    if raw_revision is None and isinstance(properties, dict):
        raw_revision = properties.get("revision")
    parsed_revision = number(raw_revision)
    revision = int(parsed_revision) if parsed_revision is not None and parsed_revision >= 0 and parsed_revision.is_integer() else None
    match = re.match(r"^(.*?):r(?:ev(?:ision)?)?(\d+)(?::(?:initial|update|retraction))?$", provider_signal_id, re.IGNORECASE)
    if match:
        group = match.group(1)
        if revision is None:
            revision = int(match.group(2))
    else:
        group = provider_signal_id
    return str(row.get("messenger_revision_group_id") or group or row.get("assertion_id") or "messenger"), revision


def _messenger_retracted(row: dict[str, Any]) -> bool:
    if row.get("retracted") is not None:
        return bool(row.get("retracted"))
    if "retract" in str(row.get("role") or "").lower() or "retract" in str(row.get("alert_type") or "").lower():
        return True
    properties = json_value(row.get("properties"), {})
    comments = properties.get("comments", []) if isinstance(properties, dict) else []
    if isinstance(comments, str):
        comments = [comments]
    return any("retract" in str(comment).lower() for comment in comments)


def derive_messenger_revisions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy and annotate messenger notices with explicit revision lineage."""

    annotated: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for source in rows:
        row = dict(source)
        group, revision = _messenger_revision(row)
        row["messenger_revision_group_id"] = group
        if revision is not None:
            row["messenger_revision"] = revision
        row["retracted"] = _messenger_retracted(row)
        groups.setdefault((str(row.get("provider") or "").lower(), group), []).append(row)
        annotated.append(row)

    for group_rows in groups.values():
        ordered = sorted(group_rows, key=lambda row: (
            _messenger_revision(row)[1] if _messenger_revision(row)[1] is not None else -1,
            str(row.get("source_published_at") or row.get("ctas_received_at") or row.get("observed_at") or ""),
            str(row.get("provider_signal_id") or row.get("assertion_id") or ""),
        ))
        for index, row in enumerate(ordered):
            if index and not row.get("supersedes_provider_signal_id"):
                row["supersedes_provider_signal_id"] = str(
                    ordered[index - 1].get("provider_signal_id") or ordered[index - 1].get("assertion_id") or ""
                ) or None
            row["superseded"] = bool(row.get("superseded")) or index < len(ordered) - 1
    return annotated


def _citation(row: dict[str, Any], source_id: str) -> dict[str, Any]:
    url = next((public_url(row.get(key)) for key in (
        "citation_url", "source_url", "canonical_url", "public_download_url",
    ) if public_url(row.get(key))), None)
    return {"label": source_id, "url": url, "bibcode": None, "doi": None, "table": None,
            "row": str(row.get("source_record_id") or row.get("provider_observation_id") or "") or None}


def _measurement(
    *, target_id: str, source_id: str, source_record_id: str, measurement_id: str,
    property_code: str, ucd: str | None, label: str, value_kind: str,
    original_value: Any, original_unit: str | None, normalized_value: Any,
    normalized_unit: str | None, uncertainty: float | None, time: dict[str, Any],
    reference_frame: str | None, method: str | None, facility: str | None,
    instrument: str | None, bandpass: str | None, calibration: str | None,
    flags: list[str], source_status: str | None, citation: dict[str, Any],
    active_state: str,
) -> dict[str, Any]:
    return {
        "measurementId": measurement_id,
        "targetId": target_id,
        "sourceContractId": source_id,
        "sourceRecordId": source_record_id,
        "solutionId": None,
        "propertyCode": property_code,
        "ucd": ucd,
        "label": label,
        "valueKind": value_kind,
        "originalValue": original_value,
        "originalUnit": original_unit,
        "normalizedValue": normalized_value,
        "normalizedUnit": normalized_unit,
        "uncertaintyPositive": uncertainty,
        "uncertaintyNegative": uncertainty,
        "intervalLower": None,
        "intervalUpper": None,
        "posteriorProductId": None,
        "covarianceState": "NOT_PROVIDED" if uncertainty is not None else "UNKNOWN",
        "covarianceGroupId": None,
        "time": time,
        "referenceFrame": reference_frame,
        "method": method,
        "facility": facility,
        "instrument": instrument,
        "bandpass": bandpass,
        "calibration": calibration,
        "qualityFlags": flags,
        "sourceStatus": source_status,
        "citation": citation,
        "transformationRecipe": "Identity projection from the source-native CTAS field; no scientific conversion or averaging.",
        "parserVersion": PROJECTION_VERSION,
        "activeState": active_state,
    }


def build_measurements(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[str]], dict[str, dict[str, Any]]]:
    target_id = str(candidate["event_id"])
    follow = candidate.get("follow_up") or {}
    measurements: list[dict[str, Any]] = []
    observation_ids: dict[str, list[str]] = {}
    compatibility: dict[str, dict[str, Any]] = {}

    for row in list(follow.get("classifications") or []) + list(follow.get("classification_history") or []):
        source_id = str(row.get("provider") or "unknown-source").lower()
        assertion_id = str(row.get("assertion_id") or stable_id("source-record", target_id, source_id, row.get("asserted_at"), row.get("classification")))
        classification_value = " ".join(
            str(value).strip() for value in (row.get("classification"), row.get("subtype")) if str(value or "").strip()
        ) or "Unclassified"
        property_code = classification_property(classification_value)
        active = "RETRACTED" if row.get("retracted") else "SUPERSEDED" if row.get("superseded") else "ACTIVE"
        flags = quality_flags(row.get("quality_flags"))
        accessed = iso_datetime(row.get("ctas_received_at"))
        if accessed:
            flags.append("ctas-accessed-at=" + accessed)
        mid = stable_id("measurement", "classification", assertion_id)
        item = _measurement(
            target_id=target_id, source_id=source_id, source_record_id=str(row.get("source_record_id") or assertion_id),
            measurement_id=mid, property_code=property_code, ucd=None,
            label="Source-reported classification or alert label", value_kind="QUALITATIVE",
            original_value=classification_value, original_unit=None,
            normalized_value=classification_value, normalized_unit=None,
            uncertainty=None, time=_time(row, "asserted_at"), reference_frame=None,
            method=row.get("method"), facility=None, instrument=None, bandpass=None, calibration=None,
            flags=flags, source_status="source assertion", citation=_citation(row, source_id), active_state=active,
        )
        measurements.append(item)
        compatibility[mid] = {"kind": "classification" if property_code == "transient.classification" else "reported-label", "method_key": "|".join(str(row.get(k) or "") for k in ("method", "model_name", "model_version"))}
        probability = number(row.get("probability"))
        if probability is not None:
            pid = stable_id("measurement", "classification-probability", assertion_id)
            measurements.append(_measurement(
                target_id=target_id, source_id=source_id, source_record_id=str(row.get("source_record_id") or assertion_id),
                measurement_id=pid, property_code=("transient.classification.probability" if property_code == "transient.classification" else property_code + ".probability"), ucd="stat.probability",
                label="Source-reported classification probability", value_kind="MEASUREMENT",
                original_value=probability, original_unit="1", normalized_value=probability, normalized_unit="1",
                uncertainty=None, time=_time(row, "asserted_at"), reference_frame=None,
                method=" · ".join(str(row.get(k)) for k in ("method", "model_name", "model_version") if row.get(k)) or None,
                facility=None, instrument=None, bandpass=None, calibration=None, flags=flags,
                source_status="source probability; calibration not assumed", citation=_citation(row, source_id), active_state=active,
            ))
            compatibility[pid] = {"kind": "probability", "method_key": compatibility[mid]["method_key"]}

    for row in follow.get("observations") or []:
        source_id = str(row.get("provider") or "unknown-source").lower()
        assertion_id = str(row.get("assertion_id") or stable_id("source-record", target_id, source_id, row.get("observed_at"), row.get("provider_observation_id")))
        source_record_id = str(row.get("provider_observation_id") or row.get("source_record_id") or assertion_id)
        flags = quality_flags(row.get("quality_flags"))
        accessed = iso_datetime(row.get("ctas_received_at"))
        if accessed:
            flags.append("ctas-accessed-at=" + accessed)
        active = "SUPERSEDED" if row.get("superseded") else "ACTIVE"
        time = _time(row, "observed_at")
        method = str(row.get("photometry_method") or row.get("pipeline") or "") or None
        diff = bool(row.get("difference_photometry"))
        common = {
            "target_id": target_id, "source_id": source_id, "source_record_id": source_record_id,
            "time": time, "reference_frame": None, "method": method,
            "facility": row.get("observatory") or row.get("telescope"), "instrument": row.get("instrument"),
            "bandpass": row.get("band") or row.get("original_band"), "calibration": row.get("calibration"),
            "flags": flags, "source_status": "detection" if row.get("detection") else "nondetection or limit",
            "citation": _citation(row, source_id), "active_state": active,
        }
        epoch_key = _time_compatibility_key(row, "observed_at")
        per_observation: list[str] = []
        magnitude = number(row.get("magnitude"))
        mag_error = number(row.get("magnitude_error"))
        if magnitude is not None:
            mid = stable_id("measurement", "magnitude", assertion_id)
            measurements.append(_measurement(
                measurement_id=mid, property_code="phot.mag", ucd="phot.mag",
                label="Source-native magnitude", value_kind="MEASUREMENT", original_value=magnitude,
                original_unit="mag", normalized_value=magnitude,
                normalized_unit="mag", uncertainty=abs(mag_error) if mag_error is not None else None,
                **{**common, "reference_frame": row.get("magnitude_system")},
            ))
            per_observation.append(mid)
            compatibility[mid] = {"kind": "photometry", "key": ("magnitude", epoch_key, row.get("band"), row.get("magnitude_system"), method, row.get("calibration"), diff)}
        flux = number(row.get("flux"))
        flux_error = number(row.get("flux_error"))
        if flux is not None:
            mid = stable_id("measurement", "flux", assertion_id)
            measurements.append(_measurement(
                measurement_id=mid, property_code="phot.flux", ucd="phot.flux.density",
                label="Source-native flux", value_kind="MEASUREMENT", original_value=flux,
                original_unit=row.get("flux_unit"), normalized_value=flux, normalized_unit=row.get("flux_unit"),
                uncertainty=abs(flux_error) if flux_error is not None else None, **common,
            ))
            per_observation.insert(0, mid)
            compatibility[mid] = {"kind": "photometry", "key": ("flux", epoch_key, row.get("band"), row.get("flux_unit"), method, row.get("calibration"), diff)}
        limiting_mag = number(row.get("limiting_magnitude"))
        if limiting_mag is not None:
            mid = stable_id("measurement", "limiting-magnitude", assertion_id)
            measurements.append(_measurement(
                measurement_id=mid, property_code="phot.mag", ucd="phot.mag",
                label="Source-native limiting magnitude (object is fainter)", value_kind="LOWER_LIMIT",
                original_value=limiting_mag, original_unit="mag", normalized_value=limiting_mag,
                normalized_unit="mag", uncertainty=None, **{**common, "reference_frame": row.get("magnitude_system")},
            ))
            per_observation.append(mid)
            compatibility[mid] = {"kind": "photometry", "key": ("magnitude", epoch_key, row.get("band"), row.get("magnitude_system"), method, row.get("calibration"), diff)}
        limiting_flux = number(row.get("limiting_flux"))
        if limiting_flux is not None:
            mid = stable_id("measurement", "limiting-flux", assertion_id)
            measurements.append(_measurement(
                measurement_id=mid, property_code="phot.flux", ucd="phot.flux.density",
                label="Source-native flux upper limit", value_kind="UPPER_LIMIT",
                original_value=limiting_flux, original_unit=row.get("flux_unit"), normalized_value=limiting_flux,
                normalized_unit=row.get("flux_unit"), uncertainty=None, **common,
            ))
            per_observation.append(mid)
            compatibility[mid] = {"kind": "photometry", "key": ("flux", epoch_key, row.get("band"), row.get("flux_unit"), method, row.get("calibration"), diff)}
        state = "DETECTION" if row.get("detection") is True else "NONDETECTION" if row.get("detection") is False else "UNSPECIFIED"
        mid = stable_id("measurement", "detection-state", assertion_id)
        measurements.append(_measurement(
            measurement_id=mid, property_code="photometry.detection_state", ucd="meta.code",
            label="Source-reported photometric detection state", value_kind="QUALITATIVE",
            original_value=state, original_unit=None, normalized_value=state, normalized_unit=None,
            uncertainty=None, **common,
        ))
        per_observation.append(mid)
        observation_ids[assertion_id] = per_observation

    for row in derive_messenger_revisions(list(follow.get("messenger_signals") or [])):
        source_id = str(row.get("provider") or "unknown-source").lower()
        assertion_id = str(row.get("assertion_id") or stable_id(
            "source-record", target_id, source_id, row.get("provider_signal_id"), row.get("observed_at"),
        ))
        source_record_id = str(row.get("provider_signal_id") or row.get("source_record_id") or assertion_id)
        group, revision = _messenger_revision(row)
        flags = quality_flags(row.get("quality_flags"))
        if revision is not None:
            flags.append(f"messenger-revision={revision}")
        if group:
            flags.append(f"messenger-revision-group={group}")
        if row.get("supersedes_provider_signal_id"):
            flags.append("supersedes-provider-signal-id=" + str(row["supersedes_provider_signal_id"]))
        accessed = iso_datetime(row.get("ctas_received_at"))
        if accessed:
            flags.append("ctas-accessed-at=" + accessed)
        active = "RETRACTED" if row.get("retracted") else "SUPERSEDED" if row.get("superseded") else "ACTIVE"
        value = str(row.get("role") or row.get("alert_type") or "notice")
        mid = stable_id("measurement", "messenger-notice", assertion_id)
        measurements.append(_measurement(
            target_id=target_id, source_id=source_id, source_record_id=source_record_id,
            measurement_id=mid, property_code="messenger.notice.role", ucd="meta.code",
            label="Source-reported messenger notice role", value_kind="QUALITATIVE",
            original_value=value, original_unit=None, normalized_value=value, normalized_unit=None,
            uncertainty=None, time=_time(row, "observed_at"), reference_frame=None,
            method=row.get("messenger"), facility=None, instrument=row.get("instrument"),
            bandpass=None, calibration=None, flags=flags,
            source_status=str(row.get("alert_type") or row.get("summary") or "source messenger notice"),
            citation=_citation(row, source_id), active_state=active,
        ))
        compatibility[mid] = {
            "kind": "messenger-revision", "group": (source_id, group), "revision": revision,
        }

    for row in follow.get("host_context") or []:
        redshift = number(row.get("redshift"))
        if redshift is None:
            continue
        source_id = str(row.get("provider") or "unknown-source").lower()
        assertion_id = str(row.get("assertion_id") or stable_id("source-record", target_id, source_id, row.get("queried_at"), row.get("canonical_name")))
        mid = stable_id("measurement", "host-redshift", assertion_id)
        flags = quality_flags(row.get("quality_flags"))
        accessed = iso_datetime(row.get("ctas_received_at") or row.get("queried_at"))
        if accessed:
            flags.append("ctas-accessed-at=" + accessed)
        measurements.append(_measurement(
            target_id=target_id, source_id=source_id, source_record_id=str(row.get("source_record_id") or assertion_id),
            measurement_id=mid, property_code="src.redshift;meta.id.assoc", ucd="src.redshift",
            label="Source-reported host redshift", value_kind="MEASUREMENT", original_value=redshift,
            original_unit="1", normalized_value=redshift, normalized_unit="1",
            uncertainty=abs(number(row.get("redshift_error"))) if number(row.get("redshift_error")) is not None else None,
            time=_time(row, "queried_at"), reference_frame=row.get("redshift_reference"), method="provider host-context query",
            facility=None, instrument=None, bandpass=None, calibration=None, flags=flags,
            source_status="reported host association; not independently validated by CTAS",
            citation=_citation(row, source_id), active_state="ACTIVE",
        ))
        compatibility[mid] = {
            "kind": "host-redshift",
            "host": str(row.get("canonical_name") or row.get("queried_name") or "").strip().lower(),
            "reference": str(row.get("redshift_reference") or "").strip().lower(),
        }

    measurements.sort(key=lambda item: item["measurementId"])
    return measurements, observation_ids, compatibility


def _conflict(target_id: str, property_code: str, ids: Iterable[str], relations: list[str], explanation: str,
              sigma: float | None = None) -> dict[str, Any]:
    measurement_ids = sorted(set(ids))
    return {
        "conflictSetId": stable_id("conflict", target_id, property_code, *measurement_ids, *sorted(relations)),
        "targetId": target_id,
        "propertyCode": property_code,
        "measurementIds": measurement_ids,
        "relations": sorted(set(relations)),
        "assessmentState": "AUTOMATED_SCREEN",
        "significanceSigma": sigma,
        "explanation": explanation,
        "selectionId": None,
    }


def build_conflicts(target_id: str, measurements: list[dict[str, Any]], compatibility: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    messenger_revision_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for item in measurements:
        meta = compatibility.get(item["measurementId"], {})
        if meta.get("kind") == "messenger-revision":
            messenger_revision_groups.setdefault(tuple(meta.get("group") or ()), []).append(item)
    for group in messenger_revision_groups.values():
        if len(group) < 2:
            continue
        conflicts.append(_conflict(
            target_id, "messenger.notice.role", [item["measurementId"] for item in group], ["SOURCE_REVISION"],
            "These messenger notices are successive provider revisions of one signal. Earlier and retracted notices remain inspectable; CTAS does not flatten the revision chain.",
        ))
    active = [item for item in measurements if item["activeState"] == "ACTIVE"]
    classes = [item for item in active if item["propertyCode"] == "transient.classification"]
    if len({str(item["normalizedValue"]).strip().lower() for item in classes}) > 1:
        conflicts.append(_conflict(
            target_id, "transient.classification", [item["measurementId"] for item in classes], ["STATUS_CONFLICT"],
            "Active source-reported classification labels disagree. CTAS preserves them without voting, averaging, or silently choosing one.",
        ))
    probabilities = [item for item in active if item["propertyCode"] == "transient.classification.probability"]
    method_keys = {compatibility.get(item["measurementId"], {}).get("method_key") for item in probabilities}
    if len(probabilities) > 1 and len(method_keys) > 1:
        conflicts.append(_conflict(
            target_id, "transient.classification.probability", [item["measurementId"] for item in probabilities], ["METHOD_INCOMPATIBLE"],
            "Reported probabilities came from different or undocumented methods/models and are not numerically combined.",
        ))

    photometry_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for item in active:
        meta = compatibility.get(item["measurementId"], {})
        if meta.get("kind") == "photometry":
            photometry_groups.setdefault(tuple(meta.get("key") or ()), []).append(item)
    for group in photometry_groups.values():
        providers = {item["sourceContractId"] for item in group}
        if len(group) < 2 or len(providers) < 2:
            continue
        inconsistent: set[str] = set()
        max_sigma: float | None = None
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                if left["sourceContractId"] == right["sourceContractId"]:
                    continue
                lv, rv = number(left["normalizedValue"]), number(right["normalizedValue"])
                if lv is None or rv is None:
                    continue
                if left["valueKind"] == right["valueKind"] == "MEASUREMENT":
                    le, re_ = number(left["uncertaintyPositive"]), number(right["uncertaintyPositive"])
                    if le and re_:
                        sigma = abs(lv - rv) / math.sqrt(le * le + re_ * re_)
                        if sigma > 3:
                            inconsistent.update((left["measurementId"], right["measurementId"]))
                            max_sigma = max(max_sigma or 0, sigma)
                elif {left["valueKind"], right["valueKind"]} & {"UPPER_LIMIT", "LOWER_LIMIT"}:
                    measurement = left if left["valueKind"] == "MEASUREMENT" else right if right["valueKind"] == "MEASUREMENT" else None
                    limit = right if measurement is left else left if measurement is right else None
                    if measurement and limit:
                        mv, limit_v = number(measurement["normalizedValue"]), number(limit["normalizedValue"])
                        contradiction = (limit["valueKind"] == "UPPER_LIMIT" and mv is not None and limit_v is not None and mv > limit_v) or (
                            limit["valueKind"] == "LOWER_LIMIT" and mv is not None and limit_v is not None and mv < limit_v
                        )
                        if contradiction:
                            inconsistent.update((measurement["measurementId"], limit["measurementId"]))
        if len(inconsistent) >= 2:
            conflicts.append(_conflict(
                target_id, group[0]["propertyCode"], inconsistent, ["STATISTICALLY_INCONSISTENT"],
                "Only exact epoch, bandpass, unit/system, method, and direct/difference-photometry matches were compared; this compatible group exceeds the declared 3-sigma or detection/limit consistency rule.",
                round(max_sigma, 6) if max_sigma is not None else None,
            ))

    redshifts = [item for item in active if item["propertyCode"] == "src.redshift;meta.id.assoc"]
    hosts = {compatibility.get(item["measurementId"], {}).get("host") for item in redshifts if compatibility.get(item["measurementId"], {}).get("host")}
    if len(hosts) > 1:
        conflicts.append(_conflict(
            target_id, "src.redshift;meta.id.assoc", [item["measurementId"] for item in redshifts], ["IDENTITY_AMBIGUOUS"],
            "Redshift assertions refer to different proposed hosts; CTAS does not treat them as measurements of one object.",
        ))
    else:
        inconsistent: set[str] = set()
        max_sigma = None
        for index, left in enumerate(redshifts):
            for right in redshifts[index + 1:]:
                if compatibility.get(left["measurementId"], {}).get("reference") != compatibility.get(right["measurementId"], {}).get("reference"):
                    continue
                le, re_ = number(left["uncertaintyPositive"]), number(right["uncertaintyPositive"])
                if le and re_:
                    sigma = abs(float(left["normalizedValue"]) - float(right["normalizedValue"])) / math.sqrt(le * le + re_ * re_)
                    if sigma > 3:
                        inconsistent.update((left["measurementId"], right["measurementId"]))
                        max_sigma = max(max_sigma or 0, sigma)
        if len(inconsistent) >= 2:
            conflicts.append(_conflict(
                target_id, "src.redshift;meta.id.assoc", inconsistent, ["STATISTICALLY_INCONSISTENT"],
                "Compatible redshift assertions for the same proposed host differ by more than 3 sigma.",
                round(max_sigma, 6) if max_sigma is not None else None,
            ))
    return sorted(conflicts, key=lambda item: item["conflictSetId"])


def build_selections(candidate: dict[str, Any], measurements: list[dict[str, Any]], conflicts: list[dict[str, Any]], generated_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_id = str(candidate["event_id"])
    conflicted = {mid for conflict in conflicts for mid in conflict["measurementIds"]}
    selections: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    def select(property_code: str, summary_value: Any, purpose: str, rationale: str) -> None:
        if summary_value in (None, "", "Unclassified"):
            return
        candidates = [item for item in measurements if item["propertyCode"] == property_code and item["activeState"] == "ACTIVE"]
        matching = [item for item in candidates if str(item["normalizedValue"]).strip().lower() == str(summary_value).strip().lower()]
        if len(matching) != 1 or matching[0]["measurementId"] in conflicted:
            return
        selected = matching[0]["measurementId"]
        rejected = sorted(item["measurementId"] for item in candidates if item["measurementId"] != selected)
        selection_id = stable_id("selection", target_id, property_code, selected)
        selections.append({
            "selectionId": selection_id,
            "targetId": target_id,
            "purpose": purpose,
            "measurementIds": [selected],
            "rule": f"{RECONCILIATION_POLICY_VERSION}: {rationale}",
            "methodVersion": PROJECTION_VERSION,
            "reviewState": "SOURCE_REPORTED",
            "selectedAt": iso_datetime(candidate.get("updated_at"), generated_at) or generated_at,
        })
        details.append({
            "selectionId": selection_id, "propertyCode": property_code,
            "selectedAssertionIds": [selected], "rejectedAssertionIds": rejected,
            "rationale": rationale, "actor": PROJECTION_VERSION,
            "selectedAt": iso_datetime(candidate.get("updated_at"), generated_at) or generated_at,
        })

    select("transient.classification", candidate.get("classification"), "DISPLAY_DEFAULT",
           "The existing CTAS display summary exactly matches one active source assertion; no averaging is performed.")
    if number(candidate.get("redshift")) is not None:
        select("src.redshift;meta.id.assoc", candidate.get("redshift"), "DISPLAY_DEFAULT",
               "The existing CTAS redshift summary exactly matches one unconflicted active host-context assertion.")
    return sorted(selections, key=lambda item: item["selectionId"]), sorted(details, key=lambda item: item["selectionId"])


def build_data_products(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    target_id = str(candidate["event_id"])
    follow = candidate.get("follow_up") or {}
    products: list[dict[str, Any]] = []
    for row in follow.get("spectra") or []:
        source_id = str(row.get("provider") or "unknown-source").lower()
        source_record = str(row.get("provider_spectrum_id") or row.get("assertion_id") or row.get("file_name") or "spectrum")
        products.append({
            "productId": stable_id("product", "spectrum", target_id, source_id, source_record),
            "targetId": target_id, "sourceContractId": source_id, "productType": "SPECTRUM",
            "accessUrl": public_url(row.get("public_download_url")), "contentType": None,
            "checksumSha256": valid_sha256(row.get("file_checksum") or row.get("checksum_sha256")),
            "rightsState": "PUBLIC", "calibrationState": row.get("calibration_state"),
            "observedStart": _time(row, "observed_at") if row.get("observed_at") is not None or row.get("mjd") is not None else None,
            "observedEnd": _time(row, "observed_at") if row.get("observed_at") is not None or row.get("mjd") is not None else None,
            "requiredAttribution": source_id,
        })
    for row in follow.get("archive_products") or []:
        source_id = str(row.get("provider") or "unknown-source").lower()
        source_record = str(row.get("provider_product_id") or row.get("assertion_id") or row.get("product_filename") or "archive-product")
        raw_kind = " ".join(str(row.get(key) or "") for key in (
            "data_product_type", "product_type", "product_subgroup", "product_filename",
        )).lower()
        kind = (
            "SPECTRUM" if "spectr" in raw_kind
            else "LIGHT_CURVE" if "light curve" in raw_kind or "lightcurve" in raw_kind or "photometr" in raw_kind
            else "ASTROMETRY" if "astrometr" in raw_kind
            else "SKYMAP" if "skymap" in raw_kind or "sky map" in raw_kind
            else "IMAGE" if "image" in raw_kind
            else "PIXEL_DATA" if any(token in raw_kind for token in ("pixel", "fits", ".fit", ".fz"))
            else "TABLE" if any(token in raw_kind for token in ("table", ".csv", ".ecsv", ".vot"))
            else "OTHER"
        )
        observed_start = (
            _time(row, "observed_start_mjd", mjd_key="observed_start_mjd", jd_key="")
            if row.get("observed_start_mjd") is not None else None
        )
        observed_end = (
            _time(row, "observed_end_mjd", mjd_key="observed_end_mjd", jd_key="")
            if row.get("observed_end_mjd") is not None else None
        )
        products.append({
            "productId": stable_id("product", "archive", target_id, source_id, source_record),
            "targetId": target_id, "sourceContractId": source_id, "productType": kind,
            "accessUrl": public_url(row.get("public_download_url")), "contentType": None,
            # response_checksum is the archive-query response checksum, not a
            # checksum of this individual product.  Never mislabel it here.
            "checksumSha256": valid_sha256(row.get("checksum_sha256") or row.get("file_checksum")), "rightsState": "PUBLIC",
            "calibrationState": (
                str(row.get("calibration_level")) if row.get("calibration_level") is not None else None
            ),
            "observedStart": observed_start,
            "observedEnd": observed_end,
            "requiredAttribution": str(row.get("attribution") or source_id),
        })
    for row in follow.get("messenger_signals") or []:
        if not public_url(row.get("skymap_url")):
            continue
        source_id = str(row.get("provider") or "unknown-source").lower()
        source_record = str(row.get("provider_signal_id") or row.get("assertion_id") or "skymap")
        products.append({
            "productId": stable_id("product", "skymap", target_id, source_id, source_record),
            "targetId": target_id, "sourceContractId": source_id, "productType": "SKYMAP",
            "accessUrl": public_url(row.get("skymap_url")), "contentType": None,
            "checksumSha256": None, "rightsState": "PUBLIC", "calibrationState": None,
            "observedStart": _time(row, "observed_at") if row.get("observed_at") is not None or row.get("mjd") is not None else None,
            "observedEnd": None,
            "requiredAttribution": source_id,
        })
    return sorted(products, key=lambda item: item["productId"])


def build_analysis_runs(candidate: dict[str, Any], raw_runs: list[dict[str, Any]], observation_ids: dict[str, list[str]]) -> list[dict[str, Any]]:
    target_id = str(candidate["event_id"])
    runs: list[dict[str, Any]] = []
    for row in raw_runs:
        if str(row.get("analysis_type")) != "light-curve-inference":
            continue
        manifest = json_value(row.get("input_manifest"), {})
        input_ids: list[str] = []
        for record in manifest.get("records", []) if isinstance(manifest.get("records"), list) else []:
            observation_id = str(record.get("observation_id") or "")
            projected = observation_ids.get(observation_id, [])
            if projected:
                input_ids.extend(projected)
        status = {
            "complete": "COMPLETE", "insufficient-data": "INSUFFICIENT_DATA", "failed": "FAILED",
            "blocked-rights": "BLOCKED_RIGHTS", "not-applicable": "NOT_APPLICABLE",
        }.get(str(row.get("status") or "").lower(), "FAILED")
        result = json_value(row.get("result"), {})
        if status == "INSUFFICIENT_DATA" and result.get("inference_available") is not False:
            result = {"inference_available": False, "gate_failures": ["Legacy result did not satisfy the declared prerequisite gate."]}
        input_checksum = valid_sha256(row.get("input_checksum")) or hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
        result_checksum = valid_sha256(row.get("result_checksum")) or hashlib.sha256(canonical_json_bytes(result)).hexdigest()
        runs.append({
            "analysisRunId": str(row.get("id") or stable_id("analysis", target_id, row.get("analysis_type"), row.get("analysis_key"))),
            "targetId": target_id, "analysisType": str(row.get("analysis_type") or "light-curve-inference"),
            "methodName": str(row.get("method_name") or "undocumented-method"),
            "methodVersion": str(row.get("method_version") or "legacy-unknown"), "status": status,
            "inputRecordIds": sorted(set(input_ids)), "inputChecksumSha256": input_checksum,
            "parameters": json_value(row.get("parameters"), {}), "softwareVersions": json_value(row.get("software_versions"), {}),
            "randomSeed": None, "result": result, "resultChecksumSha256": result_checksum,
            "warnings": [str(value) for value in json_value(row.get("warnings"), [])],
            "reviewState": {"machine": "MACHINE", "human-reviewed": "HUMAN_REVIEWED", "human-adopted": "HUMAN_ADOPTED", "rejected": "REJECTED"}.get(str(row.get("review_state") or "machine").lower(), "MACHINE"),
            "createdAt": iso_datetime(row.get("completed_at") or row.get("created_at")) or iso_datetime(candidate.get("updated_at")) or "1970-01-01T00:00:00Z",
        })
    return sorted(runs, key=lambda item: (item["createdAt"], item["analysisRunId"]))


def _latest_evidence_by_source(candidate: dict[str, Any]) -> tuple[dict[str, int], dict[str, str | None], dict[str, dict[str, int]]]:
    counts: dict[str, int] = {}
    latest: dict[str, str | None] = {}
    types: dict[str, dict[str, int]] = {}
    for evidence_type, rows in (candidate.get("follow_up") or {}).items():
        for row in rows:
            source = str(row.get("provider") or "").lower()
            if not source:
                continue
            counts[source] = counts.get(source, 0) + 1
            types.setdefault(source, {})[evidence_type] = types.setdefault(source, {}).get(evidence_type, 0) + 1
            values = [iso_datetime(row.get(key)) for key in ("ctas_received_at", "queried_at", "asserted_at", "observed_at", "published_at")]
            current = max((value for value in values if value), default=None)
            if current and (not latest.get(source) or current > str(latest[source])):
                latest[source] = current
    for alias in candidate.get("designations") or []:
        source = str(alias.get("source_key") or "").lower()
        if not source:
            continue
        counts[source] = counts.get(source, 0) + 1
        types.setdefault(source, {})["aliases"] = types.setdefault(source, {}).get("aliases", 0) + 1
        asserted = iso_datetime(alias.get("asserted_at"))
        if asserted and (not latest.get(source) or asserted > str(latest[source])):
            latest[source] = asserted
    return counts, latest, types


def build_projection(
    candidate: dict[str, Any], source_universe_rows: list[dict[str, Any]], attempts: list[dict[str, Any]],
    raw_analysis_runs: list[dict[str, Any]], generated_at: str, source_universe_version: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    target_id = str(candidate["event_id"])
    evidence_counts, evidence_latest, evidence_types = _latest_evidence_by_source(candidate)
    referenced = set(evidence_counts) | {str(row.get("source_id") or "") for row in attempts}
    contracts: list[dict[str, Any]] = []
    contract_by_id: dict[str, dict[str, Any]] = {}
    applicability_reasons: dict[str, str] = {}
    for row in source_universe_rows:
        applicable, reason = _applicability(candidate, row, referenced)
        if not applicable:
            continue
        contract = source_contract(row, generated_at, reason)
        contracts.append(contract)
        contract_by_id[contract["sourceContractId"]] = contract
        applicability_reasons[contract["sourceContractId"]] = reason

    # Provider closure is fail-closed but complete: referenced legacy providers
    # receive a transparent compatibility contract rather than disappearing.
    for source_id in sorted(referenced - set(contract_by_id)):
        if not source_id:
            continue
        fallback = {
            "source_key": source_id, "name": source_id, "organization_or_facility": source_id,
            "source_family": "legacy-referenced-source", "data_types": ["legacy retained public record"],
            "access_mode": "link-only", "implementation_state": "link-only",
            "rights_or_public_access_basis": "Only already rights-cleared public CTAS rows are projected.",
            "authentication_requirement": "Not documented in the legacy row", "query_scope": "Legacy retained records only",
            "known_limitations": "The source is referenced by a retained row but is absent from this source-universe version.",
            "last_verified": generated_at,
        }
        contract = source_contract(fallback, generated_at, "A legacy retained row or receipt references this source.")
        contracts.append(contract)
        contract_by_id[source_id] = contract
        applicability_reasons[source_id] = contract["applicabilityRule"]
    contracts.sort(key=lambda item: item["sourceContractId"])

    attempts_by_source: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        attempts_by_source.setdefault(str(attempt.get("source_id") or ""), []).append(attempt)
    receipts: list[dict[str, Any]] = []
    receipt_details: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    actual_executed = 0
    outcome_counts: dict[str, int] = {}
    generated_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    for contract in contracts:
        source_id = contract["sourceContractId"]
        rows = sorted(attempts_by_source.get(source_id, []), key=lambda row: (str(row.get("checked_at") or row.get("created_at") or ""), str(row.get("id") or "")))
        if rows:
            current_keys: dict[str, tuple[str, str]] = {}
            for attempt in rows:
                query_chain = str(attempt.get("query_kind") or "legacy-source-query")
                key = (str(attempt.get("checked_at") or attempt.get("created_at") or ""), str(attempt.get("id") or ""))
                current_keys[query_chain] = max(current_keys.get(query_chain, key), key)
            for attempt in rows:
                key = (str(attempt.get("checked_at") or attempt.get("created_at") or ""), str(attempt.get("id") or ""))
                query_chain = str(attempt.get("query_kind") or "legacy-source-query")
                receipt, detail = _attempt_receipt(attempt, target_id, contract, generated_at, key == current_keys[query_chain])
                receipts.append(receipt)
                receipt_details.append(detail)
                if detail["executionState"] == "EXECUTED":
                    actual_executed += 1
            current = next(row for row in reversed(receipts) if row["sourceContractId"] == source_id)
            current_outcome = current["outcome"]
            current_checked_at = current["completedAt"]
        else:
            if contract["accessMode"] == "LINK_ONLY" or contract["implementationState"] in {"LINK_ONLY", "REPRESENTED_THROUGH_PROVIDER"}:
                current_outcome = "LINK_ONLY_NOT_QUERIED"
            elif contract["implementationState"] in {"NOT_IMPLEMENTED", "CREDENTIAL_REQUIRED", "AUTHORIZATION_REQUIRED", "BLOCKED_POLICY"}:
                current_outcome = "NOT_CONFIGURED"
            else:
                current_outcome = "NOT_QUERIED"
            current_checked_at = None
        outcome_counts[current_outcome] = outcome_counts.get(current_outcome, 0) + 1
        retained_latest = evidence_latest.get(source_id)
        age_seconds = None
        if retained_latest:
            try:
                age_seconds = max(0, int((generated_dt - datetime.fromisoformat(retained_latest.replace("Z", "+00:00"))).total_seconds()))
            except ValueError:
                pass
        retained_count = evidence_counts.get(source_id, 0)
        evidence_state = (
            "STALE_LAST_GOOD_RETAINED"
            if retained_count and current_outcome in {"QUERY_FAILED", "QUERY_BLOCKED", "NOT_CONFIGURED", "STALE_LAST_GOOD_RETAINED"}
            else "RETAINED" if retained_count else "NO_RETAINED_EVIDENCE"
        )
        matrix.append({
            "sourceContractId": source_id, "sourceName": contract["sourceName"], "applicabilityState": "APPLICABLE",
            "applicabilityRule": applicability_reasons[source_id], "currentQueryOutcome": current_outcome,
            "currentQueryCheckedAt": current_checked_at, "executedReceiptCount": sum(
                1 for detail in receipt_details if detail["sourceContractId"] == source_id and detail["executionState"] == "EXECUTED"
            ),
            "retainedRecordCount": retained_count, "retainedRecordTypes": evidence_types.get(source_id, {}),
            "retainedEvidenceLatestAt": retained_latest, "retainedEvidenceAgeSeconds": age_seconds,
            "retainedEvidenceState": evidence_state, "documentationUrl": contract["documentationUrl"],
        })

    measurements, observation_ids, compatibility = build_measurements(candidate)
    conflicts = build_conflicts(target_id, measurements, compatibility)
    selections, selection_details = build_selections(candidate, measurements, conflicts, generated_at)
    products = build_data_products(candidate)
    analysis_runs = build_analysis_runs(candidate, raw_analysis_runs, observation_ids)
    aliases = []
    for alias in candidate.get("designations") or []:
        aliases.append({
            "value": str(alias.get("designation") or ""),
            "sourceContractId": str(alias.get("source_key") or "unknown-source"),
            "sourceRecordId": str(alias.get("alias_id") or alias.get("designation") or "") or None,
            "isPreferred": bool(alias.get("is_preferred")),
            "linkState": "AMBIGUOUS" if alias.get("ambiguous") else "ASSERTED_BY_SOURCE",
            "assertedAt": iso_datetime(alias.get("asserted_at")),
        })
    aliases.sort(key=lambda item: (item["sourceContractId"], item["value"], not item["isPreferred"]))
    identity = candidate.get("identity_resolution") or {}
    event_text = " ".join(str(candidate.get(key) or "") for key in ("event_type", "primary_messenger")).lower()
    target_kind = "MESSENGER_EVENT" if any(token in event_text for token in NON_OPTICAL_TOKENS) else "TRANSIENT_EVENT"
    projection = {
        "schemaName": ASTRO_EVIDENCE_SCHEMA_NAME,
        "schemaVersion": ASTRO_EVIDENCE_SCHEMA_VERSION,
        "generatedAt": generated_at,
        "sourceUniverseVersion": source_universe_version,
        "target": {
            "targetId": target_id, "targetKind": target_kind, "preferredName": str(candidate.get("name") or target_id),
            "aliases": aliases,
            "identityState": str(identity.get("state") or "RESOLVED"), "parentTargetId": None,
            "raDeg": number(candidate.get("ra_deg")), "decDeg": number(candidate.get("dec_deg")),
            "coordinateFrame": "ICRS" if number(candidate.get("ra_deg")) is not None else None,
            "coordinateEpoch": "J2000" if number(candidate.get("ra_deg")) is not None else None,
            "coordinateUncertaintyArcsec": number(candidate.get("coordinate_error_arcsec")),
        },
        "sourceContracts": contracts,
        "queryReceipts": sorted(receipts, key=lambda item: (item["sourceContractId"], item["startedAt"], item["receiptId"])),
        "measurements": measurements,
        "conflictSets": conflicts,
        "selections": selections,
        "dataProducts": products,
        "analysisRuns": analysis_runs,
    }
    accounting = {
        "schema": "ctas.event-source-accounting@1.0.0",
        "declaredSources": len(source_universe_rows),
        "applicableSources": len(contracts),
        "executedQueryReceipts": actual_executed,
        "dataBearingSources": sum(1 for count in evidence_counts.values() if count > 0),
        "outcomeCounts": dict(sorted(outcome_counts.items())),
        "applicableSourceIds": sorted(contract_by_id),
        "countDefinitions": {
            "declaredSources": "Source contracts in the versioned maintained universe.",
            "applicableSources": "Contracts whose executable rule applies to this event, including referenced legacy providers.",
            "executedQueryReceipts": "Persisted source-query attempts with an executed terminal outcome; compatibility-only rows are excluded.",
            "dataBearingSources": "Distinct providers with retained rights-cleared aliases, measurements, notices, reports, or products.",
        },
        "applicabilityRuleVersion": APPLICABILITY_RULE_VERSION,
    }
    metadata = {
        "receiptProvenance": sorted(receipt_details, key=lambda item: item["receiptId"]),
        "selectionProvenance": selection_details,
    }
    return projection, accounting, sorted(matrix, key=lambda item: item["sourceName"].lower()), metadata
