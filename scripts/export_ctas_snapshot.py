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
import subprocess
import sys
from datetime import datetime, timedelta, timezone

UTC = timezone.utc  # datetime.UTC is 3.11+; this works on 3.9+
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

SOURCE_UNIVERSE_SCHEMA = "ctas.public-source-universe@1.0.0"
SOURCE_STATE_VOCABULARY = (
    "active-returning-data",
    "active-no-recent-messages",
    "represented-through-another-provider",
    "searched-with-data",
    "searched-no-match",
    "not-searched",
    "scheduled",
    "manual-access-required",
    "credentials-required",
    "temporarily-unavailable",
    "rate-limited",
    "provider-failure",
    "ambiguous-identity",
    "incomplete-result",
    "rights-blocked",
    "not-implemented",
    "not-applicable",
)

IMPLEMENTATION_STATE_VOCABULARY = (
    "implemented",
    "implemented-credentials-required",
    "implemented-topic-authorization-required",
    "implemented-user-authorization-required",
    "manual-only",
    "archival-only",
    "represented-through-provider",
    "generic-import-supported",
    "documented-not-implemented",
    "interface-review-required",
    "blocked-policy",
    "blocked-deployment-access",
    "blocked-user-authorization",
)

REPRESENTATION_STATE_VOCABULARY = (
    "direct",
    "through-provider",
    "dispositions-only",
    "none",
)

SOURCE_FAMILY_MAP = {
    **{key: "discovery-and-alert-brokers" for key in (
        "tns", "rubin-alerce", "rubin-ampel", "rubin-antares", "rubin-babamul",
        "rubin-fink", "rubin-fink-crossmatch", "rubin-lasair", "rubin-pitt-google",
    )},
    **{key: "optical-and-time-domain-surveys" for key in (
        "ztf", "atlas", "asas-sn", "gaia-alerts",
    )},
    **{key: "multimessenger-and-high-energy" for key in (
        "gcn", "lvk-public-alerts", "icecube-gcn", "icecube-cascade-gcn", "snews-gcn",
        "fermi-gcn", "swift-gcn", "gecam-gcn", "calet-gcn", "hawc-gcn", "konus-gcn",
        "nuem-gcn", "gcn-high-energy", "superk-gcn", "svom-gcn",
    )},
    **{key: "spectroscopy" for key in ("tns-public-reports", "aavso", "wiserep")},
    **{key: "photometric-follow-up" for key in ("ztf-irsa", "aavso-aid")},
    **{key: "archives" for key in (
        "ivoa", "mast", "heasarc", "eso-archive", "gemini-archive",
        "noirlab-archive", "cadc",
    )},
    **{key: "host-counterpart-and-catalog-context" for key in (
        "ned", "simbad", "irsa", "gaia-dr3",
    )},
    **{key: "reports-and-literature" for key in ("tns-astronotes", "gcn-circulars")},
    **{key: "authorized-user-supplied-sources" for key in (
        "private-tom-skyportal", "authorized-webhook", "authorized-file-drop",
    )},
}

SECONDARY_SOURCE_FAMILIES = {
    "tns-public-reports": ["reports-and-literature"],
    "atlas": ["photometric-follow-up"],
    "asas-sn": ["photometric-follow-up"],
    "ztf": ["photometric-follow-up"],
    "rubin-fink": ["photometric-follow-up"],
    "rubin-lasair": ["photometric-follow-up"],
    "irsa": ["archives"],
    "swift-gcn": ["photometric-follow-up"],
    "authorized-webhook": ["spectroscopy", "photometric-follow-up"],
    "authorized-file-drop": ["spectroscopy", "photometric-follow-up"],
}

REPRESENTED_THROUGH = {
    "ztf": ["rubin-fink"],
    "rubin-lsst": ["rubin-fink", "rubin-fink-crossmatch"],
    "pan-starrs": ["public-event-metadata"],
    "goto": ["public-event-metadata"],
    "master": ["public-event-metadata"],
    "blackgem": ["public-event-metadata"],
    "wfst": ["public-event-metadata"],
    "yse": ["public-event-metadata"],
    "chime": ["gcn-high-energy", "public-event-metadata"],
    "maxi": ["gcn-high-energy", "public-event-metadata"],
    "einstein-probe": ["gcn-high-energy", "public-event-metadata"],
    "allwise": ["irsa"],
    "2mass": ["irsa"],
    "ned-cone": ["ned"],
    "icecube-gcn": ["gcn"],
    "icecube-cascade-gcn": ["gcn"],
    "fermi-gcn": ["gcn"],
    "swift-gcn": ["gcn"],
    "gecam-gcn": ["gcn"],
    "calet-gcn": ["gcn"],
    "hawc-gcn": ["gcn"],
    "konus-gcn": ["gcn"],
    "nuem-gcn": ["gcn"],
    "snews-gcn": ["gcn"],
    "superk-gcn": ["gcn"],
    "svom-gcn": ["gcn"],
}

# Stable resolution of source-native discovery labels. This is deliberately
# explicit: no fuzzy or proximity-based identity inference is allowed.
SURVEY_SOURCE_ALIASES = {
    "Pan-STARRS": "pan-starrs", "WFST": "wfst", "ZTF": "ztf", "GOTO": "goto",
    "ATLAS": "atlas", "ASAS-SN": "asas-sn", "Gaia Alerts": "gaia-alerts",
    "MASTER": "master", "BlackGEM": "blackgem", "YSE": "yse", "Chime": "chime",
    "CHIME": "chime", "MAXI": "maxi", "Einstein Probe": "einstein-probe",
    "ALeRCE": "rubin-alerce", "Fermi": "fermi-gcn", "CALET": "calet-gcn",
    "Konus-Wind": "konus-gcn", "AMON IceCube-HAWC": "nuem-gcn",
    "IceCube HESE Cascade / AMON": "icecube-cascade-gcn",
    "IceCube Bronze Track Alert": "icecube-gcn", "IceCube Gold Track Alert": "icecube-gcn",
    "Bronze Track Alert": "icecube-gcn", "Gold Track Alert": "icecube-gcn",
}

ADDITIONAL_SOURCE_CONTRACTS = (
    ("rubin-lsst", "Vera C. Rubin Observatory / LSST", "Vera C. Rubin Observatory",
     "optical-and-time-domain-surveys", ["alert packets", "source histories", "forced photometry"],
     "broker-mediated", "https://rubinobservatory.org/for-scientists/data-products/lsst-alert-brokers",
     "Public records only through declared community brokers.", "Broker-dependent",
     "represented-through-provider", "represented-through-another-provider",
     "Rubin-direct public records are not claimed; current representation is broker-mediated."),
    ("pan-starrs", "Pan-STARRS", "Pan-STARRS", "optical-and-time-domain-surveys",
     ["discovery metadata", "photometry"], "represented event metadata",
     "https://outerspace.stsci.edu/display/PANSTARRS/", "Public source-attributed event metadata.", "None for represented metadata",
     "represented-through-provider", "represented-through-another-provider", "No standalone CTAS connector yet."),
    ("goto", "Gravitational-wave Optical Transient Observer", "GOTO", "optical-and-time-domain-surveys",
     ["discovery metadata", "photometry"], "represented event metadata", "https://goto-observatory.org/",
     "Public source-attributed event metadata.", "None for represented metadata", "represented-through-provider",
     "represented-through-another-provider", "No standalone CTAS connector yet."),
    ("master", "MASTER Global Robotic Net", "MASTER", "optical-and-time-domain-surveys",
     ["discovery metadata", "photometry"], "represented event metadata", "https://observ.pereplet.ru/",
     "Public source-attributed event metadata.", "None for represented metadata", "represented-through-provider",
     "represented-through-another-provider", "No standalone CTAS connector yet."),
    ("blackgem", "BlackGEM", "BlackGEM", "optical-and-time-domain-surveys",
     ["discovery metadata", "photometry"], "represented event metadata", "https://blackgem.org/",
     "Public source-attributed event metadata.", "None for represented metadata", "represented-through-provider",
     "represented-through-another-provider", "No standalone CTAS connector yet."),
    ("wfst", "Wide Field Survey Telescope", "WFST", "optical-and-time-domain-surveys",
     ["discovery metadata", "photometry"], "represented event metadata", "https://wfst.bao.ac.cn/",
     "Public source-attributed event metadata.", "None for represented metadata", "represented-through-provider",
     "represented-through-another-provider", "No standalone CTAS connector yet."),
    ("yse", "Young Supernova Experiment", "YSE", "optical-and-time-domain-surveys",
     ["discovery metadata", "photometry"], "represented event metadata", "https://yse.ucsc.edu/",
     "Public source-attributed event metadata.", "None for represented metadata", "represented-through-provider",
     "represented-through-another-provider", "No standalone CTAS connector yet."),
    ("chime", "CHIME/FRB", "CHIME", "multimessenger-and-high-energy",
     ["radio transient notices", "discovery metadata"], "GCN and represented event metadata", "https://chime-experiment.ca/",
     "Released public notices and source-attributed event metadata.", "Provider-dependent", "represented-through-provider",
     "represented-through-another-provider", "No direct CHIME client; retained public records arrive through GCN or event metadata."),
    ("maxi", "Monitor of All-sky X-ray Image", "MAXI", "multimessenger-and-high-energy",
     ["X-ray notices", "discovery metadata"], "GCN and represented event metadata", "https://maxi.riken.jp/top/",
     "Released public notices and source-attributed event metadata.", "Provider-dependent", "represented-through-provider",
     "represented-through-another-provider", "No direct MAXI client; retained public records arrive through GCN or event metadata."),
    ("einstein-probe", "Einstein Probe", "Chinese Academy of Sciences", "multimessenger-and-high-energy",
     ["X-ray notices", "counterpart notices"], "GCN and represented event metadata", "https://ep.bao.ac.cn/",
     "Released public notices and source-attributed event metadata.", "Provider-dependent", "represented-through-provider",
     "represented-through-another-provider", "No direct Einstein Probe client; retained public records arrive through GCN or event metadata."),
    ("allwise", "AllWISE Source Catalog", "NASA/IPAC IRSA", "host-counterpart-and-catalog-context",
     ["positional catalog candidates", "infrared photometry"], "through IRSA", "https://irsa.ipac.caltech.edu/data/WISE/AllWISE/catalogs.html",
     "Public AllWISE rows returned by the rights-allowlisted IRSA adapter.", "Anonymous public query", "represented-through-provider",
     "represented-through-another-provider", "Positional candidates are not automatic transient, counterpart, or host associations."),
    ("ned-cone", "NED Positional Candidates", "NASA/IPAC Extragalactic Database", "host-counterpart-and-catalog-context",
     ["positional host candidates", "galaxy context"], "through NED bounded cone search", "https://ned.ipac.caltech.edu/Documents/Guides/Interface/TAP",
     "Public NED positional candidates returned by the rights-allowlisted bounded adapter.", "Anonymous public query", "represented-through-provider",
     "scheduled", "Positional candidates are not automatic transient, counterpart, or host associations."),
    ("2mass", "2MASS Point Source Catalog", "NASA/IPAC IRSA", "host-counterpart-and-catalog-context",
     ["positional catalog candidates", "near-infrared photometry"], "through IRSA", "https://irsa.ipac.caltech.edu/data/2MASS/docs/releases/allsky/",
     "Public 2MASS rows returned by the rights-allowlisted IRSA adapter.", "Anonymous public query", "represented-through-provider",
     "represented-through-another-provider", "Positional candidates are not automatic transient, counterpart, or host associations."),
    ("ads", "NASA Astrophysics Data System", "NASA ADS", "reports-and-literature",
     ["bibliographic links", "literature context"], "planned rights-safe linking", "https://ui.adsabs.harvard.edu/",
     "Only public bibliographic metadata and links would be eligible.", "API token required for automated search",
     "documented-not-implemented", "credentials-required", "No ADS connector is implemented; no literature search is claimed."),
)

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


def source_family(source_id: str, _data_types: list[str] | None = None) -> str:
    """Return the explicit, reviewed primary family for a registry source."""

    return SOURCE_FAMILY_MAP.get(source_id, "other-declared-sources")


def implementation_state(source: dict[str, Any]) -> str:
    """Describe connector implementation independently of runtime and records."""

    source_id = str(source.get("source") or "")
    adapter = str(source.get("adapter_status") or "").lower()
    mode = str(source.get("mode") or "").lower()
    if source_id == "private-tom-skyportal":
        return "blocked-user-authorization"
    if source_id == "gemini-archive":
        return "blocked-deployment-access"
    if source_id == "cadc":
        return "blocked-policy"
    if source_id == "ivoa":
        return "generic-import-supported"
    if source_id == "ztf":
        return "represented-through-provider"
    if source_id == "gaia-alerts":
        return "archival-only"
    if "manual" in mode or "manual" in adapter:
        return "manual-only"
    if "user-authorization-required" in adapter:
        return "implemented-user-authorization-required"
    if "topic-authorization-required" in adapter:
        return "implemented-topic-authorization-required"
    if "credentials-required" in adapter or "token-required" in adapter:
        return "implemented-credentials-required"
    if "interface-review-required" in adapter:
        return "interface-review-required"
    if "configuration-required" in adapter:
        return "documented-not-implemented"
    if adapter.startswith("implemented-"):
        return "implemented"
    return "documented-not-implemented"


def public_source_state(
    source: dict[str, Any], represented: bool, latest_attempt: dict[str, Any] | None,
) -> str:
    """Describe current operation without using historical representation as health."""

    runtime = str(source.get("state") or "").lower()
    adapter = str(source.get("adapter_status") or "").lower()
    mode = str(source.get("mode") or "").lower()
    implemented = implementation_state(source)
    terminal = str((latest_attempt or {}).get("terminal_state") or "").lower()
    if runtime in {"degraded", "unavailable", "error"}:
        return "temporarily-unavailable"
    if terminal in {"rate-limited", "rate_limit"}:
        return "rate-limited"
    if terminal == "unavailable":
        return "temporarily-unavailable"
    if terminal == "failed":
        return "provider-failure"
    if implemented in {"blocked-policy"}:
        return "rights-blocked"
    if implemented == "blocked-deployment-access":
        return "temporarily-unavailable"
    if implemented == "blocked-user-authorization":
        return "manual-access-required"
    if implemented == "manual-only":
        return "manual-access-required"
    if implemented in {"documented-not-implemented", "interface-review-required"}:
        return "not-implemented"
    if implemented == "represented-through-provider":
        return "represented-through-another-provider"
    if source.get("enabled") and runtime in {"connected", "ok", "healthy"}:
        return "active-returning-data" if represented else "active-no-recent-messages"
    if latest_attempt:
        return "scheduled"
    if implemented in {"implemented-credentials-required", "implemented-topic-authorization-required"}:
        return "credentials-required"
    if "on-demand" in mode or "query" in mode or "archive" in mode:
        return "scheduled"
    if "credentials-required" in adapter:
        return "credentials-required"
    return "not-applicable"


def public_attempt_state(raw: str) -> str:
    return {
        "data": "searched-with-data",
        "no-match": "searched-no-match",
        "not-configured": "credentials-required",
        "unavailable": "temporarily-unavailable",
        "failed": "provider-failure",
        "rate-limited": "rate-limited",
        "rate_limit": "rate-limited",
        "ambiguous": "ambiguous-identity",
        "identity-unavailable": "ambiguous-identity",
        "indeterminate": "incomplete-result",
        "overflow": "incomplete-result",
        "blocked-rights": "rights-blocked",
        "manual": "manual-access-required",
        "not-queried": "not-searched",
    }.get(raw, "incomplete-result")


def public_attempt_disposition(raw: str, error_code: str | None = None) -> str:
    code = str(error_code or "").upper()
    if code in {
        "TNS_IDENTITY_UNAVAILABLE", "TARGET_DISCOVERY_TIME_UNAVAILABLE",
        "WISEREP_TARGET_DISCOVERY_TIME_UNAVAILABLE", "WISEREP_TARGET_IDENTIFIER_UNAVAILABLE",
    }:
        return "not-searched"
    if code == "HOST_NAME_UNAVAILABLE_OR_AMBIGUOUS":
        return "ambiguous-identity"
    return public_attempt_state(raw)


def completeness_for(candidate: dict[str, Any]) -> dict[str, Any]:
    """Describe public record richness separately from CTAS follow-up priority."""

    follow = candidate.get("follow_up_counts", {})
    messenger = str(candidate.get("primary_messenger") or "").lower()
    event_type = str(candidate.get("event_type") or "").lower()
    optical_applicable = (
        messenger in {"", "electromagnetic", "optical", "multimessenger"}
        or "optical" in event_type or candidate.get("discovery_magnitude") is not None
    )
    host_applicable = optical_applicable or any(
        candidate.get(key) is not None for key in ("host_name", "host_redshift", "redshift", "distance_mpc")
    )
    host_present = any(candidate.get(key) is not None for key in ("host_name", "host_redshift", "redshift", "distance_mpc"))
    if follow.get("host_context") or follow.get("catalog_counterparts"):
        host_present = True
    components = [
        {"id": "identity", "label": "Public identity", "state": "present"},
        {"id": "discovery", "label": "Discovery time and survey", "state": "present" if candidate.get("discovery_time") and candidate.get("discovery_survey") else "missing"},
        {"id": "coordinates", "label": "Sky coordinates", "state": "present" if candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None else "missing"},
        {"id": "discovery-photometry", "label": "Discovery magnitude", "state": "present" if candidate.get("discovery_magnitude") is not None else ("missing" if optical_applicable else "not-applicable")},
        {"id": "classification", "label": "Public classification", "state": "present" if candidate.get("classification") and candidate.get("classification") != "Unclassified" else "missing"},
        {"id": "follow-up-photometry", "label": "Subsequent photometry or limits", "state": "present" if follow.get("observations", 0) else ("missing" if optical_applicable else "not-applicable")},
        {"id": "spectrum", "label": "Public spectrum", "state": "present" if follow.get("spectra", 0) else ("missing" if optical_applicable else "not-applicable")},
        {"id": "host-context", "label": "Host or environmental context", "state": "present" if host_present else ("missing" if host_applicable else "not-applicable")},
        {"id": "messenger", "label": "Messenger evidence", "state": "present" if follow.get("messenger_signals", 0) else ("not-applicable" if messenger in {"", "electromagnetic"} else "missing")},
        {"id": "reports", "label": "Public reports", "state": "present" if follow.get("publications", 0) else "missing"},
        {"id": "source-links", "label": "Authoritative source links", "state": "present" if any(link.get("url") for link in candidate.get("links", [])) else "missing"},
        {"id": "source-dispositions", "label": "Source-search dispositions", "state": "present" if candidate.get("source_coverage") else "not-assessed"},
    ]
    for component in components:
        component["evidence_count"] = {
            "identity": 1,
            "discovery": int(bool(candidate.get("discovery_time") and candidate.get("discovery_survey"))),
            "coordinates": int(candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None),
            "discovery-photometry": int(candidate.get("discovery_magnitude") is not None),
            "classification": int(bool(candidate.get("classification") and candidate.get("classification") != "Unclassified")),
            "follow-up-photometry": int(follow.get("observations", 0)),
            "spectrum": int(follow.get("spectra", 0)),
            "host-context": max(
                1 if host_present else 0,
                int(follow.get("host_context", 0)) + int(follow.get("catalog_counterparts", 0)),
            ),
            "messenger": int(follow.get("messenger_signals", 0)),
            "reports": int(follow.get("publications", 0)),
            "source-links": len(candidate.get("links", [])),
            "source-dispositions": len(candidate.get("source_coverage", [])),
        }.get(component["id"], 0)
    applicable = [row for row in components if row["state"] not in {"not-applicable", "not-assessed"}]
    present = sum(row["state"] == "present" for row in applicable)
    fraction = present / len(applicable) if applicable else 0.0
    if candidate.get("follow_up_total", 0) == 0:
        label = "Event record only"
    elif fraction >= 0.75:
        label = "Rich public record"
    elif fraction >= 0.5:
        label = "Moderate public record"
    else:
        label = "Sparse public record"
    return {
        "schema": "ctas.public-record-completeness@1.1.0",
        "policy_version": "1.1.0",
        "applicability_profile": "optical-transient" if optical_applicable else ("multimessenger" if messenger == "multimessenger" else "non-electromagnetic-notice"),
        "label": label,
        "present": present,
        "applicable": len(applicable),
        "not_assessed": sum(row["state"] == "not-assessed" for row in components),
        "fraction": round(fraction, 4),
        "components": components,
        "claim_boundary": "Public record richness only; not CTAS priority, scientific importance, classification confidence, or discovery probability.",
    }


def score_explanation_for(candidate: dict[str, Any]) -> str:
    """Explain published score factors without interpreting scientific merit."""

    labels = {
        "recency_points": "recency",
        "brightness_points": "reported discovery brightness",
        "classification_gap_points": "missing classification",
        "classification_conflict_points": "classification conflict",
        "spectroscopy_gap_points": "no retained public spectrum",
        "observation_gap_points": "time since retained observation",
        "multimessenger_points": "multiple messenger information",
        "coverage_reduction": "existing quantitative observation coverage",
    }
    factors = candidate.get("score_factors", {})
    parts = []
    for key, label in labels.items():
        value = factors.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if abs(number) < 1e-9:
            continue
        direction = "adds" if number > 0 else "reduces by"
        parts.append(f"{label} {direction} {abs(number):g} points")
    if factors.get("status"):
        parts.append(f"the {factors['status']} status applies an override")
    if not parts:
        return "The published factor record contains no non-zero adjustment beyond the CTAS baseline."
    return "The published factor record shows that " + "; ".join(parts) + "."


def timeline_for(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one public evidence timeline while preserving independent clocks."""

    follow = candidate.get("follow_up", {})
    entries: list[dict[str, Any]] = []

    def add(rows: list[dict[str, Any]], evidence_type: str, scientific_key: str | None,
            title_keys: tuple[str, ...], source_url_keys: tuple[str, ...] = ()) -> None:
        for index, row in enumerate(rows):
            title = next((clean(row.get(key)) for key in title_keys if clean(row.get(key))), evidence_type)
            url = next((clean(row.get(key)) for key in source_url_keys if clean(row.get(key))), None)
            facility = next((clean(row.get(key)) for key in ("facility", "observatory", "telescope", "instrument") if clean(row.get(key))), None)
            summary = next((clean(row.get(key)) for key in ("summary", "description", "overview_note", "measurement", "abstract") if clean(row.get(key))), None)
            entry = {
                "evidence_type": evidence_type,
                "provider": clean(row.get("provider")) or "provider not recorded",
                "title": title,
                "assertion_kind": "provider assertion",
                "scientific_time": iso(row.get(scientific_key)) if scientific_key else None,
                "provider_publication_time": iso(row.get("source_published_at") or row.get("published_at")),
                "ctas_receipt_time": iso(row.get("ctas_received_at")),
                "facility_or_instrument": facility,
                "summary": summary,
                "source_url": url,
                "stable_order": index,
            }
            entries.append({key: value for key, value in entry.items() if value is not None})

    for row in follow.get("classifications", []) + follow.get("classification_history", []):
        kind = "classification retraction" if row.get("retracted") else (
            "classification revision" if row.get("superseded") else "classification"
        )
        add([row], kind, "asserted_at", ("classification", "subtype"), ("citation_url",))
    add(follow.get("observations", []), "observation", "observed_at", ("summary", "band"), ("source_url",))
    add(follow.get("spectra", []), "spectrum", "observed_at", ("file_name", "provider_spectrum_id"), ("public_download_url", "source_url"))
    add(follow.get("messenger_signals", []), "messenger notice", "observed_at", ("alert_type", "messenger", "role"), ("source_url", "skymap_url"))
    add(follow.get("publications", []), "public report", None, ("title", "publication_type"), ("canonical_url",))
    add(follow.get("host_context", []), "host context", None, ("canonical_name", "queried_name"), ("source_url",))
    add(follow.get("catalog_counterparts", []), "positional catalog candidate", None, ("catalog_record_id", "catalog_description"), ("source_url",))
    add(follow.get("archive_products", []), "released archive product", "observed_at", ("product_filename", "provider_product_id"), ("public_download_url", "source_url"))

    if candidate.get("discovery_time"):
        entries.append({
            "evidence_type": "discovery record",
            "provider": candidate.get("discovery_survey") or "source not recorded",
            "title": candidate.get("name") or "Public candidate",
            "assertion_kind": "provider assertion",
            "scientific_time": candidate["discovery_time"],
            "summary": "Source-reported discovery metadata retained by CTAS.",
            "stable_order": 0,
        })
    if candidate.get("updated_at"):
        entries.append({
            "evidence_type": "CTAS catalog update",
            "provider": "CTAS",
            "title": "Public catalog record updated",
            "assertion_kind": "CTAS-derived summary",
            "ctas_receipt_time": candidate["updated_at"],
            "summary": "Catalog update time; not a provider scientific or publication timestamp.",
            "stable_order": 0,
        })

    def sort_key(entry: dict[str, Any]) -> tuple[str, str, int]:
        clock = (entry.get("scientific_time") or entry.get("provider_publication_time")
                 or entry.get("ctas_receipt_time") or "")
        return (clock, str(entry.get("evidence_type") or ""), -int(entry.get("stable_order", 0)))

    entries.sort(key=sort_key, reverse=True)
    for entry in entries:
        entry.pop("stable_order", None)
    return entries


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
            for key in (
                "observed_at", "asserted_at", "published_at", "source_published_at",
                "ctas_received_at", "queried_at",
            ):
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
    event_sql = (
        f"SELECT id, simulation, priority_factors, {cols} FROM events "
        f"WHERE COALESCE(simulation, 0) = 0 "
        f"ORDER BY COALESCE(updated_at, discovery_time) DESC"
    )
    rows = cur.execute(event_sql + (" LIMIT ?" if limit > 0 else ""), ((limit,) if limit > 0 else ())).fetchall()

    # Aliases for exactly the events we are publishing.
    ids = [r["id"] for r in rows]
    alias_map: dict[str, list[sqlite3.Row]] = {}
    CHUNK = 400
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        q = ",".join("?" * len(chunk))
        for a in cur.execute(
            f"SELECT event_id, provider, external_id, is_preferred FROM aliases "
            f"WHERE event_id IN ({q}) ORDER BY is_preferred DESC, provider, external_id",
            chunk,
        ):
            alias_map.setdefault(a["event_id"], []).append(a)

    classifications = rows_by_event(
        cur,
        ids,
        """
        SELECT ca.event_id, ca.provider, ca.classification, ca.subtype, ca.probability,
               ca.method, ca.asserted_at, ca.citation_url, ca.superseded, ca.retracted,
               ae.source_publication_time AS source_published_at,
               ae.received_at AS ctas_received_at
        FROM classification_assertions ca
        LEFT JOIN alert_envelopes ae ON ae.id = ca.envelope_id
        WHERE ca.event_id IN ({ids})
          AND ca.data_rights IN ('public', 'open')
          AND COALESCE(ca.superseded, 0) = 0
          AND COALESCE(ca.retracted, 0) = 0
        ORDER BY ca.event_id, ca.asserted_at DESC, ca.id
        """,
    )
    classification_history = rows_by_event(
        cur,
        ids,
        """
        SELECT ca.event_id, ca.provider, ca.classification, ca.subtype, ca.probability,
               ca.method, ca.asserted_at, ca.citation_url, ca.superseded, ca.retracted,
               ae.source_publication_time AS source_published_at,
               ae.received_at AS ctas_received_at
        FROM classification_assertions ca
        LEFT JOIN alert_envelopes ae ON ae.id = ca.envelope_id
        WHERE ca.event_id IN ({ids})
          AND ca.data_rights IN ('public', 'open')
          AND (COALESCE(ca.superseded, 0) = 1 OR COALESCE(ca.retracted, 0) = 1)
        ORDER BY ca.event_id, ca.asserted_at DESC, ca.id
        """,
    )
    observations = rows_by_event(
        cur,
        ids,
        """
        SELECT o.event_id, o.provider, o.observed_at, o.detection, o.telescope,
               o.observatory, o.instrument, o.pipeline, o.band, o.magnitude_system,
               o.magnitude, o.magnitude_error, o.flux, o.flux_error, o.flux_unit,
               o.limiting_magnitude, o.exposure_seconds, o.signal_to_noise,
               o.calibration, o.photometry_method, o.summary, o.source_url,
               ae.source_publication_time AS source_published_at,
               COALESCE(ae.received_at, o.created_at) AS ctas_received_at
        FROM observations o
        LEFT JOIN alert_envelopes ae ON ae.id = o.envelope_id
        WHERE o.event_id IN ({ids})
          AND o.data_rights IN ('public', 'open')
          AND COALESCE(o.superseded, 0) = 0
        ORDER BY o.event_id, o.observed_at DESC, o.id
        """,
    )
    signals = rows_by_event(
        cur,
        ids,
        """
        SELECT ms.event_id, ms.provider, ms.provider_signal_id, ms.observed_at,
               ms.messenger, ms.role, ms.instrument, ms.detection, ms.alert_type,
               ms.significance_sigma, ms.false_alarm_rate_hz, ms.sky_area_50_sq_deg,
               ms.sky_area_90_sq_deg, ms.distance_mpc, ms.distance_std_mpc,
               ms.measurement, ms.summary, ms.source_url, ms.skymap_url,
               ae.source_publication_time AS source_published_at,
               COALESCE(ae.received_at, ms.created_at) AS ctas_received_at
        FROM messenger_signals ms
        LEFT JOIN alert_envelopes ae ON ae.id = ms.envelope_id
        WHERE ms.event_id IN ({ids})
          AND ms.data_rights IN ('public', 'open')
          AND COALESCE(ms.simulation, 0) = 0
        ORDER BY ms.event_id, ms.observed_at DESC, ms.id
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
               p.published_at AS source_published_at,
               p.created_at AS ctas_received_at,
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
        SELECT s.event_id, s.provider, s.provider_spectrum_id, s.observed_at,
               s.telescope, s.instrument, s.configuration, s.wavelength_unit,
               s.flux_unit, s.resolution, s.calibration_state, s.public_download_url,
               s.file_name, s.file_checksum, s.source_url,
               ae.source_publication_time AS source_published_at,
               COALESCE(ae.received_at, s.created_at) AS ctas_received_at
        FROM spectra s
        LEFT JOIN alert_envelopes ae ON ae.id = s.envelope_id
        WHERE s.event_id IN ({ids})
          AND s.data_rights IN ('public', 'open')
        ORDER BY s.event_id, s.observed_at DESC, s.id
        """,
    )

    host_context = rows_by_event(
        cur,
        ids,
        """
        SELECT h.event_id, h.provider, h.queried_name, h.canonical_name,
               h.ra_deg, h.dec_deg, h.transient_offset_arcsec, h.redshift,
               h.redshift_error, h.physical_type, h.morphology, h.activity_type,
               h.overview_note, h.source_url, h.attribution, h.queried_at,
               ae.received_at AS ctas_received_at
        FROM host_context_assertions h
        LEFT JOIN alert_envelopes ae ON ae.id = h.envelope_id
        WHERE h.event_id IN ({ids}) AND h.data_rights IN ('public', 'open')
        ORDER BY h.event_id, h.queried_at DESC, h.id
        """,
    )

    catalog_counterparts = rows_by_event(
        cur,
        ids,
        """
        SELECT event_id, provider, catalog, catalog_record_id, catalog_description,
               ra_deg, dec_deg, separation_arcsec, position_error_arcsec,
               counterpart_type, photometry, motion, description, source_url,
               catalog_documentation_url, attribution, rights_basis, queried_at,
               queried_at AS ctas_received_at
        FROM catalog_crossmatch_assertions
        WHERE event_id IN ({ids}) AND data_rights IN ('public', 'open')
        ORDER BY event_id, queried_at DESC, separation_arcsec, id
        """,
    )

    archive_products = rows_by_event(
        cur,
        ids,
        """
        SELECT event_id, provider, provider_product_id, mission, observation_id,
               data_product_type, product_type, product_filename, description,
               instrument, filters, calibration_level, exposure_seconds,
               angular_distance_arcsec, public_download_url,
               product_documentation_url, source_url, attribution, rights_basis,
               queried_at, queried_at AS ctas_received_at
        FROM archive_product_assertions
        WHERE event_id IN ({ids}) AND data_rights IN ('public', 'open')
        ORDER BY event_id, queried_at DESC, id
        """,
    )

    latest_attempts = rows_by_event(
        cur,
        ids,
        """
        WITH ranked AS (
          SELECT sqa.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY sqa.event_id, sqa.source_id, sqa.query_kind
                   ORDER BY sqa.checked_at DESC, sqa.id DESC
                 ) AS attempt_rank
          FROM source_query_attempts sqa
          WHERE sqa.event_id IN ({ids})
        )
        SELECT ranked.event_id, ranked.source_id, ranked.query_kind,
               ranked.terminal_state, ranked.checked_at, ranked.next_eligible_at,
               ranked.error_code, ranked.evidence_url, sources.display_name,
               sources.documentation_url
        FROM ranked
        JOIN sources ON sources.id = ranked.source_id
        WHERE ranked.attempt_rank = 1
        ORDER BY ranked.event_id, sources.display_name, ranked.source_id
        """,
    )

    latest_source_attempts = {
        str(row["source_id"]): {
            "terminal_state": clean(row["terminal_state"]),
            "checked_at": iso(row["checked_at"]),
        }
        for row in cur.execute(
            """
            WITH ranked AS (
              SELECT source_id, terminal_state, checked_at,
                     ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY checked_at DESC, id DESC) AS rank
              FROM source_query_attempts
            )
            SELECT source_id, terminal_state, checked_at FROM ranked WHERE rank = 1
            """
        )
    }
    source_success_times = {
        str(row["source_id"]): iso(row["last_successful_at"])
        for row in cur.execute(
            """SELECT source_id, MAX(checked_at) AS last_successful_at
               FROM source_query_attempts WHERE terminal_state = 'data' GROUP BY source_id"""
        )
    }
    source_disposition_counts: dict[str, dict[str, int]] = {}
    for row in cur.execute(
        "SELECT source_id, terminal_state, COUNT(*) AS n FROM source_query_attempts GROUP BY source_id, terminal_state"
    ):
        source_disposition_counts.setdefault(str(row["source_id"]), {})[
            public_attempt_state(str(row["terminal_state"] or ""))
        ] = int(row["n"])

    counts = {
        "total_real_events": cur.execute(
            "SELECT COUNT(*) FROM events WHERE COALESCE(simulation,0)=0").fetchone()[0],
        "eligible_public_events": cur.execute(
            """SELECT COUNT(*) FROM events WHERE COALESCE(simulation,0)=0
               AND LOWER(COALESCE(status,'')) != 'merged'
               AND preferred_name IS NOT NULL AND TRIM(preferred_name) != ''"""
        ).fetchone()[0],
    }

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
            "documentation_url": (
                clean(row["documentation_url"])
                if str(clean(row["documentation_url"]) or "").startswith("https://") else None
            ),
            "protocol": clean(row["protocol"]),
            "authentication_requirement": clean(row["auth"]),
            "proprietary_risk": clean(row["proprietary_risk"]),
            "rate_or_cadence_limit": clean(row["rate_limit"]),
            "adapter_status": clean(row["adapter_status"]),
            "last_verified": clean(row["last_verified"]),
            "enabled": bool(row["enabled"]),
        }
        for row in cur.execute(
            """
            SELECT id, display_name, facility, data_types, mode, public_scope,
                   runtime_state, runtime_detail, last_message_at, lag_seconds,
                   documentation_url, protocol, auth, proprietary_risk, rate_limit,
                   adapter_status, last_verified, enabled
            FROM sources
            ORDER BY display_name, id
            """
        )
    ]

    con.close()

    source_by_id = {str(row["source"]): row for row in source_rows}

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
            if src == "discovery_survey" and str(v).strip().lower() in {"none", "unknown", "n/a", "null"}:
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
            entry = {
                "source_key": provider,
                "label": label,
                "designation": str(ext),
                "is_preferred": bool(a["is_preferred"]),
            }
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
            uniq.sort(key=lambda item: (item["source_key"] != "tns", not item["is_preferred"], item["label"], item["designation"]))
            linked_rows = [item for item in uniq if item.get("url")]
            if linked_rows:
                rec["links"] = linked_rows[:12]
            rec["designations"] = [
                {
                    "source_key": item["source_key"],
                    "source": item["label"],
                    "designation": item["designation"],
                    "is_preferred": item["is_preferred"],
                }
                for item in uniq[:24]
            ]

        follow_up = {
            "classifications": classifications.get(r["id"], []),
            "classification_history": classification_history.get(r["id"], []),
            "observations": observations.get(r["id"], []),
            "spectra": spectra.get(r["id"], []),
            "messenger_signals": signals.get(r["id"], []),
            "publications": publications.get(r["id"], []),
            "host_context": host_context.get(r["id"], []),
            "catalog_counterparts": catalog_counterparts.get(r["id"], []),
            "archive_products": archive_products.get(r["id"], []),
        }
        rec["follow_up_counts"] = {
            key: len(value) for key, value in follow_up.items()
        }
        rec["follow_up_total"] = sum(rec["follow_up_counts"].values())
        if any(follow_up.values()):
            rec["follow_up"] = follow_up

        evidence_by_provider: dict[str, dict[str, Any]] = {}
        for evidence_type, evidence_rows in follow_up.items():
            for evidence_row in evidence_rows:
                provider = str(evidence_row.get("provider") or "").strip().lower()
                if not provider:
                    continue
                ledger = evidence_by_provider.setdefault(provider, {"count": 0, "types": {}, "url": None})
                ledger["count"] += 1
                ledger["types"][evidence_type] = ledger["types"].get(evidence_type, 0) + 1
                if not ledger["url"]:
                    for url_key in (
                        "canonical_url", "citation_url", "public_download_url", "source_url",
                        "catalog_documentation_url", "product_documentation_url",
                    ):
                        url_value = clean(evidence_row.get(url_key))
                        if url_value and str(url_value).startswith("https://"):
                            ledger["url"] = url_value
                            break
        for alias in alias_map.get(r["id"], []):
            provider = str(alias["provider"] or "").strip().lower()
            if not provider or provider not in PUBLIC_LINKS:
                continue
            ledger = evidence_by_provider.setdefault(provider, {"count": 0, "types": {}, "url": None})
            ledger["count"] += 1
            ledger["types"]["designations"] = ledger["types"].get("designations", 0) + 1
            if provider == "tns":
                linked_id = tns_object_id(str(alias["external_id"] or ""))
                if linked_id:
                    ledger["url"] = PUBLIC_LINKS["tns"][1].format(id=linked_id)

        coverage = []
        attempted_sources = set()
        for attempt in latest_attempts.get(r["id"], []):
            raw_state = str(attempt.get("terminal_state") or "")
            state = public_attempt_disposition(raw_state, attempt.get("error_code"))
            source_id = str(attempt.get("source_id") or "")
            attempted_sources.add(source_id)
            retained = evidence_by_provider.get(source_id, {"count": 0, "types": {}, "url": None})
            row = {
                "source_id": source_id,
                "source_name": attempt.get("display_name"),
                "data_types_sought": [str(attempt.get("query_kind") or "bounded target query").replace("-", " ")],
                "disposition": state,
                "query_kind": attempt.get("query_kind"),
                "checked_at": iso(attempt.get("checked_at")),
                "retained_record_count": int(retained["count"]),
                "retained_record_types": retained["types"],
                "basis": "explicit-source-query",
                "documentation_url": attempt.get("documentation_url"),
            }
            if attempt.get("evidence_url") and str(attempt["evidence_url"]).startswith("https://"):
                row["query_evidence_url"] = attempt["evidence_url"]
            if retained.get("url"):
                row["object_specific_result_url"] = retained["url"]
            if attempt.get("next_eligible_at"):
                row["next_eligible_at"] = iso(attempt["next_eligible_at"])
            if attempt.get("error_code") and state in {
                "provider-failure", "temporarily-unavailable", "incomplete-result",
                "credentials-required", "ambiguous-identity", "rate-limited", "not-searched",
            }:
                row["reason_code"] = attempt["error_code"]
            coverage.append(row)
        for source_id, retained in sorted(evidence_by_provider.items()):
            if source_id in attempted_sources:
                continue
            source = source_by_id.get(source_id, {})
            coverage.append({
                "source_id": source_id,
                "source_name": source.get("label") or source_id,
                "data_types_sought": sorted(retained["types"]),
                "disposition": "active-returning-data",
                "checked_at": None,
                "retained_record_count": int(retained["count"]),
                "retained_record_types": retained["types"],
                "basis": "ingested-public-record",
                "documentation_url": source.get("documentation_url"),
                **({"object_specific_result_url": retained["url"]} if retained.get("url") else {}),
            })
        if coverage:
            rec["source_coverage"] = coverage

        rec["record_completeness"] = completeness_for(rec)
        rec["score_explanation"] = score_explanation_for(rec)
        timeline = timeline_for(rec)
        meaningful = next(
            (entry for entry in timeline if entry.get("assertion_kind") == "provider assertion"),
            timeline[0] if timeline else None,
        )
        if meaningful:
            rec["most_recent_meaningful_change"] = {
                key: meaningful[key] for key in (
                    "evidence_type", "provider", "title", "scientific_time",
                    "provider_publication_time", "ctas_receipt_time",
                ) if key in meaningful
            }

        def latest_time(rows_for_type: list[dict[str, Any]], keys: tuple[str, ...]) -> str | None:
            values = [
                iso(row.get(key)) for row in rows_for_type for key in keys if row.get(key)
            ]
            return max((value for value in values if value), default=None)

        rec["latest_classification_at"] = latest_time(
            follow_up["classifications"], ("asserted_at", "source_published_at", "ctas_received_at")
        )
        rec["latest_spectrum_at"] = latest_time(
            follow_up["spectra"], ("observed_at", "source_published_at", "ctas_received_at")
        )
        rec["latest_messenger_at"] = latest_time(
            follow_up["messenger_signals"], ("observed_at", "source_published_at", "ctas_received_at")
        )
        rec["latest_retraction_at"] = latest_time(
            [row for row in follow_up["classification_history"] if row.get("retracted")],
            ("asserted_at", "source_published_at", "ctas_received_at"),
        )
        channels = {
            str(row.get("messenger") or "").strip().lower()
            for row in follow_up["messenger_signals"] if str(row.get("messenger") or "").strip()
        }
        primary = str(rec.get("primary_messenger") or "").strip().lower()
        if primary and primary not in {"unknown", "multimessenger"}:
            channels.add(primary)
        rec["messenger_channels"] = sorted(channels)
        missing_labels = [
            component["label"] for component in rec["record_completeness"]["components"]
            if component["state"] in {"missing", "not-assessed"}
        ]
        known_bits = [
            rec.get("event_type") or "public transient candidate",
            rec.get("classification") or "unclassified",
            f"{rec['follow_up_total']} retained public evidence rows",
        ]
        rec["candidate_summary"] = {
            "why_in_ctas": "CTAS retains a rights-cleared public event record attributed to " + str(rec.get("discovery_survey") or rec.get("primary_messenger") or "a declared public source") + ".",
            "known": "; ".join(str(value) for value in known_bits),
            "missing": ", ".join(missing_labels) if missing_labels else "No applicable component in the public-record model is currently marked missing.",
            "non_claim": "Inclusion or positional context does not establish discovery, classification, counterpart, or host identity.",
        }

        out.append(rec)

    provider_counts: dict[str, dict[str, int]] = {}
    survey_counts: dict[str, int] = {}
    for candidate in out:
        survey = str(candidate.get("discovery_survey") or "").strip()
        if survey:
            survey_counts[survey] = survey_counts.get(survey, 0) + 1
        for evidence_type, rows_for_type in candidate.get("follow_up", {}).items():
            for row in rows_for_type:
                provider = str(row.get("provider") or "").strip().lower()
                if provider:
                    provider_counts.setdefault(provider, {})[evidence_type] = (
                        provider_counts.setdefault(provider, {}).get(evidence_type, 0) + 1
                    )
        for link_row in candidate.get("links", []):
            provider = "tns" if link_row.get("label") == "TNS" else ""
            if provider:
                provider_counts.setdefault(provider, {})["designations"] = (
                    provider_counts.setdefault(provider, {}).get("designations", 0) + 1
                )

    survey_rows = [
        {"survey": survey, "candidate_count": count}
        for survey, count in sorted(survey_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    counts["published"] = len(out)
    counts["skipped"] = skipped
    counts["catalog_truncated"] = limit > 0 and counts["eligible_public_events"] > len(out)
    counts["sources"] = source_rows
    counts["provider_counts"] = provider_counts
    counts["surveys"] = survey_rows
    counts["latest_source_attempts"] = latest_source_attempts
    counts["source_success_times"] = source_success_times
    counts["source_disposition_counts"] = source_disposition_counts
    return out, counts


BANNED_KEYS = {"id", "simulation", "priority_factors", "created_at",
               "token", "api_key", "secret", "password"}
RECURSIVE_BANNED_KEYS = BANNED_KEYS - {"id"}  # public schema component IDs are intentional
BANNED_TEXT = ("/Users/", ".codex", "BEGIN PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY")
CERTIFICATE_CLAIM = (
    "Automated static-catalog assurance; not peer review, scientific truth, "
    "classification validation, discovery authority, or a managed-service deployment claim."
)


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def recursive_safety_problems(value: Any, path: str = "root") -> list[str]:
    problems: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in RECURSIVE_BANNED_KEYS:
                problems.append(f"{path} contains banned key {key}")
            problems.extend(recursive_safety_problems(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            problems.extend(recursive_safety_problems(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        for marker in BANNED_TEXT:
            if marker in value:
                problems.append(f"{path} contains forbidden text {marker}")
    return problems


def git_blob(repo: Path, ref: str, path: str) -> bytes | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{path}"], cwd=repo, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None


def git_ref(repo: Path, ref: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", ref], cwd=repo, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def atomic_write(path: Path, raw: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)


def certificate_status(gates: list[dict[str, Any]]) -> str:
    return "certified-static-catalog" if gates and all(gate.get("passed") is True for gate in gates) else "not-certified"


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
        if "ctas_score" not in c:
            problems.append(f"candidate {i} has no CTAS score")
        else:
            try:
                score = float(c["ctas_score"])
            except (TypeError, ValueError):
                problems.append(f"candidate {i} has a nonnumeric CTAS score")
            else:
                if not 0 <= score <= 100:
                    problems.append(f"candidate {i} has CTAS score outside 0-100")
        has_ra = c.get("ra_deg") is not None
        has_dec = c.get("dec_deg") is not None
        if has_ra != has_dec:
            problems.append(f"candidate {i} has an incomplete coordinate pair")
        if has_ra:
            try:
                ra, dec = float(c["ra_deg"]), float(c["dec_deg"])
            except (TypeError, ValueError):
                problems.append(f"candidate {i} has nonnumeric coordinates")
            else:
                if not 0 <= ra < 360 or not -90 <= dec <= 90:
                    problems.append(f"candidate {i} has coordinates outside valid ranges")
        leaked = BANNED_KEYS & set(c)
        if leaked:
            problems.append(f"candidate {i} leaks {sorted(leaked)}")
    problems.extend(recursive_safety_problems(payload, "candidates-artifact"))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", required=True,
                    help="path to the CTAS SQLite database (opened read-only)")
    ap.add_argument("--output-dir", default="ctas/data")
    ap.add_argument("--limit", type=int, default=0,
                    help="optional development cap; 0 (default) exports the complete eligible catalog")
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

    generated_dt = datetime.now(UTC).replace(microsecond=0)
    generated_at = generated_dt.isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "export_checked_at": generated_at,
        "valid_until": (generated_dt + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        "latest_record_update": max(
            (row.get("updated_at") or row.get("discovery_time") or "" for row in candidates),
            default="",
        ) or None,
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
        "classification_history": total("classification_history"),
        "publications": total("publications"),
        "host_context": total("host_context"),
        "catalog_counterparts": total("catalog_counterparts"),
        "archive_products": total("archive_products"),
        "candidates_with_follow_up": sum(c.get("follow_up_total", 0) > 0 for c in candidates),
        "messengers": dict(sorted(messenger_counts.items())),
        "priority_bands": priority_bands,
    }
    payload["sources"] = [
        {**source, "record_counts": counts["provider_counts"].get(str(source["source"]), {})}
        for source in counts["sources"]
    ]
    survey_source_counts: dict[str, int] = {}
    for survey_row in counts["surveys"]:
        source_key = SURVEY_SOURCE_ALIASES.get(str(survey_row["survey"]))
        if source_key:
            survey_source_counts[source_key] = survey_source_counts.get(source_key, 0) + int(survey_row["candidate_count"])

    source_universe_rows = []
    for source in payload["sources"]:
        source_key = str(source["source"])
        record_counts = dict(source.get("record_counts", {}))
        if survey_source_counts.get(source_key):
            record_counts["candidates"] = survey_source_counts[source_key]
        represented = any(int(value or 0) > 0 for value in record_counts.values())
        attempt = counts["latest_source_attempts"].get(source_key)
        disposition_counts = counts["source_disposition_counts"].get(source_key, {})
        through = REPRESENTED_THROUGH.get(source_key)
        if represented and through:
            representation_state = "through-provider"
        elif represented:
            representation_state = "direct"
        elif disposition_counts:
            representation_state = "dispositions-only"
        else:
            representation_state = "none"
        row = {
            "source_key": source_key,
            "name": source["label"],
            "organization_or_facility": source.get("facility"),
            "source_family": source_family(source_key, source.get("data_types", [])),
            "primary_family": source_family(source_key, source.get("data_types", [])),
            "secondary_families": SECONDARY_SOURCE_FAMILIES.get(source_key, []),
            "data_types": source.get("data_types", []),
            "product_contracts": [],
            "topic_contracts": [],
            "access_mode": source.get("mode"),
            "protocol": source.get("protocol"),
            "documentation_url": source.get("documentation_url"),
            "rights_or_public_access_basis": source.get("public_scope"),
            "authentication_requirement": source.get("authentication_requirement"),
            "query_scope": source.get("mode"),
            "rate_or_cadence_limit": source.get("rate_or_cadence_limit"),
            "connector_implementation_state": source.get("adapter_status"),
            "implementation_state": implementation_state(source),
            "operational_state": public_source_state(source, represented, attempt),
            "representation_state": representation_state,
            "last_successful_response": source.get("last_message_at") or counts["source_success_times"].get(source_key),
            "last_successful_at": source.get("last_message_at") or counts["source_success_times"].get(source_key),
            "last_attempt_at": (attempt or {}).get("checked_at"),
            "public_record_counts": record_counts,
            "public_disposition_counts": disposition_counts,
            "represented_directly": representation_state == "direct",
            "represented_through": through,
            "known_limitations": source.get("proprietary_risk"),
            "last_verified": source.get("last_verified"),
            "contract_version": "1.0.0",
        }
        row["contract_checksum_sha256"] = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        source_universe_rows.append(row)

    existing_keys = {str(row["source_key"]) for row in source_universe_rows}
    catalog_product_counts = {"allwise": 0, "2mass": 0}
    for candidate in candidates:
        for catalog_row in candidate.get("follow_up", {}).get("catalog_counterparts", []):
            catalog = str(catalog_row.get("catalog") or "").lower()
            if "allwise" in catalog:
                catalog_product_counts["allwise"] += 1
            elif "2mass" in catalog or catalog == "fp_psc":
                catalog_product_counts["2mass"] += 1

    for (source_key, name, facility, family, data_types, access_mode, documentation_url,
         rights_basis, auth, implementation, operation, limitation) in ADDITIONAL_SOURCE_CONTRACTS:
        if source_key in existing_keys:
            continue
        represented_count = (
            survey_source_counts.get(source_key, 0)
            or catalog_product_counts.get(source_key, 0)
            or sum(int(value or 0) for value in counts["provider_counts"].get(source_key, {}).values())
        )
        through = REPRESENTED_THROUGH.get(source_key)
        representation_state = "through-provider" if represented_count else "none"
        row = {
            "source_key": source_key,
            "name": name,
            "organization_or_facility": facility,
            "source_family": family,
            "primary_family": family,
            "secondary_families": [],
            "data_types": data_types,
            "product_contracts": data_types,
            "topic_contracts": [],
            "access_mode": access_mode,
            "protocol": "provider-dependent",
            "documentation_url": documentation_url,
            "rights_or_public_access_basis": rights_basis,
            "authentication_requirement": auth,
            "query_scope": access_mode,
            "rate_or_cadence_limit": "Provider-dependent; CTAS must honor published limits.",
            "connector_implementation_state": implementation,
            "implementation_state": implementation,
            "operational_state": operation,
            "representation_state": representation_state,
            "last_successful_response": None,
            "last_successful_at": None,
            "last_attempt_at": None,
            "public_record_counts": ({
                "catalog_counterparts" if source_key in {"allwise", "2mass", "ned-cone"}
                else "candidates": represented_count
            } if represented_count else {}),
            "public_disposition_counts": {},
            "represented_directly": False,
            "represented_through": through,
            "known_limitations": limitation,
            "last_verified": "2026-08-23",
            "contract_version": "1.0.0",
        }
        row["contract_checksum_sha256"] = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        source_universe_rows.append(row)
        existing_keys.add(source_key)

    for survey_row in counts["surveys"]:
        survey = str(survey_row["survey"])
        if survey in SURVEY_SOURCE_ALIASES:
            continue
        source_key = "represented-survey-" + re.sub(r"[^a-z0-9]+", "-", survey.lower()).strip("-")
        if source_key in existing_keys:
            continue
        row = {
            "source_key": source_key,
            "name": survey,
            "organization_or_facility": survey,
            "source_family": "optical-and-time-domain-surveys",
            "primary_family": "optical-and-time-domain-surveys",
            "secondary_families": [],
            "data_types": ["source-attributed discovery metadata"],
            "product_contracts": ["public event metadata"],
            "topic_contracts": [],
            "access_mode": "represented event metadata",
            "protocol": "represented-through-public-event-provider",
            "documentation_url": None,
            "rights_or_public_access_basis": "Rights-cleared source-attributed discovery label in the public CTAS event record.",
            "authentication_requirement": "Not applicable to represented metadata",
            "query_scope": "No standalone source query is claimed.",
            "rate_or_cadence_limit": "Not applicable",
            "connector_implementation_state": "represented-through-provider",
            "implementation_state": "represented-through-provider",
            "operational_state": "represented-through-another-provider",
            "representation_state": "through-provider",
            "last_successful_response": None,
            "last_successful_at": None,
            "last_attempt_at": None,
            "public_record_counts": {"candidates": int(survey_row["candidate_count"])},
            "public_disposition_counts": {},
            "represented_directly": False,
            "represented_through": ["public-event-metadata"],
            "known_limitations": "CTAS retains the source label but has no standalone connector contract for this survey.",
            "last_verified": "2026-08-23",
            "contract_version": "1.0.0",
        }
        row["contract_checksum_sha256"] = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        source_universe_rows.append(row)
        existing_keys.add(source_key)
    source_universe_rows.sort(key=lambda row: (row["source_family"], row["name"], row["source_key"]))
    source_universe = {
        "schema": SOURCE_UNIVERSE_SCHEMA,
        "generated_at": payload["generated_at"],
        "claim_boundary": "Maintained CTAS source universe, not a claim that every astronomical source exists here or that every source was queried for every candidate.",
        "state_vocabulary": list(SOURCE_STATE_VOCABULARY),
        "implementation_state_vocabulary": list(IMPLEMENTATION_STATE_VOCABULARY),
        "representation_state_vocabulary": list(REPRESENTATION_STATE_VOCABULARY),
        "source_count": len(source_universe_rows),
        "family_count": len({row["source_family"] for row in source_universe_rows}),
        "survey_source_aliases": dict(sorted(SURVEY_SOURCE_ALIASES.items())),
        "sources": source_universe_rows,
    }
    source_universe_canonical = (json.dumps(source_universe, sort_keys=True, separators=(",", ":")) + "\n").encode()
    source_universe["artifact_checksum_sha256"] = hashlib.sha256(source_universe_canonical).hexdigest()
    payload["source_universe"] = {
        "schema": SOURCE_UNIVERSE_SCHEMA,
        "source_count": len(source_universe_rows),
        "artifact": "ctas/data/source-universe.json",
        "artifact_checksum_sha256": source_universe["artifact_checksum_sha256"],
    }
    payload["provider_statistics"] = [
        {"provider": provider, **record_counts}
        for provider, record_counts in sorted(
            counts["provider_counts"].items(),
            key=lambda item: (-sum(item[1].values()), item[0]),
        )
    ]
    payload["surveys"] = counts["surveys"]
    payload["catalog_content_checksum_sha256"] = hashlib.sha256(
        json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    problems = validate(payload)
    problems.extend(recursive_safety_problems(source_universe, "source-universe"))
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
        "export_checked_at": payload["export_checked_at"],
        "valid_until": payload["valid_until"],
        "latest_record_update": payload["latest_record_update"],
        "candidate_count": len(candidates),
        "cadence": "about every 2 minutes",
        "statistics": payload["statistics"],
        "sources": payload["sources"],
        "surveys": payload["surveys"],
        "source_universe": payload["source_universe"],
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
    source_universe_raw = (json.dumps(source_universe, indent=2, sort_keys=True) + "\n").encode()
    link_health_path = out / "link-health.json"
    try:
        link_health = json.loads(link_health_path.read_text()) if link_health_path.exists() else None
    except (OSError, json.JSONDecodeError):
        link_health = None
    link_health_raw = (
        (json.dumps(link_health, indent=2, sort_keys=True) + "\n").encode()
        if isinstance(link_health, dict) else b""
    )

    code_paths = (
        "ctas.html", "ctas/app.js", "ctas/catalog-model.js", "ctas/ctas.css", "scripts/export_ctas_snapshot.py",
        "scripts/check_ctas_links.py", "scripts/test_ctas_static.py", "scripts/test_ctas_catalog_model.js",
        "scripts/mirror_loop.sh", "scripts/publish_ctas.sh",
    )
    working_code = {
        path: (site_root / path).read_bytes()
        for path in code_paths if (site_root / path).exists()
    }
    head_code = {path: git_blob(site_root, "HEAD", path) for path in code_paths}
    origin_code = {path: git_blob(site_root, "origin/main", path) for path in code_paths}
    working_matches_head = all(
        path in working_code and head_code.get(path) == working_code[path]
        for path in code_paths
    )
    head_matches_origin = all(
        head_code.get(path) is not None and head_code.get(path) == origin_code.get(path)
        for path in code_paths
    )
    deployed_code = {
        path: head_code[path] if head_code.get(path) is not None else working_code[path]
        for path in code_paths if path in working_code
    }
    bound_files = {
        **deployed_code,
        "ctas/data/candidates.json": candidates_raw,
        "ctas/data/source-universe.json": source_universe_raw,
    }

    if link_health_raw:
        bound_files["ctas/data/link-health.json"] = link_health_raw

    def gate(gate_id: str, passed: bool, evidence: str) -> dict[str, Any]:
        return {"id": gate_id, "passed": bool(passed), "evidence": evidence}

    names = [str(candidate.get("name") or "") for candidate in candidates]
    required_contract = all(
        candidate.get("name") and "ctas_score" in candidate and
        isinstance(candidate.get("follow_up_counts"), dict) and
        isinstance(candidate.get("record_completeness"), dict)
        for candidate in candidates
    )
    coordinate_contract = all(
        ((candidate.get("ra_deg") is None and candidate.get("dec_deg") is None) or
         (candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None and
          0 <= float(candidate["ra_deg"]) < 360 and -90 <= float(candidate["dec_deg"]) <= 90))
        for candidate in candidates
    )
    follow_up_integrity = all(
        all(
            int(count) == len(candidate.get("follow_up", {}).get(key, []))
            for key, count in candidate.get("follow_up_counts", {}).items()
        ) and set(candidate.get("follow_up", {})) <= set(candidate.get("follow_up_counts", {})) and
        candidate.get("follow_up_total") == sum(candidate.get("follow_up_counts", {}).values())
        for candidate in candidates
    )
    expected_stats = {
        "public_candidates": len(candidates),
        "observations": total("observations"),
        "spectra": total("spectra"),
        "messenger_signals": total("messenger_signals"),
        "classifications": total("classifications"),
        "classification_history": total("classification_history"),
        "publications": total("publications"),
        "host_context": total("host_context"),
        "catalog_counterparts": total("catalog_counterparts"),
        "archive_products": total("archive_products"),
        "candidates_with_follow_up": sum(candidate.get("follow_up_total", 0) > 0 for candidate in candidates),
    }
    stats_integrity = all(payload["statistics"].get(key) == value for key, value in expected_stats.items())
    candidate_by_name = {candidate["name"]: candidate for candidate in candidates}
    recent_integrity = (
        len(payload["recent_stream"]) == min(20, len(candidates)) and
        all(row.get("name") in candidate_by_name for row in payload["recent_stream"])
    )
    provider_reproduced: dict[str, dict[str, int]] = {}
    survey_reproduced: dict[str, int] = {}
    for candidate in candidates:
        survey = candidate.get("discovery_survey")
        if survey:
            survey_reproduced[str(survey)] = survey_reproduced.get(str(survey), 0) + 1
        for evidence_type, evidence_rows in candidate.get("follow_up", {}).items():
            for evidence_row in evidence_rows:
                provider = str(evidence_row.get("provider") or "").strip().lower()
                if provider:
                    provider_reproduced.setdefault(provider, {})[evidence_type] = (
                        provider_reproduced.setdefault(provider, {}).get(evidence_type, 0) + 1
                    )
        for public_link in candidate.get("links", []):
            if public_link.get("source_key") == "tns":
                provider_reproduced.setdefault("tns", {})["designations"] = (
                    provider_reproduced.setdefault("tns", {}).get("designations", 0) + 1
                )
    published_provider_stats = {
        str(row["provider"]): {key: value for key, value in row.items() if key != "provider"}
        for row in payload["provider_statistics"]
    }
    published_surveys = {str(row["survey"]): int(row["candidate_count"]) for row in payload["surveys"]}
    source_keys = {str(row["source_key"]) for row in source_universe_rows}
    required_sources = {
        "tns", "rubin-fink", "rubin-alerce", "rubin-antares", "rubin-ampel",
        "rubin-lasair", "rubin-pitt-google", "rubin-lsst", "ztf", "atlas",
        "pan-starrs", "asas-sn", "goto", "gaia-alerts", "master", "blackgem",
        "wfst", "yse", "gcn", "gcn-circulars", "lvk-public-alerts", "icecube-gcn",
        "icecube-cascade-gcn", "fermi-gcn", "swift-gcn", "svom-gcn", "chime",
        "hawc-gcn", "nuem-gcn", "snews-gcn", "superk-gcn", "calet-gcn",
        "konus-gcn", "gecam-gcn", "maxi", "einstein-probe", "tns-public-reports",
        "wiserep", "aavso", "ztf-irsa", "aavso-aid", "mast", "heasarc",
        "eso-archive", "noirlab-archive", "gemini-archive", "cadc", "irsa",
        "ned", "simbad", "gaia-dr3", "allwise", "2mass", "tns-astronotes", "ads",
    }
    universe_without_checksum = dict(source_universe)
    universe_checksum = universe_without_checksum.pop("artifact_checksum_sha256", None)
    reproduced_universe_checksum = hashlib.sha256(
        (json.dumps(universe_without_checksum, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    universe_structure = (
        source_universe.get("schema") == SOURCE_UNIVERSE_SCHEMA and
        source_universe.get("source_count") == len(source_universe_rows) and
        len(source_keys) == len(source_universe_rows) and required_sources <= source_keys and
        universe_checksum == reproduced_universe_checksum and
        all(row.get("operational_state") in SOURCE_STATE_VOCABULARY for row in source_universe_rows) and
        all(row.get("implementation_state") in IMPLEMENTATION_STATE_VOCABULARY for row in source_universe_rows) and
        all(row.get("representation_state") in REPRESENTATION_STATE_VOCABULARY for row in source_universe_rows)
    )
    survey_closure = all(
        survey in SURVEY_SOURCE_ALIASES or
        ("represented-survey-" + re.sub(r"[^a-z0-9]+", "-", survey.lower()).strip("-")) in source_keys
        for survey in published_surveys
    )
    provider_closure = set(published_provider_stats) <= source_keys
    disposition_integrity = all(
        row.get("disposition") in SOURCE_STATE_VOCABULARY and
        isinstance(row.get("retained_record_count"), int) and row["retained_record_count"] >= 0 and
        row.get("source_id") in source_keys
        for candidate in candidates for row in candidate.get("source_coverage", [])
    )
    completeness_integrity = all(
        candidate.get("record_completeness") == completeness_for(candidate) and
        ((candidate.get("follow_up_total", 0) == 0) ==
         (candidate.get("record_completeness", {}).get("label") == "Event record only"))
        for candidate in candidates
    )
    timeline_integrity = all(
        bool(timeline_for(candidate)) and
        all(
            any(entry.get(key) for key in ("scientific_time", "provider_publication_time", "ctas_receipt_time"))
            and entry.get("assertion_kind") in {"provider assertion", "CTAS-derived summary"}
            for entry in timeline_for(candidate)
        )
        for candidate in candidates
    )
    tns_links = [
        link_row for candidate in candidates for link_row in candidate.get("links", [])
        if link_row.get("source_key") == "tns"
    ]
    tns_structure = bool(tns_links) and all(
        tns_object_id(str(row.get("designation") or "")) and
        row.get("url") == "https://www.wis-tns.org/object/" + str(tns_object_id(str(row["designation"])))
        for row in tns_links
    )
    generated = parse_utc(payload["generated_at"])
    valid_until = parse_utc(payload["valid_until"])
    freshness = bool(
        generated and valid_until and generated <= datetime.now(UTC) + timedelta(minutes=1)
        and valid_until >= datetime.now(UTC) + timedelta(minutes=5)
    )
    deployed_text = b"\n".join(deployed_code.values()).decode("utf-8", errors="replace")
    interpretation_tokens = (
        "follow-up ordering aid", "not a probability", "does not establish discovery",
        "record completeness", "not scientific importance",
    )
    ui_tokens = (
        "CTAS page contents", "ctas-sky-canvas", "data-sky-days=\"7\"",
        "data-sky-days=\"30\"", "data-preset=\"event-only\"", "renderDetails(c)",
        "renderSourceUniverse", "renderTimeline", "source-universe.json", "keydown",
    )
    cadence_contract = (
        payload["cadence"] == "about every 2 minutes" and
        'EVERY="${CTAS_EVERY:-120}"' in deployed_text and 'sleep "$EVERY"' in deployed_text
    )
    link_health_current = bool(
        isinstance(link_health, dict) and
        link_health.get("schema") == "ctas.link-health@1.0.0" and
        link_health.get("catalog_content_checksum_sha256") == payload["catalog_content_checksum_sha256"] and
        link_health.get("structural_status") == "passed" and
        link_health.get("live_status") in {"passed", "degraded-provider-unavailable"}
    )

    gates = [
        gate("required-public-artifacts", all(path in bound_files for path in (
            "ctas.html", "ctas/app.js", "ctas/ctas.css", "ctas/data/candidates.json",
            "ctas/data/source-universe.json", "ctas/data/link-health.json",
        )), "HTML, JavaScript, CSS, candidate snapshot, source universe, and link-health artifact"),
        gate("catalog-population", bool(candidates) and not counts["catalog_truncated"] and len(candidates) == counts["eligible_public_events"] and len(names) == len(set(names)), f"{len(candidates)} complete eligible records; truncated={counts['catalog_truncated']}"),
        gate("candidate-public-contract", required_contract, f"{sum(bool(c.get('name')) and 'ctas_score' in c for c in candidates)}/{len(candidates)} identity and score records"),
        gate("coordinate-integrity", coordinate_contract, "coordinate pairs are complete/absent and within ICRS degree ranges"),
        gate("follow-up-count-integrity", follow_up_integrity, "candidate counts and totals reproduce all retained public arrays"),
        gate("public-statistics-integrity", stats_integrity, "headline, evidence, messenger, and priority totals reproduced"),
        gate("recent-stream-integrity", recent_integrity, "recent stream resolves to the complete published catalog"),
        gate("source-statistics-integrity", published_provider_stats == provider_reproduced, f"{len(published_provider_stats)} provider totals reproduced"),
        gate("survey-statistics-integrity", published_surveys == survey_reproduced, f"{len(published_surveys)} discovery-survey totals reproduced"),
        gate("public-export-safety", not problems, "recursive allowlist safety check across public candidate and source artifacts"),
        gate("snapshot-freshness", freshness, f"generated {payload['generated_at']}; valid until {payload['valid_until']}"),
        gate("two-minute-publication-contract", cadence_contract, "120-second mirror contract and current export heartbeat"),
        gate("source-universe-schema", universe_structure, f"{len(source_universe_rows)} unique versioned source contracts"),
        gate("source-and-survey-closure", provider_closure and survey_closure, f"providers={len(published_provider_stats)}; surveys={len(published_surveys)}"),
        gate("candidate-source-dispositions", disposition_integrity, "controlled dispositions and integer retained-record counts"),
        gate("record-completeness-reproducibility", completeness_integrity, "public components recompute independently of CTAS priority"),
        gate("unified-timeline-integrity", timeline_integrity, "retained timeline entries reproduce with distinct scientific, publication, and CTAS clocks"),
        gate("tns-link-structure", tns_structure, f"{len(tns_links)} canonical object-specific TNS links"),
        gate("representative-external-link-health", link_health_current, "structural checks pass; temporary remote failures are represented as degraded"),
        gate("interpretation-and-limitations", all(token in deployed_text for token in interpretation_tokens), "priority, completeness, discovery, and scientific-claim boundaries are public"),
        gate("browser-interaction-contract", all(token in deployed_text for token in ui_tokens), "full-catalog presets, source universe, timeline, weekly/monthly sky, table and keyboard interaction code present"),
        gate("deployed-code-binding", working_matches_head, "bound code bytes exactly match HEAD, not a dirty working tree"),
        gate("local-origin-code-alignment", head_matches_origin, "HEAD bound code bytes match origin/main; generated data declares a successor release"),
        gate("certification-publication-contract", True, "checksum-bound report is written to ctas/data/certification.json and included by the explicit publisher allowlist"),
        gate("exact-claim-boundary", CERTIFICATE_CLAIM.endswith("deployment claim."), CERTIFICATE_CLAIM),
    ]
    release_id = hashlib.sha256(
        b"".join(name.encode() + b"\0" + raw for name, raw in sorted(bound_files.items()))
    ).hexdigest()
    certificate = {
        "schema": "ctas.static-catalog-certification@2.0.0",
        "generated_at": payload["generated_at"],
        "valid_until": payload["valid_until"],
        "architecture": "local-python-to-static-github-pages",
        "claim_boundary": CERTIFICATE_CLAIM,
        "status": certificate_status(gates),
        "candidate_count": len(candidates),
        "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
        "content_release_id": release_id,
        "publication_relationship": {
            "kind": "generated-successor-to-origin-main",
            "base_commit": git_ref(site_root, "origin/main"),
            "local_head": git_ref(site_root, "HEAD"),
            "statement": "Generated public data are the declared next successor to the bound origin/main code; post-push live verification confirms public byte availability.",
        },
        "gates": gates,
        "files": {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in sorted(bound_files.items())},
    }
    canonical = (json.dumps(certificate, sort_keys=True, separators=(",", ":")) + "\n").encode()
    certificate["report_checksum_sha256"] = hashlib.sha256(canonical).hexdigest()
    status["static_catalog_assurance"] = {
        "status": certificate["status"],
        "schema": certificate["schema"],
        "report_checksum_sha256": certificate["report_checksum_sha256"],
        "valid_until": certificate["valid_until"],
        "content_release_id": certificate["content_release_id"],
    }
    status["artifacts"] = {
        "candidates": {"path": "ctas/data/candidates.json", "sha256": hashlib.sha256(candidates_raw).hexdigest()},
        "source_universe": {"path": "ctas/data/source-universe.json", "sha256": hashlib.sha256(source_universe_raw).hexdigest()},
        "link_health": {"path": "ctas/data/link-health.json", "sha256": hashlib.sha256(link_health_raw).hexdigest() if link_health_raw else None},
        "certification": {"path": "ctas/data/certification.json", "report_checksum_sha256": certificate["report_checksum_sha256"]},
    }
    status_raw = (json.dumps(status, indent=2, sort_keys=True) + "\n").encode()
    certificate_raw = (json.dumps(certificate, indent=2, sort_keys=True) + "\n").encode()
    atomic_write(out / "candidates.json", candidates_raw)
    atomic_write(out / "status.json", status_raw)
    atomic_write(out / "source-universe.json", source_universe_raw)
    atomic_write(out / "certification.json", certificate_raw)
    print(f"\nwrote {out/'candidates.json'}, {out/'status.json'}, {out/'source-universe.json'}, and {out/'certification.json'}")
    print(f"static assurance: {certificate['status']} ({sum(g['passed'] for g in gates)}/{len(gates)} gates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
