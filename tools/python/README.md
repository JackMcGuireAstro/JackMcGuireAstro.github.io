# Python readers for CTAS and WorldsIndex

Two small packages over the public, static releases of
[CTAS](https://jackmcguireastro.github.io/ctas.html) and
[WorldsIndex](https://jackmcguireastro.github.io/worldsindex/). They read the same files the
web pages read; there is no account, key or server-side query. They live in the website's
repository and are **not published to PyPI**: copy this folder, or put it on your Python path.

| package | what it is |
|---|---|
| `jmastro` | an astroquery-style client: `CTAS()` and `WorldsIndex()` with `query_object`, `query_region`, `query_filter`, and the full records (`get_dossier`, `get_photometry`, `get_datalink`; `get_detail`, `get_measurements`) |
| `tom_ctas` | a [TOM Toolkit](https://tom-toolkit.readthedocs.io/) broker (`tom_alerts.alerts.GenericBroker`): query form, `fetch_alerts`, `to_generic_alert`, `fetch_alert` |

```python
import sys; sys.path.insert(0, "tools/python")
from jmastro import CTAS, WorldsIndex

ctas = CTAS()
ctas.query_object("SN 2024ggi")                 # astropy Table (a list of dicts without astropy)
ctas.query_region(210.80, 54.35, radius=0.5)    # degrees, nearest first, with distance_deg
ctas.query_filter({"schema": "jm.visitor-filter@1", "product": "ctas", "match": "all",
                   "clauses": [{"field": "classification", "op": "contains", "value": "SN Ia"},
                               {"field": "discovery_time", "op": "within_days", "value": 14}]})
dossier = ctas.get_dossier(event_id)            # complete public record, SHA-256 checked
worlds = WorldsIndex()
worlds.query_object("TRAPPIST-1 e"); worlds.get_measurements("object-trappist-1-e")
```

Filters use the format of the Follow page (`tools/follow/`; field list and operators in
`jmastro/_filters.py`, which is a byte-for-byte copy of `scripts/visitor_filters.py`); a filter
shared from the page works here unchanged. "Within the last N days" counts back from the
release time, so the answer depends on the release, not on when you run it.

**TOM Toolkit.** With `tools/python` on the path of your TOM project:

```python
# settings.py
TOM_ALERT_CLASSES = [..., "tom_ctas.CTASBroker"]
```

The broker's query form has classification, event type, minimum CTAS score, maximum discovery
magnitude, "discovered within N days" and a result limit. The CTAS score is an ordering aid for
follow-up review, not a probability or a classification. The broker's own tests use stand-ins for
the Django and `tom_alerts` classes, so it has not been run inside a live TOM here.

**Offline or against a checkout.** `CTAS(base_url=...)` and `WorldsIndex(base_url=...)` accept a
URL or a local directory holding a site tree (this repository with the data branches overlaid by
`bash scripts/overlay_published_data.sh`); the environment variable `JMASTRO_BASE_URL` does the
same. The tests use the local tree:

```
python3 -m unittest discover -s tools/python/tests -v
```

Requirements: Python 3.9+; astropy for tables, DataLink and the measurements export (optional for
the rest). Please cite the original sources named in each record; CTAS and WorldsIndex report what
public sources published.
