#!/usr/bin/env python3
"""
ctas_interop.py, the IVOA-facing exports of a CTAS release (Framework v2, Track 1).

The exporter (scripts/export_ctas_snapshot.py) builds every public table as plain
Python rows. This module turns those rows into the formats astronomy tooling reads
without a parser of its own, and proves each one reads back as what was written:

* ECSV 1.0 (Astropy APE 6) and VOTable 1.5 for every public research table, with a
  UCD on every column, a VOUnits string on every column that carries a physical unit
  (``astropy.units`` ``to_string("vounit")``), a description per column, and the
  provenance columns (event_id, provider, receipt and outcome fields) kept;
* VOParquet (IVOA Note "Parquet in Astronomy" v1.0; file metadata keys
  ``IVOA.VOTable-Parquet.version`` and ``IVOA.VOTable-Parquet.content``, the same
  two keys Astropy, TOPCAT and STILTS read and write) for the bulk tables that are
  too large for the GitHub Pages tree, rows sorted by HEALPix order 8 (nested) with
  the pixel index as a column, row groups of about 50,000 rows;
* DataLink 1.1 ``{links}`` documents, one VOTable per dossier bucket, with the
  DataLink core-vocabulary semantics for every public URL a dossier retains;
* a W3C PROV-JSON document per build naming the upstream sources, their retrieval
  times, parser versions, the collector and exporter code identities, the database
  snapshot and the release checksum.

Nothing here changes an existing artifact. Every table is built from the same rows
the CSV, VOTable summary and JSON files already carry; ``round_trip_problems`` reads
each new file back with Astropy and compares every cell with its source row, and the
exporter refuses the release when a value, a unit, a UCD or a limit flag differs.

Absent values are explicit: numbers and booleans are masked (ECSV/VOTable) or null
(Parquet); text columns use the empty string, the convention the CSV and VOTable
summary have used since 2026-08 ("empty cells mean the public record does not retain
that value"). Upper limits are never encoded as NaN or dropped: the photometry table
carries ``detection`` and ``is_upper_limit`` as explicit boolean columns beside the
``limiting_magnitude`` column.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from astropy import units as u
from astropy.table import MaskedColumn, Table
from astropy.io import votable as astropy_votable
from astropy.io.votable import from_table
from astropy.io.votable.tree import CooSys, Info, Param

SITE_BASE_URL = "https://jackmcguireastro.github.io"
VOTABLE_NAMESPACE = "http://www.ivoa.net/xml/VOTable/v1.3"
VOTABLE_VERSION = "1.5"
ECSV_FORMAT_NOTE = "ECSV 1.0 (Astropy APE 6): CSV with a YAML header carrying datatype, unit, UCD and description per column"
VOTABLE_FORMAT_NOTE = "IVOA VOTable 1.5 TABLEDATA with a UCD and VOUnits string per FIELD"
VOPARQUET_FORMAT_NOTE = (
    "Apache Parquet with IVOA VOParquet 1.0 metadata (IVOA.VOTable-Parquet.version and "
    "IVOA.VOTable-Parquet.content); rows sorted by HEALPix order 8 nested index"
)
VOPARQUET_VERSION = "1.0"
VOPARQUET_VERSION_KEY = b"IVOA.VOTable-Parquet.version"
VOPARQUET_CONTENT_KEY = b"IVOA.VOTable-Parquet.content"
HEALPIX_ORDER = 8
HEALPIX_NSIDE = 2 ** HEALPIX_ORDER
HEALPIX_COLUMN = "healpix_8_nested"
PARQUET_ROW_GROUP_ROWS = 50_000
DATALINK_STANDARD_ID = "ivo://ivoa.net/std/DataLink#links-1.1"
DATALINK_MANIFEST_SCHEMA = "ctas.datalink-manifest@1.0.0"
BULK_DOWNLOADS_SCHEMA = "ctas.bulk-downloads@1.0.0"
PROVENANCE_SCHEMA = "ctas.build-provenance@1.0.0"
DATALINK_SEMANTICS_ORDER = ("#this", "#documentation", "#progenitor", "#auxiliary", "#preview", "#derivation")
# Research tables that carry one row per public candidate: a DataLink #derivation row
# points a client at them from any dossier. Paths are release-independent.
DATALINK_DERIVATION_TABLES = (
    ("ctas/data/research/candidates.ecsv", "text/ecsv; charset=utf-8", "ECSV 1.0 research table (candidates.ecsv)"),
    ("ctas/data/research/candidates.vot", "application/x-votable+xml", "VOTable 1.5 research table (candidates.vot)"),
    ("ctas/data/research/events.csv", "text/csv; charset=utf-8", "CSV research table (events.csv)"),
    ("ctas/data/research/source-accounting.ecsv", "text/ecsv; charset=utf-8", "ECSV 1.0 source-accounting table (source-accounting.ecsv)"),
)
INTEROP_CLAIM = (
    "Formats and metadata follow the IVOA documents named in each file; the content is "
    "the same retained public evidence as the JSON release, not an independent measurement."
)

ColumnType = str  # "str" | "float" | "int" | "bool" | "json"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: ColumnType
    ucd: str
    unit: u.UnitBase | None
    description: str

    @property
    def vounit(self) -> str | None:
        return self.unit.to_string("vounit") if self.unit is not None else None


def _c(name: str, dtype: ColumnType, ucd: str, unit: u.UnitBase | None, description: str) -> ColumnSpec:
    return ColumnSpec(name, dtype, ucd, unit, description)


ISO_UTC = "ISO 8601 UTC text; empty when the public record does not retain it."

# ----------------------------------------------------------------------------- events
# One spec per CATALOG_CANDIDATE_COLUMNS entry of the exporter; the exporter's test
# asserts the two lists agree, so a new catalog column cannot be published without
# a UCD and a description.
EVENT_COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    _c("event_id", "str", "meta.id;meta.main", None, "Stable CTAS public event identifier (RFC 4122 UUID)."),
    _c("name", "str", "meta.id", None, "Preferred public designation."),
    _c("event_type", "str", "src.class", None, "CTAS event type (controlled vocabulary)."),
    _c("primary_messenger", "str", "meta.code", None, "Primary retained messenger channel."),
    _c("messenger_channels", "json", "meta.code", None, "JSON list of retained messenger channels."),
    _c("ra_deg", "float", "pos.eq.ra;meta.main", u.deg, "Source-retained ICRS right ascension."),
    _c("dec_deg", "float", "pos.eq.dec;meta.main", u.deg, "Source-retained ICRS declination."),
    _c("coordinate_error_arcsec", "float", "stat.error;pos.eq", u.arcsec, "Source-reported positional uncertainty when retained."),
    _c("discovery_time", "str", "time.epoch", None, "Source-reported discovery time. " + ISO_UTC),
    _c("discovery_survey", "str", "meta.id;instr.obsty", None, "Source-reported discovery survey or facility."),
    _c("discovery_instrument", "str", "meta.id;instr", None, "Source-reported discovery instrument."),
    _c("discovery_magnitude", "float", "phot.mag", u.mag, "Source-reported discovery magnitude when retained."),
    _c("status", "str", "meta.code.status", None, "Current retained CTAS record state."),
    _c("classification", "str", "src.class", None, "Latest retained reported classification label."),
    _c("reported_label_kind", "str", "meta.code.class", None, "Whether the latest label is a physical class, an operational label, or none."),
    _c("classification_probability", "float", "stat.probability", None, "Provider-reported classification probability when retained."),
    _c("ctas_score", "float", "stat.rank", None, "Operational CTAS follow-up ordering score (0-100); not a probability or a measure of scientific importance."),
    _c("score_as_of", "str", "time.epoch", None, "Release score clock the score refers to. " + ISO_UTC),
    _c("score_valid_until", "str", "time.end", None, "End of the score clock validity window. " + ISO_UTC),
    _c("score_method_version", "str", "meta.version", None, "Version of the score method that produced ctas_score."),
    _c("score_applicable_terms", "json", "meta.code", None, "JSON list of score terms that applied to this record."),
    _c("score_detected_messengers", "json", "meta.code", None, "JSON list of physical messengers the score counted as detected."),
    _c("follow_up_total", "int", "meta.number", None, "Count of retained public follow-up records."),
    _c("redshift", "float", "src.redshift", None, "Retained redshift value when available."),
    _c("updated_at", "str", "time.epoch", None, "Latest retained record update. " + ISO_UTC),
    _c("latest_classification_at", "str", "time.epoch", None, "Latest retained classification assertion time. " + ISO_UTC),
    _c("latest_spectrum_at", "str", "time.epoch", None, "Latest retained spectrum observation time. " + ISO_UTC),
    _c("latest_messenger_at", "str", "time.epoch", None, "Latest retained messenger signal time. " + ISO_UTC),
    _c("latest_retraction_at", "str", "time.epoch", None, "Latest retained retraction time. " + ISO_UTC),
    _c("detail_chunk", "str", "meta.ref", None, "Path of the dossier chunk, relative to ctas/data/, that holds the complete record."),
    _c("n_classifications", "int", "meta.number", None, "Retained classification assertions."),
    _c("n_classification_history", "int", "meta.number", None, "Retained superseded or retracted classification assertions."),
    _c("n_observations", "int", "meta.number", None, "Retained photometric observations and limits."),
    _c("n_spectra", "int", "meta.number", None, "Retained spectra."),
    _c("n_messenger_signals", "int", "meta.number", None, "Retained messenger signals and notices."),
    _c("n_publications", "int", "meta.number", None, "Retained public reports and circulars."),
    _c("n_publication_revisions", "int", "meta.number", None, "Retained report revisions."),
    _c("n_host_context", "int", "meta.number", None, "Retained host-context records."),
    _c("n_catalog_counterparts", "int", "meta.number", None, "Retained positional catalog counterparts."),
    _c("n_archive_products", "int", "meta.number", None, "Retained released archive products."),
    _c("photometry_outcomes", "json", "meta.number", None, "JSON object counting active, detection, limit and forced photometry rows."),
    _c("record_role", "str", "meta.code.class", None, "What kind of record this is (follow-up target, detector trigger, localization alert, known terrestrial or solar event)."),
    _c("ranking_channel", "str", "meta.code", None, "Cohort the record may be ranked inside."),
    _c("default_leaderboard_eligible", "bool", "meta.code", None, "True when the record may enter the default follow-up leaderboard."),
    _c("record_label", "str", "meta.code.qual", None, "Record-completeness label; describes retained fields, not scientific quality."),
    _c("record_present", "int", "meta.number", None, "Completeness components present."),
    _c("record_applicable", "int", "meta.number", None, "Completeness components applicable to this record."),
    _c("record_not_assessed", "int", "meta.number", None, "Completeness components not assessed."),
    _c("record_fraction", "float", "arith.ratio", None, "Present divided by applicable completeness components."),
    _c("primary_source_key", "str", "meta.id", None, "Source-universe key of the preferred public source record."),
    _c("primary_source_url", "str", "meta.ref.url", None, "Preferred public source-record URL when available."),
    _c("primary_source_designation", "str", "meta.id", None, "Designation at the preferred public source."),
    _c("identity_state", "str", "meta.code.status", None, "Provider-scoped identity resolution state."),
    _c("conflict_count", "int", "meta.number", None, "Count of retained measurement conflict sets."),
    _c("source_declared", "int", "meta.number", None, "Declared sources in the source universe of this release."),
    _c("source_applicable", "int", "meta.number", None, "Sources applicable to this record."),
    _c("source_executed", "int", "meta.number", None, "Executed query receipts for this record."),
    _c("source_data_bearing", "int", "meta.number", None, "Sources that returned retained data for this record."),
    _c("lc_fit_model", "str", "meta.code", None, "Multiband light-curve fit chosen for the features (Track 3c), when computed."),
    _c("lc_rise_days", "float", "time.duration", u.day, "Half-maximum rise time of the chosen multiband fit, when computed."),
    _c("lc_peak_abs_mag", "float", "phys.magAbs", u.mag, "Peak absolute magnitude (Planck 2018, SFD x0.86 extinction, bandwidth k-term), when computed."),
    _c("broker_consensus", "str", "meta.code.class", None, "Agreement of the broker readings and the TNS label (agree, disagree, single-reading, no-broker-reading)."),
    _c("broker_classes_seen", "str", "src.class", None, "Coarse classes given by the broker readings and the TNS label."),
)

ALIAS_COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    _c("event_id", "str", "meta.id;meta.main", None, "CTAS public event identifier the alias is bound to."),
    _c("source_key", "str", "meta.id", None, "Source-universe key of the provider that asserted the designation."),
    _c("designation", "str", "meta.id", None, "Provider-scoped public designation as asserted."),
    _c("ambiguous", "bool", "meta.code", None, "True when the unscoped designation is bound to more than one CTAS record."),
)

SOURCE_COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    _c("source_key", "str", "meta.id;meta.main", None, "Stable source-universe key."),
    _c("name", "str", "meta.id", None, "Source name."),
    _c("organization_or_facility", "str", "meta.id;instr.obsty", None, "Operating organization or facility."),
    _c("source_family", "str", "meta.code.class", None, "Primary source family."),
    _c("secondary_families", "json", "meta.code.class", None, "JSON list of secondary families."),
    _c("data_types", "json", "meta.code", None, "JSON list of data types the contract covers."),
    _c("product_contracts", "json", "meta.code", None, "JSON list of declared product contracts."),
    _c("topic_contracts", "json", "meta.code", None, "JSON list of declared topic contracts."),
    _c("access_mode", "str", "meta.code", None, "Declared access mode."),
    _c("protocol", "str", "meta.code", None, "Declared protocol."),
    _c("documentation_url", "str", "meta.ref.url", None, "Provider documentation URL."),
    _c("rights_or_public_access_basis", "str", "meta.note", None, "Rights or public-access basis for retained records."),
    _c("authentication_requirement", "str", "meta.note", None, "Authentication the provider requires."),
    _c("query_scope", "str", "meta.note", None, "Declared query scope."),
    _c("rate_or_cadence_limit", "str", "meta.note", None, "Provider rate or cadence limit CTAS honours."),
    _c("pagination_policy", "str", "meta.note", None, "Pagination policy for the provider interface, when documented."),
    _c("redistribution_constraint", "str", "meta.note", None, "Redistribution constraint on retained records, when documented."),
    _c("connector_implementation_state", "str", "meta.code.status", None, "Connector implementation state as declared by the collector."),
    _c("implementation_state", "str", "meta.code.status", None, "Public implementation state (controlled vocabulary)."),
    _c("operational_state", "str", "meta.code.status", None, "Public operational state for this release (controlled vocabulary)."),
    _c("representation_state", "str", "meta.code.status", None, "How the source is represented in this release (controlled vocabulary)."),
    _c("represented_directly", "bool", "meta.code", None, "True when retained records came from this source directly."),
    _c("represented_through", "json", "meta.code", None, "JSON list of provider keys this source is represented through, when any."),
    _c("last_successful_response", "str", "time.epoch", None, "Latest successful provider response. " + ISO_UTC),
    _c("last_successful_at", "str", "time.epoch", None, "Latest successful retrieval. " + ISO_UTC),
    _c("last_attempt_at", "str", "time.epoch", None, "Latest query attempt. " + ISO_UTC),
    _c("public_record_counts", "json", "meta.number", None, "JSON object of retained public record counts by type."),
    _c("public_disposition_counts", "json", "meta.number", None, "JSON object of query dispositions by state."),
    _c("known_limitations", "str", "meta.note", None, "Known limitations of this source in CTAS."),
    _c("last_verified", "str", "time.epoch", None, "Date the contract was last verified against provider documentation."),
    _c("contract_version", "str", "meta.version", None, "Contract version."),
    _c("contract_checksum_sha256", "str", "meta.id;meta.cryptographic", None, "SHA-256 of the contract row including operational state."),
)

SOURCE_ACCOUNTING_OUTCOMES: tuple[str, ...] = (
    "DATA_RETURNED", "SEARCHED_NO_MATCH", "PARTIAL_RESULT", "QUERY_FAILED",
    "QUERY_BLOCKED", "NOT_CONFIGURED", "NOT_APPLICABLE", "AMBIGUOUS",
    "STALE_LAST_GOOD_RETAINED", "LINK_ONLY_NOT_QUERIED", "NOT_QUERIED",
)

SOURCE_ACCOUNTING_COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    _c("event_id", "str", "meta.id;meta.main", None, "CTAS public event identifier."),
    _c("name", "str", "meta.id", None, "Preferred public designation."),
    _c("declared_sources", "int", "meta.number", None, "Sources declared in the source universe of this release."),
    _c("applicable_sources", "int", "meta.number", None, "Sources whose contract applies to this record."),
    _c("executed_query_receipts", "int", "meta.number", None, "Query receipts that were executed for this record."),
    _c("data_bearing_sources", "int", "meta.number", None, "Applicable sources that hold retained data for this record."),
) + tuple(
    _c(
        "outcome_" + outcome.lower(), "int", "meta.number", None,
        f"Applicable sources whose current query outcome is {outcome}.",
    )
    for outcome in SOURCE_ACCOUNTING_OUTCOMES
)

RELEASE_HISTORY_COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    _c("published_at", "str", "time.epoch;meta.main", None, "Release clock of the catalog change. " + ISO_UTC),
    _c("change_kind", "str", "meta.code", None, "candidate-intake or record-update."),
    _c("previous_candidate_count", "int", "meta.number", None, "Public candidates in the previous release."),
    _c("candidate_count", "int", "meta.number", None, "Public candidates in this release."),
    _c("added_count", "int", "meta.number", None, "Records added since the previous release."),
    _c("removed_count", "int", "meta.number", None, "Records removed since the previous release."),
    _c("changed_count", "int", "meta.number", None, "Existing records whose public content changed."),
    _c("summary", "str", "meta.note", None, "Plain-words summary of the change."),
    _c("added_source_summary", "json", "meta.number", None, "JSON object counting added records by discovery survey."),
    _c("sample_added", "json", "meta.id", None, "JSON list of up to twelve added designations."),
    _c("sample_removed", "json", "meta.id", None, "JSON list of up to twelve removed designations."),
    _c("catalog_content_checksum_sha256", "str", "meta.id;meta.cryptographic", None, "Semantic catalog checksum of this release."),
    _c("previous_catalog_content_checksum_sha256", "str", "meta.id;meta.cryptographic", None, "Semantic catalog checksum of the previous release."),
    _c("base_commit", "str", "meta.id", None, "Git commit of the published release the diff was taken against."),
    _c("history_basis", "str", "meta.code", None, "How the entry was derived."),
)

TOM_FIXED_COLUMN_SPECS: dict[str, ColumnSpec] = {
    spec.name: spec for spec in (
        _c("name", "str", "meta.id;meta.main", None, "Preferred public designation (TOM Toolkit target name)."),
        _c("type", "str", "meta.code", None, "TOM Toolkit target type (SIDEREAL)."),
        _c("ra", "float", "pos.eq.ra;meta.main", u.deg, "ICRS right ascension."),
        _c("dec", "float", "pos.eq.dec;meta.main", u.deg, "ICRS declination."),
        _c("epoch", "float", "time.epoch", u.yr, "Coordinate epoch (Julian year)."),
        _c("ctas_event_id", "str", "meta.id", None, "CTAS public event identifier."),
        _c("ctas_score", "float", "stat.rank", None, "Operational CTAS follow-up ordering score; not a probability."),
        _c("classification", "str", "src.class", None, "Latest retained reported classification label."),
        _c("primary_messenger", "str", "meta.code", None, "Primary retained messenger channel."),
        _c("ctas_url", "str", "meta.ref.url", None, "CTAS public dossier URL."),
        _c("source_url", "str", "meta.ref.url", None, "Preferred public source-record URL when available."),
    )
}


def tom_target_column_specs(columns: list[str]) -> tuple[ColumnSpec, ...]:
    specs = []
    for name in columns:
        if name in TOM_FIXED_COLUMN_SPECS:
            specs.append(TOM_FIXED_COLUMN_SPECS[name])
        elif re.fullmatch(r"name\d+", name):
            specs.append(_c(name, "str", "meta.id", None, f"Additional public designation ({name})."))
        else:
            raise ValueError(f"TOM target table has an unexpected column: {name}")
    return tuple(specs)


PHOTOMETRY_COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    _c(HEALPIX_COLUMN, "int", "pos.healpix", None, f"HEALPix order {HEALPIX_ORDER} (nside {HEALPIX_NSIDE}) nested index of the event position; absent for records without coordinates. Rows are sorted by this column."),
    _c("event_id", "str", "meta.id;meta.main", None, "CTAS public event identifier the observation belongs to."),
    _c("name", "str", "meta.id", None, "Preferred public designation of the event."),
    _c("ra_deg", "float", "pos.eq.ra;meta.main", u.deg, "Event ICRS right ascension (the retained event position, not a per-observation centroid)."),
    _c("dec_deg", "float", "pos.eq.dec;meta.main", u.deg, "Event ICRS declination."),
    _c("provider", "str", "meta.id;meta.dataset", None, "Source-universe key of the provider that supplied the row."),
    _c("pipeline", "str", "meta.note;instr.pipeline", None, "Provider pipeline or archive that produced the measurement."),
    _c("telescope", "str", "instr.tel", None, "Telescope."),
    _c("instrument", "str", "instr", None, "Instrument."),
    _c("band", "str", "instr.bandpass", None, "Normalized bandpass label."),
    _c("original_band", "str", "instr.bandpass", None, "Provider's own bandpass label."),
    _c("magnitude_system", "str", "meta.code;phot.mag", None, "Magnitude system (AB, Vega, ...)."),
    _c("mjd", "float", "time.epoch;obs", u.d, "Observation time as Modified Julian Date (UTC)."),
    _c("jd", "float", "time.epoch;obs", u.d, "Observation time as Julian Date (UTC)."),
    _c("observed_at", "str", "time.epoch;obs", None, "Observation time. " + ISO_UTC),
    _c("detection", "bool", "meta.code", None, "True for a detection, false for a non-detection or limit."),
    _c("is_upper_limit", "bool", "meta.code;phot.mag", None, "True when the row is an upper limit: no detection and a limiting magnitude is retained."),
    _c("magnitude", "float", "phot.mag", u.mag, "Measured magnitude; absent for limits."),
    _c("magnitude_error", "float", "stat.error;phot.mag", u.mag, "Magnitude uncertainty."),
    _c("limiting_magnitude", "float", "phot.mag;stat.max", u.mag, "Limiting magnitude for non-detections, when the provider reports one."),
    _c("flux", "float", "phot.flux.density", None, "Flux density in the unit named by flux_unit (units vary by provider, so no column unit is declared)."),
    _c("flux_error", "float", "stat.error;phot.flux.density", None, "Flux density uncertainty in the unit named by flux_unit."),
    _c("flux_unit", "str", "meta.unit", None, "Unit of flux and flux_error as reported by the provider."),
    _c("signal_to_noise", "float", "stat.snr", None, "Signal-to-noise ratio when reported."),
    _c("exposure_seconds", "float", "time.duration;obs.exposure", u.s, "Exposure time."),
    _c("photometry_method", "str", "meta.code", None, "Provider photometry method (difference-psf, forced, ...)."),
    _c("difference_photometry", "bool", "meta.code", None, "True for difference-image photometry."),
    _c("calibration", "str", "meta.note", None, "Provider calibration note."),
    _c("superseded", "bool", "meta.code.status", None, "True when a later provider row supersedes this one."),
    _c("retracted", "bool", "meta.code.status", None, "True when the provider retracted this row."),
    _c("quality_flags", "json", "meta.code.qual", None, "JSON list of provider quality flags."),
    _c("provider_observation_id", "str", "meta.id", None, "Provider's own observation identifier."),
    _c("source_record_id", "str", "meta.id", None, "CTAS receipt of the provider record the row came from."),
    _c("assertion_id", "str", "meta.id", None, "CTAS assertion identifier of this row."),
    _c("source_url", "str", "meta.ref.url", None, "Provider record or service URL."),
    _c("ctas_received_at", "str", "time.epoch", None, "CTAS receipt time. " + ISO_UTC),
    _c("summary", "str", "meta.note", None, "Provider summary text."),
)

CANDIDATE_BULK_COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    _c(HEALPIX_COLUMN, "int", "pos.healpix", None, f"HEALPix order {HEALPIX_ORDER} (nside {HEALPIX_NSIDE}) nested index of the event position; absent for records without coordinates. Rows are sorted by this column."),
) + EVENT_COLUMN_SPECS


# ------------------------------------------------------------------ table building

def _absent(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value == "":
        return True
    return False


def _coerce(spec: ColumnSpec, value: Any) -> tuple[Any, bool]:
    """Return (cell, masked) for one source value under the spec's type."""

    if spec.dtype == "json":
        if value is None:
            return "", True
        return json.dumps(value, sort_keys=True, separators=(",", ":")), False
    if _absent(value):
        return {"str": "", "float": 0.0, "int": 0, "bool": False}[spec.dtype], True
    if spec.dtype == "str":
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, separators=(",", ":")), False
        return str(value), False
    if spec.dtype == "float":
        return float(value), False
    if spec.dtype == "int":
        if isinstance(value, bool):
            return int(value), False
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"column {spec.name} expects an integer, got {value!r}")
        return int(value), False
    if spec.dtype == "bool":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True, False
            if lowered in {"false", "0", "no"}:
                return False, False
            raise ValueError(f"column {spec.name} expects a boolean, got {value!r}")
        return bool(value), False
    raise ValueError(f"unknown column type {spec.dtype}")


def build_table(
    specs: Iterable[ColumnSpec], rows: Iterable[Iterable[Any]], *,
    name: str, description: str, meta: dict[str, Any] | None = None,
) -> Table:
    """Build an Astropy table with one masked column per spec from positional rows."""

    specs = tuple(specs)
    rows = [list(row) for row in rows]
    for row in rows:
        if len(row) != len(specs):
            raise ValueError(f"{name}: row width {len(row)} does not match {len(specs)} columns")
    table = Table(meta={"name": name, "description": description, **(meta or {})})
    for position, spec in enumerate(specs):
        cells: list[Any] = []
        mask: list[bool] = []
        for row in rows:
            cell, masked = _coerce(spec, row[position])
            cells.append(cell)
            mask.append(masked)
        if spec.dtype in {"str", "json"}:
            width = max((len(cell) for cell in cells), default=1) or 1
            data = np.array(cells, dtype=f"U{width}")
        elif spec.dtype == "float":
            data = np.array(cells, dtype="float64")
        elif spec.dtype == "int":
            data = np.array(cells, dtype="int64")
        else:
            data = np.array(cells, dtype="bool")
        column = MaskedColumn(
            data, name=spec.name, mask=np.array(mask, dtype=bool), unit=spec.unit,
            description=spec.description,
        )
        column.meta["ucd"] = spec.ucd
        table.add_column(column)
    return table


def ecsv_bytes(table: Table) -> bytes:
    """Serialize the table as ECSV 1.0."""

    stream = io.StringIO()
    table.write(stream, format="ascii.ecsv")
    return stream.getvalue().encode("utf-8")


def _votable_from_table(
    table: Table, *, params: dict[str, str] | None = None, coosys: bool = True,
    infos: dict[str, str] | None = None, variable_strings: bool = True,
) -> astropy_votable.tree.VOTableFile:
    """Build a VOTable 1.5 object from the table: char* strings, boolean flags, ICRS.

    ``variable_strings=False`` keeps Astropy's fixed arraysize (the longest value, at
    least 1): the VOParquet header needs it because Astropy's ``parquet.votable``
    reader cannot stack a variable-length header column onto Parquet strings.
    """

    document = from_table(table)
    document.version = VOTABLE_VERSION
    document.description = INTEROP_CLAIM
    resource = document.resources[0]
    vo_table = resource.tables[0]
    vo_table.name = str(table.meta.get("name") or "table")
    vo_table.description = str(table.meta.get("description") or "")
    if coosys:
        resource.coordinate_systems.append(CooSys(ID="ICRS", system="ICRS"))
    for field in vo_table.fields:
        column = table[field.name]
        if field.datatype in {"unicodeChar", "char"}:
            if variable_strings:
                field.arraysize = "*"
            elif field.arraysize in (None, "0", "*"):
                field.arraysize = "1"
        if field.datatype == "bit":
            field.datatype = "boolean"
        field.ucd = column.meta.get("ucd")
        field.description = column.description
        if coosys and field.ucd in {"pos.eq.ra;meta.main", "pos.eq.dec;meta.main"}:
            field.ref = "ICRS"
    for key, value in (params or {}).items():
        vo_table.params.append(Param(document, name=key, datatype="char", arraysize="*", value=str(value)))
    for key, value in (infos or {}).items():
        vo_table.infos.append(Info(name=key, value=str(value)))
    return document


_TABLEDATA_GAP = re.compile(rb">\s+<")


def compact_tabledata(raw: bytes) -> bytes:
    """Drop the indentation Astropy writes between TABLEDATA tags (about a quarter of
    the bytes of a wide table). Safe because the writer escapes ``>`` inside cell text,
    so a ``>`` followed by whitespace and ``<`` only ever separates two tags."""

    start = raw.find(b"<TABLEDATA>")
    end = raw.find(b"</TABLEDATA>")
    if start < 0 or end < 0:
        return raw
    return raw[:start] + _TABLEDATA_GAP.sub(b"><", raw[start:end]) + raw[end:]


def votable_bytes(table: Table, *, params: dict[str, str] | None = None, coosys: bool = True) -> bytes:
    """Serialize the table as VOTable 1.5 TABLEDATA (metadata pretty, rows compact)."""

    document = _votable_from_table(table, params=params, coosys=coosys)
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        document.to_xml(stream)
    return compact_tabledata(stream.getvalue())


def voparquet_header_xml(table: Table, *, params: dict[str, str] | None = None) -> bytes:
    """The VOTable document VOParquet stores in the file metadata: FIELDs, no DATA."""

    document = _votable_from_table(table, params=params, variable_strings=False)
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        document.to_xml(stream)
    root = ET.fromstring(stream.getvalue())
    namespace = "{" + VOTABLE_NAMESPACE + "}"
    for vo_table in root.iter(namespace + "TABLE"):
        for data in vo_table.findall(namespace + "DATA"):
            vo_table.remove(data)
    ET.register_namespace("", VOTABLE_NAMESPACE)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def healpix_index(ra_deg: Any, dec_deg: Any) -> int | None:
    """HEALPix order-8 nested index of a position, or None without coordinates."""

    if _absent(ra_deg) or _absent(dec_deg):
        return None
    from astropy_healpix import lonlat_to_healpix

    return int(lonlat_to_healpix(float(ra_deg) * u.deg, float(dec_deg) * u.deg, HEALPIX_NSIDE, order="nested"))


def sort_rows_by_healpix(
    rows: list[list[Any]], specs: tuple[ColumnSpec, ...], tie_break: tuple[str, ...],
) -> list[list[Any]]:
    """Order rows by HEALPix index (rows without one last), then by the tie-break columns."""

    position = {spec.name: index for index, spec in enumerate(specs)}
    healpix_position = position[HEALPIX_COLUMN]

    def key(row: list[Any]) -> tuple:
        pixel = row[healpix_position]
        parts: list[Any] = [pixel is None, -1 if pixel is None else int(pixel)]
        for name in tie_break:
            value = row[position[name]]
            parts.append(value is None)
            parts.append("" if value is None else (value if isinstance(value, (int, float)) else str(value)))
        return tuple(parts)

    return sorted(rows, key=key)


def voparquet_bytes(
    table: Table | None = None, *, params: dict[str, str] | None = None,
    specs: tuple[ColumnSpec, ...] | None = None, rows: list[list[Any]] | None = None,
    name: str = "table", description: str = "",
) -> bytes:
    """Serialize a table as VOParquet with pyarrow; strings use "" for absent values.

    Pass either an Astropy ``table`` or ``specs`` with ``rows``. The second form never
    materialises numpy text arrays (4 bytes per character times the longest value per
    column), which for the 145,000-row photometry table is the difference between a
    few hundred megabytes and a few; the VOTable header is built from an empty table
    with the same specs.
    """

    import pyarrow as pa
    import pyarrow.parquet as pq

    arrays: dict[str, Any] = {}
    if table is None:
        if specs is None or rows is None:
            raise ValueError("voparquet_bytes needs a table or specs and rows")
        for position, spec in enumerate(specs):
            if spec.dtype in {"str", "json"}:
                arrays[spec.name] = pa.array(
                    [_coerce(spec, row[position])[0] for row in rows], type=pa.string(),
                )
            else:
                arrow_type = {"float": pa.float64(), "int": pa.int64(), "bool": pa.bool_()}[spec.dtype]
                values = []
                for row in rows:
                    cell, masked = _coerce(spec, row[position])
                    values.append(None if masked else cell)
                arrays[spec.name] = pa.array(values, type=arrow_type)
        header_table = build_table(specs, [], name=name, description=description)
        header = voparquet_header_xml(header_table, params=params)
    else:
        for column in table.itercols():
            mask = np.ma.getmaskarray(column)
            values = np.asarray(np.ma.getdata(column))
            if column.dtype.kind in {"U", "S"}:
                text = [("" if masked else str(value)) for value, masked in zip(values, mask)]
                arrays[column.name] = pa.array(text, type=pa.string())
            elif column.dtype.kind == "f":
                arrays[column.name] = pa.array([None if masked else float(value) for value, masked in zip(values, mask)], type=pa.float64())
            elif column.dtype.kind in {"i", "u"}:
                arrays[column.name] = pa.array([None if masked else int(value) for value, masked in zip(values, mask)], type=pa.int64())
            elif column.dtype.kind == "b":
                arrays[column.name] = pa.array([None if masked else bool(value) for value, masked in zip(values, mask)], type=pa.bool_())
            else:
                raise ValueError(f"unsupported column kind {column.dtype.kind} for {column.name}")
        header = voparquet_header_xml(table, params=params)
    arrow_table = pa.table(arrays)
    arrow_table = arrow_table.replace_schema_metadata({
        VOPARQUET_VERSION_KEY: VOPARQUET_VERSION.encode(),
        VOPARQUET_CONTENT_KEY: header,
    })
    sink = pa.BufferOutputStream()
    pq.write_table(arrow_table, sink, row_group_size=PARQUET_ROW_GROUP_ROWS, compression="zstd")
    return sink.getvalue().to_pybytes()


# ------------------------------------------------------------- round-trip checking

def _column_values(column: Any) -> list[Any]:
    """Plain Python values of a (masked) column; None where masked."""

    data = np.asarray(np.ma.getdata(column)).tolist()
    mask = np.ma.getmaskarray(column).tolist()
    values: list[Any] = []
    for value, masked in zip(data, mask):
        if masked:
            values.append(None)
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        values.append(value)
    return values


def _cells_agree(spec: ColumnSpec, source: Any, read: Any) -> bool:
    if spec.dtype == "json":
        if source is None:
            return read is None or read == ""
        if read is None or read == "":
            return False
        try:
            return json.loads(read) == source
        except (TypeError, ValueError):
            return False
    if _absent(source):
        return read is None or read == "" or (isinstance(read, float) and math.isnan(read))
    if read is None:
        return False
    if spec.dtype == "str":
        if isinstance(source, (dict, list)):
            source = json.dumps(source, sort_keys=True, separators=(",", ":"))
        return str(source) == str(read)
    if spec.dtype == "float":
        try:
            return math.isclose(float(source), float(read), rel_tol=1e-12, abs_tol=0.0)
        except (TypeError, ValueError):
            return False
    if spec.dtype == "int":
        try:
            return int(source) == int(read) and float(read).is_integer()
        except (TypeError, ValueError):
            return False
    if spec.dtype == "bool":
        if isinstance(read, str):
            read = read.strip().lower() in {"true", "t", "1"}
        return bool(source) == bool(read)
    return False


def _table_problems(
    label: str, specs: tuple[ColumnSpec, ...], rows: list[list[Any]], read: Table, *,
    expect_units: bool = True, expect_ucd: bool = True, limit: int = 20,
) -> list[str]:
    problems: list[str] = []
    expected_names = [spec.name for spec in specs]
    if list(read.colnames) != expected_names:
        problems.append(f"{label}: columns {list(read.colnames)[:8]}... differ from {expected_names[:8]}...")
        return problems
    if len(read) != len(rows):
        problems.append(f"{label}: {len(read)} rows read back, {len(rows)} written")
        return problems
    for index, spec in enumerate(specs):
        column = read[spec.name]
        if expect_units:
            read_unit = column.unit.to_string("vounit") if column.unit is not None else None
            if read_unit != spec.vounit:
                problems.append(f"{label}: column {spec.name} unit {read_unit!r} differs from {spec.vounit!r}")
        if expect_ucd and column.meta.get("ucd") != spec.ucd:
            problems.append(f"{label}: column {spec.name} UCD {column.meta.get('ucd')!r} differs from {spec.ucd!r}")
        values = _column_values(column)
        for row_index, row in enumerate(rows):
            if not _cells_agree(spec, row[index], values[row_index]):
                problems.append(
                    f"{label}: row {row_index} column {spec.name} read back as "
                    f"{values[row_index]!r}, written {row[index]!r}"
                )
                if len(problems) >= limit:
                    return problems
    return problems


def round_trip_problems(
    label: str, specs: tuple[ColumnSpec, ...], rows: list[list[Any]], *,
    ecsv: bytes | None = None, votable: bytes | None = None, parquet: bytes | None = None,
) -> list[str]:
    """Read each serialization back with Astropy and compare every cell with its row."""

    problems: list[str] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if ecsv is not None:
            try:
                read = Table.read(ecsv.decode("utf-8"), format="ascii.ecsv")
            except Exception as exc:  # noqa: BLE001 - any parse failure is a release defect
                problems.append(f"{label}: ECSV does not parse ({exc})")
            else:
                problems.extend(_table_problems(label + " ECSV", specs, rows, read))
        if votable is not None:
            try:
                read = Table.read(io.BytesIO(votable), format="votable")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{label}: VOTable does not parse ({exc})")
            else:
                problems.extend(_table_problems(label + " VOTable", specs, rows, read))
                root = ET.fromstring(votable)
                if root.get("version") != VOTABLE_VERSION:
                    problems.append(f"{label}: VOTable version {root.get('version')!r} is not {VOTABLE_VERSION}")
        if parquet is not None:
            problems.extend(parquet_problems(label, specs, rows, parquet))
    return problems


def parquet_problems(label: str, specs: tuple[ColumnSpec, ...], rows: list[list[Any]], parquet: bytes) -> list[str]:
    """pyarrow and Astropy both read the VOParquet file; metadata, order and cells agree."""

    import pyarrow as pa
    import pyarrow.parquet as pq

    problems: list[str] = []
    try:
        parquet_file = pq.ParquetFile(pa.BufferReader(parquet))
    except Exception as exc:  # noqa: BLE001
        return [f"{label}: Parquet does not open ({exc})"]
    metadata = parquet_file.schema_arrow.metadata or {}
    if metadata.get(VOPARQUET_VERSION_KEY) != VOPARQUET_VERSION.encode():
        problems.append(f"{label}: {VOPARQUET_VERSION_KEY.decode()} is {metadata.get(VOPARQUET_VERSION_KEY)!r}")
    header = metadata.get(VOPARQUET_CONTENT_KEY)
    if not header:
        problems.append(f"{label}: {VOPARQUET_CONTENT_KEY.decode()} metadata is missing")
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                header_document = astropy_votable.parse(io.BytesIO(header))
            header_table = header_document.get_first_table()
            header_fields = [(field.name, field.ucd, field.unit) for field in header_table.fields]
            expected = [(spec.name, spec.ucd, spec.vounit) for spec in specs]
            read_fields = [(name, ucd, (unit.to_string("vounit") if unit is not None else None)) for name, ucd, unit in header_fields]
            if read_fields != expected:
                problems.append(f"{label}: VOParquet header FIELDs differ from the column specs")
            if len(header_table.array) != 0:
                problems.append(f"{label}: VOParquet header carries data rows")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: VOParquet header is not a VOTable ({exc})")
    arrow_table = parquet_file.read()
    if arrow_table.column_names != [spec.name for spec in specs]:
        problems.append(f"{label}: Parquet columns differ from the specs")
        return problems
    if arrow_table.num_rows != len(rows):
        problems.append(f"{label}: Parquet holds {arrow_table.num_rows} rows, {len(rows)} written")
        return problems
    if parquet_file.metadata.num_row_groups != max(1, math.ceil(len(rows) / PARQUET_ROW_GROUP_ROWS)):
        problems.append(f"{label}: {parquet_file.metadata.num_row_groups} row groups for {len(rows)} rows")
    for index, spec in enumerate(specs):
        values = arrow_table.column(spec.name).to_pylist()
        for row_index, row in enumerate(rows):
            if not _cells_agree(spec, row[index], values[row_index]):
                problems.append(
                    f"{label}: Parquet row {row_index} column {spec.name} read back as "
                    f"{values[row_index]!r}, written {row[index]!r}"
                )
                if len(problems) >= 20:
                    return problems
        if spec.name == HEALPIX_COLUMN:
            pixels = [value for value in values if value is not None]
            if pixels != sorted(pixels):
                problems.append(f"{label}: rows are not sorted by {HEALPIX_COLUMN}")
            if any(value is not None for value in values[len(pixels):]):
                problems.append(f"{label}: rows without a HEALPix index are not all at the end")
        del values
    # Astropy's own reader must agree with pyarrow.
    try:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as handle:
            handle.write(parquet)
            temporary = Path(handle.name)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                astropy_table = Table.read(str(temporary), format="parquet")
        finally:
            temporary.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{label}: Astropy cannot read the Parquet file ({exc})")
    else:
        if len(astropy_table) != len(rows) or list(astropy_table.colnames) != [spec.name for spec in specs]:
            problems.append(f"{label}: Astropy reads a different table shape from the Parquet file")
    return problems


# ----------------------------------------------------------------------- datalink

DATALINK_COLUMNS = (
    ("ID", "char", "meta.id;meta.main", None, "CTAS event identifier."),
    ("access_url", "char", "meta.ref.url", None, "Link URL."),
    ("service_def", "char", "meta.ref", None, "Service descriptor; empty (direct links only)."),
    ("error_message", "char", "meta.code.error", None, "Error for this ID; empty when a link is present."),
    ("description", "char", "meta.note", None, "What the link is and which provider retained it."),
    ("semantics", "char", "meta.code", None, "DataLink core vocabulary term."),
    ("content_type", "char", "meta.code.mime", None, "Media type when known."),
    ("content_length", "long", "phys.size;meta.file", "byte", "Size in bytes when known."),
)


def _https(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.startswith("https://") else None


def _media_type_for(filename: Any, fallback: str = "application/octet-stream") -> str:
    name = str(filename or "").lower()
    if name.endswith((".fits", ".fit", ".fits.gz", ".fit.gz", ".fz")):
        return "application/fits"
    if name.endswith((".txt", ".dat", ".ascii", ".asci", ".csv", ".tsv")):
        return "text/plain"
    if name.endswith(".json"):
        return "application/json"
    if name.endswith((".png",)):
        return "image/png"
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".xml"):
        return "application/xml"
    return fallback


def datalink_rows_for(
    candidate: dict[str, Any], bucket: str, *, site_base: str = SITE_BASE_URL,
) -> list[dict[str, Any]]:
    """Every public link a dossier retains, as DataLink rows with core-vocabulary semantics.

    Semantics used:
      #this          the dossier record itself (the gzip-compressed JSON chunk the live
                     site serves; ctas/delivery.json names the encoding)
      #documentation the human-readable CTAS dossier page, GCN circulars, AstroNotes,
                     TNS reports and classification citations
      #progenitor    upstream primary records the dossier was built from: TNS object
                     page, messenger notices and localisation sky maps
      #auxiliary     broker and photometry service pages, spectra, archive products,
                     host-context and catalog-counterpart records, provider result pages
      #derivation    the research tables whose row for this event is derived from the
                     dossier (candidates.ecsv/.vot, events.csv, source-accounting.ecsv)
    Rows depend only on retained evidence, never on a release clock, so a bucket whose
    dossiers did not change produces byte-identical bytes.
    """

    event_id = str(candidate.get("event_id") or "")
    follow = candidate.get("follow_up") or {}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(url: Any, semantics: str, description: str, content_type: str | None, content_length: Any = None) -> None:
        link = _https(url)
        if not link or (link, semantics) in seen:
            return
        seen.add((link, semantics))
        length = None
        if isinstance(content_length, (int, float)) and not isinstance(content_length, bool) and content_length >= 0:
            length = int(content_length)
        rows.append({
            "ID": event_id, "access_url": link, "service_def": None, "error_message": None,
            "description": description, "semantics": semantics, "content_type": content_type,
            "content_length": length,
        })

    add(
        f"{site_base}/ctas/data/candidate-chunks/{bucket}.json.gz", "#this",
        "Complete CTAS public dossier chunk holding this record (gzip-compressed JSON; "
        "ctas/data/candidate-chunks/manifest.json carries the JSON byte length and SHA-256).",
        "application/gzip",
    )
    add(
        f"{site_base}/ctas.html?event={event_id}#dossier", "#documentation",
        "CTAS public dossier page for this record.", "text/html",
    )
    for link in candidate.get("links") or []:
        label = str(link.get("label") or link.get("source_key") or "source")
        add(link.get("url"), "#progenitor", f"{label} object record {link.get('designation') or ''}".strip(), "text/html")
    for row in follow.get("messenger_signals") or []:
        provider = row.get("provider") or "provider not recorded"
        kind = row.get("alert_type") or row.get("messenger") or "messenger notice"
        add(row.get("skymap_url"), "#progenitor", f"Localisation sky map for {kind} ({provider}).", _media_type_for(row.get("skymap_url"), "application/fits"))
        add(row.get("source_url"), "#progenitor", f"Messenger notice {kind} ({provider}).", "text/html")
    for row in follow.get("publications") or []:
        provider = row.get("provider") or "provider not recorded"
        title = row.get("title") or row.get("publication_type") or "public report"
        add(row.get("canonical_url"), "#documentation", f"{row.get('publication_type') or 'Public report'}: {title} ({provider}).", "text/html")
    for row in (follow.get("classifications") or []) + (follow.get("classification_history") or []):
        provider = row.get("provider") or "provider not recorded"
        add(row.get("citation_url"), "#documentation", f"Classification report {row.get('classification') or ''} ({provider}).".replace("  ", " "), "text/html")
    for row in follow.get("spectra") or []:
        provider = row.get("provider") or "provider not recorded"
        facility = "/".join(str(part) for part in (row.get("telescope"), row.get("instrument")) if part)
        when = row.get("observed_at") or "time not recorded"
        add(
            row.get("public_download_url"), "#auxiliary",
            f"Spectrum {row.get('file_name') or row.get('provider_spectrum_id') or ''} ({provider}; {facility or 'facility not recorded'}; observed {when}).",
            _media_type_for(row.get("file_name") or row.get("public_download_url")),
        )
        add(row.get("source_url"), "#auxiliary", f"Spectrum record page ({provider}).", "text/html")
    for row in follow.get("archive_products") or []:
        provider = row.get("provider") or "provider not recorded"
        name = row.get("product_filename") or row.get("provider_product_id") or "archive product"
        add(
            row.get("public_download_url") or row.get("data_uri"), "#auxiliary",
            f"Released archive product {name} ({provider}; {row.get('mission') or 'mission not recorded'}).",
            _media_type_for(row.get("product_filename") or row.get("public_download_url")),
            row.get("size_bytes"),
        )
        add(row.get("product_documentation_url"), "#documentation", f"Archive product documentation ({provider}).", "text/html")
        add(row.get("source_url"), "#auxiliary", f"Archive product record page ({provider}).", "text/html")
    for row in follow.get("observations") or []:
        provider = row.get("provider") or "provider not recorded"
        add(row.get("source_url"), "#auxiliary", f"Photometry service or record page ({provider}; {row.get('pipeline') or 'pipeline not recorded'}).", "text/html")
    for row in follow.get("host_context") or []:
        provider = row.get("provider") or "provider not recorded"
        add(row.get("source_url"), "#auxiliary", f"Host-context record {row.get('canonical_name') or row.get('queried_name') or ''} ({provider}).".replace(" (", " (").replace("  ", " "), "text/html")
    for row in follow.get("catalog_counterparts") or []:
        provider = row.get("provider") or "provider not recorded"
        add(row.get("source_url"), "#auxiliary", f"Positional catalog counterpart {row.get('catalog_record_id') or ''} in {row.get('catalog') or 'catalog'} ({provider}).".replace("  ", " "), "text/html")
    for row in candidate.get("source_coverage") or []:
        add(row.get("object_specific_result_url"), "#auxiliary", f"Provider result page ({row.get('source_id') or 'source'}).", "text/html")
    for path, media_type, label in DATALINK_DERIVATION_TABLES:
        add(
            f"{site_base}/{path}", "#derivation",
            f"{label}: the row whose event_id equals this ID is derived from this dossier.", media_type,
        )

    order = {term: index for index, term in enumerate(DATALINK_SEMANTICS_ORDER)}
    rows.sort(key=lambda row: (order.get(row["semantics"], len(order)), row["description"], row["access_url"]))
    return rows


def datalink_document(rows: list[dict[str, Any]], bucket: str) -> bytes:
    """One DataLink 1.1 {links} VOTable for every record in a dossier bucket."""

    root = ET.Element("VOTABLE", {
        "version": VOTABLE_VERSION,
        "xmlns": VOTABLE_NAMESPACE,
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": f"{VOTABLE_NAMESPACE} http://www.ivoa.net/xml/VOTable/VOTable-{VOTABLE_VERSION}.xsd",
    })
    ET.SubElement(root, "DESCRIPTION").text = (
        f"IVOA DataLink 1.1 links for the CTAS public records in dossier bucket {bucket}: "
        "the public URLs the dossiers retain, with DataLink core-vocabulary semantics. "
        "Availability beyond the release's link-health check is not claimed."
    )
    resource = ET.SubElement(root, "RESOURCE", {"type": "results"})
    ET.SubElement(resource, "INFO", {"name": "standardID", "value": DATALINK_STANDARD_ID})
    ET.SubElement(resource, "INFO", {"name": "QUERY_STATUS", "value": "OK"})
    table = ET.SubElement(resource, "TABLE", {"name": "links", "nrows": str(len(rows))})
    for name, datatype, ucd, unit, description in DATALINK_COLUMNS:
        attributes = {"ID": name, "name": name, "datatype": datatype, "ucd": ucd}
        if datatype == "char":
            attributes["arraysize"] = "*"
        if unit:
            attributes["unit"] = unit
        field = ET.SubElement(table, "FIELD", attributes)
        ET.SubElement(field, "DESCRIPTION").text = description
    table_data = ET.SubElement(ET.SubElement(table, "DATA"), "TABLEDATA")
    for row in rows:
        tr = ET.SubElement(table_data, "TR")
        for name, _datatype, _ucd, _unit, _description in DATALINK_COLUMNS:
            cell = ET.SubElement(tr, "TD")
            value = row.get(name)
            if value is not None:
                cell.text = str(value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True) + b"\n"


def datalink_artifacts(
    bucket_rows: dict[str, list[dict[str, Any]]], catalog_content_checksum_sha256: str, *,
    site_base: str = SITE_BASE_URL,
) -> tuple[dict[str, bytes], dict[str, Any], bytes]:
    """DataLink documents for every non-empty dossier bucket plus their manifest."""

    documents: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    event_count = 0
    link_count = 0
    for bucket, candidates in sorted(bucket_rows.items()):
        if not candidates:
            continue
        rows: list[dict[str, Any]] = []
        for candidate in sorted(candidates, key=lambda row: str(row.get("event_id") or "")):
            rows.extend(datalink_rows_for(candidate, bucket, site_base=site_base))
        raw = datalink_document(rows, bucket)
        path = f"ctas/data/links/{bucket}.xml"
        documents[path] = raw
        event_count += len(candidates)
        link_count += len(rows)
        entries.append({
            "path": path, "bucket": bucket, "event_count": len(candidates),
            "link_count": len(rows), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        })
    manifest = {
        "schema": DATALINK_MANIFEST_SCHEMA,
        "standard": DATALINK_STANDARD_ID,
        "catalog_content_checksum_sha256": catalog_content_checksum_sha256,
        "statement": (
            "One DataLink 1.1 links document per dossier bucket, at ctas/data/links/<bucket>.xml, "
            "where <bucket> is the same three-hex-digit bucket as the record's dossier chunk "
            "(the detail_chunk column of the catalog index). A client selects the rows whose ID "
            "equals the CTAS event identifier. Buckets without public records have no document. "
            "Documents are sharded by bucket rather than written per event so the Pages tree gains "
            "at most 4,096 files instead of one per record; their content depends only on retained "
            "evidence, so a bucket whose dossiers did not change is byte-identical between releases."
        ),
        "path_rule": "ctas/data/links/{bucket}.xml with bucket = detail_chunk without the candidate-chunks/ prefix and .json suffix",
        "semantics_vocabulary": "http://www.ivoa.net/rdf/datalink/core",
        "document_count": len(entries),
        "event_count": event_count,
        "link_count": link_count,
        "documents": entries,
    }
    manifest_raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return documents, manifest, manifest_raw


def datalink_problems(raw: bytes) -> list[str]:
    """Parse one links document with Astropy and check the DataLink 1.1 column set."""

    problems: list[str] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            document = astropy_votable.parse(io.BytesIO(raw))
        table = document.get_first_table()
    except Exception as exc:  # noqa: BLE001
        return [f"DataLink document does not parse as a VOTable ({exc})"]
    names = [field.name for field in table.fields]
    if names != [column[0] for column in DATALINK_COLUMNS]:
        problems.append(f"DataLink columns {names} differ from the DataLink 1.1 set")
    infos = {info.name: info.value for info in document.resources[0].infos}
    if infos.get("standardID") != DATALINK_STANDARD_ID:
        problems.append("DataLink document does not declare the DataLink 1.1 standardID")
    semantics = {str(value) for value in table.array["semantics"]}
    if not semantics <= set(DATALINK_SEMANTICS_ORDER):
        problems.append(f"DataLink semantics {sorted(semantics - set(DATALINK_SEMANTICS_ORDER))} are outside the terms CTAS uses")
    return problems


# --------------------------------------------------------------------- provenance

def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def provenance_document(
    *,
    generated_at: str,
    catalog_content_checksum_sha256: str,
    publication_state_checksum_sha256: str,
    candidate_count: int,
    source_universe: dict[str, Any],
    candidates: list[dict[str, Any]],
    database: dict[str, Any],
    code_hashes: dict[str, str],
    collector: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    site_base: str = SITE_BASE_URL,
    site: dict[str, Any] | None = None,
    state_asset: dict[str, Any] | None = None,
    versions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """W3C PROV-JSON for one build.

    Entities: the release, the database snapshot, the state asset the snapshot was
    restored from (when the cloud runner's marker names one), every upstream source
    (with its contract version, retrieval times and the parser versions seen in its
    receipts), every bound code file and every top-level public artifact. Activities:
    the collector run that produced the snapshot and the export that produced the
    release, with the collector commit, the site commit and the exporter's schema and
    certificate versions. Agents: the collector software, the exporter software and
    CTAS. Deterministic apart from the times it names.
    """

    prefix = {
        "prov": "http://www.w3.org/ns/prov#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "ctas": f"{site_base}/ctas/prov#",
    }
    release_id = "ctas:release/" + catalog_content_checksum_sha256
    snapshot_id = "ctas:database-snapshot/" + str(database.get("sha256") or "unknown")
    export_id = "ctas:activity/export"
    collect_id = "ctas:activity/collect"
    exporter_agent = "ctas:agent/exporter"
    collector_agent = "ctas:agent/collector"
    operator_agent = "ctas:agent/ctas"

    parser_versions: dict[str, set[str]] = {}
    receipt_windows: dict[str, list[str]] = {}
    for candidate in candidates:
        for receipt in (candidate.get("astro_evidence") or {}).get("persistedQueryReceipts") or []:
            source_key = str(receipt.get("sourceContractId") or "")
            if not source_key:
                continue
            for key in ("parserVersion", "schemaVersion"):
                value = receipt.get(key)
                if value:
                    parser_versions.setdefault(source_key, set()).add(f"{key}={value}")
            for key in ("startedAt", "completedAt"):
                value = receipt.get(key)
                if value:
                    receipt_windows.setdefault(source_key, []).append(str(value))

    entity: dict[str, Any] = {
        release_id: {
            "prov:type": "ctas:Release",
            "prov:label": f"CTAS public release {catalog_content_checksum_sha256[:16]}",
            "prov:generatedAtTime": {"$": generated_at, "type": "xsd:dateTime"},
            "ctas:catalog_content_checksum_sha256": catalog_content_checksum_sha256,
            "ctas:publication_state_checksum_sha256": publication_state_checksum_sha256,
            "ctas:candidate_count": candidate_count,
            "ctas:source_universe_definition_checksum_sha256": source_universe.get("definition_checksum_sha256"),
            "ctas:source_universe_contract_set_checksum_sha256": source_universe.get("contract_set_checksum_sha256"),
        },
        snapshot_id: {
            "prov:type": "ctas:DatabaseSnapshot",
            "prov:label": "Frozen read-only SQLite snapshot the export read",
            **{f"ctas:{key}": value for key, value in sorted(database.items())},
        },
    }
    used: dict[str, Any] = {}
    was_derived_from: dict[str, Any] = {}
    counter = 0
    state_id = None
    if state_asset and state_asset.get("asset"):
        state_id = "ctas:state-asset/" + str(state_asset["asset"])
        entity[state_id] = {
            "prov:type": "ctas:StateAsset",
            "prov:label": "State archive the cloud runner restored the database from",
            **{f"ctas:{key}": value for key, value in sorted(state_asset.items())},
        }

    def relation(store: dict[str, Any], name: str, body: dict[str, Any]) -> None:
        nonlocal counter
        counter += 1
        store[f"_:{name}{counter}"] = body

    for row in source_universe.get("sources") or []:
        source_key = str(row.get("source_key") or "")
        if not source_key:
            continue
        source_id = f"ctas:source/{source_key}"
        windows = sorted(receipt_windows.get(source_key, []))
        entity[source_id] = {
            "prov:type": "ctas:UpstreamSource",
            "prov:label": row.get("name"),
            "ctas:source_key": source_key,
            "ctas:organization_or_facility": row.get("organization_or_facility"),
            "ctas:documentation_url": row.get("documentation_url"),
            "ctas:implementation_state": row.get("implementation_state"),
            "ctas:operational_state": row.get("operational_state"),
            "ctas:representation_state": row.get("representation_state"),
            "ctas:last_successful_response": row.get("last_successful_response"),
            "ctas:last_attempt_at": row.get("last_attempt_at"),
            "ctas:earliest_receipt_in_release": windows[0] if windows else None,
            "ctas:latest_receipt_in_release": windows[-1] if windows else None,
            "ctas:receipt_count_in_release": len(windows) // 2 if windows else 0,
            "ctas:parser_versions": sorted(parser_versions.get(source_key, set())),
            "ctas:contract_version": row.get("contract_version"),
            "ctas:contract_checksum_sha256": row.get("contract_checksum_sha256"),
            "ctas:rights_or_public_access_basis": row.get("rights_or_public_access_basis"),
        }
        relation(used, "u", {"prov:activity": collect_id, "prov:entity": source_id, "prov:role": "upstream source"})
        relation(was_derived_from, "d", {"prov:generatedEntity": release_id, "prov:usedEntity": source_id})
    for path, digest in sorted(code_hashes.items()):
        code_id = f"ctas:code/{path}"
        entity[code_id] = {"prov:type": "ctas:SoftwareFile", "ctas:path": path, "ctas:sha256": digest}
        relation(used, "u", {"prov:activity": export_id, "prov:entity": code_id, "prov:role": "bound code"})
    for path, detail in sorted(artifacts.items()):
        artifact_id = f"ctas:artifact/{path}"
        entity[artifact_id] = {"prov:type": "ctas:PublicArtifact", "ctas:path": path, **{f"ctas:{key}": value for key, value in sorted(detail.items())}}
        relation(was_derived_from, "d", {"prov:generatedEntity": artifact_id, "prov:usedEntity": release_id})
    relation(used, "u", {"prov:activity": export_id, "prov:entity": snapshot_id, "prov:role": "database snapshot"})
    relation(was_derived_from, "d", {"prov:generatedEntity": release_id, "prov:usedEntity": snapshot_id})
    if state_id:
        relation(used, "u", {"prov:activity": collect_id, "prov:entity": state_id, "prov:role": "restored state"})
        relation(was_derived_from, "d", {"prov:generatedEntity": snapshot_id, "prov:usedEntity": state_id})

    activity = {
        collect_id: {
            "prov:type": "ctas:CollectorRun",
            "prov:label": "CTAS collector: source queries, receipts and retained evidence",
            "prov:endTime": {"$": database.get("latest_record_update") or generated_at, "type": "xsd:dateTime"},
            **{f"ctas:{key}": value for key, value in sorted(collector.items())},
        },
        export_id: {
            "prov:type": "ctas:Export",
            "prov:label": "scripts/export_ctas_snapshot.py: read-only export, projection, verification gates",
            "prov:endTime": {"$": generated_at, "type": "xsd:dateTime"},
            "ctas:exporter_sha256": code_hashes.get("scripts/export_ctas_snapshot.py"),
            "ctas:interop_module_sha256": code_hashes.get("scripts/ctas_interop.py"),
            **{f"ctas:site_{key}": value for key, value in sorted((site or {}).items())},
            **{f"ctas:{key}": value for key, value in sorted((versions or {}).items())},
        },
    }
    agent = {
        collector_agent: {
            "prov:type": "prov:SoftwareAgent", "prov:label": "CTAS collector (supernova-ops)",
            "ctas:commit": collector.get("commit"),
        },
        exporter_agent: {
            "prov:type": "prov:SoftwareAgent", "prov:label": "CTAS static exporter",
            "ctas:sha256": code_hashes.get("scripts/export_ctas_snapshot.py"),
            "ctas:site_commit": (site or {}).get("commit"),
            "ctas:repository": (site or {}).get("repository"),
        },
        operator_agent: {"prov:type": "prov:Organization", "prov:label": "CTAS (jackmcguireastro.github.io)"},
    }
    document = {
        "schema": PROVENANCE_SCHEMA,
        "prefix": prefix,
        "entity": entity,
        "activity": activity,
        "agent": agent,
        "used": used,
        "wasGeneratedBy": {
            "_:g1": {"prov:entity": release_id, "prov:activity": export_id, "prov:time": {"$": generated_at, "type": "xsd:dateTime"}},
            "_:g2": {"prov:entity": snapshot_id, "prov:activity": collect_id},
        },
        "wasDerivedFrom": was_derived_from,
        "wasAssociatedWith": {
            "_:a1": {"prov:activity": export_id, "prov:agent": exporter_agent},
            "_:a2": {"prov:activity": collect_id, "prov:agent": collector_agent},
        },
        "wasAttributedTo": {
            "_:t1": {"prov:entity": release_id, "prov:agent": operator_agent},
        },
    }
    return document


def provenance_problems(document: Any, *, catalog_content_checksum_sha256: str, source_keys: set[str]) -> list[str]:
    """Structural checks of a PROV-JSON document this exporter writes."""

    problems: list[str] = []
    if not isinstance(document, dict):
        return ["provenance is not a JSON object"]
    for section in ("prefix", "entity", "activity", "agent", "used", "wasGeneratedBy", "wasDerivedFrom", "wasAssociatedWith", "wasAttributedTo"):
        if not isinstance(document.get(section), dict):
            problems.append(f"provenance lacks the {section} section")
    if problems:
        return problems
    if (document["prefix"].get("prov")) != "http://www.w3.org/ns/prov#":
        problems.append("provenance does not declare the prov namespace")
    release_id = "ctas:release/" + catalog_content_checksum_sha256
    if release_id not in document["entity"]:
        problems.append("provenance does not name this release")
    sources = {key[len("ctas:source/"):] for key in document["entity"] if key.startswith("ctas:source/")}
    if sources != source_keys:
        problems.append("provenance sources differ from the source universe")
    if not any(key.startswith("ctas:database-snapshot/") for key in document["entity"]):
        problems.append("provenance does not name the database snapshot")
    if "ctas:code/scripts/export_ctas_snapshot.py" not in document["entity"]:
        problems.append("provenance does not name the exporter code hash")
    generated = [row for row in document["wasGeneratedBy"].values() if row.get("prov:entity") == release_id]
    if not generated:
        problems.append("provenance does not say what generated the release")
    for key, body in document["used"].items():
        if body.get("prov:activity") not in document["activity"] or body.get("prov:entity") not in document["entity"]:
            problems.append(f"provenance usage {key} refers to an unknown activity or entity")
            break
    for key, body in document["wasDerivedFrom"].items():
        if body.get("prov:generatedEntity") not in document["entity"] or body.get("prov:usedEntity") not in document["entity"]:
            problems.append(f"provenance derivation {key} refers to an unknown entity")
            break
    return problems


# ------------------------------------------------------------------ bulk downloads

def bulk_downloads_document(
    *, catalog_content_checksum_sha256: str, generated_at: str, repository: str, release_tag: str,
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """The Pages-tree index of the release assets that hold the VOParquet files."""

    release_url = f"https://github.com/{repository}/releases/tag/{release_tag}"
    return {
        "schema": BULK_DOWNLOADS_SCHEMA,
        "catalog_content_checksum_sha256": catalog_content_checksum_sha256,
        "generated_at": generated_at,
        "release_tag": release_tag,
        "release_url": release_url,
        "statement": (
            "Bulk tables are published as assets of one rolling GitHub Release, not in the "
            "GitHub Pages tree. The publisher replaces each asset after the release is built; "
            "verify the SHA-256 below against the downloaded file, because an asset can lag the "
            "Pages release by one cycle while an upload is in flight or has failed."
        ),
        "format": VOPARQUET_FORMAT_NOTE,
        "claim_boundary": INTEROP_CLAIM,
        "assets": [
            {
                **asset,
                "download_url": f"https://github.com/{repository}/releases/download/{release_tag}/{asset['name']}",
            }
            for asset in assets
        ],
    }


def git_identity(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Collector identity the cloud runner passes through the environment."""

    env = os.environ if env is None else env
    return {
        "commit": env.get("CTAS_COLLECTOR_COMMIT") or None,
        "run_id": env.get("GITHUB_RUN_ID") or None,
        "run_url": (
            f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
            if env.get("GITHUB_REPOSITORY") and env.get("GITHUB_RUN_ID") else None
        ),
        "workflow": env.get("GITHUB_WORKFLOW") or None,
    }


__all__ = [
    "ALIAS_COLUMN_SPECS", "BULK_DOWNLOADS_SCHEMA", "CANDIDATE_BULK_COLUMN_SPECS", "ColumnSpec",
    "DATALINK_COLUMNS", "DATALINK_DERIVATION_TABLES", "DATALINK_MANIFEST_SCHEMA", "DATALINK_STANDARD_ID", "ECSV_FORMAT_NOTE",
    "EVENT_COLUMN_SPECS", "HEALPIX_COLUMN", "HEALPIX_ORDER", "PARQUET_ROW_GROUP_ROWS",
    "PHOTOMETRY_COLUMN_SPECS", "PROVENANCE_SCHEMA", "RELEASE_HISTORY_COLUMN_SPECS",
    "SOURCE_ACCOUNTING_COLUMN_SPECS", "SOURCE_ACCOUNTING_OUTCOMES", "SOURCE_COLUMN_SPECS",
    "VOPARQUET_FORMAT_NOTE", "VOTABLE_FORMAT_NOTE", "build_table", "bulk_downloads_document",
    "datalink_artifacts", "datalink_document", "datalink_problems", "datalink_rows_for",
    "ecsv_bytes", "git_identity", "healpix_index", "parquet_problems", "provenance_document",
    "provenance_problems", "round_trip_problems", "sha256_file", "sort_rows_by_healpix",
    "tom_target_column_specs", "voparquet_bytes", "votable_bytes",
]
