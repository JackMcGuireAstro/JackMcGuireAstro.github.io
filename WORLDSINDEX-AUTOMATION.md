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
30 h, or older than 3 h while CTAS is still publishing, raises the alert. See CTAS-AUTOMATION.md
for the full description; the limits are in the workflow's `env:` block.

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
