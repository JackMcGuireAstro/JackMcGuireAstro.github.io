#!/usr/bin/env python3
"""Plain-words release changelog for CTAS (ctas/data/changelog.json).

The exporter already diffs each release against the previous published one (the
release-history entry counts added, removed and changed records by name). This module
turns the same two snapshots into what a reader would want to know, keyed by the
stable event id so a rename is a rename and not a removal plus an addition:

- new events (by stable event id) and removed events;
- new classifications (unclassified to a label) and revised classifications;
- retractions (a record whose status became retracted or bogus, or that gained a
  retraction clock);
- renames (the name changed, the event id stayed: AT2026zxx becoming SN2026zxx);
- source-state changes (a source going degraded or recovering, from the previous
  release's source universe);
- coverage milestones (the catalog passing a round number of records, a source
  contributing its first retained records, new source contracts declared).

Every sentence is a fixed template over counts and names: no model writes anything
here. The entry is deterministic for a given pair of releases; the only clock it
carries is the release time the exporter passes in. Entries are carried forward from
the previous release's changelog, newest first, at most ``KEEP_ENTRIES``, and an
entry whose content equals one already kept is not repeated (``entry_id`` is the
SHA-256 of the entry without its publication time).

The public schema is ``ctas/schemas/changelog.schema.json``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

CHANGELOG_SCHEMA = "ctas.public-release-changelog@1.0.0"
KEEP_ENTRIES = 24
LIST_LIMIT = 200
DEGRADED_STATES = frozenset({"temporarily-unavailable", "rate-limited", "provider-failure"})
RETRACTED_STATUSES = frozenset({"retracted", "bogus"})
UNCLASSIFIED = frozenset({"", "unclassified", "none", "unknown"})
CLAIM_BOUNDARY = (
    "Entries describe changes between two public CTAS releases, read from the retained "
    "provider rows. A new event is a new public record, not a new discovery; a new "
    "classification is a provider's assertion, not CTAS's; a source-state change describes "
    "CTAS's own ability to query a provider, not the provider's service."
)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _is_unclassified(label: Any) -> bool:
    return _text(label).strip().lower() in UNCLASSIFIED


def _brief(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": _text(candidate.get("event_id")),
        "name": _text(candidate.get("name")),
        "event_type": candidate.get("event_type"),
        "primary_messenger": candidate.get("primary_messenger"),
        "discovery_survey": candidate.get("discovery_survey"),
        "classification": candidate.get("classification"),
    }


def _capped(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows[:LIST_LIMIT]


def _source_rows(universe: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(universe, dict):
        return {}
    rows = universe.get("sources")
    if not isinstance(rows, list):
        return {}
    return {_text(row.get("source_key")): row for row in rows if isinstance(row, dict) and row.get("source_key")}


def _record_total(row: dict[str, Any]) -> int:
    counts = row.get("public_record_counts")
    if not isinstance(counts, dict):
        return 0
    total = 0
    for value in counts.values():
        try:
            total += int(value or 0)
        except (TypeError, ValueError):
            continue
    return total


def _thousands_crossed(previous: int, current: int, step: int) -> list[int]:
    if previous is None or current is None or step <= 0 or current <= previous:
        return []
    return [mark for mark in range((previous // step + 1) * step, current + 1, step)]


def build_entry(
    *,
    previous_candidates: list[dict[str, Any]] | None,
    current_candidates: list[dict[str, Any]],
    previous_universe: dict[str, Any] | None,
    current_universe: dict[str, Any],
    previous_accounting: dict[str, Any] | None,
    current_accounting: dict[str, Any],
    published_at: str,
    catalog_content_checksum_sha256: str,
    previous_catalog_content_checksum_sha256: str | None,
    base_commit: str | None,
) -> dict[str, Any] | None:
    """One changelog entry for the pair of releases, or None when nothing changed
    or there is no previous release to compare with."""

    if previous_candidates is None:
        return None
    previous_by_id = {_text(row.get("event_id")): row for row in previous_candidates if row.get("event_id")}
    current_by_id = {_text(row.get("event_id")): row for row in current_candidates if row.get("event_id")}

    new_events = sorted((_brief(current_by_id[event_id]) for event_id in set(current_by_id) - set(previous_by_id)),
                        key=lambda row: (row["name"], row["event_id"]))
    removed_events = sorted(({"event_id": event_id, "name": _text(previous_by_id[event_id].get("name"))}
                             for event_id in set(previous_by_id) - set(current_by_id)),
                            key=lambda row: (row["name"], row["event_id"]))

    classification_changes: list[dict[str, Any]] = []
    retractions: list[dict[str, Any]] = []
    renames: list[dict[str, Any]] = []
    for event_id in sorted(set(current_by_id) & set(previous_by_id)):
        before, after = previous_by_id[event_id], current_by_id[event_id]
        name_before, name_after = _text(before.get("name")), _text(after.get("name"))
        if name_before != name_after:
            renames.append({"event_id": event_id, "previous_name": name_before, "name": name_after})
        label_before, label_after = before.get("classification"), after.get("classification")
        if _text(label_before) != _text(label_after) and not _is_unclassified(label_after):
            classification_changes.append({
                "event_id": event_id, "name": name_after,
                "kind": "new" if _is_unclassified(label_before) else "revised",
                "previous_classification": None if _is_unclassified(label_before) else _text(label_before),
                "classification": _text(label_after),
            })
        status_before, status_after = _text(before.get("status")).lower(), _text(after.get("status")).lower()
        became_retracted = status_after in RETRACTED_STATUSES and status_before not in RETRACTED_STATUSES
        gained_retraction_clock = bool(after.get("latest_retraction_at")) and not before.get("latest_retraction_at")
        if became_retracted or gained_retraction_clock:
            retractions.append({
                "event_id": event_id, "name": name_after,
                "previous_status": status_before or None, "status": status_after or None,
                "basis": "status" if became_retracted else "retraction-notice",
            })

    previous_sources = _source_rows(previous_universe)
    current_sources = _source_rows(current_universe)
    source_state_changes: list[dict[str, Any]] = []
    first_records: list[dict[str, Any]] = []
    for key in sorted(set(current_sources) & set(previous_sources)):
        before, after = previous_sources[key], current_sources[key]
        state_before, state_after = _text(before.get("operational_state")), _text(after.get("operational_state"))
        if state_before != state_after:
            if state_after in DEGRADED_STATES and state_before not in DEGRADED_STATES:
                direction = "degraded"
            elif state_before in DEGRADED_STATES and state_after not in DEGRADED_STATES:
                direction = "recovered"
            else:
                direction = "changed"
            source_state_changes.append({
                "source_key": key, "name": _text(after.get("name") or key),
                "previous_state": state_before or None, "state": state_after or None, "direction": direction,
            })
        if _record_total(before) == 0 and _record_total(after) > 0:
            first_records.append({"source_key": key, "name": _text(after.get("name") or key), "records": _record_total(after)})
    new_sources = sorted(({"source_key": key, "name": _text(current_sources[key].get("name") or key)}
                          for key in set(current_sources) - set(previous_sources)), key=lambda row: row["source_key"])

    milestones: list[dict[str, Any]] = []
    previous_count, current_count = len(previous_by_id), len(current_by_id)
    for mark in _thousands_crossed(previous_count, current_count, 1000):
        milestones.append({"kind": "catalog-size", "value": mark,
                           "text": f"The public catalog passed {mark:,} records ({previous_count:,} to {current_count:,})."})
    previous_accounting = previous_accounting if isinstance(previous_accounting, dict) else {}
    for field, step, words in (
        ("dataBearingSourceEvaluations", 1000, "source evaluations that returned retained data"),
        ("executedQueryReceipts", 10000, "executed source-query receipts"),
    ):
        try:
            before_value = int(previous_accounting.get(field) or 0)
            after_value = int(current_accounting.get(field) or 0)
        except (TypeError, ValueError):
            continue
        for mark in _thousands_crossed(before_value, after_value, step):
            milestones.append({"kind": field, "value": mark, "text": f"CTAS passed {mark:,} {words} ({before_value:,} to {after_value:,})."})
    for row in first_records:
        milestones.append({"kind": "first-retained-records", "source_key": row["source_key"],
                           "text": f"{row['name']} contributed its first retained public records ({row['records']:,})."})
    if new_sources:
        milestones.append({"kind": "source-contracts-declared", "value": len(new_sources),
                           "text": f"{len(new_sources)} new source contract{'s' if len(new_sources) != 1 else ''} declared: "
                                   + ", ".join(row["name"] for row in new_sources) + "."})

    entry = {
        "published_at": published_at,
        "catalog_content_checksum_sha256": catalog_content_checksum_sha256,
        "previous_catalog_content_checksum_sha256": previous_catalog_content_checksum_sha256,
        "base_commit": base_commit,
        "previous_candidate_count": previous_count,
        "candidate_count": current_count,
        "new_event_count": len(new_events),
        "removed_event_count": len(removed_events),
        "classification_change_count": len(classification_changes),
        "retraction_count": len(retractions),
        "rename_count": len(renames),
        "source_state_change_count": len(source_state_changes),
        "new_events": _capped(new_events),
        "removed_events": _capped(removed_events),
        "classification_changes": _capped(classification_changes),
        "retractions": _capped(retractions),
        "renames": _capped(renames),
        "source_state_changes": source_state_changes,
        "coverage_milestones": milestones,
    }
    entry["summary"] = plain_words(entry)
    if not any((new_events, removed_events, classification_changes, retractions, renames, source_state_changes, milestones)):
        return None
    entry["entry_id"] = entry_id(entry)
    return entry


def entry_id(entry: dict[str, Any]) -> str:
    content = {key: value for key, value in entry.items() if key not in {"published_at", "entry_id", "base_commit"}}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count:,} {singular if count == 1 else (plural or singular + 's')}"


def _names(rows: list[dict[str, Any]], total: int | None = None, key: str = "name", limit: int = 5) -> str:
    shown = [_text(row.get(key)) for row in rows[:limit]]
    rest = (len(rows) if total is None else total) - len(shown)
    return ", ".join(shown) + (f" and {rest:,} more" if rest > 0 else "")


def plain_words(entry: dict[str, Any]) -> list[str]:
    """Fixed-template sentences for one entry (what a reader sees first)."""

    sentences: list[str] = []
    new_events = entry.get("new_events") or []
    if entry.get("new_event_count"):
        surveys: dict[str, int] = {}
        for row in new_events:
            survey = _text(row.get("discovery_survey")) or "survey not recorded"
            surveys[survey] = surveys.get(survey, 0) + 1
        by_survey = ", ".join(f"{count} from {survey}" for survey, count in sorted(surveys.items(), key=lambda item: (-item[1], item[0]))[:4])
        if entry["new_event_count"] > len(new_events):
            by_survey = f"of the first {len(new_events)}: {by_survey}"
        sentences.append(f"{_plural(entry['new_event_count'], 'new public record')}"
                         + (f" ({by_survey})" if by_survey else "") + f": {_names(new_events, entry['new_event_count'])}.")
    if entry.get("removed_event_count"):
        sentences.append(f"{_plural(entry['removed_event_count'], 'record')} left the public catalog: {_names(entry.get('removed_events') or [], entry['removed_event_count'])}.")
    changes = entry.get("classification_changes") or []
    new_labels = [row for row in changes if row.get("kind") == "new"]
    revised = [row for row in changes if row.get("kind") == "revised"]
    if new_labels:
        sentences.append(f"{_plural(len(new_labels), 'new classification')}: "
                         + "; ".join(f"{row['name']} is now reported as {row['classification']}" for row in new_labels[:5])
                         + (f"; and {len(new_labels) - 5:,} more" if len(new_labels) > 5 else "") + ".")
    if revised:
        sentences.append(f"{_plural(len(revised), 'revised classification')}: "
                         + "; ".join(f"{row['name']} from {row['previous_classification']} to {row['classification']}" for row in revised[:5])
                         + (f"; and {len(revised) - 5:,} more" if len(revised) > 5 else "") + ".")
    retractions = entry.get("retractions") or []
    if retractions:
        sentences.append(f"{_plural(entry.get('retraction_count') or len(retractions), 'retraction')}: {_names(retractions, entry.get('retraction_count'))}.")
    renames = entry.get("renames") or []
    if renames:
        sentences.append(f"{_plural(len(renames), 'rename')} (same event id): "
                         + "; ".join(f"{row['previous_name']} is now {row['name']}" for row in renames[:5])
                         + (f"; and {len(renames) - 5:,} more" if len(renames) > 5 else "") + ".")
    states = entry.get("source_state_changes") or []
    for direction, words in (("degraded", "went degraded"), ("recovered", "recovered"), ("changed", "changed state")):
        rows = [row for row in states if row.get("direction") == direction]
        if rows:
            sentences.append(f"{_plural(len(rows), 'source')} {words}: "
                             + "; ".join(f"{row['name']} ({row['previous_state']} to {row['state']})" for row in rows[:5])
                             + (f"; and {len(rows) - 5:,} more" if len(rows) > 5 else "") + ".")
    for milestone in entry.get("coverage_milestones") or []:
        sentences.append(_text(milestone.get("text")))
    if not sentences:
        sentences.append("No public record, classification, status, name or source-state change between these two releases.")
    return sentences


def carry_forward(previous_changelog: Any, entry: dict[str, Any] | None, *, generated_at: str) -> dict[str, Any]:
    """The published changelog: the previous release's entries plus this one, newest first."""

    entries: list[dict[str, Any]] = []
    if isinstance(previous_changelog, dict) and previous_changelog.get("schema") == CHANGELOG_SCHEMA \
            and isinstance(previous_changelog.get("entries"), list):
        entries = [row for row in previous_changelog["entries"] if isinstance(row, dict) and row.get("entry_id")]
    if entry is not None and all(row.get("entry_id") != entry["entry_id"] for row in entries):
        entries.insert(0, entry)
    entries = entries[:KEEP_ENTRIES]
    return {
        "schema": CHANGELOG_SCHEMA,
        "generated_at": entries[0]["published_at"] if entries else generated_at,
        "statement": (
            "What changed between consecutive public CTAS releases, in plain words, derived from "
            "the previous release's published files (the exporter's --release-base-ref). "
            f"Newest first, at most {KEEP_ENTRIES} entries; each list is capped at {LIST_LIMIT} rows "
            "and carries its full count."
        ),
        "claim_boundary": CLAIM_BOUNDARY,
        "entry_count": len(entries),
        "entries": entries,
    }


def changelog_problems(document: dict[str, Any]) -> list[str]:
    """Structural problems a release must not ship with."""

    problems: list[str] = []
    if document.get("schema") != CHANGELOG_SCHEMA:
        problems.append("changelog: unexpected schema")
    entries = document.get("entries")
    if not isinstance(entries, list) or document.get("entry_count") != len(entries):
        return problems + ["changelog: entry_count does not match entries"]
    ids = [row.get("entry_id") for row in entries]
    if len(ids) != len(set(ids)):
        problems.append("changelog: duplicate entry_id")
    for row in entries:
        if row.get("entry_id") != entry_id(row):
            problems.append(f"changelog: entry_id does not reproduce for {row.get('published_at')}")
        for key in ("new_events", "removed_events", "classification_changes", "retractions", "renames"):
            if len(row.get(key) or []) > LIST_LIMIT:
                problems.append(f"changelog: {key} exceeds the list cap")
        if not isinstance(row.get("summary"), list) or not row["summary"]:
            problems.append("changelog: entry without plain-words summary")
    return problems
