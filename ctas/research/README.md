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

## Complete dossiers and large records

The complete-catalog manifest at `ctas/data/candidate-chunks/manifest.json`
lists 4096 stable UUID buckets and any supplemental files under `parts`.
Ordinary buckets contain complete candidate JSON directly. An oversized bucket
contains a `ctas.candidate-chunk-parts@1.0.0` descriptor: verify its ordered
part paths against the global manifest, check each part's byte count and SHA-256,
and concatenate the `json_fragment` strings from the verified JSON envelopes.
Check the assembled byte count and SHA-256 before parsing the original bucket.
Every observation, source receipt, and other published field is preserved.

On the live site the bucket roots and parts are served gzip-compressed: fetch
`<path>.gz` for any `path` in the manifest (for example
`ctas/data/candidate-chunks/efe.json.gz`) and decompress it. The decompressed bytes
are exactly the file the manifest describes, so verify byte counts and SHA-256
after decompression. The manifest itself is served uncompressed.

The website performs this reconstruction when a dossier is opened. Python
consumers can use `scripts/ctas_chunks.py` for the same lossless decoding;
`scripts/check_ctas_links.py` also verifies manifest integrity, exact part
coverage, and the complete catalog's event identities and reconstruction hash.
Each detail download remains at or below 4 MiB even when a single dossier grows
beyond that size. Download all files declared by the manifest when retaining
the entire catalog; a multipart descriptor alone is not the scientific record.

## Interoperable formats (added 2026-10-19)

Every public research table is also published as ECSV 1.0 (Astropy APE 6) and
VOTable 1.5, with a UCD on every column, a VOUnits string on every column that
carries a physical unit, and a description per column:

- `ctas/data/research/candidates.{ecsv,vot}`: the complete catalog-index table,
  one row per public candidate (every `candidate_columns` column, with the
  release score clock; `events.csv` keeps the same columns without the clock
  and `events.vot` stays the fifteen-column summary it has been since August);
- `aliases`, `sources` (the complete source universe), `tom-targets`,
  `source-accounting` (per-record source accounting with the count of
  applicable sources in each query outcome) and `release-history`, in the same
  two formats.

Absent values are masked in ECSV and VOTable (text columns are empty); JSON-typed
columns hold compact JSON text. Each pair was read back with Astropy and compared
cell for cell with its JSON source before the release was certified
(`interoperability-exports-round-trip` in `ctas/data/certification.json`); the
column metadata is repeated in `ctas/data/research/manifest.json`.

Bulk tables are Apache Parquet files with IVOA VOParquet 1.0 metadata (the
`IVOA.VOTable-Parquet.version` and `IVOA.VOTable-Parquet.content` keys TOPCAT,
STILTS and Astropy read), rows sorted by HEALPix order 8 nested index (column
`healpix_8_nested`), row groups of 50,000 rows:

- `ctas-candidates.parquet`: the candidate table;
- `ctas-photometry.parquet`: every retained public photometric observation and
  limit with its provider and CTAS receipt identifiers; `detection` and
  `is_upper_limit` are explicit boolean columns and `magnitude` is null for a
  limit, never NaN.

They are assets of the rolling GitHub Release tagged `ctas-data-latest`, not files in the
Pages tree; `ctas/data/bulk-downloads.json` lists their names, sizes, SHA-256
digests and download URLs. Verify the digest after downloading: an asset can lag
the Pages release by one cycle. In Astropy, `Table.read(path, format="parquet")`
or `format="parquet.votable"` (units and UCDs restored); in TOPCAT, open the
file directly.

`ctas/data/links/<bucket>.xml` is an IVOA DataLink 1.1 links document for every
dossier in that bucket (the three-hex-digit bucket of the dossier chunk, the
`detail_chunk` column of the catalog index). Keep the rows whose `ID` is the
CTAS event identifier: `#this` is the dossier chunk, `#documentation` the
dossier page and the circulars, reports and classification citations,
`#progenitor` the TNS object page, messenger notices and sky maps, `#auxiliary`
broker and photometry pages, spectra, archive products and host or catalog
records, `#derivation` the research tables whose row for that event is derived
from the dossier. The documents are sharded by bucket rather than written per
event so the Pages tree gains at most 4,096 files, not one per record; their
content depends only on retained evidence (no release clock), so a bucket whose
dossiers did not change is byte-identical between releases and the publisher
commits nothing for it. `ctas/data/links/manifest.json` lists every document
with its checksum.

`ctas/data/provenance.json` is W3C PROV-JSON for the build: the upstream sources
with their contract versions, retrieval times and parser versions, the frozen
database snapshot and the state archive it was restored from, the bound code
hashes, the collector commit, the site commit, the exporter versions, the
collector run and the export; `status.json` names it under `provenance_path`.
`ctas/openapi.yaml` describes every static endpoint (OpenAPI 3.1) and names the
JSON Schema under `ctas/schemas/` that each JSON response validates against;
`scripts/validate_ctas_schemas.py` runs those checks over every release before
it is committed (the publisher phase `validate_ctas_schemas.py`).

What the certificate binds among these: every ECSV, VOTable, DataLink document
and manifest, `bulk-downloads.json` and `provenance.json` are bound as data
files; `ctas/openapi.yaml`, `ctas/schemas/*.json`, `scripts/ctas_interop.py`
and `scripts/validate_ctas_schemas.py` are bound as code. The VOParquet
release assets are not bound directly, because they live outside the Pages
tree: the certificate binds `bulk-downloads.json`, which carries each asset's
byte length and SHA-256, so verifying a downloaded asset against it is the
binding. `CITATION.cff` and `codemeta.json` at the repository root are
repository metadata, not release data, and are not bound.
