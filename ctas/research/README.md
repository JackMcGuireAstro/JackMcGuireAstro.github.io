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

## Feeds, changelog, status page and sky tools (added 2026-10-19, part C)

**Feeds.** `ctas/data/feeds/` holds Atom 1.0 and JSON Feed 1.1 files written by
the exporter through `scripts/ctas_feed.py`: `all-events`, one feed per event
type the exporter enumerates (`event-type/<event-type>`), one per ranking
channel, i.e. messenger family (`channel/optical`, `channel/x-ray-gamma-ray`,
`channel/radio`, `channel/neutrino-multimessenger`,
`channel/contextual-non-target`), and one per sky region (`region/...`). The
regions are the 192 HEALPix order-2 NESTED pixels in ICRS (nside 4, about 215
square degrees each, `healpix2-000` to `healpix2-191`), the galactic plane
(`galactic-plane`: J2000 galactic latitude within 10 degrees) and the two
equatorial hemispheres (`north`: Dec >= 0, `south`: Dec < 0); a record can be
in up to three region feeds. Order 2 was chosen over order 3 (768 regions, 1,536
files) because a region of about 215 square degrees is a useful "my patch of
sky" for a reader and keeps the Pages tree to 428 feed files. Every feed holds
its newest 100 records ordered by the record's public change clock (the latest
of discovery, classification, spectrum, messenger-notice and retraction clocks;
never the export time), its item ids are the stable event ids, and its bytes
carry no release clock, so an unchanged feed is byte-identical between
releases. The set of feeds is fixed, so every address is stable and an empty
set is still a valid feed. `ctas/data/feeds/index.json` lists every file with
byte length and SHA-256; the exporter validates each file (JSON Feed required
members; Atom well-formed with feed id, updated and entry ids) before writing,
`scripts/validate_ctas_schemas.py` repeats the check on the published tree, and
`ctas/data/feed.xml` (strong new follow-up targets) is unchanged.

**Changelog.** `ctas/data/changelog.json` (`scripts/ctas_changelog.py`, schema
`ctas/schemas/changelog.schema.json`) compares each release with the previous
published one by stable event id: new and removed records, new and revised
classifications, retractions (status or retraction notice), renames (the name
changed, the id stayed: AT2026zxx becoming SN2026zxx), source-state changes
from the previous release's source universe (degraded, recovered, changed) and
coverage milestones (round numbers of records or evaluations, a source's first
retained records, new contracts). Each entry carries fixed-template sentences in
plain words, is identified by the SHA-256 of its content, and entries are
carried forward from the previous release's changelog (newest first, at most
24). No model writes any of it. The catalog page shows it inside "Publication
history" (fetched only when that panel is opened); `ctas/status.html` shows it
in full.

**Status page.** `ctas/status.html` (`ctas/status.js`) reads `status.json`
and the files it names: `ctas/data/source-accounting.json` (schema
`source-accounting.schema.json`) gives the four denominators per source across
the release, declared (the contract is in this universe version), applicable
(records whose rule names the source), executed (query receipts with an executed
terminal outcome, and the records holding one) and data-bearing (records whose
current query outcome is DATA_RETURNED, beside the records that keep retained
rows from the source), with totals that must reproduce the aggregate
`source_accounting` of `status.json` or the release fails (gate
`changelog-feeds-and-source-accounting-integrity`). Per source, "declared" is a
yes/no fact and the other three are record counts; the aggregates are their
sums. The page also shows the provider connector health from `status.json`, the
release clocks and freshness window, the watchdog's limits as the workflow file
sets them (they are not in any release), the cloud-run marker when the release
names one (`cloud_run_marker_path`), the changelog, the feeds and the links to
`openapi.yaml`, the schemas, `provenance.json`, the bulk downloads and the
DataLink manifest. Its one optional live request (open `freshness-watchdog`
issues from the public GitHub API) runs only when a button is pressed and is
labelled live.

**Sky tools.** `ctas/sky.js` adds to the dossier's sky panel and to the
catalog page's Sky section: Aladin Lite v3 from the CDS CDN pinned to one
version (`https://aladin.cds.unistra.fr/AladinLite/api/v3/3.6.1/aladin.js`;
if the CDN no longer serves that version the page says so and uses `latest`),
loaded only when a reader opens an atlas, with background layers DSS2 colour
(`CDS/P/DSS2/color`), Legacy Surveys DR10 colour
(`CDS/P/DESI-Legacy-Surveys/DR10/color`), Pan-STARRS DR1 colour
(`CDS/P/PanSTARRS/DR1/color-z-zg-g`, Dec > -30) and 2MASS colour
(`CDS/P/2MASS/color`); a layer that fails falls back to DSS2 and says so. The
HiPS identifiers are CDS identifiers (checked against the CDS HiPS list by
`ID` in the MOCServer, `https://alasky.cds.unistra.fr/MocServer/query?ID=<id>&get=id`);
if CDS renames one, the layer falls back and the identifier is the only line to
change. Aladin Lite is CDS software under LGPL-3.0-or-later; its logo and link
stay in every view, as the licence and the CDS terms ask. For multimessenger
records the atlas draws an approximate localisation region: a circle of area
equal to the newest retained 90% sky area, or of radius equal to the retained
coordinate uncertainty, labelled approximate until the collector publishes MOCs
(Track 3); no MOC or HEALPix product is in `ctas/data` today. The printable
finder chart (2' and 10') is fetched by the browser, in order, from the Legacy
Surveys viewer (`https://www.legacysurvey.org/viewer/jpeg-cutout?...&layer=ls-dr10`,
then `ls-dr9`), the Pan-STARRS cutout service north of Dec -30
(`ps1filenames.py` then `fitscut.cgi`), and CDS HiPS2FITS DSS2, with north and
east arrows, crosshair, scale bar, name, ICRS coordinates and epoch; "Open print
view" opens a print-friendly page in a new tab. Nothing fetched is stored. The
Web SAMP button sends `coord.pointAt.sky` for the retained position (the catalog
page's button sends the all-events VOTable); Web SAMP works in Chrome, Firefox
and Edge with a running hub and not in Safari. Every dossier carries a "Report a
problem" link: a prefilled GitHub issue on the website repository (title
`<name> (<event id>)`, label `data-problem`, body with the stable event id, the
release checksum and the panel). The deploy workflow creates the `data-problem`
label idempotently.
