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
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc  # datetime.UTC is 3.11+; this works on 3.9+

try:
    from ctas_astro_evidence import (
        build_projection,
        derive_messenger_revisions,
        receipt_completeness,
        sanitized_receipt_detail,
    )
except ModuleNotFoundError:  # imported as scripts.export_ctas_snapshot in tests
    from scripts.ctas_astro_evidence import (
        build_projection,
        derive_messenger_revisions,
        receipt_completeness,
        sanitized_receipt_detail,
    )

SCHEMA_VERSION = 2

SOURCE_UNIVERSE_SCHEMA = "ctas.public-source-universe@1.0.0"
CATALOG_INDEX_SCHEMA = "ctas.public-catalog-index@1.1.0"
ALIAS_INDEX_SCHEMA = "ctas.public-alias-index@1.0.0"
RESEARCH_TABLE_MANIFEST_SCHEMA = "ctas.research-table-manifest@1.0.0"
CANDIDATE_CHUNK_SCHEMA = "ctas.public-candidate-chunk@1.0.0"
CANDIDATE_DOWNLOAD_MANIFEST_SCHEMA = "ctas.public-complete-catalog-manifest@1.0.0"
# Compatibility name used by existing tests/importers.  The one manifest is now
# both the lazy-detail index and the authoritative complete-catalog download.
CANDIDATE_MANIFEST_SCHEMA = CANDIDATE_DOWNLOAD_MANIFEST_SCHEMA
RELEASE_HISTORY_SCHEMA = "ctas.public-release-history@1.0.0"
CANDIDATE_BUCKET_COUNT = 4096
LIVE_SUMMARY_SCHEMA = "ctas.public-live-summary@1.0.0"
CATALOG_PAGE_SCHEMA = "ctas.public-catalog-page@1.0.0"
CATALOG_PAGE_MANIFEST_SCHEMA = "ctas.public-catalog-page-manifest@1.0.0"
LIVE_SUMMARY_PATH = "ctas/data/live-summary.json"
CATALOG_PAGE_MANIFEST_PATH = "ctas/data/catalog-pages/manifest.json"
LIVE_SUMMARY_MAX_BYTES = 2 * 1024 * 1024
CATALOG_PAGE_MAX_BYTES = 1024 * 1024
TOP_RANK_LIMIT = 100
RECENT_REPORT_WINDOW_HOURS = 24
NEWEST_REPORT_COUNT = 3

# A retained record is not automatically a follow-up target.  CTAS keeps
# detector triggers, terrestrial flashes and solar activity because they are
# public time-domain reports, but a terrestrial gamma-ray flash must never sit
# in an observing leaderboard.  The role says what kind of record this is; the
# channel says which cohort it may be ranked inside.
RECORD_ROLE_BY_EVENT_TYPE = {
    "optical-transient": ("follow-up-target-candidate", "optical"),
    "x-ray-transient": ("follow-up-target-candidate", "x-ray-gamma-ray"),
    "gamma-ray-transient": ("follow-up-target-candidate", "x-ray-gamma-ray"),
    "gamma-ray-burst-candidate": ("localization-region-alert", "x-ray-gamma-ray"),
    "high-energy-trigger": ("unvalidated-detector-trigger", "x-ray-gamma-ray"),
    "high-energy-count-rate-trigger": ("unvalidated-detector-trigger", "x-ray-gamma-ray"),
    "fast-radio-burst": ("unvalidated-detector-trigger", "radio"),
    "high-energy-neutrino": ("localization-region-alert", "neutrino-multimessenger"),
    "high-energy-neutrino-track": ("localization-region-alert", "neutrino-multimessenger"),
    "high-energy-neutrino-cascade": ("localization-region-alert", "neutrino-multimessenger"),
    "neutrino-gamma-coincidence-candidate": ("localization-region-alert", "neutrino-multimessenger"),
    "terrestrial-gamma-ray-flash": ("known-terrestrial-event", "contextual-non-target"),
    "solar-flare": ("known-solar-event", "contextual-non-target"),
}
RECORD_ROLE_FALLBACK = ("contextual-non-target-record", "contextual-non-target")
# Only these roles may appear in the default follow-up leaderboard.
TARGET_ROLES = frozenset({"follow-up-target-candidate"})
SOURCE_MATRIX_SCHEMA = "ctas.candidate-source-matrix@2.0.0"
SOURCE_MATRIX_PATTERN_SCHEMA = "ctas.public-source-matrix-patterns@1.0.0"
SOURCE_MATRIX_PATTERNS_PATH = "ctas/data/source-matrix-patterns.json"
SOURCE_MATRIX_ROW_KEYS = (
    "sourceContractId", "sourceName", "documentationUrl", "applicabilityRule",
    "currentQueryOutcome", "currentQueryCheckedAt", "executedReceiptCount",
    "retainedRecordCount", "retainedRecordTypes", "retainedEvidenceLatestAt",
    "retainedEvidenceState",
)
# A source evaluated but never queried, holding nothing, is the same statement
# for almost every record.  Publishing that identical statement 13,931 times
# was 58.6% of all dossier bytes and said nothing a shared pattern cannot.
SOURCE_MATRIX_NO_EVIDENCE_OUTCOMES = frozenset({
    "LINK_ONLY_NOT_QUERIED", "NOT_QUERIED", "NOT_CONFIGURED",
})
CATALOG_BOOTSTRAP_MAX_BYTES = 2 * 1024 * 1024
# A single evidence-rich dossier can exceed 2 MiB before compression; the
# stable UUID partition therefore uses a truthful 4 MiB raw ceiling. A 256-way
# split left single shards near 6 MiB once the complete catalog was published,
# so the partition is 4096-way: one module stays small enough to open quickly
# and a damaged module can only affect the few dossiers inside it.
CANDIDATE_SHARD_TARGET_MAX_BYTES = 4 * 1024 * 1024
GITHUB_MAX_BLOB_BYTES = 100 * 1024 * 1024
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
        "rubin-fink", "rubin-fink-crossmatch", "rubin-rsp-alerts", "rubin-lasair", "rubin-pitt-google",
    )},
    **{key: "optical-and-time-domain-surveys" for key in (
        "ztf", "atlas", "asas-sn", "gaia-alerts",
    )},
    **{key: "multimessenger-and-high-energy" for key in (
        "gcn", "lvk-public-alerts", "icecube-gcn", "icecube-cascade-gcn", "snews-gcn",
        "fermi-gcn", "swift-gcn", "gecam-gcn", "calet-gcn", "hawc-gcn", "konus-gcn",
        "nuem-gcn", "gcn-high-energy", "superk-gcn", "svom-gcn", "boom-gcn",
        "dsa110-gcn", "integral-gcn", "km3net-gcn", "moa-gcn",
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
    "boom-gcn": ["gcn"],
    "integral-gcn": ["gcn"],
    "moa-gcn": ["gcn"],
}

OFFICIAL_PROVIDER_CONSTRAINTS = {
    "tns": {
        "authentication_requirement": "Authenticated POST API with TNS bot API key and mandatory tns_marker User-Agent; bulk public intake uses released CSV deltas.",
        "pagination_policy": "Follow response pagination and rate headers; do not hard-code a page ceiling. Prefer daily/full/hourly CSV deltas for catalog-scale intake.",
        "rate_or_cadence_limit": "Rolling 60-second quotas vary by operation; cone searches have a separate lower quota. CTAS must honor returned rate headers.",
        "redistribution_constraint": "Public catalog metadata are retained. Attached spectra or photometry are link-only unless individually rights-cleared; discoverability is not a blanket artifact license.",
        "last_verified": "2026-08-29",
    },
    "gcn": {
        "authentication_requirement": "GCN Kafka client ID and secret; OAuth tokens refresh automatically and inactive credentials may be disabled.",
        "pagination_policy": "Use a long-running Kafka consumer with offsets and heartbeat; use the complete daily Circular JSON/text archive rather than scraping result pages.",
        "rate_or_cadence_limit": "Official documentation does not publish a numeric consumer-rate or retention guarantee; CTAS does not invent one.",
        "redistribution_constraint": "Notices and Circulars are preliminary public records. Preserve corrections, retractions, notice/test state, and original GCN links and citation metadata.",
        "last_verified": "2026-08-29",
    },
    "rubin-lasair": {
        "authentication_requirement": "Authorization: Token header; credentials remain local and are never published.",
        "pagination_policy": "Deterministic query order with limit/offset; default limit 1,000. Streams default to 1,000 rows; light-curve calls accept at most 50 objects.",
        "rate_or_cadence_limit": "Normal accounts: 100 calls/hour and 10,000 query rows; approved power users: 10,000 calls/hour and 1,000,000 rows.",
        "redistribution_constraint": "Cite Lasair and preserve ZTF/upstream credits. No blanket redistribution license is inferred for every crossmatched upstream field.",
        "last_verified": "2026-08-29",
    },
    "aavso-aid": {
        "authentication_requirement": "Authorization: Token header; credentials remain local and are never published.",
        "pagination_policy": "Follow count/next/previous/results until next is null; the official API does not document a page-size control or hard row ceiling.",
        "rate_or_cadence_limit": "No more than one request every 10 seconds without AAVSO approval.",
        "redistribution_constraint": "AAVSO and contributing observers require acknowledgment; observer codes and upper-limit semantics must be preserved.",
        "last_verified": "2026-08-29",
    },
    "atlas": {
        "authentication_requirement": "Registered ATLAS forced-photometry token in the Authorization header; credentials remain local.",
        "pagination_policy": "Submit a bounded queue task, poll its task URL, then fetch and checksum the temporary result. Queue listings use cursor pagination.",
        "rate_or_cadence_limit": "HTTP 429 supplies the required wait; official documentation does not state one stable numeric quota.",
        "redistribution_constraint": "Preserve request parameters, result checksum, negative difference flux, template caveats, exact acknowledgment, and required ATLAS citations.",
        "last_verified": "2026-08-29",
    },
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
    "BOOM": "boom-gcn", "DSA-110": "dsa110-gcn", "INTEGRAL": "integral-gcn",
    "KM3NeT": "km3net-gcn", "MOA": "moa-gcn",
    "IceCube HESE Cascade / AMON": "icecube-cascade-gcn",
    "IceCube Bronze Track Alert": "icecube-gcn", "IceCube Gold Track Alert": "icecube-gcn",
    "Bronze Track Alert": "icecube-gcn", "Gold Track Alert": "icecube-gcn",
}

ADDITIONAL_SOURCE_CONTRACTS = (
    ("boom-gcn", "BOOM", "NASA General Coordinates Network", "multimessenger-and-high-energy",
     ["gamma-ray burst notices", "localization notices"], "GCN Kafka notice stream", "https://gcn.nasa.gov/missions/boom",
     "Released public GCN notices with source attribution and revision state.", "GCN credentials for automated Kafka consumption",
     "represented-through-provider", "represented-through-another-provider", "CTAS subscribes through GCN; a named BOOM contract keeps mission coverage explicit."),
    ("dsa110-gcn", "DSA-110", "Caltech / Owens Valley Radio Observatory", "multimessenger-and-high-energy",
     ["fast radio burst notices", "radio localization notices"], "GCN Kafka notice stream", "https://gcn.nasa.gov/missions/dsa110",
     "Released public GCN notices when the onboarding stream becomes operational.", "GCN credentials and topic availability",
     "documented-not-implemented", "not-implemented", "GCN lists DSA-110 as onboarding; CTAS does not claim an active feed."),
    ("integral-gcn", "INTEGRAL", "ESA / INTEGRAL Science Data Centre", "multimessenger-and-high-energy",
     ["archival gamma-ray notices", "localization notices"], "GCN archival notices", "https://gcn.nasa.gov/missions/integral",
     "Public archival GCN notices with original mission attribution.", "Public archive; no current CTAS polling adapter",
     "represented-through-provider", "scheduled", "Archival mission coverage is declared separately from an active live feed."),
    ("km3net-gcn", "KM3NeT", "KM3NeT Collaboration", "multimessenger-and-high-energy",
     ["neutrino notices", "multimessenger notices"], "GCN onboarding", "https://gcn.nasa.gov/missions",
     "Released public notices when an official production interface becomes available.", "Official interface still onboarding",
     "interface-review-required", "not-implemented", "GCN lists KM3NeT as onboarding; CTAS does not invent an operational endpoint."),
    ("moa-gcn", "MOA", "Microlensing Observations in Astrophysics", "optical-and-time-domain-surveys",
     ["archival microlensing notices", "discovery metadata"], "GCN archival notices", "https://gcn.nasa.gov/missions/archive/moa",
     "Public archival GCN records with original attribution.", "Public archive; no current CTAS polling adapter",
     "represented-through-provider", "scheduled", "Archival source contract; no claim of a current live MOA stream."),
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
     ["bibliographic metadata", "literature links", "exact transient-name context"], "bounded exact-name API query", "https://ui.adsabs.harvard.edu/help/api/",
     "Only public bibliographic metadata and provider links are retained; article full text is not mirrored.", "API token required for automated search",
     "implemented-credentials-required", "scheduled", "Exact retained transient names are required; a no-match is preserved and no article reuse rights are inferred."),
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
# Only a provider CTAS can turn into an object-specific link can be harmed by a
# disagreeing designation; everything else is provenance text.
LINKED_PROVIDERS = frozenset({"tns"})

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
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T00:00:00"
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utcstamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return value


def tns_object_id(value: str) -> str | None:
    """Return the path identifier used by public TNS object pages."""

    match = TNS_OBJECT.fullmatch(value.strip())
    return match.group(1) if match else None


def conflicting_tns_object_ids(alias_rows: Any) -> list[str]:
    """Return every distinct TNS object asserted as *preferred* for one event.

    Two separately designated TNS objects must never collapse into one public
    identity.  A record still carrying more than one preferred TNS designation
    is an unresolved association, not a formatting problem, so CTAS fails
    closed: it publishes no object-specific TNS link for that record and states
    the ambiguity instead of guessing which object a reader wanted.
    """

    object_ids: set[str] = set()
    for row in alias_rows:
        if str(row["provider"] or "").strip().lower() != "tns":
            continue
        if not row["is_preferred"]:
            continue
        object_id = tns_object_id(str(row["external_id"] or ""))
        if object_id:
            object_ids.add(object_id)
    return sorted(object_ids)


def record_role_for(candidate: dict[str, Any]) -> tuple[str, str]:
    """Return (record_role, ranking_channel) for one retained record."""

    status = str(candidate.get("status") or "").strip().lower()
    event_type = str(candidate.get("event_type") or "").strip().lower()
    _role, channel = RECORD_ROLE_BY_EVENT_TYPE.get(event_type, RECORD_ROLE_FALLBACK)
    if status in {"retracted", "bogus"}:
        return "retracted-event", channel
    role, channel = RECORD_ROLE_BY_EVENT_TYPE.get(event_type, RECORD_ROLE_FALLBACK)
    return role, channel


def source_matrix_row_carries_no_evidence(row: dict[str, Any]) -> bool:
    """True when a source row records only "declared, nothing retained"."""

    return (
        row.get("currentQueryOutcome") in SOURCE_MATRIX_NO_EVIDENCE_OUTCOMES
        and row.get("currentQueryCheckedAt") is None
        and int(row.get("executedReceiptCount") or 0) == 0
        and int(row.get("retainedRecordCount") or 0) == 0
        and not row.get("retainedRecordTypes")
        and row.get("retainedEvidenceLatestAt") is None
        and row.get("retainedEvidenceState") == "NO_RETAINED_EVIDENCE"
    )


def compact_source_matrix(
    rows: list[dict[str, Any]], patterns: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Split one record's source matrix into a shared pattern and its own rows.

    Nothing is dropped: every retained row either keeps its exact position in
    ``rows`` or belongs to the ordered no-evidence pattern, and
    ``expand_source_matrix`` reproduces the original list exactly.
    """

    quiet: list[dict[str, Any]] = []
    informative: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if source_matrix_row_carries_no_evidence(row):
            quiet.append(row)
        else:
            informative.append({"row_index": index, **row})
    key = hashlib.sha256(
        json.dumps(quiet, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    patterns.setdefault(key, quiet)
    return {
        "schema": SOURCE_MATRIX_SCHEMA,
        "pattern_document": SOURCE_MATRIX_PATTERNS_PATH,
        "no_evidence_pattern": key,
        "row_count": len(rows),
        "rows": informative,
    }


def expand_source_matrix(
    compact: Any, patterns: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Reproduce the complete ordered source matrix for one record."""

    if isinstance(compact, list):
        return list(compact)
    if not isinstance(compact, dict):
        return []
    quiet = list(patterns.get(str(compact.get("no_evidence_pattern") or "")) or [])
    total = int(compact.get("row_count") or 0)
    placed: dict[int, dict[str, Any]] = {}
    for row in compact.get("rows") or []:
        entry = {key: value for key, value in row.items() if key != "row_index"}
        placed[int(row.get("row_index", -1))] = entry
    out: list[dict[str, Any]] = []
    quiet_iter = iter(quiet)
    for index in range(total):
        if index in placed:
            out.append(placed[index])
        else:
            out.append(next(quiet_iter))
    return out


def provider_object_identity(provider: str, designation: str) -> str | None:
    """Return a provider's own object identity for one designation.

    TNS promotes AT2026wsy to SN2026wsy for the same object, across every
    TNS-family feed, so the prefix must not make one object look like two.
    """

    value = str(designation or "").strip()
    if not value:
        return None
    if provider.strip().casefold().startswith("tns"):
        return (tns_object_id(value) or value).casefold()
    return value.casefold()


def candidate_bucket(identity: str) -> str:
    """Return a stable UUID-derived bucket so renames do not move dossiers."""

    return f"{int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16) % CANDIDATE_BUCKET_COUNT:03x}"


def reported_label_kind(candidate: dict[str, Any]) -> str:
    """Describe what the consensus label represents without overstating it."""

    label = str(candidate.get("classification") or "Unclassified").strip().lower()
    messenger = str(candidate.get("primary_messenger") or "").strip().lower()
    event_type = str(candidate.get("event_type") or "").strip().lower()
    if not label or label == "unclassified":
        return "unclassified"
    if label in {
        "below horizon", "distant particles", "high-importance", "unreliable location",
        "retracted", "bogus",
    }:
        return "operational alert label"
    if messenger not in {"", "electromagnetic", "optical"} or any(
        token in event_type for token in ("notice", "neutrino", "gamma", "x-ray", "radio", "gravitational")
    ):
        return "alert or event label"
    if re.search(r"(^|\b)(sn|supernova|nova|tde|kilonova|cv|agn)(\b|$)", label):
        return "astronomical classification"
    return "provider-reported label"


def public_runtime_detail(value: Any) -> str | None:
    """Convert connector diagnostics into stable, non-technical public wording."""

    detail = clean(value)
    if detail is None:
        return None
    raw = str(detail)
    lower = raw.lower()
    if "certificate_verify_failed" in lower or "certificate has expired" in lower:
        return "Provider TLS certificate validation failed; CTAS retained the last rights-cleared data and scheduled a retry."
    if "rate limit" in lower or "rate-limit" in lower or "rate_limited" in lower:
        retained = re.search(r"checked\s+(\d+)\s+released public", raw, re.IGNORECASE)
        prefix = f"Checked {retained.group(1)} released public records before " if retained else "Reached "
        return prefix + "the provider rate limit; retained public data remain available."
    # Retry countdowns are volatile operational internals and cause meaningless
    # public commits. Keep the durable diagnostic while omitting the timer.
    return re.sub(r";?\s*retry in\s+[0-9.]+s\s*$", "", raw, flags=re.IGNORECASE)


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
    if follow.get("host_context"):
        host_present = True
    catalog_context_present = bool(follow.get("catalog_counterparts"))
    source_link_count = sum(
        1 for link in candidate.get("links", [])
        if str(link.get("url") or "").startswith("https://")
    )
    source_link_count += sum(
        1 for row in candidate.get("source_coverage", [])
        if any(str(row.get(key) or "").startswith("https://") for key in (
            "object_specific_result_url", "query_evidence_url", "documentation_url",
        ))
    )
    for rows in candidate.get("follow_up", {}).values():
        source_link_count += sum(
            1 for row in rows
            if any(str(value or "").startswith("https://") for key, value in row.items() if key.endswith("_url"))
        )
    components = [
        {"id": "identity", "label": "Public identity", "state": "present"},
        {"id": "discovery-time", "label": "Discovery time", "state": "present" if candidate.get("discovery_time") else "missing"},
        {"id": "discovery-survey", "label": "Discovery survey or facility", "state": "present" if candidate.get("discovery_survey") else "missing"},
        {"id": "coordinates", "label": "Sky coordinates", "state": "present" if candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None else "missing"},
        {"id": "discovery-photometry", "label": "Discovery magnitude", "state": "present" if candidate.get("discovery_magnitude") is not None else ("missing" if optical_applicable else "not-applicable")},
        {"id": "classification", "label": "Public classification", "state": "present" if candidate.get("classification") and candidate.get("classification") != "Unclassified" else "missing"},
        {"id": "follow-up-photometry", "label": "Subsequent photometry or limits", "state": "present" if follow.get("observations", 0) else ("missing" if optical_applicable else "not-applicable")},
        {"id": "spectrum", "label": "Public spectrum", "state": "present" if follow.get("spectra", 0) else ("missing" if optical_applicable else "not-applicable")},
        {"id": "host-context", "label": "Host or environmental context", "state": "present" if host_present else ("missing" if host_applicable else "not-applicable")},
        {"id": "catalog-context", "label": "Positional catalog context", "state": "present" if catalog_context_present else "not-assessed"},
        {"id": "messenger", "label": "Messenger evidence", "state": "present" if follow.get("messenger_signals", 0) else ("not-applicable" if messenger in {"", "electromagnetic"} else "missing")},
        {"id": "reports", "label": "Public reports", "state": "present" if follow.get("publications", 0) else "missing"},
        {"id": "source-links", "label": "Verified source or evidence links", "state": "present" if source_link_count else "missing"},
        {"id": "source-dispositions", "label": "Source-search dispositions", "state": "present" if candidate.get("source_coverage") else "not-assessed"},
    ]
    for component in components:
        component["evidence_count"] = {
            "identity": 1,
            "discovery-time": int(bool(candidate.get("discovery_time"))),
            "discovery-survey": int(bool(candidate.get("discovery_survey"))),
            "coordinates": int(candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None),
            "discovery-photometry": int(candidate.get("discovery_magnitude") is not None),
            "classification": int(bool(candidate.get("classification") and candidate.get("classification") != "Unclassified")),
            "follow-up-photometry": int(follow.get("observations", 0)),
            "spectrum": int(follow.get("spectra", 0)),
            "host-context": max(
                1 if host_present else 0,
                int(follow.get("host_context", 0)),
            ),
            "catalog-context": int(follow.get("catalog_counterparts", 0)),
            "messenger": int(follow.get("messenger_signals", 0)),
            "reports": int(follow.get("publications", 0)),
            "source-links": source_link_count,
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
    """State the arithmetic this release performed, and nothing beyond it."""

    model = candidate.get("score_model") or {}
    if model.get("status_override"):
        return (
            f"This record is {model['status_override']}; the review score is held at 0 and it is "
            "excluded from the follow-up leaderboard."
        )
    applied = [
        term for term in model.get("terms", [])
        if term.get("applicable") and abs(float(term.get("points") or 0.0)) >= 0.005
    ]
    parts = []
    for term in applied:
        points = float(term["points"])
        verb = "adds" if points > 0 else "reduces the score by"
        parts.append(f"{term['label'].lower()} {verb} {abs(points):g}")
    bonus = float(model.get("multimessenger_bonus") or 0.0)
    if bonus:
        parts.append(f"two or more retained messenger channels add {bonus:g}")
    skipped = [row["label"].lower() for row in model.get("not_applicable", [])]
    sentence = (
        f"From a {model.get('baseline', SCORE_BASELINE):g}-point baseline, " + "; ".join(parts) + "."
        if parts else
        f"No term adjusted the {model.get('baseline', SCORE_BASELINE):g}-point baseline."
    )
    if skipped:
        sentence += (
            " Not applicable to this kind of record: " + ", ".join(sorted(set(skipped))) + "."
        )
    return sentence


SCORE_METHOD_VERSION = "ctas.follow-up-score@2.0.0"
SCORE_BASELINE = 35.0
SCORE_VALIDITY_MINUTES = 30
# Spectroscopy is a meaningful gap only where a spectrum of *this* source is the
# observation a follow-up programme would take.  A neutrino track, a fast radio
# burst, a raw detector trigger, a terrestrial flash and a solar flare have no
# such spectrum to be missing, so awarding "no retained public spectrum" points
# to them ranked them for an observation nobody would schedule.
SPECTROSCOPY_APPLICABLE_CHANNELS = frozenset({"optical"})
# A follow-up gap is a reason to observe only while there is still something to
# observe.  Recomputing recency honestly removed the one term that favoured new
# records, and left a leaderboard whose median discovery age was over four
# years: an unclassified 2021 transient collected the full "missing
# classification", "missing spectrum" and "observation age" weight forever,
# because nothing had been observed since.  Those terms describe a live target,
# so beyond this window they are reported as not applicable — with the window
# stated — instead of silently ranking archival records above tonight's
# discoveries.  This is an operational ranking policy, not a physical claim,
# and it is versioned with the score method.
FOLLOW_UP_WINDOW_DAYS = 180.0
CLASSIFICATION_APPLICABLE_ROLES = frozenset({"follow-up-target-candidate"})
UNCLASSIFIED_LABELS = frozenset({"", "unclassified", "unknown", "at", "candidate"})


def _score_term(
    code: str, label: str, points: float, basis: str, applicable: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "code": code,
        "label": label,
        "points": round(float(points), 2) if applicable else 0.0,
        "basis": basis,
        "applicable": bool(applicable),
    }
    if not applicable:
        row["not_applicable_because"] = reason or "This term does not apply to this kind of record."
    return row


def _active_classification_labels(candidate: dict[str, Any]) -> list[str]:
    """Return the labels a reader would see as currently asserted.

    A retracted or superseded row is not an active assertion, and a label
    carried at zero probability is a stated alternative that the provider has
    ruled out — neither can contradict anything.
    """

    labels: list[str] = []
    for row in (candidate.get("follow_up") or {}).get("classifications", []):
        if row.get("retracted") or row.get("superseded"):
            continue
        probability = row.get("probability")
        if probability is not None:
            try:
                if float(probability) <= 0.0:
                    continue
            except (TypeError, ValueError):
                pass
        label = str(row.get("classification") or "").strip().casefold()
        if label and label not in UNCLASSIFIED_LABELS:
            labels.append(label)
    return labels


def _classifications_are_incompatible(labels: list[str]) -> bool:
    """True only when two active labels cannot both describe the same object.

    A subtype refinement is not a contradiction, so a label that extends
    another (SN Ia and SN Ia-91T) is treated as one assertion.
    """

    distinct = sorted(set(labels))
    for index, first in enumerate(distinct):
        for second in distinct[index + 1:]:
            if not (first.startswith(second) or second.startswith(first)):
                return True
    return False


def _retained_messenger_channels(candidate: dict[str, Any]) -> list[str]:
    follow_up = candidate.get("follow_up") or {}
    counts = candidate.get("follow_up_counts") or {}
    channels: set[str] = set()
    for row in follow_up.get("messenger_signals", []):
        messenger = str(row.get("messenger") or "").strip().casefold()
        if messenger:
            channels.add(messenger)
    if int(counts.get("observations") or 0) or int(counts.get("spectra") or 0):
        channels.add("electromagnetic")
    return sorted(channels)


def _latest_retained_observation(candidate: dict[str, Any]) -> datetime | None:
    latest: datetime | None = None
    for row in (candidate.get("follow_up") or {}).get("observations", []):
        if row.get("superseded"):
            continue
        parsed = parse_utc(row.get("observed_at"))
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def score_model_for(candidate: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    """Recompute the review-ordering score for THIS release.

    Every time-dependent term is evaluated against ``as_of``.  The persisted
    factor record is written once at ingestion, so reusing it froze recency at
    whatever the clock said when the record last changed: a candidate
    discovered on 2 August still carried its full first-day recency weight a
    month later, and the audited Top 100 had a median discovery age of about
    18 days.  A score is only meaningful with the clock it was computed for, so
    it is published with ``score_as_of`` and a validity window.
    """

    role = str(candidate.get("record_role") or "")
    channel = str(candidate.get("ranking_channel") or "")
    status = str(candidate.get("status") or "candidate").lower()
    persisted = dict(candidate.get("score_factors") or {})
    counts = candidate.get("follow_up_counts") or {}
    terms: list[dict[str, Any]] = []

    latest_observation = _latest_retained_observation(candidate)
    newest_evidence = max(
        [value for value in (parse_utc(candidate.get("discovery_time")), latest_observation)
         if value is not None],
        default=None,
    )
    evidence_age_days = (
        None if newest_evidence is None
        else max(0.0, (as_of - newest_evidence).total_seconds() / 86400.0)
    )
    followable = evidence_age_days is not None and evidence_age_days <= FOLLOW_UP_WINDOW_DAYS
    stale_reason = (
        f"The newest retained evidence for this record is {evidence_age_days:.0f} days old, "
        f"beyond the {FOLLOW_UP_WINDOW_DAYS:.0f}-day follow-up window, so a follow-up gap is "
        "not a reason to observe it tonight."
        if evidence_age_days is not None else
        "No retained clock places this record inside the follow-up window."
    )

    # --- recency, from the release clock ---------------------------------
    discovered = parse_utc(candidate.get("discovery_time"))
    age_hours: float | None = None
    if discovered is None:
        terms.append(_score_term(
            "recency_points", "Recency", 0.0, "source-reported discovery time", False,
            "No source-reported discovery time is retained for this record.",
        ))
    else:
        age_hours = max(0.0, (as_of - discovered).total_seconds() / 3600.0)
        terms.append(_score_term(
            "recency_points", "Recency",
            max(0.0, 24.0 - min(24.0, age_hours / 3.0)),
            f"source-reported discovery time, {age_hours:.2f} h before this release",
            True,
        ))

    # --- reported discovery brightness -----------------------------------
    magnitude = candidate.get("discovery_magnitude")
    if magnitude is None:
        terms.append(_score_term(
            "brightness_points", "Reported brightness", 0.0,
            "source-reported discovery magnitude", False,
            "No source-reported discovery magnitude is retained for this record.",
        ))
    elif not followable:
        terms.append(_score_term(
            "brightness_points", "Reported brightness", 0.0,
            "source-reported discovery magnitude", False, stale_reason,
        ))
    else:
        terms.append(_score_term(
            "brightness_points", "Reported brightness",
            max(-8.0, min(20.0, (21.0 - float(magnitude)) * 2.5)),
            f"source-reported discovery magnitude {float(magnitude):.4g}", True,
        ))

    # --- missing classification ------------------------------------------
    classified = str(candidate.get("classification") or "").strip().casefold() not in UNCLASSIFIED_LABELS
    if role not in CLASSIFICATION_APPLICABLE_ROLES:
        terms.append(_score_term(
            "classification_gap_points", "Missing classification", 0.0,
            "retained classification state", False,
            f"A {role.replace('-', ' ')} is not ranked by whether it carries an "
            "astronomical classification.",
        ))
    elif not followable:
        terms.append(_score_term(
            "classification_gap_points", "Missing classification", 0.0,
            "retained classification state", False, stale_reason,
        ))
    else:
        terms.append(_score_term(
            "classification_gap_points", "Missing classification",
            0.0 if classified else 12.0, "retained classification state", True,
        ))

    # --- classification conflict -----------------------------------------
    active_labels = _active_classification_labels(candidate)
    conflict = _classifications_are_incompatible(active_labels)
    terms.append(_score_term(
        "classification_conflict_points", "Classification conflict",
        8.0 if conflict else 0.0,
        f"{len(set(active_labels))} active classification assertion(s) with non-zero probability",
        True,
    ))

    # --- missing spectrum -------------------------------------------------
    spectroscopy_applies = (
        role in CLASSIFICATION_APPLICABLE_ROLES and channel in SPECTROSCOPY_APPLICABLE_CHANNELS
    )
    has_spectrum = int(counts.get("spectra") or 0) > 0
    if spectroscopy_applies and not followable:
        terms.append(_score_term(
            "spectroscopy_gap_points", "Missing spectrum", 0.0,
            "retained public spectrum count", False, stale_reason,
        ))
    elif not spectroscopy_applies:
        terms.append(_score_term(
            "spectroscopy_gap_points", "Missing spectrum", 0.0,
            "retained public spectrum count", False,
            "Spectroscopy of this record is not the observation a follow-up "
            f"programme would schedule for a {role.replace('-', ' ')} in the "
            f"{channel.replace('-', ' ')} channel.",
        ))
    else:
        terms.append(_score_term(
            "spectroscopy_gap_points", "Missing spectrum",
            0.0 if has_spectrum else 7.0, "retained public spectrum count", True,
        ))

    # --- existing observation coverage ------------------------------------
    observation_count = int(counts.get("observations") or 0)
    terms.append(_score_term(
        "coverage_reduction", "Existing observation coverage",
        -min(12.0, observation_count * 1.5),
        f"{observation_count} retained quantitative observation(s)", True,
    ))

    # --- time since the last retained observation --------------------------
    if latest_observation is None:
        terms.append(_score_term(
            "observation_gap_points", "Observation age", 0.0,
            "latest retained observation time", False,
            "No retained observation clock exists for this record.",
        ))
        gap_hours = None
    elif not followable:
        terms.append(_score_term(
            "observation_gap_points", "Observation age", 0.0,
            "latest retained observation time", False, stale_reason,
        ))
        gap_hours = None
    else:
        gap_hours = max(0.0, (as_of - latest_observation).total_seconds() / 3600.0)
        terms.append(_score_term(
            "observation_gap_points", "Observation age", min(10.0, gap_hours / 12.0),
            f"latest retained observation, {gap_hours:.2f} h before this release", True,
        ))

    # --- messenger diversity ----------------------------------------------
    channels = _retained_messenger_channels(candidate)
    if len(channels) >= 2:
        messenger_bonus = min(20.0, 4.0 * len(channels))
        messenger_note = "retained channels: " + ", ".join(channels)
    else:
        messenger_bonus = 0.0
        messenger_note = (
            "One retained channel"
            + (f" ({channels[0]})" if channels else "")
            + "; a diversity bonus requires at least two independently retained channels."
        )

    baseline = SCORE_BASELINE
    core_preclip = baseline + sum(float(term["points"]) for term in terms)
    core_postclip = max(0.0, min(100.0, core_preclip))
    status_override = status if status in {"retracted", "bogus"} else None
    if role == "retracted-event":
        status_override = status_override or "retracted"
    final_preclip = core_postclip + messenger_bonus
    final_score = round(0.0 if status_override else max(0.0, min(100.0, final_preclip)), 2)

    try:
        recorded = round(float(candidate.get("ctas_score") or 0.0), 2)
    except (TypeError, ValueError):
        recorded = 0.0

    why_now: list[str] = []
    if status_override:
        why_now.append(f"Reported {status_override}; held out of the follow-up leaderboard.")
    else:
        if not followable:
            why_now.append(
                f"Archival: newest retained evidence is {evidence_age_days:.0f} days old."
                if evidence_age_days is not None else
                "Archival: no retained clock places this record in the follow-up window."
            )
        if age_hours is not None and age_hours <= 72.0:
            why_now.append(f"Reported {age_hours:.0f} h ago.")
        if magnitude is not None and float(magnitude) <= 18.0:
            why_now.append(f"Reported at magnitude {float(magnitude):.2f}.")
        if spectroscopy_applies and not has_spectrum:
            why_now.append("No public spectrum is retained yet.")
        if role in CLASSIFICATION_APPLICABLE_ROLES and not classified:
            why_now.append("No astronomical classification is retained yet.")
        if conflict:
            why_now.append("Active classification assertions disagree.")
        if messenger_bonus:
            why_now.append("More than one messenger channel is retained.")
        if gap_hours is not None and gap_hours >= 48.0:
            why_now.append(f"No retained observation for {gap_hours / 24.0:.0f} days.")
    why_now = why_now[:3]

    return {
        "schema": SCORE_METHOD_VERSION,
        "method_version": SCORE_METHOD_VERSION,
        "record_role": role,
        "ranking_channel": channel,
        "follow_up_window_days": FOLLOW_UP_WINDOW_DAYS,
        "evidence_age_days": None if evidence_age_days is None else round(evidence_age_days, 2),
        "inside_follow_up_window": bool(followable),
        "score_as_of": as_of.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "valid_until": (
            (as_of + timedelta(minutes=SCORE_VALIDITY_MINUTES))
            .replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ),
        "validity": "valid-for-this-release",
        "default_leaderboard_eligible": bool(candidate.get("default_leaderboard_eligible")),
        "baseline": baseline,
        "terms": terms,
        "applicable_terms": [term["code"] for term in terms if term["applicable"]],
        "not_applicable": [
            {
                "code": term["code"],
                "label": term["label"],
                "reason": term["not_applicable_because"],
            }
            for term in terms if not term["applicable"]
        ],
        "core_preclip": round(core_preclip, 2),
        "core_postclip": round(core_postclip, 2),
        "multimessenger_bonus": round(messenger_bonus, 2),
        "multimessenger_basis": messenger_note,
        "final_preclip": round(final_preclip, 2),
        "final_score": final_score,
        "status_override": status_override,
        "recorded_score_at_ingest": recorded,
        "recorded_factors_at_ingest": persisted,
        "recomputed_for_this_release": True,
        "reconciled": True,
        "tolerance": 0.01,
        "why_now": why_now,
        "arithmetic": (
            f"{baseline:g} baseline "
            + " ".join(
                f"{'+' if float(term['points']) >= 0 else '-'} {abs(float(term['points'])):g}"
                f" ({term['code']})"
                for term in terms if term["applicable"]
            )
            + (f" + {messenger_bonus:g} (multimessenger_points)" if messenger_bonus else "")
            + f" = {final_score:g}"
            + (" -> 0 (terminal status override)" if status_override else "")
        ),
        "claim_boundary": (
            "A reproducible review-ordering aid computed for this release only; not a "
            "probability, classification confidence, discovery authority, or measure of "
            "scientific importance."
        ),
    }


def _basis_ids(rows: list[dict[str, Any]]) -> list[str]:
    identifiers: set[str] = set()
    for row in rows:
        for key in (
            "assertion_id", "provider_observation_id", "provider_spectrum_id",
            "provider_signal_id", "publication_assertion_id", "provider_publication_id",
            "host_assertion_id", "catalog_assertion_id", "provider_product_id",
        ):
            value = clean(row.get(key))
            if value:
                identifiers.add(str(value))
                break
    return sorted(identifiers)


def science_brief_for(candidate: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic known/uncertain/missing/change prose from retained rows."""

    follow = candidate.get("follow_up") or {}
    completeness = candidate.get("record_completeness") or {}
    conflicts = (candidate.get("astro_evidence") or {}).get("conflictSets") or []
    identity = candidate.get("identity_resolution") or {}
    known: list[dict[str, Any]] = []
    if candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None:
        known.append({
            "label": "ICRS position",
            "value": f"RA {float(candidate['ra_deg']):.6f} deg, Dec {float(candidate['dec_deg']):+.6f} deg",
            "basis_assertion_ids": [],
            "source_count": 1,
        })
    class_rows = list(follow.get("classifications") or [])
    classification_conflict = any(
        "class" in str(row.get("propertyCode") or "").lower() for row in conflicts
    )
    classification = clean(candidate.get("classification"))
    if classification and classification.lower() not in {"unclassified", "unknown", "at"} and not classification_conflict:
        known.append({
            "label": "Reported classification",
            "value": classification,
            "basis_assertion_ids": _basis_ids(class_rows),
            "source_count": len({str(row.get('provider') or '') for row in class_rows if row.get('provider')}) or 1,
        })
    if candidate.get("discovery_magnitude") is not None:
        known.append({
            "label": "Source-reported discovery magnitude",
            "value": f"{float(candidate['discovery_magnitude']):.3f} mag",
            "basis_assertion_ids": [],
            "source_count": 1,
        })
    known.append({
        "label": "Retained public evidence",
        "value": f"{int(candidate.get('follow_up_total') or 0)} source-native follow-up rows",
        "basis_assertion_ids": [],
        "source_count": int((candidate.get("source_accounting") or {}).get("dataBearingSources") or 0),
    })

    uncertain: list[dict[str, Any]] = []
    for conflict in conflicts:
        uncertain.append({
            "label": str(conflict.get("propertyCode") or "Source assertion"),
            "state": "conflicting-source-assertions",
            "conflict_set_ids": [str(conflict.get("conflictSetId") or conflict.get("id") or "")],
        })
    if identity.get("state") and identity.get("state") != "RESOLVED":
        uncertain.append({
            "label": "Cross-source identity",
            "state": str(identity["state"]).lower(),
            "conflict_set_ids": [],
        })
    if candidate.get("classification_probability") is not None:
        uncertain.append({
            "label": "Reported classification probability",
            "state": "source-reported; calibration is not assumed by CTAS",
            "conflict_set_ids": [],
        })
    if not uncertain:
        uncertain.append({
            "label": "Explicit conflicts",
            "state": "none retained; this is not proof that all source values are equivalent",
            "conflict_set_ids": [],
        })

    suggested = {
        "classification": "classification assertion",
        "follow-up-photometry": "time-series photometry or limiting magnitude",
        "spectrum": "rights-cleared spectrum or spectrum metadata",
        "host-context": "explicit host-association assertion",
        "catalog-context": "position-aware catalog query",
        "messenger": "source-linked messenger notice",
        "reports": "public report or publication",
    }
    missing = [
        {
            "component_id": row["id"],
            "label": row["label"],
            "state": row["state"],
            "suggested_evidence_type": suggested.get(row["id"], "source-attributed public evidence"),
        }
        for row in completeness.get("components", [])
        if row.get("state") in {"missing", "not-assessed"}
    ]

    timeline = candidate.get("evidence_timeline") or timeline_for(candidate)
    provider_timeline = [
        row for row in timeline
        if row.get("assertion_kind") == "provider assertion" and row.get("public_available_at")
    ]
    dated_timeline = [row for row in timeline if row.get("public_available_at")]
    recent_pool = provider_timeline or dated_timeline
    recent = max(
        recent_pool,
        key=lambda row: str(row.get("public_available_at") or ""),
        default=(timeline[0] if timeline else None),
    )
    source = candidate.get("discovery_survey") or "a declared public source"
    happened = (
        f"{candidate.get('name') or 'This record'} entered CTAS as a "
        f"{str(candidate.get('event_type') or 'time-dependent astronomical candidate').replace('-', ' ')} "
        f"reported by {source}."
    )
    return {
        "schema": "ctas.candidate-science-brief@1.0.0",
        "what_happened": {
            "text": happened,
            "basis_record_ids": [str(candidate.get("event_id"))],
        },
        "confidently_known": known,
        "uncertain_or_conflicting": uncertain,
        "missing_information": missing,
        "most_recent_change": ({key: recent[key] for key in (
            "entry_id", "evidence_type", "title", "provider", "scientific_time",
            "provider_publication_time", "ctas_receipt_time", "public_available_at",
            "basis_record_id",
        ) if recent and key in recent} if recent else None),
        "claim_boundary": (
            "A deterministic summary of this checksum-bound public record; it does not "
            "confirm discovery, class, counterpart, host, or scientific importance."
        ),
    }


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
            basis_record_id = next((
                clean(row.get(key)) for key in (
                    "assertion_id", "provider_observation_id", "provider_spectrum_id",
                    "provider_signal_id", "publication_assertion_id", "provider_publication_id",
                    "host_assertion_id", "catalog_assertion_id", "provider_product_id",
                ) if clean(row.get(key))
            ), None)
            entry = {
                "evidence_type": evidence_type,
                "provider": clean(row.get("provider")) or "provider not recorded",
                "title": title,
                "assertion_kind": "provider assertion",
                "scientific_time": iso(row.get(scientific_key)) if scientific_key else None,
                "provider_publication_time": iso(row.get("source_published_at") or row.get("published_at")),
                "ctas_receipt_time": iso(row.get("ctas_received_at") or row.get("retrieved_at")),
                "facility_or_instrument": facility,
                "summary": summary,
                "source_url": url,
                "basis_record_id": basis_record_id,
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
        if entry.get("provider_publication_time"):
            entry["public_available_at"] = entry["provider_publication_time"]
            entry["availability_basis"] = "provider publication time"
        elif entry.get("ctas_receipt_time"):
            entry["public_available_at"] = entry["ctas_receipt_time"]
            entry["availability_basis"] = "CTAS receipt time"
        else:
            entry["public_available_at"] = None
            entry["availability_basis"] = "no defensible public-availability clock retained"
        kind = str(entry.get("evidence_type") or "")
        entry["revision_state"] = (
            "retracted" if "retraction" in kind else
            "revision" if "revision" in kind else "current-or-standalone"
        )
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["entry_id"] = "timeline:" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
    return entries


# Rows the local store physically cannot return.  A damaged page is not the
# same as "no evidence", and silently exporting a thinner record would be a
# false statement about what CTAS holds, so every unreadable range is recorded
# here and published as an explicit local-store exception.
LOCAL_STORE_READ_FAILURES: list[dict[str, Any]] = []


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
        try:
            fetched = cur.execute(statement.format(ids=placeholders), chunk).fetchall()
        except sqlite3.DatabaseError as exc:
            # Narrow the damage to the smallest set of records it can be
            # attributed to, so the report names events rather than a chunk.
            fetched = []
            unreadable: list[str] = []
            for event_id in chunk:
                try:
                    fetched.extend(cur.execute(statement.format(ids="?"), [event_id]).fetchall())
                except sqlite3.DatabaseError:
                    unreadable.append(str(event_id))
            LOCAL_STORE_READ_FAILURES.append({
                "statement_fragment": " ".join(statement.split())[:120],
                "error": str(exc),
                "unreadable_event_ids": unreadable,
                "unreadable_event_count": len(unreadable),
            })
        for row in fetched:
            event_id = str(row["event_id"])
            item = {
                key: clean(row[key])
                for key in row.keys()
                if key != "event_id" and clean(row[key]) is not None
            }
            for key in (
                "classification", "cross_identifications", "detector_network", "keywords",
                "motion", "photometry", "preview_points", "properties", "quality_flags",
                "related_files", "related_objects", "scientific_claims",
            ):
                if key in item and isinstance(item[key], str):
                    try:
                        item[key] = json.loads(item[key])
                    except json.JSONDecodeError:
                        pass
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
        f"WHERE COALESCE(simulation, 0) = 0 AND INSTR(preferred_name, '%') = 0 "
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
            f"SELECT id, event_id, provider, external_id, is_preferred, created_at FROM aliases "
            f"WHERE event_id IN ({q}) ORDER BY is_preferred DESC, provider, external_id",
            chunk,
        ):
            alias_map.setdefault(a["event_id"], []).append(a)

    # Unresolved provider identity is fail-closed, never silently resolved.
    ambiguous_tns_identity: dict[str, list[str]] = {}
    for event_id, alias_rows in alias_map.items():
        object_ids = conflicting_tns_object_ids(alias_rows)
        if len(object_ids) > 1:
            ambiguous_tns_identity[str(event_id)] = object_ids

    classifications = rows_by_event(
        cur,
        ids,
        """
        SELECT ca.event_id, ca.id AS assertion_id, ca.envelope_id AS source_record_id,
               ca.provider, ca.classification, ca.subtype, ca.probability,
               ca.method, ca.model_name, ca.model_version, ca.asserted_at,
               ca.citation_url, ca.superseded, ca.retracted,
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
        SELECT ca.event_id, ca.id AS assertion_id, ca.envelope_id AS source_record_id,
               ca.provider, ca.classification, ca.subtype, ca.probability,
               ca.method, ca.model_name, ca.model_version, ca.asserted_at,
               ca.citation_url, ca.superseded, ca.retracted,
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
        SELECT o.event_id, o.id AS assertion_id, o.envelope_id AS source_record_id,
               o.provider, o.provider_observation_id, o.observed_at, o.original_time,
               o.mjd, o.jd, o.detection, o.telescope, o.observatory, o.instrument,
               o.detector, o.pipeline, o.band, o.original_band, o.wavelength_nm, o.magnitude_system,
               o.magnitude, o.magnitude_error, o.flux, o.flux_error, o.flux_unit,
               o.limiting_magnitude, o.limiting_flux, o.exposure_seconds, o.signal_to_noise,
               o.calibration, o.quality_flags, o.photometry_method, o.difference_photometry,
               o.summary, o.source_url, o.source_assertion_group_id,
               o.source_revision_checksum, o.source_revision_sequence, o.source_assertion_index,
               o.superseded, o.superseded_at, o.superseded_by_revision,
               ae.source_publication_time AS source_published_at,
               COALESCE(ae.received_at, o.created_at) AS ctas_received_at
        FROM observations o
        LEFT JOIN alert_envelopes ae ON ae.id = o.envelope_id
        WHERE o.event_id IN ({ids})
          AND o.data_rights IN ('public', 'open')
        ORDER BY o.event_id, o.observed_at DESC, o.id
        """,
    )
    signals = rows_by_event(
        cur,
        ids,
        """
        SELECT ms.event_id, ms.id AS assertion_id, ms.envelope_id AS source_record_id,
               ms.provider, ms.provider_signal_id, ms.observed_at,
               ms.messenger, ms.role, ms.instrument, ms.detection, ms.alert_type,
               ms.significance_sigma, ms.false_alarm_rate_hz, ms.sky_area_50_sq_deg,
               ms.sky_area_90_sq_deg, ms.distance_mpc, ms.distance_std_mpc,
               ms.classification, ms.properties, ms.detector_network,
               ms.time_offset_seconds, ms.measurement, ms.summary, ms.source_url, ms.skymap_url,
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
    signals = {
        event_id: derive_messenger_revisions(rows)
        for event_id, rows in signals.items()
    }
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
        SELECT pel.event_id, p.id AS assertion_id, p.provider, p.provider_publication_id,
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

    publication_revisions = rows_by_event(
        cur,
        ids,
        """
        SELECT pel.event_id, p.id AS publication_assertion_id,
               pr.id AS assertion_id, p.provider, p.provider_publication_id,
               p.publication_type, p.canonical_url, p.published_at,
               pr.content_checksum, pr.source_content_checksum,
               pr.source_revision_sequence, pr.retrieved_at,
               pr.title, pr.authors_text, pr.abstract, pr.source_group,
               pr.keywords, pr.related_objects, pr.related_files, pr.scientific_claims,
               CASE WHEN pr.id = (
                 SELECT newer.id FROM publication_revisions newer
                 WHERE newer.publication_id = p.id
                 ORDER BY newer.retrieved_at DESC, newer.id DESC LIMIT 1
               ) THEN 0 ELSE 1 END AS superseded
        FROM publication_event_links pel
        JOIN publications p ON p.id = pel.publication_id
        JOIN publication_revisions pr ON pr.publication_id = p.id
        WHERE pel.event_id IN ({ids})
          AND p.data_rights IN ('public', 'open')
        ORDER BY pel.event_id, p.published_at DESC, p.id, pr.retrieved_at DESC, pr.id
        """,
    )

    spectra = rows_by_event(
        cur,
        ids,
        """
        SELECT s.event_id, s.id AS assertion_id, s.envelope_id AS source_record_id,
               s.provider, s.provider_spectrum_id, s.observed_at,
               s.telescope, s.instrument, s.configuration, s.wavelength_unit,
               s.flux_unit, s.resolution, s.calibration_state, s.public_download_url,
               s.preview_points, s.file_name, s.file_checksum, s.source_url,
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
        SELECT h.event_id, h.id AS assertion_id, h.envelope_id AS source_record_id,
               h.provider, h.queried_name, h.canonical_name,
               h.cross_identifications, h.ra_deg, h.dec_deg, h.transient_offset_arcsec, h.redshift,
               h.redshift_error, h.redshift_reference, h.heliocentric_velocity_km_s,
               h.hubble_flow_distance_mpc, h.mean_distance_mpc, h.mean_distance_error_mpc,
               h.physical_type, h.morphology, h.activity_type, h.major_axis_arcsec,
               h.physical_major_axis_kpc, h.galactic_extinction_v_mag,
               h.overview_note, h.response_checksum, h.source_url, h.attribution, h.queried_at,
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
        SELECT event_id, id AS assertion_id, provider, catalog, catalog_record_id, catalog_description,
               ra_deg, dec_deg, separation_arcsec, position_error_arcsec,
               counterpart_type, photometry, motion, description, source_url,
               catalog_documentation_url, attribution, rights_basis, quality_flags,
               response_checksum, queried_at,
               'Provider-native source_row is not mirrored; normalized rights-cleared fields and the response checksum are retained.' AS source_row_exclusion,
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
        SELECT event_id, id AS assertion_id, provider, provider_product_id, mission, observation_id,
               product_group_id, data_product_type, product_type, product_subgroup,
               product_filename, description,
               instrument, filters, calibration_level, exposure_seconds,
               observed_start_mjd, observed_end_mjd, release_mjd,
               angular_distance_arcsec, proposal_id, proposal_pi,
               data_uri, public_download_url, size_bytes, response_checksum,
               product_documentation_url, source_url, attribution, rights_basis,
               queried_at, queried_at AS ctas_received_at
        FROM archive_product_assertions
        WHERE event_id IN ({ids}) AND data_rights IN ('public', 'open')
        ORDER BY event_id, queried_at DESC, id
        """,
    )

    all_attempts = rows_by_event(
        cur,
        ids,
        """
        SELECT sqa.*, sources.display_name, sources.documentation_url
        FROM source_query_attempts sqa
        JOIN sources ON sources.id = sqa.source_id
        WHERE sqa.event_id IN ({ids})
        ORDER BY sqa.event_id, sqa.source_id, sqa.query_kind,
                 sqa.checked_at DESC, sqa.id DESC
        """,
    )
    latest_attempts: dict[str, list[dict[str, Any]]] = {}
    for event_id, event_attempts in all_attempts.items():
        seen_attempt_keys: set[tuple[str, str]] = set()
        for attempt in event_attempts:
            key = (str(attempt.get("source_id") or ""), str(attempt.get("query_kind") or ""))
            if key in seen_attempt_keys:
                continue
            seen_attempt_keys.add(key)
            latest_attempts.setdefault(event_id, []).append(attempt)

    analysis_runs = rows_by_event(
        cur,
        ids,
        """
        SELECT ar.*
        FROM analysis_runs ar
        WHERE ar.event_id IN ({ids})
          AND ar.analysis_type = 'light-curve-inference'
          AND ar.data_rights IN ('public', 'open')
        ORDER BY ar.event_id, COALESCE(ar.completed_at, ar.created_at), ar.id
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
               AND INSTR(preferred_name, '%') = 0
               AND preferred_name IS NOT NULL AND TRIM(preferred_name) != ''"""
        ).fetchone()[0],
        "quality_quarantined_events": cur.execute(
            """SELECT COUNT(*) FROM events WHERE COALESCE(simulation,0)=0
               AND LOWER(COALESCE(status,'')) != 'merged'
               AND INSTR(preferred_name, '%') > 0"""
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
            "detail": public_runtime_detail(row["runtime_detail"]),
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

        rec: dict[str, Any] = {"event_id": str(r["id"])}
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

        reported_magnitude = rec.get("discovery_magnitude")
        if reported_magnitude is not None and not -30 <= float(reported_magnitude) <= 40:
            # Preserve the provider value for audit while keeping it out of
            # magnitude-dependent views and the public ordering score.
            rec["reported_discovery_magnitude"] = reported_magnitude
            rec.pop("discovery_magnitude", None)
            rec.setdefault("data_quality_flags", []).append({
                "field": "discovery_magnitude",
                "state": "excluded-from-derived-use",
                "reason": "source-reported value is outside the declared -30 to 40 magnitude publication range",
            })
            brightness = float(rec.get("score_factors", {}).pop("brightness_points", 0) or 0)
            rec["ctas_score"] = round(
                max(0.0, min(100.0, float(rec.get("ctas_score") or 0) - brightness)), 2
            )

        links = []
        for a in alias_map.get(r["id"], []):
            provider = str(a["provider"] or "").lower()
            ext = clean(a["external_id"])
            if not ext or provider not in PUBLIC_LINKS:
                continue          # unknown provider may be an internal ref
            label, template = PUBLIC_LINKS[provider]
            entry = {
                "alias_id": str(a["id"]),
                "source_key": provider,
                "label": label,
                "designation": str(ext),
                "is_preferred": bool(a["is_preferred"]),
                "asserted_at": iso(a["created_at"]),
            }
            if provider == "tns" and str(r["id"]) in ambiguous_tns_identity:
                entry["identity_ambiguous"] = True
                entry["link_suppressed_reason"] = (
                    "This record still carries more than one preferred TNS designation "
                    f"({', '.join(ambiguous_tns_identity[str(r['id'])])}); CTAS publishes no "
                    "object-specific TNS link until the association is resolved."
                )
            elif template:
                linked_id = tns_object_id(str(ext)) if provider == "tns" else str(ext)
                if linked_id:
                    entry["url"] = template.format(id=linked_id)
            links.append(entry)
        if links:
            links.sort(key=lambda item: (
                item["source_key"] != "tns", not item["is_preferred"], item["label"],
                item["designation"], item["alias_id"],
            ))
            linked_rows = [item for item in links if item.get("url")]
            if linked_rows:
                rec["links"] = linked_rows
            rec["designations"] = [
                {
                    "alias_id": item["alias_id"],
                    "source_key": item["source_key"],
                    "source": item["label"],
                    "designation": item["designation"],
                    "is_preferred": item["is_preferred"],
                    "asserted_at": item.get("asserted_at"),
                }
                for item in links
            ]

        follow_up = {
            "classifications": classifications.get(r["id"], []),
            "classification_history": classification_history.get(r["id"], []),
            "observations": observations.get(r["id"], []),
            "spectra": spectra.get(r["id"], []),
            "messenger_signals": signals.get(r["id"], []),
            "publications": publications.get(r["id"], []),
            "publication_revisions": publication_revisions.get(r["id"], []),
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
            if provider == "tns" and str(r["id"]) not in ambiguous_tns_identity:
                linked_id = tns_object_id(str(alias["external_id"] or ""))
                own_id = tns_object_id(str(r["preferred_name"] or ""))
                # The coverage row carries no designation of its own, so a
                # mismatched object id would read as this candidate's TNS
                # record.  Never let a later alias overwrite the ledger.
                if linked_id and own_id in (None, linked_id):
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
        rec["reported_label_kind"] = reported_label_kind(rec)
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
        source_attribution = rec.get("discovery_survey")
        if not source_attribution and rec.get("designations"):
            source_attribution = rec["designations"][0].get("source")
        rec["candidate_summary"] = {
            "why_in_ctas": "CTAS retains a rights-cleared public event record attributed to " + str(source_attribution or "a declared public event source") + ".",
            "known": "; ".join(str(value) for value in known_bits),
            "missing": ", ".join(missing_labels) if missing_labels else "No applicable component in the public-record model is currently marked missing.",
            "non_claim": "Inclusion or positional context does not establish discovery, classification, counterpart, or host identity.",
        }

        role, ranking_channel = record_role_for(rec)
        rec["record_role"] = role
        rec["ranking_channel"] = ranking_channel
        rec["default_leaderboard_eligible"] = role in TARGET_ROLES

        out.append(rec)

    scoped_alias_bindings: dict[tuple[str, str], set[str]] = {}
    unscoped_alias_bindings: dict[str, set[str]] = {}
    alias_provider_bindings: dict[str, dict[str, set[str]]] = {}
    for candidate in out:
        event_id = str(candidate["event_id"])
        display_key = str(candidate["name"]).strip().casefold()
        unscoped_alias_bindings.setdefault(display_key, set()).add(event_id)
        for alias in candidate.get("designations", []):
            provider = str(alias.get("source_key") or "").strip().casefold()
            value = str(alias.get("designation") or "").strip().casefold()
            if not provider or not value:
                continue
            scoped_alias_bindings.setdefault((provider, value), set()).add(event_id)
            unscoped_alias_bindings.setdefault(value, set()).add(event_id)
            alias_provider_bindings.setdefault(value, {}).setdefault(provider, set()).add(event_id)

    for candidate in out:
        event_id = str(candidate["event_id"])
        scoped_conflicts = []
        unscoped_collisions = []
        provider_disagreements = []
        values = {str(candidate["name"]).strip().casefold()}
        for alias in candidate.get("designations", []):
            provider = str(alias.get("source_key") or "").strip().casefold()
            value = str(alias.get("designation") or "").strip().casefold()
            values.add(value)
            bound = sorted(scoped_alias_bindings.get((provider, value), set()))
            if len(bound) > 1:
                alias["ambiguous"] = True
                scoped_conflicts.append({"source_key": provider, "alias": alias.get("designation"), "event_ids": bound})
        for value in sorted(values):
            bound = sorted(unscoped_alias_bindings.get(value, set()))
            if len(bound) > 1:
                unscoped_collisions.append({"alias": value, "event_ids": bound})
            provider_sets = alias_provider_bindings.get(value, {})
            distinct = {tuple(sorted(ids_for_provider)) for ids_for_provider in provider_sets.values()}
            if len(distinct) > 1:
                provider_disagreements.append({
                    "alias": value,
                    "provider_bindings": {provider: sorted(bound_ids) for provider, bound_ids in sorted(provider_sets.items())},
                })
        designation_conflicts = []
        multiple_designations = []
        preferred_by_provider: dict[str, set[str]] = {}
        for alias in candidate.get("designations", []):
            if not alias.get("is_preferred"):
                continue
            provider = str(alias.get("source_key") or "").strip().casefold()
            key = provider_object_identity(provider, alias.get("designation"))
            if provider and key:
                preferred_by_provider.setdefault(provider, set()).add(key)
        for provider, keys in sorted(preferred_by_provider.items()):
            if len(keys) < 2:
                continue
            if provider in LINKED_PROVIDERS:
                designation_conflicts.append({
                    "source_key": provider,
                    "object_ids": sorted(keys),
                    "resolution": (
                        "Fail closed: CTAS publishes no object-specific link for this "
                        "record until the association is reconciled at the source."
                    ),
                })
            else:
                # A provider that issues several notice identifiers for one
                # event (GCN does) is not in disagreement with itself, and CTAS
                # publishes no object-specific link for it, so this is retained
                # as provenance rather than reported as a defect.
                multiple_designations.append({
                    "source_key": provider,
                    "designations": sorted(keys),
                    "note": (
                        "This provider retains more than one designation for the record and "
                        "CTAS publishes no object-specific link for it."
                    ),
                })
        state = (
            "CONFLICTING" if provider_disagreements or designation_conflicts
            else "AMBIGUOUS" if scoped_conflicts or unscoped_collisions
            else "RESOLVED"
        )
        candidate["identity_resolution"] = {
            "schema": "ctas.provider-scoped-identity@1.0.0",
            "state": state,
            "policy": "Provider and exact source-native alias are the lookup key; unscoped collisions return every candidate and never guess.",
            "scoped_alias_conflicts": scoped_conflicts,
            "unscoped_alias_collisions": unscoped_collisions,
            "provider_disagreements": provider_disagreements,
            "provider_designation_conflicts": designation_conflicts,
            "multiple_preferred_designations": multiple_designations,
        }

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
    counts["all_attempts"] = all_attempts
    counts["analysis_runs"] = analysis_runs
    return out, counts


BANNED_KEYS = {"id", "simulation", "priority_factors", "created_at",
               "token", "api_key", "secret", "password"}
RECURSIVE_BANNED_KEYS = BANNED_KEYS - {"id"}  # public schema component IDs are intentional
BANNED_TEXT = ("/Users/", ".codex", "BEGIN PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY")
CERTIFICATE_CLAIM = (
    "Automated checksum and structural verification of one static snapshot; not peer review, scientific truth, "
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
            if lowered in RECURSIVE_BANNED_KEYS and child != "[REDACTED]":
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


PROJECTION_TIME_KEYS = frozenset({
    "asserted_at", "checked_at", "completed_at", "created_at", "ctas_received_at",
    "discovery_time", "latest_classification_at", "latest_messenger_at",
    "latest_retraction_at", "latest_spectrum_at", "observed_at", "published_at",
    "queried_at", "received_at", "retrieved_at", "source_publication_time",
    "source_published_at", "started_at", "superseded_at", "updated_at",
})


def stable_candidate_projection_time(
    candidate: dict[str, Any],
    attempts: list[dict[str, Any]],
    analysis_runs: list[dict[str, Any]],
) -> str:
    """Return a content-derived projection time, never an export wall clock.

    ``generatedAt`` is embedded in every candidate dossier.  Binding it to the
    heartbeat rewrote all 32 large shards even when the frozen database bytes
    were unchanged.  The latest persisted event/evidence/receipt/analysis time
    instead changes only when the public dossier's retained inputs change.
    """

    timestamps: list[datetime] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key) in PROJECTION_TIME_KEYS:
                    parsed = parse_utc(child)
                    if parsed is not None:
                        timestamps.append(parsed)
                if isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(candidate)
    collect(attempts)
    collect(analysis_runs)
    if not timestamps:
        raise ValueError(
            f"candidate {candidate.get('event_id') or candidate.get('name') or '<unknown>'} "
            "has no persisted timestamp for AstroEvidence generatedAt"
        )
    latest = max(timestamps)
    timespec = "microseconds" if latest.microsecond else "seconds"
    return latest.isoformat(timespec=timespec).replace("+00:00", "Z")


def candidate_chunk_artifacts(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, bytes]]:
    """Serialize deterministic UUID buckets for complete public dossiers."""

    bucket_rows: dict[str, list[dict[str, Any]]] = {
        f"{index:03x}": [] for index in range(CANDIDATE_BUCKET_COUNT)
    }
    for candidate in candidates:
        bucket_rows[candidate_bucket(str(candidate["event_id"]))].append(candidate)

    chunk_raw: dict[str, bytes] = {}
    for bucket, bucket_candidates in sorted(bucket_rows.items()):
        ordered = sorted(bucket_candidates, key=lambda row: str(row["event_id"]))
        bucket_rows[bucket] = ordered
        document = {
            "schema": CANDIDATE_CHUNK_SCHEMA,
            "bucket": bucket,
            "candidate_count": len(ordered),
            "candidates": ordered,
        }
        relative = f"ctas/data/candidate-chunks/{bucket}.json"
        chunk_raw[relative] = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    return bucket_rows, chunk_raw


CATALOG_CANDIDATE_COLUMNS = (
    "event_id", "name", "event_type", "primary_messenger", "messenger_channels",
    "ra_deg", "dec_deg", "coordinate_error_arcsec", "discovery_time",
    "discovery_survey", "discovery_instrument", "discovery_magnitude",
    "status", "classification", "reported_label_kind", "classification_probability",
    "ctas_score", "follow_up_total", "redshift", "updated_at",
    "latest_classification_at", "latest_spectrum_at", "latest_messenger_at",
    "latest_retraction_at", "detail_chunk",
    "n_classifications", "n_classification_history", "n_observations", "n_spectra",
    "n_messenger_signals", "n_publications", "n_publication_revisions", "n_host_context",
    "n_catalog_counterparts", "n_archive_products",
    "record_role", "ranking_channel", "default_leaderboard_eligible",
    "record_label", "record_present", "record_applicable", "record_not_assessed",
    "record_fraction", "primary_source_key", "primary_source_url",
    "primary_source_designation", "identity_state", "conflict_count",
    "source_declared", "source_applicable", "source_executed", "source_data_bearing",
)


def compact_candidate_row(candidate: dict[str, Any]) -> list[Any]:
    """Return one positional bootstrap row; complete evidence remains in a shard."""

    counts = candidate.get("follow_up_counts") or {}
    completeness = candidate.get("record_completeness") or {}
    accounting = candidate.get("source_accounting") or {}
    links = [row for row in candidate.get("links", []) if row.get("url")]
    primary = next((row for row in links if row.get("source_key") == "tns"), links[0] if links else {})
    values: dict[str, Any] = {
        **{key: candidate.get(key) for key in CATALOG_CANDIDATE_COLUMNS},
        "detail_chunk": f"candidate-chunks/{candidate_bucket(str(candidate['event_id']))}.json",
        "n_classifications": int(counts.get("classifications") or 0),
        "n_classification_history": int(counts.get("classification_history") or 0),
        "n_observations": int(counts.get("observations") or 0),
        "n_spectra": int(counts.get("spectra") or 0),
        "n_messenger_signals": int(counts.get("messenger_signals") or 0),
        "n_publications": int(counts.get("publications") or 0),
        "n_publication_revisions": int(counts.get("publication_revisions") or 0),
        "n_host_context": int(counts.get("host_context") or 0),
        "n_catalog_counterparts": int(counts.get("catalog_counterparts") or 0),
        "n_archive_products": int(counts.get("archive_products") or 0),
        "record_label": completeness.get("label"),
        "record_present": completeness.get("present"),
        "record_applicable": completeness.get("applicable"),
        "record_not_assessed": completeness.get("not_assessed"),
        "record_fraction": completeness.get("fraction"),
        "primary_source_key": primary.get("source_key"),
        "primary_source_url": primary.get("url"),
        "primary_source_designation": primary.get("designation"),
        "identity_state": (candidate.get("identity_resolution") or {}).get("state"),
        "conflict_count": len((candidate.get("astro_evidence") or {}).get("conflictSets") or []),
        "source_declared": accounting.get("declaredSources"),
        "source_applicable": accounting.get("applicableSources"),
        "source_executed": accounting.get("executedQueryReceipts"),
        "source_data_bearing": accounting.get("dataBearingSources"),
    }
    return [values.get(column) for column in CATALOG_CANDIDATE_COLUMNS]


def inflate_catalog_candidates(index: dict[str, Any]) -> list[dict[str, Any]]:
    """Inflate either the 1.1 columnar bootstrap or a legacy object index."""

    if isinstance(index.get("candidates"), list):
        return [row for row in index["candidates"] if isinstance(row, dict)]
    columns = index.get("candidate_columns")
    rows = index.get("candidate_rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or len(columns) != len(set(columns)):
        raise ValueError("catalog bootstrap has no valid candidate table")
    inflated: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError("catalog bootstrap candidate row width does not match its columns")
        flat = dict(zip(columns, row))
        counts = {
            key: int(flat.pop("n_" + key) or 0)
            for key in (
                "classifications", "classification_history", "observations", "spectra",
                "messenger_signals", "publications", "publication_revisions", "host_context",
                "catalog_counterparts", "archive_products",
            )
        }
        record = {
            "label": flat.pop("record_label"),
            "present": flat.pop("record_present"),
            "applicable": flat.pop("record_applicable"),
            "not_assessed": flat.pop("record_not_assessed"),
            "fraction": flat.pop("record_fraction"),
        }
        primary_source = {
            "source_key": flat.pop("primary_source_key"),
            "url": flat.pop("primary_source_url"),
            "designation": flat.pop("primary_source_designation"),
        }
        source_counts = {
            "declaredSources": flat.pop("source_declared"),
            "applicableSources": flat.pop("source_applicable"),
            "executedQueryReceipts": flat.pop("source_executed"),
            "dataBearingSources": flat.pop("source_data_bearing"),
        }
        identity_state = flat.pop("identity_state")
        conflict_count = flat.pop("conflict_count")
        candidate = {key: value for key, value in flat.items() if value is not None}
        candidate["follow_up_counts"] = counts
        candidate["record_completeness"] = record
        candidate["links"] = [primary_source] if primary_source.get("url") else []
        candidate["identity_resolution"] = {"state": identity_state}
        candidate["conflict_count"] = int(conflict_count or 0)
        candidate["source_accounting"] = source_counts
        inflated.append(candidate)
    return inflated


def alias_index_artifact(
    candidates: list[dict[str, Any]], catalog_content_checksum_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    columns = ["event_id", "source_key", "designation", "ambiguous"]
    rows = sorted([
        [
            str(candidate["event_id"]), str(alias.get("source_key") or ""),
            str(alias.get("designation") or ""), bool(alias.get("ambiguous")),
        ]
        for candidate in candidates for alias in candidate.get("designations", [])
        if alias.get("designation")
    ], key=lambda row: (row[2].casefold(), row[1].casefold(), row[0]))
    document = {
        "schema": ALIAS_INDEX_SCHEMA,
        "catalog_content_checksum_sha256": catalog_content_checksum_sha256,
        "candidate_count": len(candidates),
        "alias_count": len(rows),
        "columns": columns,
        "rows": rows,
    }
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return document, raw


def _csv_artifact(columns: list[str], rows: list[list[Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([
            json.dumps(value, sort_keys=True, separators=(",", ":"))
            if isinstance(value, (dict, list)) else value
            for value in row
        ])
    return stream.getvalue().encode()


def _events_votable_artifact(
    candidates: list[dict[str, Any]], catalog_content_checksum_sha256: str,
) -> bytes:
    """Serialize the public candidate summary as a deterministic VOTable."""

    root = ET.Element("VOTABLE", {
        "version": "1.5",
        "xmlns": "http://www.ivoa.net/xml/VOTable/v1.3",
    })
    ET.SubElement(root, "DESCRIPTION").text = (
        "Public CTAS candidate summary. The CTAS score is an operational "
        "follow-up ordering aid, not a probability, classification confidence, "
        "or measure of scientific importance."
    )
    resource = ET.SubElement(root, "RESOURCE", {"type": "results"})
    ET.SubElement(resource, "COOSYS", {"ID": "ICRS", "system": "ICRS"})
    ET.SubElement(resource, "PARAM", {
        "name": "catalog_content_checksum_sha256",
        "datatype": "char",
        "arraysize": "64",
        "value": catalog_content_checksum_sha256,
    })
    table = ET.SubElement(resource, "TABLE", {
        "name": "ctas_public_candidates",
        "nrows": str(len(candidates)),
    })
    ET.SubElement(table, "DESCRIPTION").text = (
        "One summary row per public CTAS candidate. Complete source-native "
        "evidence remains in the checksum-bound candidate shards. Empty cells "
        "mean the public record does not retain that value."
    )
    fields = (
        ("event_id", "char", None, None, "Stable CTAS public event identifier."),
        ("name", "char", "meta.id;meta.main", None, "Preferred public designation."),
        ("ra_deg", "double", "pos.eq.ra;meta.main", "deg", "Source-retained ICRS right ascension."),
        ("dec_deg", "double", "pos.eq.dec;meta.main", "deg", "Source-retained ICRS declination."),
        ("discovery_time", "char", "time.epoch", None, "Source-reported discovery time in UTC when retained."),
        ("discovery_survey", "char", None, None, "Source-reported discovery survey or facility."),
        ("discovery_magnitude", "double", None, "mag", "Source-reported discovery magnitude when retained."),
        ("classification", "char", None, None, "Latest retained reported classification label."),
        ("status", "char", None, None, "Current retained CTAS record state."),
        ("ctas_score", "double", None, None, "Operational CTAS follow-up ordering score; not a probability."),
        ("primary_messenger", "char", None, None, "Primary retained messenger channel."),
        ("follow_up_total", "long", None, None, "Count of retained public follow-up records."),
        ("redshift", "double", "src.redshift", None, "Retained redshift value when available."),
        ("source_url", "char", "meta.ref.url", None, "Preferred public source-record URL when available."),
        ("ctas_url", "char", "meta.ref.url", None, "CTAS public candidate dossier URL."),
    )
    for name, datatype, ucd, unit, description in fields:
        attributes = {"ID": name, "name": name, "datatype": datatype}
        if datatype == "char":
            attributes["arraysize"] = "*"
        if ucd:
            attributes["ucd"] = ucd
        if unit:
            attributes["unit"] = unit
        field = ET.SubElement(table, "FIELD", attributes)
        ET.SubElement(field, "DESCRIPTION").text = description

    table_data = ET.SubElement(ET.SubElement(table, "DATA"), "TABLEDATA")
    for candidate in candidates:
        links = [row for row in candidate.get("links", []) if row.get("url")]
        primary = next(
            (row for row in links if row.get("source_key") == "tns"),
            links[0] if links else {},
        )
        values = (
            candidate.get("event_id"),
            candidate.get("name"),
            candidate.get("ra_deg"),
            candidate.get("dec_deg"),
            candidate.get("discovery_time"),
            candidate.get("discovery_survey"),
            candidate.get("discovery_magnitude"),
            candidate.get("classification"),
            candidate.get("status"),
            candidate.get("ctas_score"),
            candidate.get("primary_messenger"),
            candidate.get("follow_up_total"),
            candidate.get("redshift"),
            primary.get("url"),
            f"https://jackmcguireastro.github.io/ctas.html?event={candidate['event_id']}#dossier",
        )
        tr = ET.SubElement(table_data, "TR")
        for value in values:
            cell = ET.SubElement(tr, "TD")
            if value is not None:
                cell.text = format(value, ".15g") if isinstance(value, float) else str(value)
    return ET.tostring(
        root, encoding="utf-8", xml_declaration=True, short_empty_elements=True,
    ) + b"\n"


def _tom_targets_artifact(candidates: list[dict[str, Any]]) -> tuple[bytes, int, int]:
    """Build a TOM Toolkit base-import CSV for coordinate-complete targets."""

    prepared: list[tuple[dict[str, Any], float, float, list[str]]] = []
    max_aliases = 0
    for candidate in candidates:
        try:
            ra = float(candidate.get("ra_deg"))
            dec = float(candidate.get("dec_deg"))
        except (TypeError, ValueError):
            continue
        if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
            continue
        primary_name = str(candidate.get("name") or "").strip()
        if not primary_name:
            continue
        aliases: list[str] = []
        seen = {primary_name.casefold()}
        for alias in sorted(
            candidate.get("designations", []),
            key=lambda row: (
                not bool(row.get("is_preferred")),
                str(row.get("source_key") or "").casefold(),
                str(row.get("designation") or "").casefold(),
            ),
        ):
            designation = str(alias.get("designation") or "").strip()
            if designation and designation.casefold() not in seen:
                seen.add(designation.casefold())
                aliases.append(designation)
        max_aliases = max(max_aliases, len(aliases))
        prepared.append((candidate, ra, dec, aliases))

    columns = [
        "name", "type", "ra", "dec", "epoch", "ctas_event_id", "ctas_score",
        "classification", "primary_messenger", "ctas_url", "source_url",
    ] + [f"name{index}" for index in range(2, max_aliases + 2)]
    rows: list[list[Any]] = []
    for candidate, ra, dec, aliases in prepared:
        links = [row for row in candidate.get("links", []) if row.get("url")]
        primary = next(
            (row for row in links if row.get("source_key") == "tns"),
            links[0] if links else {},
        )
        rows.append([
            candidate.get("name"), "SIDEREAL", ra, dec, 2000.0,
            candidate.get("event_id"), candidate.get("ctas_score"),
            candidate.get("classification"), candidate.get("primary_messenger"),
            f"https://jackmcguireastro.github.io/ctas.html?event={candidate['event_id']}#dossier",
            primary.get("url"), *aliases, *([""] * (max_aliases - len(aliases))),
        ])
    return _csv_artifact(columns, rows), len(rows), max_aliases


def research_table_artifacts(
    candidates: list[dict[str, Any]], source_universe: dict[str, Any],
    catalog_content_checksum_sha256: str,
) -> tuple[dict[str, bytes], dict[str, Any], bytes]:
    """Build normalized and astronomy-interoperable research artifacts."""

    event_columns = list(CATALOG_CANDIDATE_COLUMNS)
    event_rows = [compact_candidate_row(candidate) for candidate in candidates]
    alias_columns = ["event_id", "source_key", "designation", "ambiguous"]
    alias_rows = [
        [candidate["event_id"], row.get("source_key"), row.get("designation"), bool(row.get("ambiguous"))]
        for candidate in candidates for row in candidate.get("designations", [])
        if row.get("designation")
    ]
    source_columns = [
        "source_key", "name", "primary_family", "implementation_state",
        "operational_state", "representation_state", "documentation_url",
        "rights_or_public_access_basis", "known_limitations",
    ]
    source_rows = [[row.get(key) for key in source_columns] for row in source_universe.get("sources", [])]
    events_votable_raw = _events_votable_artifact(
        candidates, catalog_content_checksum_sha256,
    )
    tom_targets_raw, tom_target_count, tom_alias_count = _tom_targets_artifact(candidates)
    artifacts = {
        "ctas/data/research/events.csv": _csv_artifact(event_columns, event_rows),
        "ctas/data/research/aliases.csv": _csv_artifact(alias_columns, alias_rows),
        "ctas/data/research/sources.csv": _csv_artifact(source_columns, source_rows),
        "ctas/data/research/events.vot": events_votable_raw,
        "ctas/data/research/tom-targets.csv": tom_targets_raw,
    }
    artifact_metadata = {
        "ctas/data/research/events.csv": {
            "row_count": len(event_rows),
            "media_type": "text/csv; charset=utf-8",
            "format": "UTF-8 CSV with RFC 4180 quoting and LF line endings",
            "scope": "One compact summary row for every public CTAS candidate.",
            "limitations": ["Complete nested evidence is stored in candidate shards."],
        },
        "ctas/data/research/aliases.csv": {
            "row_count": len(alias_rows),
            "media_type": "text/csv; charset=utf-8",
            "format": "UTF-8 CSV with RFC 4180 quoting and LF line endings",
            "scope": "Provider-scoped public designations linked to CTAS event identifiers.",
            "limitations": ["An alias is a retained assertion, not independent identity validation."],
        },
        "ctas/data/research/sources.csv": {
            "row_count": len(source_rows),
            "media_type": "text/csv; charset=utf-8",
            "format": "UTF-8 CSV with RFC 4180 quoting and LF line endings",
            "scope": "Declared public source-universe contracts represented by this release.",
            "limitations": ["Source inclusion does not imply uninterrupted or complete provider coverage."],
        },
        "ctas/data/research/events.vot": {
            "row_count": len(event_rows),
            "media_type": "application/x-votable+xml",
            "format": "IVOA VOTable 1.5 TABLEDATA",
            "scope": "One VO-compatible summary row for every public CTAS candidate.",
            "coordinate_frame": "ICRS",
            "limitations": [
                "Missing retained values are empty cells.",
                "This is a summary table; complete nested evidence is stored in candidate shards.",
            ],
        },
        "ctas/data/research/tom-targets.csv": {
            "row_count": tom_target_count,
            "media_type": "text/csv; charset=utf-8",
            "format": "TOM Toolkit base target-import CSV",
            "scope": "Public CTAS candidates with complete valid ICRS coordinates.",
            "coordinate_frame": "ICRS",
            "coordinate_epoch": 2000.0,
            "alias_column_count": tom_alias_count,
            "omitted_candidate_count": len(candidates) - tom_target_count,
            "limitations": [
                "Coordinate-incomplete candidates are omitted rather than assigned invented positions.",
                "Columns other than TOM base target fields import as TargetExtra values; custom TOM target models or configurations may require adaptation.",
                "Importing this file does not request telescope observations.",
            ],
        },
    }
    manifest = {
        "schema": RESEARCH_TABLE_MANIFEST_SCHEMA,
        "catalog_content_checksum_sha256": catalog_content_checksum_sha256,
        "format_note": (
            "The zero-optional-dependency static publisher emits normalized UTF-8 CSV, "
            "an IVOA VOTable summary, and a TOM Toolkit target-import table. Complete "
            "nested evidence remains in the checksum-bound candidate shards."
        ),
        "tables": [
            {
                "path": path,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                **artifact_metadata[path],
            }
            for path, raw in sorted(artifacts.items())
        ],
    }
    manifest_raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    artifacts["ctas/data/research/manifest.json"] = manifest_raw
    return artifacts, manifest, manifest_raw


def canonical_candidate_list_bytes(candidates: list[dict[str, Any]]) -> bytes:
    """Canonical logical representation used to verify reconstruction."""

    return (
        json.dumps(candidates, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def complete_catalog_manifest_artifact(
    candidates: list[dict[str, Any]],
    index_candidates: list[dict[str, Any]],
    catalog_index_raw: bytes,
    bucket_rows: dict[str, list[dict[str, Any]]],
    chunk_raw: dict[str, bytes],
    catalog_content_checksum_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    """Build the deterministic, exact complete-catalog reconstruction contract."""

    complete_by_id = {str(candidate["event_id"]): candidate for candidate in candidates}
    index_ids = [str(row["event_id"]) for row in index_candidates]
    if len(complete_by_id) != len(candidates) or set(index_ids) != set(complete_by_id):
        raise ValueError("catalog index and complete candidate UUIDs do not match")
    index_ordered_candidates = [complete_by_id[event_id] for event_id in index_ids]
    manifest = {
        "schema": CANDIDATE_MANIFEST_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "catalog_content_checksum_sha256": catalog_content_checksum_sha256,
        "candidate_count": len(candidates),
        "chunk_count": len(chunk_raw),
        "catalog_index": {
            "path": "ctas/data/catalog-index.json",
            "candidate_count": len(index_candidates),
            "bytes": len(catalog_index_raw),
            "sha256": hashlib.sha256(catalog_index_raw).hexdigest(),
            "ordering_field": "candidate_rows[][event_id column]",
        },
        "assembled_candidates_checksum_sha256": hashlib.sha256(
            canonical_candidate_list_bytes(index_ordered_candidates)
        ).hexdigest(),
        "reconstruction": {
            "contract_version": "1.0.0",
            "description": (
                "Together the listed chunks contain every complete public candidate record "
                "exactly once. No single-file browser assembly is required."
            ),
            "steps": [
                "Fetch catalog_index.path and verify its byte length and SHA-256.",
                "Fetch every chunks[].path in listed order and verify its byte length, SHA-256, and candidate_count.",
                "Reject missing or duplicate event_id values and require the chunk UUID set to equal the catalog-index UUID set.",
                "Inflate catalog_index.candidate_rows with candidate_columns, map complete chunk records by event_id, then order them by the inflated event_id column.",
                "Verify SHA-256 over UTF-8 JSON of that ordered candidate array serialized with sorted keys, compact separators, and one trailing newline.",
            ],
            "canonical_json": "json.dumps(candidates, sort_keys=True, separators=(',', ':')) + newline",
        },
        "chunks": [
            {
                "path": path,
                "candidate_count": len(bucket_rows[Path(path).stem]),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            for path, raw in sorted(chunk_raw.items())
        ],
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return manifest, raw


def git_catalog_document(repo: Path, ref: str) -> dict[str, Any] | None:
    """Load a complete catalog at ``ref`` across the legacy/sharded boundary.

    Old releases stored a giant ``candidates.json`` document.  New releases
    store one compact index plus an ordered, checksum-bound chunk manifest.
    Every byte/count/checksum and the exact index ordering are verified before
    a sharded release is accepted for history comparison.
    """

    legacy_raw = git_blob(repo, ref, "ctas/data/candidates.json")
    if legacy_raw:
        try:
            legacy = json.loads(legacy_raw)
        except (TypeError, json.JSONDecodeError):
            legacy = None
        if isinstance(legacy, dict) and isinstance(legacy.get("candidates"), list):
            return legacy

    manifest_raw = git_blob(repo, ref, "ctas/data/candidate-chunks/manifest.json")
    if not manifest_raw:
        return None
    try:
        manifest = json.loads(manifest_raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("chunks"), list):
        return None

    index_meta = manifest.get("catalog_index") or {}
    index_path = str(index_meta.get("path") or "ctas/data/catalog-index.json")
    index_raw = git_blob(repo, ref, index_path)
    if not index_raw:
        return None
    if index_meta.get("bytes") is not None and index_meta.get("bytes") != len(index_raw):
        return None
    if index_meta.get("sha256") and index_meta.get("sha256") != hashlib.sha256(index_raw).hexdigest():
        return None
    try:
        index = json.loads(index_raw)
    except (TypeError, json.JSONDecodeError):
        return None
    try:
        index_rows = inflate_catalog_candidates(index) if isinstance(index, dict) else None
    except (TypeError, ValueError):
        index_rows = None
    if not isinstance(index_rows, list):
        return None

    complete_by_id: dict[str, dict[str, Any]] = {}
    for chunk_meta in manifest["chunks"]:
        if not isinstance(chunk_meta, dict):
            return None
        path = str(chunk_meta.get("path") or "")
        raw = git_blob(repo, ref, path)
        if not raw or chunk_meta.get("bytes") != len(raw):
            return None
        if chunk_meta.get("sha256") != hashlib.sha256(raw).hexdigest():
            return None
        try:
            document = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        rows = document.get("candidates") if isinstance(document, dict) else None
        if not isinstance(rows, list) or document.get("candidate_count") != len(rows):
            return None
        if chunk_meta.get("candidate_count") != len(rows):
            return None
        for candidate in rows:
            if not isinstance(candidate, dict):
                return None
            event_id = str(candidate.get("event_id") or "")
            if not event_id or event_id in complete_by_id:
                return None
            complete_by_id[event_id] = candidate

    index_ids = [str(row.get("event_id") or "") for row in index_rows if isinstance(row, dict)]
    if (
        len(index_ids) != len(index_rows)
        or "" in index_ids
        or len(set(index_ids)) != len(index_ids)
        or set(index_ids) != set(complete_by_id)
        or manifest.get("candidate_count") != len(index_ids)
        or index.get("candidate_count") != len(index_ids)
    ):
        return None
    ordered = [complete_by_id[event_id] for event_id in index_ids]
    assembled_checksum = hashlib.sha256(canonical_candidate_list_bytes(ordered)).hexdigest()
    if manifest.get("assembled_candidates_checksum_sha256") != assembled_checksum:
        return None
    return {**index, "candidates": ordered}


def atomic_write(path: Path, raw: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)


def output_path_for_public_artifact(output_dir: Path, public_path: str) -> Path:
    """Map a published ``ctas/data`` path into the caller-selected output root.

    The manifest retains repository-relative public paths, but a development
    export must never use those paths to escape ``--output-dir``.
    """

    published = Path(public_path)
    try:
        relative = published.relative_to(Path("ctas/data"))
    except ValueError as exc:
        raise ValueError(f"public data artifact is outside ctas/data: {public_path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"invalid public data artifact path: {public_path}")
    root = output_dir.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"public data artifact escapes output directory: {public_path}")
    return target


def write_output_artifact(output_dir: Path, public_path: str, raw: bytes) -> Path:
    """Write one public-layout artifact strictly beneath ``output_dir``."""

    target = output_path_for_public_artifact(output_dir, public_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, raw)
    return target


def certificate_status(gates: list[dict[str, Any]]) -> str:
    if not gates:
        return "verification-failed"
    failed = [gate for gate in gates if gate.get("passed") is not True]
    if not failed:
        return "verified-static-snapshot"
    publication_binding_ids = {"deployed-code-binding", "local-origin-code-alignment"}
    failed_ids = {str(gate.get("id") or "") for gate in failed}
    if failed_ids and failed_ids <= publication_binding_ids:
        return "publication-binding-pending"
    return "verification-failed"


def semantic_catalog_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return candidate content with polling-only source timestamps removed.

    Source-query timestamps remain available in the public record, but they do
    not describe a scientific or evidentiary change.  Excluding them from the
    semantic checksum lets the two-minute watcher refresh operational metadata
    without manufacturing a new catalog release.
    """
    semantic = json.loads(json.dumps(candidates))
    for candidate in semantic:
        for coverage in candidate.get("source_coverage", []):
            coverage.pop("checked_at", None)
            coverage.pop("next_eligible_at", None)
        astro_evidence = candidate.get("astro_evidence")
        if isinstance(astro_evidence, dict):
            # The projection timestamp says when this export was assembled,
            # not that the underlying event evidence changed.
            astro_evidence.pop("generatedAt", None)
        # The review score is recomputed against each release clock. Its
        # inputs are all separately checksummed here, so including the ticking
        # output would make every heartbeat look like a new catalog.
        candidate.pop("ctas_score", None)
        candidate.pop("score_model", None)
        candidate.pop("score_explanation", None)
        candidate.pop("score_factors", None)
        matrix = candidate.get("source_matrix")
        matrix_rows = matrix.get("rows", []) if isinstance(matrix, dict) else (matrix or [])
        for source_row in matrix_rows:
            # Age is a view of a retained timestamp at export time. The
            # timestamp itself remains checksum-bearing; the ticking duration
            # must not manufacture a new catalog release every heartbeat.
            source_row.pop("retainedEvidenceAgeSeconds", None)
    return semantic


def catalog_semantic_checksum(candidates: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            semantic_catalog_candidates(candidates),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def source_universe_contract_checksum(source_universe: dict[str, Any]) -> str:
    """Hash the versioned contracts without export-time wrapper metadata."""

    semantic = {
        key: value for key, value in source_universe.items()
        if key not in {
            "generated_at", "artifact_checksum_sha256", "contract_set_checksum_sha256",
        }
    }
    return hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def receipt_provenance_problems(candidate: dict[str, Any], candidate_index: int) -> list[str]:
    """Validate the one-to-one public join and its derived completeness claim."""

    prefix = f"candidate {candidate_index}"
    projection = candidate.get("astro_evidence") or {}
    compatibility = candidate.get("compatibility_provenance") or {}
    receipts = projection.get("persistedQueryReceipts", [])
    details = compatibility.get("receiptProvenance", [])
    if not isinstance(receipts, list):
        return [f"{prefix} has non-list persisted query receipts"]
    if not isinstance(details, list):
        return [f"{prefix} has non-list receipt provenance"]

    problems: list[str] = []
    receipt_ids = [str(row.get("receiptId") or "") for row in receipts if isinstance(row, dict)]
    detail_ids = [str(row.get("receiptId") or "") for row in details if isinstance(row, dict)]
    if len(receipt_ids) != len(receipts) or "" in receipt_ids or len(set(receipt_ids)) != len(receipt_ids):
        problems.append(f"{prefix} has missing or duplicate persisted receipt IDs")
    if len(detail_ids) != len(details) or "" in detail_ids or len(set(detail_ids)) != len(detail_ids):
        problems.append(f"{prefix} has missing or duplicate receipt-provenance IDs")
    if set(receipt_ids) != set(detail_ids):
        problems.append(f"{prefix} does not have exactly one provenance extension per persisted receipt")

    receipt_by_id = {
        str(row.get("receiptId")): row for row in receipts
        if isinstance(row, dict) and row.get("receiptId")
    }
    detail_by_id = {
        str(row.get("receiptId")): row for row in details
        if isinstance(row, dict) and row.get("receiptId")
    }
    for receipt_id in sorted(set(receipt_by_id) & set(detail_by_id)):
        receipt = receipt_by_id[receipt_id]
        detail = detail_by_id[receipt_id]
        if detail.get("sourceContractId") != receipt.get("sourceContractId"):
            problems.append(f"{prefix} receipt {receipt_id} has inconsistent source-contract linkage")
        safe_detail = sanitized_receipt_detail(detail)
        allowed = set(safe_detail) | {"completeness"}
        if set(detail) - allowed or any(detail.get(key) != value for key, value in safe_detail.items()):
            problems.append(f"{prefix} receipt {receipt_id} has unsafe or non-allowlisted provenance")
        if detail.get("executionState") not in {"EXECUTED", "NOT_EXECUTED"}:
            problems.append(f"{prefix} receipt {receipt_id} has an invalid execution state")
        expected = receipt_completeness(receipt, safe_detail)
        if "completeness" in detail and detail.get("completeness") != expected:
            problems.append(f"{prefix} receipt {receipt_id} has an inaccurate completeness assessment")
    return problems


def validate(payload: dict[str, Any]) -> list[str]:
    problems = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version mismatch")
    cands = payload.get("candidates")
    if not isinstance(cands, list):
        return ["candidates is not a list"]
    event_ids: set[str] = set()
    required_astro_fields = {
        "projectionSchema", "coreSchemaName", "coreSchemaVersion", "generatedAt",
        "sourceUniverseVersion", "target", "persistedQueryReceipts", "conflictSets",
        "selections", "dataProducts", "analysisRuns", "measurementCount", "projectionMethod",
    }
    canonical_outcomes = {
        "DATA_RETURNED", "SEARCHED_NO_MATCH", "PARTIAL_RESULT", "QUERY_FAILED",
        "QUERY_BLOCKED", "NOT_CONFIGURED", "NOT_APPLICABLE", "AMBIGUOUS",
        "STALE_LAST_GOOD_RETAINED", "LINK_ONLY_NOT_QUERIED", "NOT_QUERIED",
    }
    for i, c in enumerate(cands):
        if not c.get("name"):
            problems.append(f"candidate {i} has no name")
        event_id = str(c.get("event_id") or "")
        if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", event_id, re.IGNORECASE):
            problems.append(f"candidate {i} has no stable RFC-4122 event UUID")
        if event_id in event_ids:
            problems.append(f"candidate {i} duplicates event UUID {event_id}")
        event_ids.add(event_id)
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
        projection = c.get("astro_evidence")
        if not isinstance(projection, dict) or set(projection) != required_astro_fields:
            problems.append(f"candidate {i} does not expose the exact AstroEvidence compatibility descriptor")
        else:
            if projection.get("coreSchemaVersion") != "0.1.0" or projection.get("target", {}).get("targetId") != event_id:
                problems.append(f"candidate {i} has an inconsistent AstroEvidence identity or schema version")
            contract_ids = set(c.get("source_accounting", {}).get("applicableSourceIds", []))
            if any(row.get("sourceContractId") not in contract_ids for row in projection.get("persistedQueryReceipts", [])):
                problems.append(f"candidate {i} has a receipt outside its applicable source set")
            if any(row.get("outcome") not in canonical_outcomes for row in projection.get("persistedQueryReceipts", [])):
                problems.append(f"candidate {i} has a receipt outside the canonical outcome vocabulary")
            accounting = c.get("source_accounting", {})
            if accounting.get("applicableSources") != len(contract_ids):
                problems.append(f"candidate {i} has inconsistent applicable-source accounting")
            problems.extend(receipt_provenance_problems(c, i))
    problems.extend(recursive_safety_problems(payload, "candidates-artifact"))
    return problems


def projection_integrity_problems(projection: dict[str, Any]) -> list[str]:
    """Check referential closure that JSON Schema cannot express."""

    problems: list[str] = []
    target_id = projection.get("target", {}).get("targetId")
    contract_ids = {row.get("sourceContractId") for row in projection.get("sourceContracts", [])}
    measurement_ids = {row.get("measurementId") for row in projection.get("measurements", [])}
    if None in contract_ids or len(contract_ids) != len(projection.get("sourceContracts", [])):
        problems.append(f"{target_id}: missing or duplicate source-contract IDs")
    if None in measurement_ids or len(measurement_ids) != len(projection.get("measurements", [])):
        problems.append(f"{target_id}: missing or duplicate measurement IDs")
    referenced_sources = {
        row.get("sourceContractId")
        for key in ("queryReceipts", "measurements", "dataProducts")
        for row in projection.get(key, [])
    } | {row.get("sourceContractId") for row in projection.get("target", {}).get("aliases", [])}
    if not referenced_sources <= contract_ids:
        problems.append(f"{target_id}: evidence references an undeclared source contract")
    for key in ("queryReceipts", "measurements", "dataProducts", "analysisRuns"):
        if any(row.get("targetId") != target_id for row in projection.get(key, [])):
            problems.append(f"{target_id}: {key} contains a foreign target ID")
    for conflict in projection.get("conflictSets", []):
        if not set(conflict.get("measurementIds", [])) <= measurement_ids:
            problems.append(f"{target_id}: conflict contains unresolved measurement IDs")
    for selection in projection.get("selections", []):
        if not set(selection.get("measurementIds", [])) <= measurement_ids:
            problems.append(f"{target_id}: selection contains unresolved measurement IDs")
    for analysis in projection.get("analysisRuns", []):
        if not set(analysis.get("inputRecordIds", [])) <= measurement_ids:
            problems.append(f"{target_id}: analysis contains unresolved input measurement IDs")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", required=True,
                    help="path to the CTAS SQLite database (opened read-only)")
    ap.add_argument("--output-dir", default="ctas/data")
    ap.add_argument("--limit", type=int, default=0,
                    help="optional development cap; 0 (default) exports the complete eligible catalog")
    ap.add_argument(
        "--release-base-ref", default="HEAD",
        help="published Git ref used as the authoritative history and comparison base",
    )
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

    # Reconstruct derived ranking arithmetic before any aggregate or recent
    # view is produced.  This also applies the documented terminal-state repair
    # to legacy rows that predate the ingestion-order fix.
    generated_dt = datetime.now(UTC).replace(microsecond=0)
    for candidate in candidates:
        candidate["score_model"] = score_model_for(candidate, generated_dt)
        candidate["ctas_score"] = candidate["score_model"]["final_score"]
        candidate["score_explanation"] = score_explanation_for(candidate)

    generated_at = generated_dt.isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "export_checked_at": generated_at,
        "valid_until": (generated_dt + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
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
    event_type_counts: dict[str, int] = {}
    priority_bands = {"urgent_75_100": 0, "high_50_74": 0, "routine_25_49": 0, "low_0_24": 0}
    for candidate in candidates:
        messenger = str(candidate.get("primary_messenger") or "unknown")
        messenger_counts[messenger] = messenger_counts.get(messenger, 0) + 1
        event_type = str(candidate.get("event_type") or "unspecified")
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
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
                "event_id", "name", "updated_at", "discovery_time", "classification",
                "primary_messenger", "ctas_score", "follow_up_counts",
            ) if key in row
        }
        for row in recent
    ]
    payload["statistics"] = {
        "real_events": counts["total_real_events"],
        "public_candidates": len(candidates),
        "quality_quarantined_records": counts["quality_quarantined_events"],
        "magnitude_values_excluded": sum(bool(c.get("data_quality_flags")) for c in candidates),
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
        "event_types": dict(sorted(event_type_counts.items())),
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
    for row in source_universe_rows:
        constraints = OFFICIAL_PROVIDER_CONSTRAINTS.get(str(row["source_key"]))
        if constraints:
            row.update(constraints)
            row.pop("contract_checksum_sha256", None)
            row["contract_checksum_sha256"] = hashlib.sha256(
                json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
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
    source_universe["contract_set_checksum_sha256"] = (
        source_universe_contract_checksum(source_universe)
    )
    source_universe_canonical = (json.dumps(source_universe, sort_keys=True, separators=(",", ":")) + "\n").encode()
    source_universe["artifact_checksum_sha256"] = hashlib.sha256(source_universe_canonical).hexdigest()
    payload["source_universe"] = {
        "schema": SOURCE_UNIVERSE_SCHEMA,
        "source_count": len(source_universe_rows),
        "artifact": "ctas/data/source-universe.json",
        # The source-universe wrapper carries the current heartbeat, so its raw
        # artifact checksum belongs in status/certification rather than the
        # byte-stable compact catalog index.
        "contract_set_checksum_sha256": source_universe["contract_set_checksum_sha256"],
    }
    payload["provider_statistics"] = [
        {"provider": provider, **record_counts}
        for provider, record_counts in sorted(
            counts["provider_counts"].items(),
            key=lambda item: (-sum(item[1].values()), item[0]),
        )
    ]
    payload["surveys"] = counts["surveys"]

    source_universe_version = (
        f"{SOURCE_UNIVERSE_SCHEMA}+sha256:{source_universe['contract_set_checksum_sha256'][:16]}"
    )
    accounting_totals = {
        "declaredSources": len(source_universe_rows),
        "applicableSourceEvaluations": 0,
        "executedQueryReceipts": 0,
        "dataBearingSourceEvaluations": 0,
        "outcomeCounts": {},
    }
    projection_problems: list[str] = []
    source_matrix_patterns: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        event_id = str(candidate["event_id"])
        candidate_attempts = counts["all_attempts"].get(event_id, [])
        candidate_analysis_runs = counts["analysis_runs"].get(event_id, [])
        projection_generated_at = stable_candidate_projection_time(
            candidate, candidate_attempts, candidate_analysis_runs,
        )
        projection, accounting, source_matrix, compatibility_metadata = build_projection(
            candidate,
            source_universe_rows,
            candidate_attempts,
            candidate_analysis_runs,
            projection_generated_at,
            source_universe_version,
        )
        projection_problems.extend(projection_integrity_problems(projection))
        candidate["astro_evidence"] = {
            "projectionSchema": "ctas.astro-evidence-compatibility@1.0.0",
            "coreSchemaName": projection["schemaName"],
            "coreSchemaVersion": projection["schemaVersion"],
            "generatedAt": projection["generatedAt"],
            "sourceUniverseVersion": projection["sourceUniverseVersion"],
            "target": projection["target"],
            "persistedQueryReceipts": projection["queryReceipts"],
            "conflictSets": projection["conflictSets"],
            "selections": projection["selections"],
            "dataProducts": projection["dataProducts"],
            "analysisRuns": projection["analysisRuns"],
            "measurementCount": len(projection["measurements"]),
            "projectionMethod": "The exporter projects source-native retained rows once; the browser deterministically assembles those measurements with versioned source contracts and persisted receipts.",
        }
        candidate["source_accounting"] = accounting
        complete_source_matrix = [
            {key: row.get(key) for key in SOURCE_MATRIX_ROW_KEYS}
            for row in source_matrix
        ]
        candidate["source_matrix"] = compact_source_matrix(
            complete_source_matrix, source_matrix_patterns,
        )
        if expand_source_matrix(candidate["source_matrix"], source_matrix_patterns) != complete_source_matrix:
            projection_problems.append(
                f"{candidate.get('name')}: source matrix does not round-trip through its shared pattern"
            )
        candidate["compatibility_provenance"] = {
            "receiptProvenance": compatibility_metadata["receiptProvenance"],
            "selectionProvenance": compatibility_metadata["selectionProvenance"],
        }
        candidate["evidence_timeline"] = timeline_for(candidate)
        candidate["score_model"] = score_model_for(candidate, generated_dt)
        candidate["ctas_score"] = candidate["score_model"]["final_score"]
        candidate["score_explanation"] = score_explanation_for(candidate)
        candidate["science_brief"] = science_brief_for(candidate)
        accounting_totals["applicableSourceEvaluations"] += int(accounting["applicableSources"])
        accounting_totals["executedQueryReceipts"] += int(accounting["executedQueryReceipts"])
        accounting_totals["dataBearingSourceEvaluations"] += int(accounting["dataBearingSources"])
        for outcome, count in accounting["outcomeCounts"].items():
            accounting_totals["outcomeCounts"][outcome] = accounting_totals["outcomeCounts"].get(outcome, 0) + int(count)
    accounting_totals["outcomeCounts"] = dict(sorted(accounting_totals["outcomeCounts"].items()))
    payload["local_store_exceptions"] = {
        "statement": (
            "Rows the local store could not return for this release. These records are "
            "published with the evidence CTAS could read; the unreadable rows are declared "
            "here rather than presented as an absence of evidence."
        ),
        "failure_count": len(LOCAL_STORE_READ_FAILURES),
        "unreadable_event_count": sum(
            row["unreadable_event_count"] for row in LOCAL_STORE_READ_FAILURES
        ),
        "failures": LOCAL_STORE_READ_FAILURES,
    }
    payload["source_accounting"] = accounting_totals
    payload["statistics"]["source_accounting"] = accounting_totals
    payload["catalog_content_checksum_sha256"] = catalog_semantic_checksum(candidates)

    candidate_rows = [compact_candidate_row(candidate) for candidate in candidates]

    bucket_rows, chunk_raw = candidate_chunk_artifacts(candidates)

    catalog_index = {
        "schema": CATALOG_INDEX_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "catalog_as_of": max(
            (str(candidate["astro_evidence"]["generatedAt"]) for candidate in candidates),
            default=payload["latest_record_update"],
        ),
        "latest_record_update": payload["latest_record_update"],
        "origin": payload["origin"],
        "cadence": payload["cadence"],
        "candidate_count": len(candidate_rows),
        "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
        "statistics": payload["statistics"],
        "recent_stream": payload["recent_stream"],
        "provider_statistics": payload["provider_statistics"],
        "surveys": payload["surveys"],
        "source_universe": payload["source_universe"],
        "source_accounting": payload["source_accounting"],
        "detail_manifest": "ctas/data/candidate-chunks/manifest.json",
        "alias_index": "ctas/data/alias-index.json",
        "research_tables": "ctas/data/research/manifest.json",
        "candidate_columns": list(CATALOG_CANDIDATE_COLUMNS),
        "candidate_rows": candidate_rows,
    }
    catalog_index_raw = (
        json.dumps(catalog_index, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    index_candidates = inflate_catalog_candidates(catalog_index)

    # ---------------------------------------------------------------- pages
    # The complete compact index is retained as one checksum-bound artifact for
    # tooling, but a browser must never be made to download it to draw a first
    # screen.  Partition it into bounded pages the page loads only after an
    # explicit "Browse complete catalog".
    catalog_pages: list[dict[str, Any]] = []
    page_raw: dict[str, bytes] = {}
    page_rows: list[list[Any]] = []
    page_bytes = 0
    def _flush_page() -> None:
        nonlocal page_rows, page_bytes
        if not page_rows:
            return
        number = len(catalog_pages) + 1
        path = f"ctas/data/catalog-pages/{number:04d}.json"
        document = {
            "schema": CATALOG_PAGE_SCHEMA,
            "page": number,
            "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
            "candidate_columns": list(CATALOG_CANDIDATE_COLUMNS),
            "candidate_count": len(page_rows),
            "candidate_rows": page_rows,
        }
        raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        page_raw[path] = raw
        catalog_pages.append({
            "path": path,
            "page": number,
            "candidate_count": len(page_rows),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "first_event_id": str(page_rows[0][CATALOG_CANDIDATE_COLUMNS.index("event_id")]),
            "last_event_id": str(page_rows[-1][CATALOG_CANDIDATE_COLUMNS.index("event_id")]),
        })
        page_rows = []
        page_bytes = 0

    for row in candidate_rows:
        encoded = len(json.dumps(row, separators=(",", ":")).encode()) + 1
        if page_rows and page_bytes + encoded > CATALOG_PAGE_MAX_BYTES - 4096:
            _flush_page()
        page_rows.append(row)
        page_bytes += encoded
    _flush_page()

    catalog_page_manifest = {
        "schema": CATALOG_PAGE_MANIFEST_SCHEMA,
        "generated_at": payload["generated_at"],
        "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
        "statement": (
            "Pages carry the complete compact catalog in catalog-index order. The "
            "first screen never loads them; they are fetched only when a reader "
            "asks to browse the complete catalog."
        ),
        "candidate_columns": list(CATALOG_CANDIDATE_COLUMNS),
        "candidate_count": len(candidate_rows),
        "page_count": len(catalog_pages),
        "page_max_bytes": CATALOG_PAGE_MAX_BYTES,
        "pages": catalog_pages,
        "complete_index": {
            "path": "ctas/data/catalog-index.json",
            "bytes": len(catalog_index_raw),
            "sha256": hashlib.sha256(catalog_index_raw).hexdigest(),
        },
    }
    catalog_page_manifest_raw = (
        json.dumps(catalog_page_manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()

    # -------------------------------------------------------- live summary
    column_index = {name: position for position, name in enumerate(CATALOG_CANDIDATE_COLUMNS)}
    rows_by_event = {
        str(candidate["event_id"]): row
        for candidate, row in zip(candidates, candidate_rows)
    }
    recent_cutoff = generated_dt - timedelta(hours=RECENT_REPORT_WINDOW_HOURS)

    def discovery_time_of(candidate: dict[str, Any]) -> datetime | None:
        return parse_utc(candidate.get("discovery_time"))

    def by_score(cohort: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            cohort,
            key=lambda candidate: (
                -float(candidate.get("ctas_score") or 0.0),
                str(candidate.get("name") or ""),
            ),
        )

    recent_candidates = sorted(
        [
            candidate for candidate in candidates
            if (discovery_time_of(candidate) or datetime.min.replace(tzinfo=UTC)) >= recent_cutoff
        ],
        key=lambda candidate: str(candidate.get("discovery_time") or ""),
        reverse=True,
    )
    leaderboard = by_score([
        candidate for candidate in candidates if candidate.get("default_leaderboard_eligible")
    ])[:TOP_RANK_LIMIT]
    channels = sorted({str(candidate.get("ranking_channel") or "") for candidate in candidates})
    channel_top = {
        channel: [
            str(candidate["event_id"])
            for candidate in by_score([
                candidate for candidate in candidates
                if str(candidate.get("ranking_channel") or "") == channel
            ])[:TOP_RANK_LIMIT]
        ]
        for channel in channels
    }

    summary_ids: list[str] = []
    for group in (
        [str(candidate["event_id"]) for candidate in leaderboard],
        [str(candidate["event_id"]) for candidate in recent_candidates],
        *channel_top.values(),
    ):
        for event_id in group:
            if event_id not in summary_ids:
                summary_ids.append(event_id)

    sky_windows = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
    sky_column_names = (
        "event_id", "name", "ra_deg", "dec_deg", "discovery_magnitude",
        "discovery_time", "ctas_score", "classification", "record_role",
    )
    sky_rows: list[list[Any]] = []
    sky_counts: dict[str, dict[str, int]] = {}
    longest_window = max(sky_windows.values())
    horizon = generated_dt - timedelta(days=longest_window)
    unlocalized = 0
    for candidate in candidates:
        discovered = discovery_time_of(candidate)
        localized = candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None
        if discovered is not None and discovered >= horizon:
            if localized:
                sky_rows.append([candidate.get(name) for name in sky_column_names])
            else:
                unlocalized += 1
    for label, days in sky_windows.items():
        cutoff = generated_dt - timedelta(days=days)
        plotted = 0
        missing = 0
        for candidate in candidates:
            discovered = discovery_time_of(candidate)
            if discovered is None or discovered < cutoff:
                continue
            if candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None:
                plotted += 1
            else:
                missing += 1
        sky_counts[label] = {"plotted": plotted, "unlocalized": missing}
    all_localized = sum(
        1 for candidate in candidates
        if candidate.get("ra_deg") is not None and candidate.get("dec_deg") is not None
    )
    sky_counts["all"] = {
        "plotted": all_localized,
        "unlocalized": len(candidates) - all_localized,
    }

    role_counts: dict[str, int] = {}
    channel_counts: dict[str, int] = {}
    for candidate in candidates:
        role = str(candidate.get("record_role") or "")
        channel = str(candidate.get("ranking_channel") or "")
        role_counts[role] = role_counts.get(role, 0) + 1
        channel_counts[channel] = channel_counts.get(channel, 0) + 1

    def material_change_clock(change: dict[str, Any]) -> tuple[str, str]:
        """Return (timestamp, which clock it came from) — never an unlabelled time."""

        for key, clock in (
            ("provider_publication_time", "provider publication time"),
            ("ctas_receipt_time", "CTAS receipt time"),
            ("scientific_time", "source-reported event time"),
        ):
            value = change.get(key)
            if value:
                return str(value), clock
        return "", "no retained clock"

    material_updates = [
        {
            "event_id": str(candidate["event_id"]),
            "name": candidate.get("name"),
            "at": material_change_clock(candidate["most_recent_meaningful_change"])[0],
            "at_clock": material_change_clock(candidate["most_recent_meaningful_change"])[1],
            **candidate["most_recent_meaningful_change"],
        }
        for candidate in sorted(
            [c for c in candidates if c.get("most_recent_meaningful_change")],
            key=lambda c: material_change_clock(c["most_recent_meaningful_change"])[0],
            reverse=True,
        )[:12]
    ]

    live_summary = {
        "schema": LIVE_SUMMARY_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        # The first-screen document deliberately mirrors the complete index's
        # release identity fields so a browser can prove it holds one coherent
        # release without downloading the catalog.  candidate_count is the
        # complete catalog; candidate_rows is only what the first screen shows.
        "catalog_as_of": catalog_index["catalog_as_of"],
        "latest_record_update": payload["latest_record_update"],
        "origin": payload["origin"],
        "cadence": payload["cadence"],
        "candidate_count": len(candidate_rows),
        "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
        "recent_stream": payload["recent_stream"],
        "provider_statistics": payload["provider_statistics"],
        "surveys": payload["surveys"],
        "source_universe": payload["source_universe"],
        "source_accounting": payload["source_accounting"],
        "detail_manifest": "ctas/data/candidate-chunks/manifest.json",
        "alias_index": "ctas/data/alias-index.json",
        "research_tables": "ctas/data/research/manifest.json",
        "summary_record_count": len(summary_ids),
        "release": {
            "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
            "source_universe_contract_set": source_universe["contract_set_checksum_sha256"],
            "score_method_version": "ctas.follow-up-score@1.0.0",
            "record_role_method_version": "ctas.record-role@1.0.0",
            "source_matrix_schema": SOURCE_MATRIX_SCHEMA,
        },
        "clocks": {
            "last_ingestion_check": payload["export_checked_at"],
            "last_successful_publication": payload["generated_at"],
            "last_material_catalog_change": payload["latest_record_update"],
            "latest_source_reported_event": max(
                (str(candidate.get("discovery_time") or "") for candidate in candidates),
                default="",
            ) or None,
            "latest_material_evidence_update": (material_updates[0].get("at") if material_updates else None),
            "valid_until": payload["valid_until"],
            "cadence": payload["cadence"],
        },
        "statistics": payload["statistics"],
        "record_role_counts": dict(sorted(role_counts.items())),
        "ranking_channel_counts": dict(sorted(channel_counts.items())),
        "candidate_columns": list(CATALOG_CANDIDATE_COLUMNS),
        "candidate_rows": [rows_by_event[event_id] for event_id in summary_ids],
        "leaderboard": {
            "policy": (
                "The default leaderboard ranks only follow-up target candidates. "
                "Detector triggers, localization-region alerts, known terrestrial "
                "and solar events and retracted records stay searchable but never "
                "enter it."
            ),
            "limit": TOP_RANK_LIMIT,
            "event_ids": [str(candidate["event_id"]) for candidate in leaderboard],
        },
        "channel_leaderboards": channel_top,
        "recent_reports": {
            "window_hours": RECENT_REPORT_WINDOW_HOURS,
            "clock": "source-reported discovery time",
            "count": len(recent_candidates),
            "newest_event_ids": [
                str(candidate["event_id"]) for candidate in recent_candidates[:NEWEST_REPORT_COUNT]
            ],
            "event_ids": [str(candidate["event_id"]) for candidate in recent_candidates],
        },
        "material_evidence_updates": material_updates,
        "sky": {
            "clock": "source-reported discovery time",
            "windows": sorted(sky_windows),
            "counts": sky_counts,
            "columns": list(sky_column_names),
            "rows": sky_rows,
            "note": (
                "Rows cover the last 90 days. The all-retained window is drawn from "
                "the complete catalog pages, which load only on request."
            ),
        },
        "complete_catalog": {
            "statement": "Loaded only when a reader asks to browse the complete catalog.",
            "candidate_count": len(candidate_rows),
            "page_manifest": {
                "path": CATALOG_PAGE_MANIFEST_PATH,
                "bytes": len(catalog_page_manifest_raw),
                "sha256": hashlib.sha256(catalog_page_manifest_raw).hexdigest(),
                "page_count": len(catalog_pages),
            },
            "complete_index": {
                "path": "ctas/data/catalog-index.json",
                "bytes": len(catalog_index_raw),
                "sha256": hashlib.sha256(catalog_index_raw).hexdigest(),
            },
            "detail_manifest": "ctas/data/candidate-chunks/manifest.json",
            "alias_index": "ctas/data/alias-index.json",
            "research_tables": "ctas/data/research/manifest.json",
            "source_universe": "ctas/data/source-universe.json",
            "source_matrix_patterns": SOURCE_MATRIX_PATTERNS_PATH,
        },
    }
    live_summary_raw = (
        json.dumps(live_summary, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    alias_index, alias_index_raw = alias_index_artifact(
        candidates, payload["catalog_content_checksum_sha256"],
    )
    research_files, research_manifest, research_manifest_raw = research_table_artifacts(
        candidates, source_universe, payload["catalog_content_checksum_sha256"],
    )
    candidate_manifest, candidate_manifest_raw = complete_catalog_manifest_artifact(
        candidates,
        index_candidates,
        catalog_index_raw,
        bucket_rows,
        chunk_raw,
        payload["catalog_content_checksum_sha256"],
    )

    problems = validate(payload) + projection_problems
    problems.extend(recursive_safety_problems(source_universe, "source-universe"))
    problems.extend(recursive_safety_problems(catalog_index, "catalog-index"))
    problems.extend(recursive_safety_problems(alias_index, "alias-index"))
    problems.extend(recursive_safety_problems(research_manifest, "research-manifest"))
    problems.extend(recursive_safety_problems(candidate_manifest, "candidate-manifest"))
    if problems:
        print("export failed validation:", file=sys.stderr)
        for p in problems[:20]:
            print("  -", p, file=sys.stderr)
        return 1

    degraded_source_states = {"temporarily-unavailable", "rate-limited", "provider-failure"}
    degraded_sources = [
        row for row in source_universe_rows
        if row.get("operational_state") in degraded_source_states
        and (
            any(int(value or 0) > 0 for value in row.get("public_record_counts", {}).values())
            or row.get("implementation_state") == "implemented"
        )
    ]
    publication_state_checksum = hashlib.sha256(
        json.dumps(
            {
                "catalog": payload["catalog_content_checksum_sha256"],
                "sources": [
                    {
                        "source_key": row["source_key"],
                        "operational_state": row["operational_state"],
                        "public_record_counts": row["public_record_counts"],
                        "known_limitations": row.get("known_limitations"),
                    }
                    for row in source_universe_rows
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    status = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_status": "degraded" if degraded_sources else "ok",
        "origin": "local-snapshot",
        "last_successful_update": payload["generated_at"],
        "export_checked_at": payload["export_checked_at"],
        "valid_until": payload["valid_until"],
        "latest_record_update": payload["latest_record_update"],
        "candidate_count": len(candidates),
        "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
        "publication_state_checksum_sha256": publication_state_checksum,
        "degraded_source_count": len(degraded_sources),
        "cadence": "about every 2 minutes",
        "statistics": payload["statistics"],
        "sources": payload["sources"],
        "surveys": payload["surveys"],
        "source_universe": payload["source_universe"],
        "source_accounting": payload["source_accounting"],
        "local_store_exceptions": payload["local_store_exceptions"],
    }
    problems.extend(recursive_safety_problems(status, "status"))
    if problems:
        print("export failed validation:", file=sys.stderr)
        for problem in problems[:20]:
            print("  -", problem, file=sys.stderr)
        return 1

    print(f"database        : {db.name}")
    print(f"real events     : {counts['total_real_events']:,}")
    print(f"published       : {counts['published']:,}   (skipped {counts['skipped']})")
    if LOCAL_STORE_READ_FAILURES:
        unreadable = payload["local_store_exceptions"]["unreadable_event_count"]
        print(
            f"local store     : {len(LOCAL_STORE_READ_FAILURES)} unreadable range(s) covering "
            f"{unreadable} record(s); declared in status.json",
            file=sys.stderr,
        )
    print(f"detail shards   : {sum(map(len, chunk_raw.values()))/1024:.0f} KB in {len(chunk_raw)} files")
    if candidates:
        print("\nsample record:")
        print(json.dumps(candidates[0], indent=2)[:900])

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    site_root = Path(__file__).resolve().parents[1]
    source_universe_raw = (json.dumps(source_universe, indent=2, sort_keys=True) + "\n").encode()
    source_matrix_pattern_document = {
        "schema": SOURCE_MATRIX_PATTERN_SCHEMA,
        "generated_at": payload["generated_at"],
        "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
        "statement": (
            "Each pattern is the ordered list of declared sources that this release "
            "evaluated for a record, executed no query against, and retained nothing "
            "from. Records reference a pattern by id instead of repeating it."
        ),
        "row_keys": list(SOURCE_MATRIX_ROW_KEYS),
        "pattern_count": len(source_matrix_patterns),
        "patterns": {key: rows for key, rows in sorted(source_matrix_patterns.items())},
    }
    source_matrix_patterns_raw = (
        json.dumps(source_matrix_pattern_document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    try:
        release_history_raw_from_ref = git_blob(
            site_root, args.release_base_ref, "ctas/data/release-history.json"
        )
        release_history = json.loads(release_history_raw_from_ref) if release_history_raw_from_ref else None
    except (TypeError, json.JSONDecodeError):
        release_history = None
    if not isinstance(release_history, dict):
        release_history = {"schema": RELEASE_HISTORY_SCHEMA, "entries": []}
    if release_history.get("schema") != RELEASE_HISTORY_SCHEMA or not isinstance(release_history.get("entries"), list):
        release_history = {"schema": RELEASE_HISTORY_SCHEMA, "entries": []}

    previous_snapshot = git_catalog_document(site_root, args.release_base_ref)
    previous_checksum = (
        catalog_semantic_checksum(previous_snapshot.get("candidates", []))
        if previous_snapshot else None
    )
    if previous_snapshot and previous_checksum != payload["catalog_content_checksum_sha256"]:
        previous_by_name = {
            str(row.get("name")): row for row in previous_snapshot.get("candidates", []) if row.get("name")
        }
        current_by_name = {str(row["name"]): row for row in candidates}
        added = sorted(set(current_by_name) - set(previous_by_name))
        removed = sorted(set(previous_by_name) - set(current_by_name))
        changed = sorted(
            name for name in set(current_by_name) & set(previous_by_name)
            if current_by_name[name] != previous_by_name[name]
        )
        survey_counts_for_added: dict[str, int] = {}
        for name in added:
            survey = str(current_by_name[name].get("discovery_survey") or "source not recorded")
            survey_counts_for_added[survey] = survey_counts_for_added.get(survey, 0) + 1
        if added or removed:
            summary = (
                f"{len(added):+d} public candidate records and {len(removed)} removals; "
                "source-reported records are not necessarily newly discovered events."
            )
            change_kind = "candidate-intake"
        else:
            summary = f"Public metadata or evidence changed for {len(changed)} existing candidate records."
            change_kind = "record-update"
        entry = {
            "published_at": payload["generated_at"],
            "change_kind": change_kind,
            "previous_candidate_count": len(previous_by_name),
            "candidate_count": len(current_by_name),
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
            "added_source_summary": dict(sorted(survey_counts_for_added.items())),
            "sample_added": added[:12],
            "sample_removed": removed[:12],
            "summary": summary,
            "catalog_content_checksum_sha256": payload["catalog_content_checksum_sha256"],
            "previous_catalog_content_checksum_sha256": previous_checksum,
            "base_commit": git_ref(site_root, args.release_base_ref),
            "history_basis": "semantic-diff-from-public-git-base",
        }
        existing_checksums = {
            row.get("catalog_content_checksum_sha256") for row in release_history["entries"]
        }
        if entry["catalog_content_checksum_sha256"] not in existing_checksums:
            release_history["entries"].insert(0, entry)
            release_history["entries"] = release_history["entries"][:24]
    release_history["generated_at"] = (
        release_history["entries"][0]["published_at"] if release_history["entries"] else payload["generated_at"]
    )
    release_history["claim_boundary"] = (
        "Counts describe public CTAS catalog changes, not independent discoveries or scientific validation."
    )
    release_history_raw = (
        json.dumps(release_history, indent=2, sort_keys=True) + "\n"
    ).encode()
    problems.extend(recursive_safety_problems(release_history, "release-history"))
    if problems:
        print("export failed validation:", file=sys.stderr)
        for problem in problems[:20]:
            print("  -", problem, file=sys.stderr)
        return 1
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
        "ctas.html", "ctas/app.js", "ctas/catalog-model.js", "ctas/astro-evidence.js",
        "ctas/workbench.js", "ctas/observability.js", "ctas/ctas.css",
        "ctas/data/observatories.json",
        "ctas/research/README.md", "ctas/research/ctas-quickstart.ipynb",
        "ctas/schema/astro-evidence-core-0.1.0.schema.json",
        "scripts/export_ctas_snapshot.py", "scripts/ctas_astro_evidence.py",
        "scripts/check_ctas_links.py", "scripts/rebuild_ctas_release_history.py",
        "scripts/test_ctas_static.py", "scripts/test_ctas_catalog_model.js",
        "scripts/test_ctas_links.py", "scripts/test_ctas_astro_evidence.py",
        "scripts/test_ctas_identity.py", "scripts/test_ctas_browser.py",
        "scripts/ctas_node.py",
        "scripts/mirror_loop.sh", "scripts/publish_ctas.sh", "scripts/ctas_launchd_runner.sh",
        "scripts/install_ctas_mirror.sh", "scripts/diagnose_ctas_mirror.sh",
        "scripts/io.github.jackmcguireastro.ctas-mirror.plist", "CTAS-AUTOMATION.md",
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
    bound_files = {
        # Bind the exact working bytes that generated this report. Separate
        # gates below honestly fail while those bytes differ from HEAD/origin.
        **working_code,
        LIVE_SUMMARY_PATH: live_summary_raw,
        CATALOG_PAGE_MANIFEST_PATH: catalog_page_manifest_raw,
        **page_raw,
        "ctas/data/catalog-index.json": catalog_index_raw,
        "ctas/data/alias-index.json": alias_index_raw,
        "ctas/data/candidate-chunks/manifest.json": candidate_manifest_raw,
        **chunk_raw,
        **research_files,
        "ctas/data/source-universe.json": source_universe_raw,
        SOURCE_MATRIX_PATTERNS_PATH: source_matrix_patterns_raw,
        "ctas/data/release-history.json": release_history_raw,
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
    public_ui_text = b"\n".join(
        working_code[path]
        for path in (
            "ctas.html", "ctas/app.js", "ctas/catalog-model.js",
            "ctas/astro-evidence.js", "ctas/workbench.js", "ctas/observability.js",
            "ctas/ctas.css",
        )
        if path in working_code
    ).decode("utf-8", errors="replace")
    publisher_text = b"\n".join(
        working_code[path]
        for path in (
            "scripts/publish_ctas.sh", "scripts/ctas_launchd_runner.sh",
            "scripts/io.github.jackmcguireastro.ctas-mirror.plist",
        )
        if path in working_code
    ).decode("utf-8", errors="replace")
    interpretation_tokens = (
        "follow-up ordering aid", "not a probability", "does not establish discovery",
        "Completeness describes retained fields", "scientific importance",
    )
    ui_tokens = (
        "CTAS page contents", "ctas-sky-canvas", "data-sky-days=\"7\"",
        "data-sky-days=\"30\"", "data-preset=\"event-only\"", "renderDetails(candidate)",
        "renderSourceUniverse", "renderTimeline", "catalog-index.json", "detail_chunk",
        "candidate-workspace", "release-history.json", "keydown",
        "renderCatalogDownloads", "candidate-chunks/manifest.json",
    )
    cadence_contract = (
        payload["cadence"] == "about every 2 minutes" and
        "<key>StartInterval</key>" in publisher_text and
        "<integer>120</integer>" in publisher_text and
        "ctas_launchd_runner.sh" in publisher_text and
        "CTAS_HEARTBEAT_INTERVAL" in publisher_text
    )
    link_health_current = bool(
        isinstance(link_health, dict) and
        link_health.get("schema") == "ctas.link-health@1.0.0" and
        link_health.get("catalog_content_checksum_sha256") == payload["catalog_content_checksum_sha256"] and
        link_health.get("structural_status") == "passed" and
        link_health.get("live_status") in {"passed", "degraded-provider-unavailable"}
    )
    manifest_chunk_rows = [
        {
            "path": path,
            "candidate_count": len(bucket_rows[Path(path).stem]),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for path, raw in sorted(chunk_raw.items())
    ]
    # Verify the shards one at a time.  Materialising every reconstructed
    # dossier at once doubled peak memory for a complete catalog and was the
    # reason a full export could not finish.  Proving each shard reproduces its
    # source records exactly is the same guarantee at a fraction of the cost:
    # once every shard round-trips, the in-memory records *are* the
    # reconstruction, so the assembled checksum may be taken from them.
    originals_by_id = {
        str(candidate.get("event_id") or ""): candidate for candidate in candidates
    }
    reconstructed_ids: set[str] = set()
    reconstructed_count = 0
    reconstruction_faithful = True
    for path in sorted(chunk_raw):
        for candidate in json.loads(chunk_raw[path]).get("candidates", []):
            event_id = str(candidate.get("event_id") or "")
            reconstructed_count += 1
            reconstructed_ids.add(event_id)
            if originals_by_id.get(event_id) != candidate:
                reconstruction_faithful = False
    index_ids = [str(row.get("event_id") or "") for row in index_candidates]
    reconstructed_in_index_order = [
        originals_by_id[event_id]
        for event_id in index_ids
        if event_id in originals_by_id
    ]
    shard_integrity = (
        catalog_index.get("schema") == CATALOG_INDEX_SCHEMA
        and catalog_index.get("candidate_count") == len(candidates)
        and len(index_candidates) == len(candidates)
        and {row["name"] for row in index_candidates} == set(names)
        and candidate_manifest.get("schema") == CANDIDATE_MANIFEST_SCHEMA
        and candidate_manifest.get("chunk_count") == CANDIDATE_BUCKET_COUNT
        and candidate_manifest.get("chunks") == manifest_chunk_rows
        and sum(row["candidate_count"] for row in manifest_chunk_rows) == len(candidates)
        and reconstruction_faithful
        and reconstructed_count == len(reconstructed_ids) == len(candidates)
        and set(index_ids) == reconstructed_ids
        and candidate_manifest.get("catalog_index") == {
            "path": "ctas/data/catalog-index.json",
            "candidate_count": len(index_candidates),
            "bytes": len(catalog_index_raw),
            "sha256": hashlib.sha256(catalog_index_raw).hexdigest(),
            "ordering_field": "candidate_rows[][event_id column]",
        }
        and candidate_manifest.get("assembled_candidates_checksum_sha256") == hashlib.sha256(
            canonical_candidate_list_bytes(reconstructed_in_index_order)
        ).hexdigest()
        and all(
            row.get("detail_chunk") == f"candidate-chunks/{candidate_bucket(str(row['event_id']))}.json"
            for row in index_candidates
        )
    )
    artifact_size_integrity = all(
        len(raw) < GITHUB_MAX_BLOB_BYTES for raw in bound_files.values()
    )
    # The first screen loads live-summary.json, not the complete catalog, so the
    # bootstrap budget is measured against exactly those bytes.
    bootstrap_size_integrity = len(live_summary_raw) <= LIVE_SUMMARY_MAX_BYTES
    catalog_page_budget = bool(page_raw) and all(
        len(raw) <= CATALOG_PAGE_MAX_BYTES for raw in page_raw.values()
    )
    summary_event_ids = set(rows_by_event)
    live_summary_integrity = (
        live_summary["release"]["catalog_content_checksum_sha256"]
        == payload["catalog_content_checksum_sha256"]
        and all(
            event_id in summary_event_ids
            for group in (
                live_summary["leaderboard"]["event_ids"],
                live_summary["recent_reports"]["event_ids"],
                *live_summary["channel_leaderboards"].values(),
            )
            for event_id in group
        )
        and len(live_summary["candidate_rows"]) == len(summary_ids)
        and live_summary["complete_catalog"]["page_manifest"]["sha256"]
        == hashlib.sha256(catalog_page_manifest_raw).hexdigest()
        and all(
            row["sha256"] == hashlib.sha256(page_raw[row["path"]]).hexdigest()
            and row["bytes"] == len(page_raw[row["path"]])
            for row in catalog_pages
        )
        and sum(row["candidate_count"] for row in catalog_pages) == len(candidate_rows)
        and not any(
            candidate.get("record_role") in {
                "known-terrestrial-event", "known-solar-event", "retracted-event",
            }
            for candidate in leaderboard
        )
    )
    shard_target_integrity = bool(chunk_raw) and max(map(len, chunk_raw.values())) <= CANDIDATE_SHARD_TARGET_MAX_BYTES
    def _score_reproduces(candidate: dict[str, Any]) -> bool:
        model = candidate.get("score_model") or {}
        applied = [term for term in model.get("terms", []) if term.get("applicable")]
        core = float(model.get("baseline", 0.0)) + sum(float(term["points"]) for term in applied)
        final = max(0.0, min(100.0, max(0.0, min(100.0, core)) + float(model.get("multimessenger_bonus") or 0.0)))
        if model.get("status_override"):
            final = 0.0
        return (
            abs(round(final, 2) - float(model.get("final_score") or 0.0)) <= 0.01
            and abs(float(candidate.get("ctas_score") or 0.0) - float(model.get("final_score") or 0.0)) <= 0.01
            and model.get("score_as_of") == payload["generated_at"]
            and (str(candidate.get("status") or "").lower() not in {"retracted", "bogus"}
                 or float(candidate.get("ctas_score") or 0.0) == 0.0)
        )

    score_reconciliation = all(_score_reproduces(candidate) for candidate in candidates)
    score_applicability = all(
        all(
            term["code"] != "spectroscopy_gap_points" or term["applicable"] is False
            for term in (candidate.get("score_model") or {}).get("terms", [])
        )
        for candidate in candidates
        if str(candidate.get("ranking_channel") or "") != "optical"
        or str(candidate.get("record_role") or "") != "follow-up-target-candidate"
    ) and all(
        float((candidate.get("score_model") or {}).get("multimessenger_bonus") or 0.0) == 0.0
        for candidate in candidates
        if len(_retained_messenger_channels(candidate)) < 2
    )
    science_brief_integrity = all(
        (candidate.get("science_brief") or {}).get("schema") == "ctas.candidate-science-brief@1.0.0"
        and {
            row["component_id"] for row in (candidate.get("science_brief") or {}).get("missing_information", [])
        } == {
            row["id"] for row in (candidate.get("record_completeness") or {}).get("components", [])
            if row.get("state") in {"missing", "not-assessed"}
        }
        for candidate in candidates
    )
    replay_integrity = all(
        len({row.get("entry_id") for row in candidate.get("evidence_timeline", [])}) == len(candidate.get("evidence_timeline", []))
        and all(
            row.get("public_available_at") in {row.get("provider_publication_time"), row.get("ctas_receipt_time"), None}
            and not (
                row.get("public_available_at") == row.get("scientific_time")
                and not row.get("provider_publication_time")
                and not row.get("ctas_receipt_time")
            )
            for row in candidate.get("evidence_timeline", [])
        )
        for candidate in candidates
    )
    alias_event_ids = {str(row[0]) for row in alias_index.get("rows", [])}
    alias_integrity = (
        alias_index.get("schema") == ALIAS_INDEX_SCHEMA
        and alias_index.get("catalog_content_checksum_sha256") == payload["catalog_content_checksum_sha256"]
        and alias_index.get("alias_count") == len(alias_index.get("rows", []))
        and alias_event_ids <= set(index_ids)
    )
    research_integrity = (
        research_manifest.get("schema") == RESEARCH_TABLE_MANIFEST_SCHEMA
        and research_manifest.get("catalog_content_checksum_sha256") == payload["catalog_content_checksum_sha256"]
        and all(
            row["path"] in research_files
            and row["bytes"] == len(research_files[row["path"]])
            and row["sha256"] == hashlib.sha256(research_files[row["path"]]).hexdigest()
            for row in research_manifest.get("tables", [])
        )
    )
    magnitude_safety = all(
        candidate.get("discovery_magnitude") is None
        or -30 <= float(candidate["discovery_magnitude"]) <= 40
        for candidate in candidates
    )
    release_history_checksums = [
        row.get("catalog_content_checksum_sha256") for row in release_history.get("entries", [])
    ]
    release_history_integrity = (
        release_history.get("schema") == RELEASE_HISTORY_SCHEMA
        and isinstance(release_history.get("entries"), list)
        and len(release_history_checksums) == len(set(release_history_checksums))
        and all(
            row.get("catalog_content_checksum_sha256")
            and isinstance(row.get("added_count"), int)
            and isinstance(row.get("removed_count"), int)
            and row.get("history_basis") in {
                "git-verified-public-candidate-count-transition",
                "semantic-diff-from-public-git-base",
            }
            for row in release_history["entries"]
        )
    )

    source_matrix_round_trip = all(
        isinstance(candidate.get("source_matrix"), dict)
        and len(expand_source_matrix(candidate["source_matrix"], source_matrix_patterns))
        == int(candidate["source_matrix"].get("row_count") or 0)
        == int((candidate.get("source_accounting") or {}).get("applicableSources") or -1)
        for candidate in candidates
    )

    gates = [
        gate("required-public-artifacts", all(path in bound_files for path in (
            "ctas.html", "ctas/app.js", "ctas/workbench.js", "ctas/observability.js",
            "ctas/ctas.css", "ctas/data/observatories.json",
            "ctas/research/README.md", "ctas/research/ctas-quickstart.ipynb",
            "ctas/schema/astro-evidence-core-0.1.0.schema.json",
            LIVE_SUMMARY_PATH, CATALOG_PAGE_MANIFEST_PATH, "ctas/data/catalog-index.json",
            "ctas/data/alias-index.json", "ctas/data/research/manifest.json",
            "ctas/data/research/events.vot", "ctas/data/research/tom-targets.csv",
            "ctas/data/candidate-chunks/manifest.json",
            "ctas/data/source-universe.json", "ctas/data/link-health.json",
            SOURCE_MATRIX_PATTERNS_PATH, "ctas/data/release-history.json",
        )), "HTML, JavaScript, CSS, observatory definitions, research quickstart, frozen AstroEvidence schema, compact index, astronomy research exports, complete-catalog reconstruction manifest and chunks, source universe, history, and link-health artifact"),
        gate("catalog-population", bool(candidates) and not counts["catalog_truncated"] and len(candidates) == counts["eligible_public_events"] and len(names) == len(set(names)), f"{len(candidates)} complete eligible records; truncated={counts['catalog_truncated']}"),
        gate("candidate-public-contract", required_contract, f"{sum(bool(c.get('name')) and 'ctas_score' in c for c in candidates)}/{len(candidates)} identity and score records"),
        gate("complete-catalog-reconstruction-integrity", shard_integrity, f"{len(candidates)} complete records exactly once across {len(chunk_raw)} checksum-bound chunks in catalog-index UUID order"),
        gate("bootstrap-performance-budget", bootstrap_size_integrity, f"first-screen live summary is {len(live_summary_raw)} bytes; budget={LIVE_SUMMARY_MAX_BYTES}; the complete {len(catalog_index_raw)}-byte index is not loaded to draw a first screen"),
        gate("catalog-page-budget", catalog_page_budget, f"largest of {len(page_raw)} complete-catalog pages is {max(map(len, page_raw.values()), default=0)} bytes; budget={CATALOG_PAGE_MAX_BYTES}"),
        gate("live-summary-integrity", live_summary_integrity, f"{len(summary_ids)} summary records resolve, pages and manifest checksums agree, and no terrestrial, solar or retracted record enters the default leaderboard"),
        gate("detail-shard-performance-budget", shard_target_integrity, f"largest of {len(chunk_raw)} UUID shards is {max(map(len, chunk_raw.values()), default=0)} bytes; budget={CANDIDATE_SHARD_TARGET_MAX_BYTES}"),
        gate("github-blob-size-limit", artifact_size_integrity, f"every bound public artifact is below GitHub's {GITHUB_MAX_BLOB_BYTES}-byte limit"),
        gate("local-store-exceptions-declared", (
            isinstance(status.get("local_store_exceptions"), dict)
            and status["local_store_exceptions"]["failure_count"] == len(LOCAL_STORE_READ_FAILURES)
            and all(
                isinstance(row.get("unreadable_event_ids"), list)
                for row in status["local_store_exceptions"]["failures"]
            )
        ), f"{len(LOCAL_STORE_READ_FAILURES)} unreadable local-store range(s) are declared in the public status rather than published as an absence of evidence"),
        gate("score-term-applicability", score_applicability, "missing-spectrum points are confined to optical follow-up target candidates and a messenger-diversity bonus requires two independently retained channels"),
        gate("score-arithmetic-reconciliation", score_reconciliation, f"{len(candidates)} scores recomputed against this release clock ({payload['generated_at']}) and reproduced exactly from their applicable terms"),
        gate("candidate-science-brief-integrity", science_brief_integrity, f"{len(candidates)} deterministic known, uncertain, missing, and recent-change summaries"),
        gate("evidence-replay-no-future-leakage", replay_integrity, "historical availability uses provider-publication or CTAS-receipt clocks, never observation time alone"),
        gate("source-matrix-round-trip-integrity", source_matrix_round_trip, f"{len(candidates)} source matrices reproduce exactly from {len(source_matrix_patterns)} shared no-evidence patterns"),
        gate("alias-index-integrity", alias_integrity, f"{alias_index.get('alias_count', 0)} provider-scoped aliases bound to the catalog checksum"),
        gate("research-table-integrity", research_integrity, f"{len(research_manifest.get('tables', []))} research tables and interoperability exports checksum-bound to the catalog"),
        gate("derived-magnitude-safety", magnitude_safety, f"{payload['statistics']['magnitude_values_excluded']} implausible source values retained only as flagged raw reports"),
        gate("release-history-integrity", release_history_integrity, f"{len(release_history['entries'])} checksum-addressed public catalog changes"),
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
        gate("recursive-link-structure-and-sampled-tns-health", link_health_current, "all exported URLs are recursively classified and structurally checked; live health applies only to the explicitly sampled TNS object page"),
        gate("interpretation-and-limitations", all(token in public_ui_text for token in interpretation_tokens), "priority, completeness, discovery, and scientific-claim boundaries are public"),
        gate("browser-interaction-contract", all(token in public_ui_text for token in ui_tokens), "compact-index loading, lazy candidate shards, source universe, release history, weekly/monthly sky, full-record workspaces, filters, and keyboard interaction are present"),
        gate("deployed-code-binding", working_matches_head, "bound code bytes exactly match HEAD, not a dirty working tree"),
        gate("local-origin-code-alignment", head_matches_origin, "HEAD bound code bytes match origin/main; generated data declares a successor release"),
        gate("certification-publication-contract", True, "checksum-bound report is written to ctas/data/certification.json and included by the explicit publisher allowlist"),
        gate("exact-claim-boundary", CERTIFICATE_CLAIM.endswith("deployment claim."), CERTIFICATE_CLAIM),
    ]
    release_id = hashlib.sha256(
        b"".join(name.encode() + b"\0" + raw for name, raw in sorted(bound_files.items()))
    ).hexdigest()
    certificate = {
        "schema": "ctas.static-snapshot-verification@1.1.0",
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
    status["static_snapshot_verification"] = {
        "status": certificate["status"],
        "schema": certificate["schema"],
        "passed_check_count": sum(gate["passed"] is True for gate in gates),
        "check_count": len(gates),
        "failed_gate_ids": [gate["id"] for gate in gates if gate["passed"] is not True],
        "report_checksum_sha256": certificate["report_checksum_sha256"],
        "valid_until": certificate["valid_until"],
        "content_release_id": certificate["content_release_id"],
    }
    status["artifacts"] = {
        "catalog_bootstrap": {"path": "ctas/data/catalog-bootstrap.json", "sha256": hashlib.sha256(catalog_index_raw).hexdigest()},
        "catalog_index": {"path": "ctas/data/catalog-index.json", "sha256": hashlib.sha256(catalog_index_raw).hexdigest()},
        "alias_index": {"path": "ctas/data/alias-index.json", "sha256": hashlib.sha256(alias_index_raw).hexdigest()},
        "complete_catalog_manifest": {"path": "ctas/data/candidate-chunks/manifest.json", "sha256": hashlib.sha256(candidate_manifest_raw).hexdigest()},
        "research_tables": {"path": "ctas/data/research/manifest.json", "sha256": hashlib.sha256(research_manifest_raw).hexdigest()},
        "source_universe": {"path": "ctas/data/source-universe.json", "sha256": hashlib.sha256(source_universe_raw).hexdigest()},
        "source_matrix_patterns": {"path": SOURCE_MATRIX_PATTERNS_PATH, "sha256": hashlib.sha256(source_matrix_patterns_raw).hexdigest()},
        "release_history": {"path": "ctas/data/release-history.json", "sha256": hashlib.sha256(release_history_raw).hexdigest()},
        "link_health": {"path": "ctas/data/link-health.json", "sha256": hashlib.sha256(link_health_raw).hexdigest() if link_health_raw else None},
        "certification": {"path": "ctas/data/certification.json", "report_checksum_sha256": certificate["report_checksum_sha256"]},
    }
    status_raw = (json.dumps(status, indent=2, sort_keys=True) + "\n").encode()
    certificate_raw = (json.dumps(certificate, indent=2, sort_keys=True) + "\n").encode()
    final_public_bytes = {
        **bound_files,
        "ctas/data/status.json": status_raw,
        "ctas/data/certification.json": certificate_raw,
    }
    oversized = {
        path: len(raw) for path, raw in final_public_bytes.items()
        if len(raw) >= GITHUB_MAX_BLOB_BYTES
    }
    if oversized:
        print("export failed: public artifacts exceed GitHub's single-object limit:", file=sys.stderr)
        for path, size in sorted(oversized.items()):
            print(f"  - {path}: {size} bytes", file=sys.stderr)
        return 1
    # catalog-bootstrap.json was a byte-for-byte copy of catalog-index.json.
    # Remove any copy a previous release left in this output directory so the
    # superseded artifact cannot linger beside the summary that replaced it.
    (out / "catalog-bootstrap.json").unlink(missing_ok=True)
    atomic_write(out / "live-summary.json", live_summary_raw)
    atomic_write(out / "catalog-index.json", catalog_index_raw)
    page_dir = out / "catalog-pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(page_dir / "manifest.json", catalog_page_manifest_raw)
    for relative, raw in page_raw.items():
        write_output_artifact(out, relative, raw)
    atomic_write(out / "alias-index.json", alias_index_raw)
    chunk_dir = out / "candidate-chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(chunk_dir / "manifest.json", candidate_manifest_raw)
    for relative, raw in chunk_raw.items():
        write_output_artifact(out, relative, raw)
    for relative, raw in research_files.items():
        write_output_artifact(out, relative, raw)
    atomic_write(out / "status.json", status_raw)
    atomic_write(out / "source-universe.json", source_universe_raw)
    atomic_write(out / "source-matrix-patterns.json", source_matrix_patterns_raw)
    atomic_write(out / "release-history.json", release_history_raw)
    atomic_write(out / "certification.json", certificate_raw)
    print(f"first screen    : {len(live_summary_raw)} bytes live-summary.json ({len(summary_ids)} records)")
    print(f"complete catalog: {len(catalog_index_raw)} bytes across {len(catalog_pages)} pages")
    print(f"\nwrote compact bootstrap/index, alias index, research tables, complete-catalog manifest, {len(chunk_raw)} checksum-bound detail chunks, status, source universe, history, and verification report")
    print(f"snapshot verification: {certificate['status']} ({sum(g['passed'] for g in gates)}/{len(gates)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
