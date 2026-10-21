# WorldsIndex local refresh and GitHub publication

WorldsIndex uses the same operating pattern as CTAS: the research checks run on
this Mac, a dedicated local checkout publishes only generated public artifacts (to its own data branch),
and GitHub Pages serves the site. ChatGPT-hosted Sites and Codex scheduled tasks
are not part of this path.

## Cloud operation (current)

Since October 2026 WorldsIndex is built in GitHub Actions, not on the Mac.
`.github/workflows/worldsindex-cloud.yml` runs about hourly, started by the live-data
relay's keeper (`relay/worker.js`, Cloudflare cron every 20 minutes, when the last real
run is over 55 minutes old; GitHub's own minute-41 schedule is unreliable here) and on demand:
it checks out main (`site/`) and the ExoNexus builder from the private repository
`JackMcGuireAstro/worldsindex-state` (`source/`, then `npm ci`), restores the builder's
working data with `scripts/worldsindex_cloud_state.py restore` (release `state`, asset
`wi-state-<UTC stamp>.tar.gz`: `data/snapshots`, `data/observations`, `data/atlas`,
`public/data`, `outputs`), runs the unchanged `scripts/publish_worldsindex.sh --full`
with `DATA_BRANCH_DISPATCHER=0`, saves the working data back (an archive without frozen
snapshots is never uploaded; newest three plus one per day for two days are kept) and
starts "Validate and deploy site" when a release was published. The K2 refresh needs
the Mac-only science runtime and is skipped (prior photometry retained), as it already
was on the Mac. The builder's `.env.local`, if any, is the `WORLDSINDEX_ENV` secret and
its values are scrubbed from the kept publish log. The move was done by
`Move WorldsIndex to GitHub.command`. The sections below describe the earlier Mac operation.

Resilience (2026-10-22, RESILIENCE.md). What changed in plain words:

* Every release check has a class in `scripts/check-classes.json`. An **advisory** failure
  (the schema of an optional document such as the monitor's, the front-end helper tests,
  the round trips of the derived exports) never stops a release; it is a `WARN` line and
  one `check-warning` issue. An **integrity** failure blocks; an **always_block** failure
  (credential-shaped content, files outside the manifest, the manifest's own checksums)
  always blocks.
* A **new** integrity check, and each **new or changed** schema file under
  `worldsindex/schemas`, only warns until it was first seen 24 hours ago and passed 3
  consecutive real releases (`worldsindex/data/check-history.json`, published with each
  release and read back by the next).
* When a site check blocks, the cloud run rebuilds the static release **once with the code
  that produced the release currently online**, from the same builder outputs (`--fast
  --force`). If that passes, it is published, `worldsindex/data/cloud-run.json` says
  `code_fallback` and an issue "WorldsIndex published with fallback code" is opened.
  Builder-side failures (monitor, ExoNexus gates, atlas) and the restore never fall back.
  The working data is saved exactly as before.
* Two new guards: `worldsindex.checksums` (always_block) and `worldsindex.count-drop` (the
  object count may not drop by more than 20%; in watch mode for its first day).
* Each run's marker now records `site_commit`, the website commit whose code built it.
* Failed or fallback runs write a sanitized report to the `run-reports` branch
  (`reports/worldsindex-cloud/latest.json`); the watchdog raises `deploy-stale` when the
  live site lags the data branch by more than 60 minutes.

## What runs

`io.github.jackmcguireastro.worldsindex-mirror` runs **every two minutes** while the Mac is
awake, online, and the user is logged in — the same shape as the CTAS mirror
(local files → static export → validation gate → launchd loop → commit and push → Pages).

Every cycle (fast path, `publish_worldsindex.sh --fast`):

1. refreshes the dedicated public-site checkout from `origin/main`;
2. fingerprints the publication inputs in the local ExoNexus source — the atlas gzip, the
   Exoplanet.eu active-snapshot pointer, the release contract, every snapshot manifest, the
   monitor status, the latest promotion outcome — plus the site's builder and assets;
3. exits in well under a second when nothing changed since the last publication;
4. otherwise rebuilds the static release from those local files, runs the static release test,
   the science test, the syntax checks and the artifact guard, stages only the explicit
   allowlist, commits, and pushes. Any failure publishes nothing.

Every hour (`WORLDSINDEX_FULL_EVERY`, 3600 s), or when no full run has completed yet, the cycle
runs the full path instead: the provider monitor over every declared source, the Exoplanet.eu
promotion gate when that source changed, ExoNexus type checking, tests, lint and production
build, atlas regeneration — and then the fast path publishes whatever those produced. So an
upstream change is detected within the hour and is on the public site within two minutes of
the gate accepting it,
and a change you make locally (a new snapshot, a rollback, an edited contract) is published on
the next two-minute cycle without waiting for the monitor.

GitHub then runs the `Validate and deploy site` workflow. **Validation is the deployment
gate**: the `deploy` job runs only when `validate` succeeds, so a commit that fails the static
release tests never becomes the live site and the previous successful deployment stays up. A
final `verify-live` job fetches the deployed manifest and catalog index and confirms their
hashes match the commit. This requires the repository's Pages source to be **GitHub Actions**
(Settings → Pages → Build and deployment → Source).

Rollback: re-run the last green workflow run from the Actions tab, or `git revert` and push.
Nothing is ever force-pushed.

### Which local files it follows

`WORLDSINDEX_SOURCE_MODE=copy` (the default): macOS blocks background launch agents from
reading `~/Documents`, so the installer makes an operational copy of ExoNexus at
`~/Library/Application Support/WorldsIndexPublisher/source` and the agent follows *that*.
Code and frozen inputs mirror the editable checkout at install time; receipts and promotions
advance in the copy and do not reach git. Rerun the installer after changing source code.

`WORLDSINDEX_SOURCE_MODE=direct`: the agent runs against the editable git checkout itself, so
the site follows every local change and each promotion or rollback is committed into the
repository by explicit path. This needs the checkout to live outside `~/Documents`,
`~/Desktop` and `~/Downloads` (for example `~/Projects/`); the installer refuses otherwise.
This is the mode that makes "constantly updated from my local files" literally true.

`.env.local` remains local with mode 0600 and is never copied into the public-site checkout.

## Where releases are published

Releases are not committed to `main`. Each publication replaces the
`worldsindex-data` branch with one commit holding exactly the allowlisted artifacts
below (`scripts/data_branch.sh`, store in the runtime checkout's
`.git/worldsindex-data-store`); the deploy workflow combines `main` with the latest
commit of `worldsindex-data` and `ctas-data`. Only this publisher writes the branch,
so the two publishers can no longer collide on `main`, and the repository keeps no
history of data releases. Every build starts from an empty `worldsindex/data`
(ignored on `main`). `bash scripts/overlay_published_data.sh` places the published
data in a local checkout for previews.

## Scientific boundary

A provider change marker is evidence that a source may have changed. It is not
permission to mix unreviewed upstream rows into the public catalog. The monitor
receipt updates automatically, while measurements stay on the last reconciled
snapshot until that source's fetch, normalization, identity reconciliation,
provenance, and regression gates pass. Failed and quarantined runs leave the
last-good catalog measurements unchanged; their typed failure state remains
publicly visible instead of being mistaken for freshness.

The publisher can publish only:

- `worldsindex/data/manifest.json`
- `worldsindex/data/registry.json.gz`
- `worldsindex/data/sky-detections.json.gz`
- `worldsindex/data/source-monitor.json`
- `worldsindex/data/transit-ephemerides.json.gz`
- `worldsindex/data/details/00.json.gz` through `ff.json.gz`

No token, `.env` file, source checkout, raw private receipt, or arbitrary site
file is included.

## Disk use on this Mac

The runner shares `scripts/publisher_housekeeping.sh` with CTAS: it skips a cycle when free
space is below `PUBLISHER_MIN_FREE_GB` (default 25 GB), and once a day it keeps the runtime
checkout to the newest 100 commits, drops `refs/worldsindex-recovery/*` older than 14 days,
clears automatic recovery stash entries and collects Git garbage.

## Freshness watchdog

`.github/workflows/freshness-watchdog.yml` checks the live `worldsindex/data/manifest.json`
(and the CTAS status) hourly from GitHub Actions and keeps one `freshness-watchdog` issue open
while either publisher has stalled, closing it on recovery. A WorldsIndex release older than
6 h, or older than 3 h while CTAS is still publishing, raises the alert; so do three failed
`worldsindex-cloud.yml` runs in a row (a run can publish and then fail saving its working
state, as every run did from 2026-10-14) and a published `worldsindex/data/cloud-run.json`
marker whose restored state is older than 6 h. See "Freshness watchdog" and "When an alert
fires" in CTAS-AUTOMATION.md for the full description and what to do; the limits are in the
workflow's `env:` block.

## Operations

Install or repair the service:

```sh
./scripts/install_worldsindex_mirror.sh
```

Inspect it without printing credentials:

```sh
./scripts/diagnose_worldsindex_mirror.sh
```

Run a non-publishing end-to-end build in a suitable clean checkout (`--fast` follows the
local files only; `--full` also runs the monitor, the promotion gate and the test suite):

```sh
./scripts/publish_worldsindex.sh --fast --dry-run
./scripts/publish_worldsindex.sh --full --dry-run
```

Force the next scheduled cycle to run the full path: `touch`-free — delete
`~/Library/Logs/worldsindex-mirror/.last-full-run`, or run the installer again.

If a build is interrupted and leaves regenerated files in the runtime checkout, the next
cycle snapshots them on `refs/worldsindex-recovery/<stamp>-unfinished-<commit>` (through a
temporary index, never a patch) and returns the checkout to `HEAD` before syncing; the
publisher itself returns `worldsindex/data` to `HEAD` on every exit that does not commit.
Such references are safe to delete once inspected; the next build reproduces the data.

Disable the service while retaining its recoverable checkout:

```sh
./scripts/install_worldsindex_mirror.sh --uninstall
```

Operational files live in:

- runtime checkout: `~/Library/Application Support/WorldsIndexPublisher/site`
- operational ExoNexus mirror: `~/Library/Application Support/WorldsIndexPublisher/source`
- logs: `~/Library/Logs/worldsindex-mirror`
- launch agent: `~/Library/LaunchAgents/io.github.jackmcguireastro.worldsindex-mirror.plist`

The public application is
<https://jackmcguireastro.github.io/worldsindex/>.

## Transit ephemerides

`worldsindex/data/transit-ephemerides.json.gz` (schema
`worldsindex-transit-ephemerides.v1`, declared in `manifest.artifacts` and summarised in
`manifest.transitEphemerides`) lists one ephemeris per transiting planet for the Tonight's
sky transit predictor and the light-curve fold. It is columnar (`columns` + `rows`): objectId,
name, host, raDeg, decDeg, status, periodDays (+ periodErrDays), t0Bjd (BJD_TDB, + t0ErrDays),
durationHours, depthPpt, hostMag + hostMagBand (V or TESS), timeSystem, and the source
row (sourceId, sourceTable, sourceRecordId, reference, referenceUrl, matchedVia).

`scripts/worldsindex_transit_ephemerides.mjs` builds it from the builder's detail records:
period and epoch always from one row (PSCompPars, then the PS default row, then the newest PS
row, then the TOI/K2/KOI row with the smallest mid-time uncertainty propagated to the atlas
date), false-positive rows never used, each catalog row used for one planet only (NASA
planets first), and a separate TOI record attached to a NASA planet only when the TIC matches
and the periods agree within 0.5%. KOI epochs are converted from BKJD (+2454833). Symmetric
1-sigma = the larger reported error. The file is stamped with the atlas time, so it changes
only when the atlas or its rows do.

The frozen NASA PS / PSCompPars snapshot (2026-08-23) has no `pl_tranmid`, durations, depths
or magnitudes, so today every ephemeris comes from the TOI, KOI or K2 tables (about 12,700
planets on the 2026-10-03 state; about 4,480 of the 4,690 NASA planets discovered by transit).
When the ExoNexus snapshot adds `pl_tranmid`, `pl_tranmiderr1/2`, `pl_trandur`, `pl_trandep`,
`pl_tsystemref`, `sy_vmag`, `sy_tmag` (and `pl_tranmid_reflink` for PSCompPars), the builder
copies them and the policy prefers them automatically. Measure a release with
`node scripts/worldsindex_transit_ephemerides.mjs worldsindex/data`; tests:
`node scripts/test_worldsindex_ephemerides.mjs` and the checks in
`scripts/test_worldsindex_static.py` (every period and epoch equals its cited row).

## Live light curves from MAST

Packaged light curves are only the HATNet discovery light curves the builder copies
(`public/data/lightcurves`, ~58 HAT-P planets). For every object whose host star has a
TIC, KIC or EPIC number in its catalog rows (PSCompPars `tic_id`, TOI `tid`, KOI `kepid`,
K2 `epic_hostname`/`tic_id`, or a host named "TIC …"), the Light curves tab can load TESS,
Kepler and K2 light curves from MAST on request (`worldsindex/assets/live-mast.js`):

- **Lookup** runs directly against the MAST API (`Mast.Caom.Filtered`, exact
  `target_name`: the TIC number, `kplr` + 9-digit KIC, `ktwo` + 9-digit EPIC), then
  `Mast.Caom.Products`. Canonical planets without an identifier in the rows ask the NASA
  Exoplanet Archive (PSCompPars, through the relay's TAP route) for the TIC number. Only
  when nothing is found by identifier does it fall back to a 5″ cone around the position;
  if that cone holds several stars, each file is labelled with its star.
- **Files offered**: TESS SPOC 2-minute light curves, TESS-SPOC full-frame light curves
  (a MAST high-level science product; the relay route for these is in `relay/worker.js`
  and must be deployed before they load), Kepler and K2 30- and 1-minute light curves,
  grouped by mission with the sector, quarter (from the file timestamp) or campaign.
- **Reading**: the FITS file comes through the relay (`/live-config.json`) and is parsed in
  the browser: PDCSAP_FLUX (SAP_FLUX when PDCSAP is empty), TSCAL/TZERO/TNULL applied,
  cadences with lightkurve's default QUALITY bits dropped (TESS 175, Kepler/K2 1130799;
  or strictly QUALITY = 0), each file normalised to its median. Several files can be
  loaded and viewed separately or together.
- **Folding**: the period and mid-transit epoch of one catalog row (the same policy as
  `transit-ephemerides.json.gz`, see `worldsindex/assets/ephemeris.js`) shade the
  predicted transit windows on the time view and drive a folded view with binned medians,
  optional running-median trend removal (transits masked), a rough observed depth and the
  propagated mid-time uncertainty.

Nothing is stored by WorldsIndex. Tests: `node scripts/test_worldsindex_live_mast.mjs`
(synthetic FITS files written by astropy, `scripts/make_worldsindex_lc_fixtures.py`),
`node scripts/test_worldsindex_photometry.mjs`, `node scripts/test_worldsindex_ephemerides.mjs`
and `python3 scripts/test_worldsindex_lightcurves_browser.py` (MAST and relay stubbed).

The Sources tab's "Live from the archives" section fetches, on request and through
the same relay, the planet's atmospheric spectra (NASA Exoplanet Archive `spectra`
table, with the data file plotted in the browser), host-star records
(`stellarhosts`), microlensing solutions (`ml`) for microlensing planets, the
ExoFOP-TESS overview for TOIs, and papers from NASA ADS (free key held by the relay).

## Interoperability, provenance and the status page (Framework v2, Track 1)

Everything in this section is additive: new files under `worldsindex/data/`, new keys in
`manifest.json`, new panels that render only when their data exists. Nothing is published
or announced anywhere else (standing decision, 2026-10-19): no DOIs, no registry entries,
no bots.

### Exports

`scripts/worldsindex_interop.mjs` (called by the static builder) writes ECSV 1.0 and
VOTable 1.5 copies of every public table under `worldsindex/data/exports/`: `objects`,
`measurements` (one row per retained catalog value per publication, as the source states
it), `transit_ephemerides`, `source_coverage`, `identity_states` and `references`, each
`.ecsv.gz` and `.vot.gz`, plus `exports/index.json` with every column's datatype, UCD1+,
VOUnits unit and description. A column-metadata registry is the single source of those
descriptions; a table that emits an unregistered column, or omits a registered one, stops
the build. Rows carry a HEALPix order-8 nested index and are sorted by it. VOParquet copies
of `objects` and `measurements` (`scripts/worldsindex_parquet.py`, astropy + pyarrow) are
built outside the Pages tree and uploaded by the publisher as assets of the rolling GitHub
Release `worldsindex-data-latest`; `bulk-downloads.json` names them with bytes, SHA-256 and
URLs, or says why none was built. Tests: `node --test scripts/test_worldsindex_interop.mjs`
and `python3 scripts/test_worldsindex_interop.py` (Astropy/pyarrow round trip of a built
release).

### Schemas and the API description

`worldsindex/openapi.yaml` (OpenAPI 3.1) describes every static endpoint; one JSON Schema
2020-12 file per record type lives under `worldsindex/schemas/` (`manifest`,
`catalog-index`, `details-shard`, `sky-detections`, `registry`, `transit-ephemerides`,
`source-coverage`, `source-monitor`, `lightcurve-index`, `lightcurve`, `cloud-run`,
`exports-index`, `bulk-downloads`, `provenance`, `changelog`, `feed`, `common`).
`scripts/validate_worldsindex_schemas.py` runs `check-jsonschema` over every published
top-level JSON file and a time-bounded sample of the detail shards, checks the schemas
against their metaschema and the OpenAPI document against the schema set, and fails on any
published JSON file without a schema; the publisher and the deploy workflow refuse a release
on a mismatch. The status page links every schema file, and the static test checks that the
list and the directory agree.

### Provenance

`worldsindex/data/provenance.json` is W3C PROV-JSON per build
(`scripts/worldsindex_provenance.mjs`): the release and every artifact with its SHA-256,
the atlas projection, every declared source with contract and parser version, snapshot id
and digest, retrieval time, last attempt and monitor state, the identity rules with the
reviewed re-identification pins, the builder and site commits, the state asset restored
(from the cloud-run marker) and the VOParquet assets. `manifest.json` names it by
`provenancePath`.

### Changelog and feeds

`scripts/worldsindex_changelog.mjs` compares the build with the previous published release
(the data-branch tip, read through the data store) and writes `changelog.json`,
`changelog.md`, `feed.json` (JSON Feed 1.1) and `feed.xml` (Atom): objects added and
removed, promotions to confirmed, demotions, identity-state changes, native rows and
spectra added per object, source-state and monitor changes; capped lists, complete counts;
"not computable" rather than a guess when the previous release lacks the object export.
The feeds are passive files a reader fetches; nothing posts anywhere. The catalog page's
footer and `<link rel="alternate">` point at them.

### Source contributions, v1 and v2

The builder's `public/data/source-contributions.json.gz` (per object: aliases and
source-native rows from the frozen context sources) is consumed by the static builder
through `scripts/worldsindex_contributions.mjs`, which accepts schema versions
`worldsindex-source-contributions.v1` (2026-10-05) and `.v2` (builder commit `c54691a`,
2026-10-16; additive `link`/`via` per row and `links`/`viaCrossIdentifier`/`omitted` per
source) for the matching atlas projection, logs which version it consumed and how many
rows, and ignores any other file whole with the reason. Until 2026-10-20 the static
builder accepted only v1, so every release built from a v2 file — every cloud run since
the 2026-10-16 landing (builder `56f699f`) — silently carried no context rows, no NASA
aliases and `sourceContributions: null` in its manifest (the live manifest of 2026-10-20
05:00 UTC: 86,109 packaged rows, `sourceContributions: null`; the restored 2026-10-05
state builds 314,135 packaged rows with its v2 file consumed and 89,595 without it).
The Evidence tab's "Packaged source records" table shows the attaching identifier and,
for rows that arrived through a cross-identifier another source stated, which source
stated it. Tests: `node --test scripts/test_worldsindex_front.mjs` (v1/v2 fixtures) and
the release-level checks in `scripts/test_worldsindex_static.py`.

### Status page

`worldsindex/status.html` (+ `worldsindex/assets/status.js`) reads only published files
and shows, with a text summary before every table: the release facts and the cloud-run
marker; per source the four denominators from `source-coverage.json`'s `denominators`
block (declared / applicable / executed / data-bearing, with object counts), the
builder's eligible / attached / missing-at-provider / blocked counts, the source state and
the last retrieval attempt (`RETRIEVED`, `UNCHANGED`, `REFUSED`, `FAILED`, `SKIPPED …` with
category and the provider's words); the provider monitor's state including a quarantine
and the provider's retained error message per failing required check; identity and
status counts; and links to `openapi.yaml`, every schema, `provenance.json`,
`bulk-downloads.json`, the exports index, the feeds and the changelog. One h1, an unbroken
heading outline; nothing about the reader is kept.

### Sky panel and report-a-problem link

Every object workspace with a catalog coordinate (all but nine of the 2026-10 releases'
objects, the Solar System rows the Open Exoplanet Catalogue lists without coordinates)
gains a **Sky** tab (`worldsindex/assets/sky.js`), and the Sky (catalog) page an
interactive atlas of the filtered objects. Both load nothing until opened:

- **Aladin Lite v3** from the CDS CDN, pinned to `3.6.1`
  (`https://aladin.cds.unistra.fr/AladinLite/api/v3/3.6.1/aladin.js`, the build the CTAS
  pages pin; "latest" only as a stated fallback when the CDN stops serving it), with four
  background layers — DSS2 colour, Legacy Surveys DR10 colour, Pan-STARRS DR1 colour
  (north of Dec −30°) and 2MASS colour — and DSS2 as the fallback when a layer fails. The
  host marker sits at the catalog coordinate; the other catalog objects within 5′ (same
  host and nearby hosts, from the catalog index already in the browser) are drawn as a
  second marker layer and listed beneath. **Licence:** Aladin Lite is CDS software under
  the LGPL-3.0-or-later; it is loaded unmodified from CDS, its logo and link stay in the
  view, and the panel credits each HiPS provider.
- **Printable finder chart** at 2′ and 10′: JPEG cutouts fetched by the browser from the
  Legacy Surveys viewer (DR10, then DR9), the Pan-STARRS cutout service north of Dec −30°,
  and CDS HiPS2FITS (DSS2) last, with north/east arrows, crosshair, scale bar, name, ICRS
  coordinates and epoch; a print-friendly view opens in a new tab. Nothing is stored.
- **Web SAMP** (`http://localhost:21012/`, XML-RPC built in the page): `coord.pointAt.sky`
  for the catalog position and `table.load.votable` with the object's rows (a VOTable
  built in the browser from the release's selected values and the packaged native rows,
  handed to the hub as a `data:` URL and downloadable); the catalog page sends the
  published `exports/objects.vot.gz`. Works in Chrome, Firefox and Edge with a running hub
  (TOPCAT, Aladin); not in Safari — the panel says so.
- **Report a problem**: every object workspace carries a link to a prefilled GitHub issue
  on the website repository — title `<name> (<object id>)`, label `data-problem` (kept by
  the deploy workflow with `gh label create --force`), body with the stable object id, the
  atlas projection id, the release checksum (atlas SHA-256), the build time, the panel the
  reader was on and the dossier URL. Nothing is sent until the reader posts it.

Tests: `node --test scripts/test_worldsindex_front.mjs` (pure helpers) and
`python3 scripts/test_worldsindex_sky_browser.py` (Playwright; Aladin, the image services
and the SAMP hub stubbed locally; axe-core when installed).

## Conflict sets, selections and the Compare tab (Framework v2, Track 2a)

The ExoNexus builder derives, from the atlas and the frozen snapshots, one row per **source
assertion** (one source, one record, one property: NASA PS per publication, PSCompPars, TOI,
KOI, K2, Exoplanet.eu, OEC, Gaia DR3 planetary transits, CoRoT, and the frozen NASA
transiting-planet and microlensing tables), normalised in unit, definition and time frame
without touching the source's own value, and compares every pair of rows of a property only
after identity, definition, unit, frame/epoch and method are assessed. Pairs carry a relation
from the contract vocabulary (`statistically-inconsistent`, `method-incompatible`,
`definition-incompatible`, `epoch-dependent`, `identity-ambiguity`, `status-conflict`,
`revision-or-retraction`, `not-yet-assessed`) and comparable rows a verdict at k = 3 combined
sigma. Selections follow rule `nasa-pscomppars-like.v1` (the most recent default parameter set,
gaps filled from the latest publication, closed-form values labelled "calculated" with formula
and inputs); each is a *selection, not a measurement*, with rule, selector `builder`, date,
review state `unreviewed`, chosen rows and reason, and never replaces or averages a source row.
Reconciliation flags follow Exo-MerCat's published rules (period mismatch over 10 %, fallback
merge, duplicate and misnamed entries, status reconciliation).

`scripts/worldsindex_conflicts.mjs` turns the builder's `public/data/conflict-sets.ndjson.gz`
into release artifacts, streaming (the product is about 640 MB as JSON):

- `conflicts/<xx>.json.gz` — 256 shards keyed like `details/` (sha256 of the object id), so
  the Compare tab fetches one file of about 200 kB;
- `conflict-summary.json` — counts per property, relation, verdict and source pair;
- `conflict-index.json.gz` — every set in conflict, the base of the changelog's **new** and
  **resolved conflicts** (`changelog.json` `conflicts`, `summary.newConflicts/resolvedConflicts`);
- `controversial-retracted-excluded.json.gz` — every object a source flags as controversial,
  retracted or refuted, or dispositions as a false positive, and every status conflict, with
  the reasons; the status page lists it on request;
- the flat **`conflicts`** and **`selections`** export tables (ECSV/VOTable, registered with
  UCDs in `scripts/worldsindex_interop.mjs`); PROV-JSON names the conflict sets, the comparison
  policy and the selection rule (a `prov:Plan`).

All of it is optional: a builder without the product gives a release with
`manifest.conflictSets = null`, no Compare tab and no new tables, exactly as before.

The **Compare** tab on every object (`worldsindex/assets/compare.js`, imported only when the
tab opens; the button is hidden without `manifest.conflictSets`) shows one row per source
assertion with the original and normalised value, uncertainty or limit (limits and ranges in
words and with a shape), the time system and frame beside every epoch, method or instrument,
source and reference (bibcode link), compatibility with the selected row and the row's state,
the selection row above them, every row when the property is in conflict, a candidate-solutions
toggle otherwise, filters by method, date, source and state, a display-unit toggle
(R⊕/R♃, M⊕/M♃, days/years, au/R☉) that never changes the original value, "export exactly these
rows" as ECSV and VOTable built in the browser with the release's column conventions plus a
manifest (filters, release digest, file SHA-256) and a citation list, copy/download per
property, and a time-evolution plot with its table for each property with two or more dated
publications.

Tests: `scripts/test_worldsindex_compare.mjs` (rendering, filters, unit toggle, export rows ==
visible rows, sharding, changelog), `scripts/test_worldsindex_compare_browser.py` (lazy loading,
a known conflict, filters, export, outline, no sideways scroll, axe), and the conflict checks in
`test_worldsindex_static.py`, `test_worldsindex_interop.py` and `validate_worldsindex_schemas.py`
(schemas `conflict-sets-shard`, `conflict-summary`, `conflict-index`, `status-exceptions`; the
conflict shards are sampled within the same time budget as the detail shards).
