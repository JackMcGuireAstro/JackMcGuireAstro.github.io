"""The CTAS broker for TOM Toolkit (tom_alerts.alerts.GenericBroker) over the static release.

fetch_alerts(parameters) reads the public catalog index (jmastro.CTAS) and yields catalog rows
matching the query form; to_generic_alert(alert) turns a row into a tom_alerts GenericAlert;
fetch_alert(id) returns the complete public dossier. Nothing is sent to CTAS (there is no CTAS
server): every call is a read of files on GitHub Pages.

When TOM Toolkit (Django) is not installed, small stand-ins with the same names let the module
import, so its logic can be tested and used from plain Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

try:  # pragma: no cover - exercised only inside a TOM Toolkit project
    from django import forms
    from tom_alerts.alerts import GenericAlert, GenericBroker, GenericQueryForm
    HAVE_TOM = True
except Exception:  # ImportError, or Django not configured
    HAVE_TOM = False

    @dataclass
    class GenericAlert:  # same fields as tom_alerts.alerts.GenericAlert
        timestamp: Any
        url: str
        id: str
        name: str
        ra: float
        dec: float
        mag: float | None
        score: float | None

    class GenericBroker:  # the parts of tom_alerts.alerts.GenericBroker the CTAS broker uses
        def to_target(self, alert):
            generic = self.to_generic_alert(alert)
            return {"name": generic.name, "ra": generic.ra, "dec": generic.dec, "type": "SIDEREAL", "epoch": 2000}, {}, []

    class _Field:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class _Forms:
        CharField = FloatField = IntegerField = _Field

    forms = _Forms()

    class GenericQueryForm:
        def __init__(self, *args, **kwargs):
            pass

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jmastro import CTAS  # noqa: E402

FILTER_SCHEMA = "jm.visitor-filter@1"


class CTASQueryForm(GenericQueryForm):
    classification = forms.CharField(required=False, help_text="Reported classification contains this text, e.g. SN Ia")
    event_type = forms.CharField(required=False, help_text="CTAS event type, e.g. optical-transient")
    min_score = forms.FloatField(required=False, help_text="Minimum CTAS follow-up score (an ordering aid, not a probability)")
    max_magnitude = forms.FloatField(required=False, help_text="Discovery magnitude at most this (brighter)")
    discovered_within_days = forms.IntegerField(required=False, help_text="Discovered within this many days of the release")
    max_results = forms.IntegerField(required=False, initial=100, help_text="Return at most this many alerts (newest change first)")


def query_parameters_to_filter(parameters: dict) -> dict | None:
    """The query form's fields as a visitor filter (jm.visitor-filter@1), or None for no condition."""
    clauses = []
    if parameters.get("classification"):
        clauses.append({"field": "classification", "op": "contains", "value": str(parameters["classification"])})
    if parameters.get("event_type"):
        clauses.append({"field": "event_type", "op": "eq", "value": str(parameters["event_type"])})
    if parameters.get("min_score") not in (None, ""):
        clauses.append({"field": "ctas_score", "op": "gte", "value": float(parameters["min_score"])})
    if parameters.get("max_magnitude") not in (None, ""):
        clauses.append({"field": "discovery_magnitude", "op": "lte", "value": float(parameters["max_magnitude"])})
    if parameters.get("discovered_within_days") not in (None, ""):
        clauses.append({"field": "discovery_time", "op": "within_days", "value": int(parameters["discovered_within_days"])})
    if not clauses:
        return None
    return {"schema": FILTER_SCHEMA, "product": "ctas", "match": "all", "clauses": clauses}


def _when(row: dict) -> datetime | None:
    clocks = [row.get(k) for k in ("latest_retraction_at", "latest_messenger_at", "latest_spectrum_at", "latest_classification_at", "discovery_time")]
    stamps = []
    for value in clocks:
        if value:
            try:
                stamps.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
            except ValueError:
                pass
    stamps = [s if s.tzinfo else s.replace(tzinfo=timezone.utc) for s in stamps]
    return max(stamps) if stamps else None


def alert_to_generic(row: dict) -> GenericAlert:
    return GenericAlert(
        timestamp=_when(row),
        url=f"https://jackmcguireastro.github.io/ctas.html?event={row['event_id']}",
        id=row["event_id"],
        name=row.get("name") or row["event_id"],
        ra=row.get("ra_deg"),
        dec=row.get("dec_deg"),
        mag=row.get("discovery_magnitude"),
        score=row.get("ctas_score"),
    )


class CTASBroker(GenericBroker):
    name = "CTAS"
    form = CTASQueryForm
    score_description = "CTAS follow-up score: an ordering aid for follow-up review, not a probability or a classification."

    def __init__(self, base_url: str | None = None, *args, **kwargs):
        try:
            super().__init__(*args, **kwargs)
        except TypeError:  # pragma: no cover
            pass
        self.client = CTAS(base_url)

    def fetch_alerts(self, parameters: dict) -> Iterator[dict]:
        flt = query_parameters_to_filter(parameters or {})
        rows = self.client.query_filter(flt, as_table=False) if flt else list(self.client.catalog_rows())
        rows = [r for r in rows if isinstance(r.get("ra_deg"), (int, float)) and isinstance(r.get("dec_deg"), (int, float))]
        rows.sort(key=lambda r: (_when(r) or datetime(1970, 1, 1, tzinfo=timezone.utc), r["event_id"]), reverse=True)
        limit = int((parameters or {}).get("max_results") or 100)
        return iter(rows[:max(1, limit)])

    def fetch_alert(self, id: str) -> dict:
        return self.client.get_dossier(id)

    def to_generic_alert(self, alert: dict) -> GenericAlert:
        return alert_to_generic(alert)

    def process_reduced_data(self, target, alert=None):  # pragma: no cover - TOM hook, nothing to ingest
        return None
