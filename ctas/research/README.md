# CTAS research quickstart

The [CTAS quickstart notebook](ctas-quickstart.ipynb) is a small, reproducible
example of using the public CTAS research tables with Python's standard library.
It downloads no private data and requires no API key.

The notebook:

1. fetches the public CTAS status document;
2. verifies the research-manifest SHA-256 recorded by that status document;
3. verifies every table's byte count and SHA-256 against the manifest;
4. checks CSV row counts and the catalog-content checksum shared by the status
   document and research manifest; and
5. constructs a coordinate-valid cohort with a CTAS follow-up score of at least
   70, excluding records currently marked `retracted` or `bogus`.

That cohort is an operational triage example, not a list of confirmed
discoveries or scientifically preferred targets. A CTAS score is a follow-up
ordering aid, not a probability, confidence, classification, or measurement of
scientific importance.

## Run it

Download the notebook from the CTAS Research view or open this file in a local
Jupyter installation. The code cells themselves use only Python 3 standard
library modules. They fetch the current public release from:

`https://jackmcguireastro.github.io/`

For a local static-site test, set `CTAS_PUBLIC_ROOT` to the root URL of that
HTTP server before starting the notebook. The override is a URL, not a file
path, and the notebook refuses cross-origin artifact paths supplied by a
manifest.

## Public data contract

The notebook begins with `ctas/data/status.json`, then follows the
`artifacts.research_tables` pointer to:

- `ctas/data/research/manifest.json`
- `ctas/data/research/events.csv`
- `ctas/data/research/aliases.csv`
- `ctas/data/research/sources.csv`
- additional table formats listed by that exact manifest

Nested measurements, spectra, notices, reports, source-query receipts, and
other complete event evidence remain in the checksum-bound candidate shards.
The normalized event table is intended for catalog-scale selection; open or
export the corresponding dossier before using a candidate in scientific work.

## Integrity boundary

Checksum verification detects incomplete or internally mixed CTAS artifacts.
It does not independently establish the scientific validity of provider claims
or authenticate GitHub Pages against a separate trust authority. Always retain
the displayed catalog-content checksum, access time, stable event UUIDs, and
original-provider citations with a scientific result.
