"""Incremental CTAS releases: input fingerprints, the dossier store, the previous release
and the chunk writer (2026-11-24).

Every release used to rebuild all 20,000 dossiers from the database although a cycle
changes a few hundred of them. The exporter (scripts/export_ctas_snapshot.py) now:

1. reads the light layer of every eligible event (the events row, its aliases, identity
   resolution and positional neighbours) and streams every other row the dossier pipeline
   would read through a per-event fingerprint (``fingerprint_events``): the same SQL, the
   same column sets, nothing retained but a 16-byte digest and a few facts per event;
2. reads the previous release (``read_previous_release``): its dossiers as compressed
   canonical JSON, its input fingerprints and its score clock;
3. reuses a previous dossier byte for byte when its fingerprint, the release-wide context
   (bound code, source-universe definition) and its time-dependent score terms are all
   unchanged (``plan_release``); every other event is rebuilt from the database;
4. keeps every dossier of the release as compressed canonical JSON (``DossierStore``) and
   streams the catalog from it, so memory no longer grows with the catalog;
5. writes each bucket's chunk from the store, copying the previous release's chunk bytes
   when none of the bucket's dossiers changed (``ChunkWriter``).

Safety: a sample of the reused dossiers is rebuilt from the database every cycle and compared
byte for byte (a mismatch turns the cycle into a full rebuild and is reported); a full
rebuild runs at least every CTAS_FULL_REBUILD_HOURS (24) and whenever the context changes;
CTAS_INCREMENTAL=0 turns reuse off. The published file ctas/data/input-fingerprints.json
carries the fingerprints and the build record from one release to the next.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

UTC = timezone.utc

INPUT_FINGERPRINTS_PATH = "ctas/data/input-fingerprints.json"
INPUT_FINGERPRINTS_SCHEMA = "ctas.input-fingerprints@1.0.0"
DEFAULT_FULL_REBUILD_HOURS = 24.0
DEFAULT_SAMPLE_FRACTION = 0.01
DEFAULT_SAMPLE_MINIMUM = 150
# The score terms that move with the release clock (score_model_for): recency points fade
# for 72 h after discovery, the observation-gap points grow for 120 h after the latest
# retained observation, and the follow-up window closes FOLLOW_UP_WINDOW_DAYS after the
# newest evidence. A dossier is reused only when none of these can differ between the
# previous clock and this one.
RECENCY_HOURS = 72.0
OBSERVATION_GAP_HOURS = 120.0


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


# --------------------------------------------------------------------------- fingerprints
def _encode_value(value: Any) -> bytes:
    if value is None:
        return b"\x00"
    if value is True:
        return b"T"
    if value is False:
        return b"F"
    if isinstance(value, int):
        return b"i" + str(value).encode()
    if isinstance(value, float):
        return b"f" + repr(value).encode()
    if isinstance(value, str):
        raw = value.encode("utf-8", "surrogatepass")
        return b"s" + str(len(raw)).encode() + b":" + raw
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return b"b" + str(len(raw)).encode() + b":" + raw
    raw = repr(value).encode()
    return b"r" + str(len(raw)).encode() + b":" + raw


def encode_row(tag: bytes, values: Iterable[Any]) -> bytes:
    return tag + b"\x1e" + b"\x1f".join(_encode_value(value) for value in values) + b"\n"


@dataclass
class EventFacts:
    """What the fingerprint pass keeps per event besides the digest."""

    digest: Any = None                                   # hashlib blake2b, then hex in `fingerprint`
    fingerprint: str | None = None
    provider_counts: dict[str, dict[str, int]] = field(default_factory=dict)   # evidence type -> provider -> rows
    follow_up_counts: dict[str, int] = field(default_factory=dict)
    latest_observation: datetime | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)          # magnitude/detection only (multimessenger index)
    catalog_products: dict[str, int] = field(default_factory=lambda: {"allwise": 0, "2mass": 0})
    envelope_ids: list[str] = field(default_factory=list)
    has_signals: bool = False
    has_screens: bool = False
    readable: bool = True


def stream_rows(
    cur: sqlite3.Cursor, event_ids: list[str], statement: str,
    on_row: Callable[[str, sqlite3.Row], None], chunk_size: int = 400,
) -> list[str]:
    """Run ``statement`` (with an ``{ids}`` placeholder list) for the events in chunks and hand
    every row to ``on_row`` without keeping it. Returns the event ids whose rows could not be
    read (the same narrowing as rows_by_event: those events are rebuilt by the loader, which
    records the failure)."""

    unreadable: list[str] = []
    for index in range(0, len(event_ids), chunk_size):
        chunk = event_ids[index:index + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        try:
            for row in cur.execute(statement.format(ids=placeholders), chunk):
                on_row(str(row["event_id"]), row)
        except sqlite3.DatabaseError:
            for event_id in chunk:
                try:
                    for row in cur.execute(statement.format(ids="?"), [event_id]):
                        on_row(str(row["event_id"]), row)
                except sqlite3.DatabaseError:
                    unreadable.append(str(event_id))
    return unreadable


def fingerprint_events(
    cur: sqlite3.Cursor,
    event_ids: list[str],
    light_payload: Callable[[str], bytes],
    statements: list[tuple[str, str]],
    *,
    envelope_checksum_sql: str,
    follow_up_types: dict[str, str],
    provider_key: Callable[[Any], str],
    clean: Callable[[Any], Any],
    iso: Callable[[Any], str | None],
    parse_utc: Callable[[Any], datetime | None],
    signal_tag: str = "messenger_signals",
    screen_tag: str = "analysis_screen_runs",
    observation_tag: str = "observations",
    counterpart_tag: str = "catalog_counterparts",
) -> dict[str, EventFacts]:
    """One digest per event over every database row the dossier pipeline reads for it.

    ``statements`` are (tag, sql) pairs: exactly the loader's queries. ``follow_up_types``
    maps the tags of the ten follow-up queries to the dossier's follow_up keys (the provider
    and row counts of those rows are kept, so the source universe and the release
    statistics can be built without reading any dossier)."""

    facts: dict[str, EventFacts] = {event_id: EventFacts(digest=hashlib.blake2b(digest_size=16)) for event_id in event_ids}
    for event_id in event_ids:
        facts[event_id].digest.update(b"light\x1e")
        facts[event_id].digest.update(light_payload(event_id))
    envelope_owners: dict[str, list[str]] = {}
    for tag, statement in statements:
        tag_bytes = tag.encode()
        follow_up_key = follow_up_types.get(tag)

        def on_row(event_id: str, row: sqlite3.Row, tag_bytes=tag_bytes, tag=tag, follow_up_key=follow_up_key) -> None:
            fact = facts.get(event_id)
            if fact is None:
                return
            values = tuple(row)
            fact.digest.update(encode_row(tag_bytes, values))
            if follow_up_key is not None:
                fact.follow_up_counts[follow_up_key] = fact.follow_up_counts.get(follow_up_key, 0) + 1
                provider = provider_key(clean(row["provider"]) if "provider" in row.keys() else None)
                if provider:
                    by_provider = fact.provider_counts.setdefault(follow_up_key, {})
                    by_provider[provider] = by_provider.get(provider, 0) + 1
            if tag == observation_tag:
                keys = row.keys()
                if not row["superseded"]:
                    observed = parse_utc(iso(row["observed_at"]))
                    if observed is not None and (fact.latest_observation is None or observed > fact.latest_observation):
                        fact.latest_observation = observed
                mini = {}
                if "magnitude" in keys and clean(row["magnitude"]) is not None:
                    mini["magnitude"] = clean(row["magnitude"])
                if "detection" in keys and clean(row["detection"]) is not None:
                    mini["detection"] = clean(row["detection"])
                fact.observations.append(mini)
                envelope = clean(row["source_record_id"]) if "source_record_id" in keys else None
                if envelope is not None:
                    envelope_owners.setdefault(str(envelope), []).append(event_id)
            elif tag == counterpart_tag:
                catalog = str(clean(row["catalog"]) or "").lower()
                if "allwise" in catalog:
                    fact.catalog_products["allwise"] += 1
                elif "2mass" in catalog or catalog == "fp_psc":
                    fact.catalog_products["2mass"] += 1
            elif tag == signal_tag:
                fact.has_signals = True
            elif tag == screen_tag:
                fact.has_screens = True

        for event_id in stream_rows(cur, event_ids, statement, on_row):
            facts[event_id].readable = False
    # The comparison block reads the receipts of the envelopes behind the photometry rows
    # (comparison_envelope_receipts): their checksum and topic enter the owning events' digests.
    envelope_ids = sorted(envelope_owners)
    for index in range(0, len(envelope_ids), 400):
        part = envelope_ids[index:index + 400]
        try:
            rows = cur.execute(envelope_checksum_sql.format(ids=",".join("?" * len(part))), part).fetchall()
        except sqlite3.DatabaseError:
            for envelope_id in part:
                for owner in envelope_owners[envelope_id]:
                    facts[owner].readable = False
            continue
        for row in rows:
            encoded = encode_row(b"envelope", tuple(row))
            for owner in envelope_owners[str(row["id"])]:
                facts[owner].digest.update(encoded)
                facts[owner].envelope_ids.append(str(row["id"]))
    for fact in facts.values():
        fact.fingerprint = fact.digest.hexdigest()
        fact.digest = None
    return facts


def context_digest(parts: dict[str, Any]) -> str:
    """The release-wide inputs a dossier depends on besides its own rows: the bound code,
    the source-universe definition fields the projection reads, the analysis switch."""

    return hashlib.sha256(canonical_json(parts)).hexdigest()


def time_stable(
    discovery: datetime | None, latest_observation: datetime | None,
    previous_as_of: datetime, as_of: datetime, follow_up_window_days: float,
) -> bool:
    """True when score_model_for gives the same model at ``as_of`` as at ``previous_as_of``
    for a record with these clocks (see RECENCY_HOURS / OBSERVATION_GAP_HOURS)."""

    if as_of < previous_as_of:
        return False
    if discovery is not None and (previous_as_of - discovery) < timedelta(hours=RECENCY_HOURS):
        return False
    if latest_observation is not None and (previous_as_of - latest_observation) < timedelta(hours=OBSERVATION_GAP_HOURS):
        return False
    newest = max([value for value in (discovery, latest_observation) if value is not None], default=None)
    if newest is None:
        return True
    window = timedelta(days=follow_up_window_days)
    inside_before = (previous_as_of - newest) <= window
    inside_now = (as_of - newest) <= window
    return inside_before == inside_now


# ------------------------------------------------------------------------- dossier store
class DossierStore:
    """Every dossier of the release as compressed canonical JSON, in catalog order."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self._compressed: dict[str, bytes] = {}
        self._sha256: dict[str, str] = {}
        self.reused: set[str] = set()

    def __len__(self) -> int:
        return len(self.order)

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._compressed

    def put_canonical(self, event_id: str, canonical: bytes, *, reused: bool = False) -> None:
        if event_id in self._compressed:
            raise ValueError(f"duplicate dossier {event_id}")
        self.order.append(event_id)
        self._compressed[event_id] = zlib.compress(canonical, 1)
        self._sha256[event_id] = hashlib.sha256(canonical).hexdigest()
        if reused:
            self.reused.add(event_id)

    def put_compressed(self, event_id: str, compressed: bytes, sha256: str, *, reused: bool = True) -> None:
        if event_id in self._compressed:
            raise ValueError(f"duplicate dossier {event_id}")
        self.order.append(event_id)
        self._compressed[event_id] = compressed
        self._sha256[event_id] = sha256
        if reused:
            self.reused.add(event_id)

    def canonical(self, event_id: str) -> bytes:
        return zlib.decompress(self._compressed[event_id])

    def sha256(self, event_id: str) -> str:
        return self._sha256[event_id]

    def decode(self, event_id: str) -> dict[str, Any]:
        return json.loads(self.canonical(event_id))

    def iter_canonical(self, ids: Iterable[str] | None = None) -> Iterator[tuple[str, bytes]]:
        for event_id in (self.order if ids is None else ids):
            yield event_id, self.canonical(event_id)

    def iter_decoded(self, ids: Iterable[str] | None = None) -> Iterator[tuple[str, dict[str, Any], bytes]]:
        for event_id, canonical in self.iter_canonical(ids):
            yield event_id, json.loads(canonical), canonical

    def compressed_bytes(self) -> int:
        return sum(len(raw) for raw in self._compressed.values())


# ---------------------------------------------------------------------- previous release
@dataclass
class PreviousRelease:
    index: dict[str, Any]
    index_ids: list[str]
    manifest: dict[str, Any]
    chunk_meta: dict[str, dict[str, Any]]            # bucket -> manifest row
    part_meta: dict[str, dict[str, Any]]             # part path -> manifest row
    sha_by_id: dict[str, str]
    slim_by_id: dict[str, dict[str, Any]]
    compressed_by_id: dict[str, bytes]               # only the events asked for (keep=...)
    index_minis: dict[str, dict[str, Any]]           # multimessenger.public_index inputs, kept events only
    bucket_members: dict[str, list[str]]             # bucket -> event ids (sorted)
    semantic_checksum: str
    assembled_checksum: str
    digest_by_name: dict[str, str]
    fingerprints: dict[str, Any] | None              # the previous input-fingerprints document
    patterns: dict[str, list[dict[str, Any]]]        # the previous source-matrix patterns


FINGERPRINT_HEX = re.compile(r"[0-9a-f]{32}")


def parse_fingerprints_document(raw: bytes | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("schema") != INPUT_FINGERPRINTS_SCHEMA:
        return None
    fingerprints = document.get("fingerprints")
    if not isinstance(fingerprints, dict) or not isinstance(document.get("context_digest"), str):
        return None
    if not isinstance(document.get("score_as_of"), str) or not isinstance(document.get("build"), dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) and FINGERPRINT_HEX.fullmatch(v) for k, v in fingerprints.items()):
        return None
    return document


def index_mini(candidate: dict[str, Any]) -> dict[str, Any]:
    """The fields multimessenger.public_index reads, from a complete dossier."""

    host = candidate.get("host_association") or {}
    runs = [
        {"analysisType": run.get("analysisType"), "status": run.get("status"),
         "result": {"flagged": (run.get("result") or {}).get("flagged")}}
        for run in (host.get("runs") or []) if run.get("analysisType") == "prior-activity"
    ]
    observations = [
        {key: row[key] for key in ("magnitude", "detection") if key in row}
        for row in (candidate.get("follow_up") or {}).get("observations") or [] if isinstance(row, dict)
    ]
    return {
        "event_id": candidate.get("event_id"), "name": candidate.get("name"), "status": candidate.get("status"),
        "ra_deg": candidate.get("ra_deg"), "dec_deg": candidate.get("dec_deg"),
        "discovery_time": candidate.get("discovery_time"), "primary_messenger": candidate.get("primary_messenger"),
        "follow_up": {"observations": observations},
        **({"host_association": {"runs": runs}} if runs else {}),
    }


def read_previous_release(
    git_blob: Callable[[str], bytes | None],
    *,
    decode_chunk: Callable[[bytes, Callable[[str], bytes]], dict],
    inflate_index: Callable[[dict[str, Any]], list[dict[str, Any]]],
    canonical_candidate_bytes: Callable[[dict[str, Any]], bytes],
    semantic_candidate_bytes: Callable[[dict[str, Any]], bytes],
    list_sha256: Callable[[Iterable[bytes]], str],
    readable_manifest_schemas: Iterable[str],
    slim_fields: Iterable[str],
    keep: Callable[[str], bool],
    patterns_path: str,
) -> PreviousRelease | None:
    """The previous release, read once from its chunks under the same byte, count and
    checksum verification as git_catalog_digest (None exactly when that would be None).

    ``keep(event_id)`` says which dossiers to retain as compressed canonical JSON (the
    candidates for reuse); every dossier's canonical sha256 and slim fields are kept."""

    manifest_raw = git_blob("ctas/data/candidate-chunks/manifest.json")
    if not manifest_raw:
        return None
    try:
        manifest = json.loads(manifest_raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if (not isinstance(manifest, dict) or manifest.get("schema") not in set(readable_manifest_schemas)
            or not isinstance(manifest.get("chunks"), list)
            or type(manifest.get("chunk_count")) is not int
            or manifest["chunk_count"] != len(manifest["chunks"])
            or type(manifest.get("candidate_count")) is not int
            or manifest["candidate_count"] < 0):
        return None

    def valid_metadata(row: Any, path_pattern: str, *, candidate_count: bool = False) -> bool:
        return bool(
            isinstance(row, dict)
            and isinstance(row.get("path"), str)
            and re.fullmatch(path_pattern, row["path"])
            and type(row.get("bytes")) is int and row["bytes"] > 0
            and isinstance(row.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
            and (not candidate_count or (
                type(row.get("candidate_count")) is int and row["candidate_count"] >= 0
            ))
        )

    chunks = manifest["chunks"]
    if not all(valid_metadata(row, r"ctas/data/candidate-chunks/[0-9a-f]{2,4}\.json", candidate_count=True) for row in chunks):
        return None
    chunk_paths = [row["path"] for row in chunks]
    if chunk_paths != sorted(set(chunk_paths)):
        return None
    index_meta = manifest.get("catalog_index")
    if not valid_metadata(index_meta, r"ctas/data/catalog-index\.json"):
        return None
    index_raw = git_blob(str(index_meta.get("path") or "ctas/data/catalog-index.json"))
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
        index_rows = inflate_index(index) if isinstance(index, dict) else None
    except (TypeError, ValueError):
        index_rows = None
    if not isinstance(index_rows, list):
        return None
    part_rows = manifest.get("parts", [])
    if not isinstance(part_rows, list):
        return None
    if not all(valid_metadata(row, r"ctas/data/candidate-chunks/[0-9a-f]{3}\.part-[0-9]{6}\.json") for row in part_rows):
        return None
    part_paths = [row["path"] for row in part_rows]
    if part_paths != sorted(set(part_paths)):
        return None
    declared_parts = {row["path"]: row for row in part_rows}
    used_parts: set[str] = set()

    def read_part(path: str) -> bytes:
        metadata = declared_parts.get(path)
        if metadata is None or path in used_parts:
            raise ValueError("undeclared or duplicate historical candidate part")
        raw = git_blob(path)
        if not raw or metadata.get("bytes") != len(raw) or metadata.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError("invalid historical candidate part")
        used_parts.add(path)
        return raw

    slim_fields = tuple(slim_fields)
    sha_by_id: dict[str, str] = {}
    slim_by_id: dict[str, dict[str, Any]] = {}
    compressed_by_id: dict[str, bytes] = {}
    compressed_semantic: dict[str, bytes] = {}
    minis: dict[str, dict[str, Any]] = {}
    bucket_members: dict[str, list[str]] = {}
    chunk_meta: dict[str, dict[str, Any]] = {}
    for chunk_row in chunks:
        path = str(chunk_row.get("path") or "")
        raw = git_blob(path)
        if not raw or chunk_row.get("bytes") != len(raw) or chunk_row.get("sha256") != hashlib.sha256(raw).hexdigest():
            return None
        try:
            document = decode_chunk(raw, read_part)
        except (TypeError, ValueError):
            return None
        bucket = Path(path).stem
        if document.get("bucket") != bucket:
            return None
        rows = document.get("candidates") if isinstance(document, dict) else None
        if not isinstance(rows, list) or document.get("candidate_count") != len(rows) or chunk_row.get("candidate_count") != len(rows):
            return None
        members: list[str] = []
        for candidate in rows:
            if not isinstance(candidate, dict):
                return None
            event_id = str(candidate.get("event_id") or "")
            if not event_id or event_id in sha_by_id:
                return None
            canonical = canonical_candidate_bytes(candidate)
            sha_by_id[event_id] = hashlib.sha256(canonical).hexdigest()
            slim_by_id[event_id] = {field: candidate.get(field) for field in slim_fields}
            members.append(event_id)
            # Every dossier's canonical and semantic bytes, compressed, for the two ordered
            # digests below (about a sixth of the raw bytes each); the canonical bytes of the
            # dossiers that may be reused stay, the rest is released after the digests.
            compressed_by_id[event_id] = zlib.compress(canonical, 1)
            compressed_semantic[event_id] = zlib.compress(semantic_candidate_bytes(candidate), 1)
            if keep(event_id):
                minis[event_id] = index_mini(candidate)
        bucket_members[bucket] = members
        chunk_meta[bucket] = dict(chunk_row)
        del document, rows
    if used_parts != set(declared_parts):
        return None
    index_ids = [str(row.get("event_id") or "") for row in index_rows if isinstance(row, dict)]
    if (len(index_ids) != len(index_rows) or "" in index_ids or len(set(index_ids)) != len(index_ids)
            or set(index_ids) != set(sha_by_id) or manifest.get("candidate_count") != len(index_ids)
            or index.get("candidate_count") != len(index_ids)):
        return None
    # The assembled and semantic checksums are the digests git_catalog_digest computed, in
    # index order, so the previous release is proven consistent before anything is reused.
    assembled = list_sha256(zlib.decompress(compressed_by_id[event_id]) for event_id in index_ids)
    if manifest.get("assembled_candidates_checksum_sha256") != assembled:
        return None
    semantic = hashlib.sha256(b"[")
    for position, event_id in enumerate(index_ids):
        if position:
            semantic.update(b",")
        semantic.update(zlib.decompress(compressed_semantic[event_id]))
    semantic.update(b"]")
    del compressed_semantic
    for event_id in list(compressed_by_id):
        if event_id not in minis:
            del compressed_by_id[event_id]
    digest_by_name = {
        str(slim_by_id[event_id].get("name")): sha_by_id[event_id]
        for event_id in index_ids if slim_by_id[event_id].get("name")
    }
    patterns_raw = git_blob(patterns_path)
    patterns: dict[str, list[dict[str, Any]]] = {}
    if patterns_raw:
        try:
            patterns_document = json.loads(patterns_raw)
            if isinstance(patterns_document, dict) and isinstance(patterns_document.get("patterns"), dict):
                patterns = {str(key): list(rows) for key, rows in patterns_document["patterns"].items() if isinstance(rows, list)}
        except (TypeError, json.JSONDecodeError):
            patterns = {}
    return PreviousRelease(
        index={key: value for key, value in index.items() if key != "candidates"},
        index_ids=index_ids, manifest=manifest, chunk_meta=chunk_meta,
        part_meta=declared_parts, sha_by_id=sha_by_id, slim_by_id=slim_by_id,
        compressed_by_id=compressed_by_id, index_minis=minis, bucket_members=bucket_members,
        semantic_checksum=semantic.hexdigest(), assembled_checksum=assembled,
        digest_by_name=digest_by_name, fingerprints=parse_fingerprints_document(git_blob(INPUT_FINGERPRINTS_PATH)),
        patterns=patterns,
    )


# ------------------------------------------------------------------------------- plan
@dataclass
class ReleasePlan:
    mode: str                                   # "full" | "incremental"
    reason: str
    reused: set[str] = field(default_factory=set)
    rebuilt: set[str] = field(default_factory=set)
    sample: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    full_rebuild_at: str | None = None
    previous_as_of: str | None = None


def parse_clock(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def plan_release(
    *,
    enabled: bool,
    as_of: datetime,
    context: str,
    current_ids: list[str],
    facts: dict[str, EventFacts],
    discovery_by_id: dict[str, datetime | None],
    previous_fingerprints: dict[str, Any] | None,
    full_rebuild_hours: float,
    follow_up_window_days: float,
    sample_fraction: float = DEFAULT_SAMPLE_FRACTION,
    sample_minimum: int = DEFAULT_SAMPLE_MINIMUM,
) -> ReleasePlan:
    """Decide which dossiers this release reuses. Full when reuse is off, the previous release
    or its fingerprints are missing, the context changed, the last full rebuild is older than
    the limit, or a sample mismatch was recorded within the limit."""

    now = as_of.isoformat().replace("+00:00", "Z")
    all_ids = set(current_ids)
    if not enabled:
        return ReleasePlan("full", "CTAS_INCREMENTAL=0", rebuilt=all_ids, full_rebuild_at=now)
    document = previous_fingerprints
    if document is None:
        return ReleasePlan("full", "the previous release carries no input fingerprints", rebuilt=all_ids, full_rebuild_at=now)
    if document.get("context_digest") != context:
        return ReleasePlan("full", "the bound code or the source-universe definition changed", rebuilt=all_ids, full_rebuild_at=now)
    previous_as_of = parse_clock(document.get("score_as_of"))
    build = document.get("build") or {}
    last_full = parse_clock(build.get("full_rebuild_at"))
    if previous_as_of is None or last_full is None:
        return ReleasePlan("full", "the previous build record is incomplete", rebuilt=all_ids, full_rebuild_at=now)
    if as_of - last_full >= timedelta(hours=full_rebuild_hours):
        return ReleasePlan("full", f"scheduled full rebuild ({full_rebuild_hours:g} h since {build.get('full_rebuild_at')})",
                           rebuilt=all_ids, full_rebuild_at=now)
    mismatch_at = parse_clock(build.get("sample_mismatch_at"))
    if mismatch_at is not None and as_of - mismatch_at < timedelta(hours=full_rebuild_hours):
        return ReleasePlan("full", f"a reuse sample mismatched at {build.get('sample_mismatch_at')}; full rebuilds for {full_rebuild_hours:g} h",
                           rebuilt=all_ids, full_rebuild_at=now)
    previous_fingerprints = document["fingerprints"]
    reused: set[str] = set()
    counts = {"new": 0, "changed": 0, "clock": 0, "cross-event": 0, "unreadable": 0, "unchanged": 0}
    for event_id in current_ids:
        fact = facts.get(event_id)
        before = previous_fingerprints.get(event_id)
        if before is None:
            counts["new"] += 1
            continue
        if fact is None or not fact.readable:
            counts["unreadable"] += 1
            continue
        if fact.fingerprint != before:
            counts["changed"] += 1
            continue
        if fact.has_signals or fact.has_screens:
            # Multimessenger blocks and collector screens read other events of the release
            # (the public event count, names, positions): rebuilt every cycle.
            counts["cross-event"] += 1
            continue
        if not time_stable(discovery_by_id.get(event_id), fact.latest_observation, previous_as_of, as_of, follow_up_window_days):
            counts["clock"] += 1
            continue
        counts["unchanged"] += 1
        reused.add(event_id)
    rebuilt = all_ids - reused
    size = max(sample_minimum, int(len(reused) * sample_fraction)) if reused else 0
    picker = random.Random(hashlib.sha256(now.encode()).digest())
    sample = set(picker.sample(sorted(reused), min(size, len(reused)))) if size else set()
    return ReleasePlan(
        "incremental", "previous release reused", reused=reused, rebuilt=rebuilt, sample=sample, counts=counts,
        full_rebuild_at=str(build.get("full_rebuild_at")), previous_as_of=document.get("score_as_of"),
    )


def confirm_plan(plan: ReleasePlan, previous: PreviousRelease | None, *, as_of: datetime) -> ReleasePlan:
    """Settle an incremental plan against the previous release as read: without a readable
    previous release nothing is reused; a dossier whose bytes the previous release does not
    hold after all is rebuilt."""

    if plan.mode != "incremental":
        return plan
    now = as_of.isoformat().replace("+00:00", "Z")
    all_ids = plan.reused | plan.rebuilt
    if previous is None:
        return ReleasePlan("full", "the previous release could not be verified", rebuilt=all_ids, full_rebuild_at=now)
    missing = {event_id for event_id in plan.reused if event_id not in previous.compressed_by_id or event_id not in previous.index_minis}
    if missing:
        plan.reused -= missing
        plan.rebuilt |= missing
        plan.sample -= missing
        plan.counts["new"] = plan.counts.get("new", 0) + len(missing)
        plan.counts["unchanged"] = plan.counts.get("unchanged", 0) - len(missing)
    return plan


def fingerprints_document(
    *, generated_at: str, score_as_of: str, context: str, facts: dict[str, EventFacts], ids: Iterable[str],
    build: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": INPUT_FINGERPRINTS_SCHEMA,
        "generated_at": generated_at,
        "score_as_of": score_as_of,
        "context_digest": context,
        "statement": (
            "One 128-bit digest per published record over every database row its dossier was "
            "built from, plus the release-wide context digest. A later release reuses a record's "
            "dossier bytes only while both are unchanged and its clock-dependent score terms "
            "cannot have moved; a sample of reused records is rebuilt and compared every cycle."
        ),
        "build": build,
        "fingerprints": {event_id: facts[event_id].fingerprint for event_id in ids if facts.get(event_id) and facts[event_id].fingerprint},
    }


# ------------------------------------------------------------------------- chunk writer
@dataclass
class ChunkResult:
    chunk_rows: list[dict[str, Any]]                # manifest "chunks" rows (path, candidate_count, bytes, sha256)
    part_rows: list[dict[str, Any]]                 # manifest "parts" rows
    bucket_counts: dict[str, int]
    bucket_members: dict[str, list[str]]            # bucket -> event ids in chunk order
    reused_buckets: int
    written_buckets: int
    largest_file: int
    faithful: bool


def write_chunks(
    *,
    store: DossierStore,
    bucket_of: Callable[[str], str],
    bucket_count: int,
    chunk_schema: str,
    encode_chunk: Callable[[dict[str, Any], int], tuple[bytes, dict[str, bytes]]],
    decode_chunk: Callable[[bytes, Callable[[str], bytes]], dict],
    shard_max_bytes: int,
    write: Callable[[str, bytes], None],
    previous: PreviousRelease | None,
    previous_blob: Callable[[str], bytes | None],
) -> ChunkResult:
    """Write every bucket's chunk (and parts). A bucket whose member set is the previous
    release's and whose members are all reused dossiers takes the previous chunk bytes
    verbatim (verified against the previous manifest); every other bucket is encoded from
    the store, written, read back and compared with the store byte for byte."""

    members_by_bucket: dict[str, list[str]] = {f"{index:03x}": [] for index in range(bucket_count)}
    for event_id in store.order:
        members_by_bucket[bucket_of(event_id)].append(event_id)
    chunk_rows: list[dict[str, Any]] = []
    part_rows: list[dict[str, Any]] = []
    bucket_counts: dict[str, int] = {}
    bucket_members: dict[str, list[str]] = {}
    reused_buckets = written_buckets = 0
    largest = 0
    faithful = True
    previous_parts_by_bucket: dict[str, list[str]] = {}
    if previous is not None:
        for path in previous.part_meta:
            previous_parts_by_bucket.setdefault(Path(path).name.split(".part-")[0], []).append(path)
    for bucket in sorted(members_by_bucket):
        ordered = sorted(members_by_bucket[bucket])
        bucket_members[bucket] = ordered
        bucket_counts[bucket] = len(ordered)
        path = f"ctas/data/candidate-chunks/{bucket}.json"
        reusable = (
            previous is not None and bucket in previous.chunk_meta
            and previous.bucket_members.get(bucket) == ordered
            and all(event_id in store.reused for event_id in ordered)
        )
        if reusable:
            meta = previous.chunk_meta[bucket]
            raw = previous_blob(path)
            if raw is not None and len(raw) == meta["bytes"] and hashlib.sha256(raw).hexdigest() == meta["sha256"]:
                parts_ok = True
                part_bytes: dict[str, bytes] = {}
                for part_path in sorted(previous_parts_by_bucket.get(bucket, [])):
                    part_raw = previous_blob(part_path)
                    part_meta = previous.part_meta[part_path]
                    if part_raw is None or len(part_raw) != part_meta["bytes"] or hashlib.sha256(part_raw).hexdigest() != part_meta["sha256"]:
                        parts_ok = False
                        break
                    part_bytes[part_path] = part_raw
                if parts_ok:
                    write(path, raw)
                    for part_path, part_raw in part_bytes.items():
                        write(part_path, part_raw)
                        part_rows.append({"path": part_path, "bytes": len(part_raw), "sha256": hashlib.sha256(part_raw).hexdigest()})
                        largest = max(largest, len(part_raw))
                    chunk_rows.append({"path": path, "candidate_count": len(ordered), "bytes": len(raw), "sha256": meta["sha256"]})
                    largest = max(largest, len(raw))
                    reused_buckets += 1
                    continue
        document = {
            "schema": chunk_schema, "bucket": bucket, "candidate_count": len(ordered),
            "candidates": [store.decode(event_id) for event_id in ordered],
        }
        raw, parts = encode_chunk(document, shard_max_bytes)
        del document
        write(path, raw)
        for part_path, part_raw in sorted(parts.items()):
            write(part_path, part_raw)
            part_rows.append({"path": part_path, "bytes": len(part_raw), "sha256": hashlib.sha256(part_raw).hexdigest()})
            largest = max(largest, len(part_raw))
        chunk_rows.append({"path": path, "candidate_count": len(ordered), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        largest = max(largest, len(raw))
        written_buckets += 1
        # Read the written chunk back: every dossier must come out as the store holds it.
        decoded = decode_chunk(raw, parts.__getitem__)
        seen = [str(row.get("event_id") or "") for row in decoded.get("candidates", [])]
        if seen != ordered or any(
            canonical_json(row) != store.canonical(event_id) for row, event_id in zip(decoded.get("candidates", []), ordered)
        ):
            faithful = False
        del decoded, parts, raw
    part_rows.sort(key=lambda row: row["path"])
    return ChunkResult(chunk_rows, part_rows, bucket_counts, bucket_members, reused_buckets, written_buckets, largest, faithful)
