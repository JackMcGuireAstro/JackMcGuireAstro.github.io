#!/usr/bin/env python3
"""The visitor-filter engine of tools/lib/visitor-filters.js, in Python (Framework v2 Track 5).

The deploy publishes one JSON Feed and one Atom feed per gallery filter
(tools/follow/gallery.json) with this module; the Follow page evaluates the same filters in
the visitor's browser with the JavaScript module. scripts/test_visitor_products.py runs both
on the same release and requires identical results, so the field list, operators and the
"within the last N days of the release" rule below must stay in step with the JavaScript.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable

FILTER_SCHEMA = "jm.visitor-filter@1"
DAY_MS = 86_400_000

FIELDS: dict[str, dict[str, str]] = {
    "ctas": {
        "name": "text", "event_type": "text", "primary_messenger": "text", "messenger_channels": "list",
        "ra_deg": "number", "dec_deg": "number", "discovery_time": "time", "discovery_survey": "text",
        "discovery_magnitude": "number", "status": "text", "classification": "text", "ctas_score": "number",
        "redshift": "number", "updated_at": "time", "latest_classification_at": "time", "latest_spectrum_at": "time",
        "latest_retraction_at": "time", "n_observations": "number", "n_spectra": "number", "conflict_count": "number",
        "ranking_channel": "text", "identity_state": "text", "lc_fit_model": "text", "lc_rise_days": "number",
        "lc_peak_abs_mag": "number", "broker_consensus": "text",
    },
    "worldsindex": {
        "name": "text", "hostName": "text", "raDeg": "number", "decDeg": "number", "normalizedStatus": "text",
        "methodCode": "text", "methodCodes": "list", "discoveryYear": "number", "discoveryFacility": "text",
        "identityState": "text", "sourceIds": "list", "sourceRecordCount": "number",
    },
}
ID_FIELD = {"ctas": "event_id", "worldsindex": "objectId"}
OPS_BY_TYPE = {
    "text": {"eq", "ne", "in", "contains", "exists", "missing"},
    "list": {"contains", "exists", "missing"},
    "number": {"eq", "ne", "gte", "lte", "gt", "lt", "exists", "missing"},
    "time": {"within_days", "after", "before", "exists", "missing"},
}


def _missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, list) and not value)


def time_ms(value: Any) -> int | None:
    """Milliseconds since the epoch, as JavaScript's Date.parse reads the ISO forms we publish."""
    if _missing(value):
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        else:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                # JavaScript reads a date-time without an offset as local time; we publish none
                # of those, so treat them as UTC and keep both engines on the same footing.
                moment = moment.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    seconds = moment.timestamp()
    return math.floor(round(seconds * 1000, 3))


def validate_filter(flt: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(flt, dict):
        return ["The filter is not an object."]
    if flt.get("schema") != FILTER_SCHEMA:
        problems.append(f'schema must be "{FILTER_SCHEMA}".')
    fields = FIELDS.get(flt.get("product"))
    if fields is None:
        problems.append('product must be "ctas" or "worldsindex".')
        fields = {}
    if flt.get("match", "all") not in ("all", "any"):
        problems.append('match must be "all" or "any".')
    clauses = flt.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        problems.append("A filter needs at least one condition.")
        clauses = []
    elif len(clauses) > 20:
        problems.append("A filter takes at most 20 conditions.")
    for i, clause in enumerate(clauses, 1):
        if not isinstance(clause, dict):
            problems.append(f"Condition {i} is not an object.")
            continue
        kind = fields.get(clause.get("field"))
        if kind is None:
            problems.append(f'Condition {i}: "{clause.get("field")}" is not a field of {flt.get("product")}.')
            continue
        op = clause.get("op")
        if op not in OPS_BY_TYPE[kind]:
            problems.append(f'Condition {i}: "{op}" does not apply.')
            continue
        value = clause.get("value")
        if op in ("exists", "missing"):
            continue
        if op == "in":
            if not isinstance(value, list) or not value:
                problems.append(f"Condition {i}: give a list of values.")
            continue
        if kind == "number":
            try:
                if not math.isfinite(float(value)):
                    raise ValueError
            except (TypeError, ValueError):
                problems.append(f"Condition {i}: needs a number.")
        if op == "within_days":
            try:
                if not float(value) > 0:
                    raise ValueError
            except (TypeError, ValueError):
                problems.append(f"Condition {i}: give a number of days above 0.")
        if op in ("after", "before") and time_ms(value) is None:
            problems.append(f"Condition {i}: give a date.")
        if op in ("contains", "eq", "ne") and _missing(value):
            problems.append(f"Condition {i}: give a value.")
    return problems


def _clause(clause: dict, record: dict, kind: str, now_ms: int | None) -> bool:
    value = record.get(clause["field"])
    op = clause["op"]
    if op == "exists":
        return not _missing(value)
    if op == "missing":
        return _missing(value)
    if _missing(value):
        return False
    if kind == "number":
        try:
            x = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(x):
            return False
        y = float(clause["value"])
        return {"eq": x == y, "ne": x != y, "gte": x >= y, "lte": x <= y, "gt": x > y, "lt": x < y}.get(op, False)
    if kind == "time":
        t = time_ms(value)
        if t is None:
            return False
        if op == "within_days":
            return now_ms is not None and now_ms - float(clause["value"]) * DAY_MS <= t <= now_ms + DAY_MS
        if op == "after":
            return t > time_ms(clause["value"])
        if op == "before":
            return t < time_ms(clause["value"])
        return False
    if kind == "list":
        items = [str(v).lower() for v in value] if isinstance(value, list) else [str(value).lower()]
        return op == "contains" and str(clause["value"]).lower() in items
    text = str(value).lower()
    if op == "eq":
        return text == str(clause["value"]).lower()
    if op == "ne":
        return text != str(clause["value"]).lower()
    if op == "in":
        return text in [str(v).lower() for v in clause["value"]]
    if op == "contains":
        return str(clause["value"]).lower() in text
    return False


def apply_filter(flt: dict, records: Iterable[dict], now: str | None) -> list[dict]:
    problems = validate_filter(flt)
    if problems:
        raise ValueError(" ".join(problems))
    fields = FIELDS[flt["product"]]
    now_ms = time_ms(now)
    any_mode = flt.get("match") == "any"
    out = []
    for record in records:
        results = [_clause(c, record, fields[c["field"]], now_ms) for c in flt["clauses"]]
        if (any(results) if any_mode else all(results)):
            out.append(record)
    return out


def inflate_ctas_rows(index: dict) -> list[dict]:
    cols = index.get("candidate_columns") or []
    return [dict(zip(cols, row)) for row in index.get("candidate_rows") or []]
