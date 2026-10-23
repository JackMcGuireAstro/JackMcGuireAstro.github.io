# CTAS public-catalog automation

CTAS is published as a public static catalog. Since October 2026 the scientific
database and the Python ingestion pipeline run in GitHub Actions, not on the Mac
(see "Cloud operation" below); the publisher pushes only an explicit allowlist
of public artifacts to this GitHub Pages repository. The LaunchAgent sections
further down describe the earlier Mac operation, kept for reference and for
falling back to it.

## Cloud operation (current)

`.github/workflows/ctas-cloud.yml` runs in a chain: each cycle that saved its database
starts the next (about every 30 minutes). GitHub's own schedule (minute 11) fires only
every few hours for this repository, so the live-data relay's keeper (`relay/worker.js`,
a Cloudflare cron trigger every 20 minutes) restarts the chain whenever no run is going
and the last real run started more than 75 minutes ago. Each cycle:

1. `scripts/ctas_cloud_state.py restore` downloads the collector's database and
   fixed input files from the private repository `JackMcGuireAstro/ctas-state`
   (release `state`: `static-inputs.tar.gz` and `soc-<UTC stamp>.db.gz`).
2. The collector (`supernova-ops api`, code from the same private repository,
   settings from the `CTAS_BACKEND_ENV` secret) gathers alerts for 15 minutes.
3. `scripts/publish_ctas.sh` builds, verifies and publishes a release to
   `ctas-data` while the collector keeps running, exactly as on the Mac, with
   `DATA_BRANCH_DISPATCHER=0` because an Actions token may not push workflow files;
   the run then starts "Validate and deploy site" itself.
4. The collector stops cleanly, `scripts/ctas_db_retention.py daily` trims superseded
   derived records (as the Mac's daily job did), and `ctas_cloud_state.py save` checkpoints the
   database, requires `PRAGMA quick_check` = ok, uploads a new copy and keeps the
   newest three plus one per day for the two days before. A damaged or failed copy
   never replaces the last good one.

Secrets: `CTAS_STATE_TOKEN` (the owner's `gh` sign-in key; fine-grained keys could not
see the new private repository) and `CTAS_BACKEND_ENV`; the relay holds the same key as
`GH_DISPATCH_TOKEN`. If the key stops working (gh signed out, password change), every
run fails at the private checkout; `Renew cloud key.command` replaces both copies. The collector's own log is not kept (it may contain
source addresses with keys); the publisher's log is a five-day artifact. Dry run:
Actions -> "CTAS cloud collector and publisher" -> Run workflow -> dry run.
The move from the Mac was done by `Move CTAS to GitHub.command`, which also
dropped all but the newest three `certification_runs` (August audit evidence,
1.8 GB) from the uploaded copy.

No local dashboard, managed database, secret manager, or human approval step is
part of this publication path.

Resilience (2026-10-22, RESILIENCE.md). What changed in plain words:

* Every release check has a class in `scripts/check-classes.json`. An **advisory** failure
  (layout, byte budgets, page copy, a test that pins a real event by name, the round trips
  of the derived ECSV/VOTable/VOParquet exports, feed and changelog formatting) never stops
  a release: the log says `WARN`, and one `check-warning` issue per check is opened, closed
  again when the check passes. An **integrity** failure blocks. An **always_block** failure
  (secrets or banned keys, unmanifested files, the release's own checksums) always blocks.
* A **new** integrity check, and each **new or changed** schema file under `ctas/schemas`,
  only warns until it was first seen 24 hours ago and passed 4 consecutive real releases;
  the counts are published with each release in `ctas/data/check-history.json` and read
  back by the next one, so promotion needs no commit.
* When a check blocks with the current site code, the cloud run publishes **once more with
  the code that produced the release currently online**, on the same frozen database. If
  that passes, the release goes out, its `ctas/data/cloud-run.json` says `code_fallback`
  (which commit and check failed, which commit was used) and an issue "CTAS published with
  fallback code" is opened. If the same check fails with that code too, the data is bad:
  nothing is published and a `release-blocked` issue says so. Never more than one attempt
  per run; the database is saved exactly as before.
* Two new guards: `ctas.checksums` (every file the certificate lists has the bytes it
  states; always_block) and `ctas.count-drop` (the candidate count may not drop by more
  than 20% against the published release; integrity, in watch mode for its first day).
* The deploy validates main and, if main's blocking checks fail, the code that produced
  the current CTAS release, then the last deployed code, and deploys the first that passes
  (section 4 of RESILIENCE.md). Right after a CTAS code landing the deployed front end is
  the one the live certificate binds until the next CTAS release certifies main.
* The hourly watchdog compares the live site with the data branches and raises
  `deploy-stale` when the site lags by more than 60 minutes.
* A failed or fallback run writes a sanitized report to the `run-reports` branch:
  `https://raw.githubusercontent.com/JackMcGuireAstro/JackMcGuireAstro.github.io/run-reports/reports/ctas-cloud/latest.json`.

## Architecture

```text
local CTAS SQLite database
        |
        | transactionally consistent SQLite backup
        v
Python public exporter and validators
        |
        | sub-2 MiB live summary + 4096 stable detail roots with bounded parts + research tables
        v
dedicated runtime checkout (public repository only, code from main)
        |
        | allowlisted release -> one parentless commit replacing the ctas-data branch
        v
GitHub Actions: main's code + latest ctas-data + latest worldsindex-data,
validated, dossier chunks gzipped, deployed
        |
        v
GitHub Pages /ctas.html
```

Generated data is not committed to `main`. Each release replaces the `ctas-data`
branch with a single commit holding exactly the allowlisted artifacts (see
`scripts/data_branch.sh`); the previous release is simply superseded, so the
repository keeps no history of data releases. Only this publisher writes
`ctas-data`, and `main` is never force-pushed. The snapshot is built in a small
bare store inside the runtime checkout's `.git` directory
(`.git/ctas-data-store`), which keeps only the current release; the exporter reads
the previous release from it to compute release history. A data-branch push starts
the deployment through `.github/workflows/data-branch-deploy.yml`, which that
commit carries. To preview the complete site locally, run
`bash scripts/overlay_published_data.sh` in any checkout of `main`; generated paths
are ignored by `.gitignore`.

On the live site the dossier roots and overflow parts are served gzip-compressed as
`<path>.json.gz` (the deployed copy is compressed by `scripts/compress_ctas_chunks.sh`;
the release commit and its certificate keep plain JSON). Decompressed bytes match the
manifest's byte counts and SHA-256 exactly. `ctas/delivery.json` names the form: it
says `identity` on `main` (plain JSON, as in any local preview) and the deploy step
rewrites the deployed copy to `gzip`; `ctas/chunk-loader.js` makes exactly one request
per file in the declared form.

The runtime checkout is kept at
`~/Library/Application Support/CTASPublisher/site`. It is intentionally outside
`Documents`, because macOS can deny background jobs access to protected folders.
The authoring checkout remains under `~/Documents/Codex/JackMcGuireAstro Website/Development`; it is separate from the background runtime.

## Schedule and freshness

- `launchd` runs the short job every 120 seconds and once at login/load.
- It runs while the Mac is awake, online, and the user is logged in. Sleep or
  power-off delays publication; the existing static snapshot remains online.
- Candidate or durable source-state changes publish immediately on the next
  check.
- Unchanged state does not create a commit every two minutes. A bounded
  15-minute heartbeat refreshes the certificate well before its 90-minute validity
  window expires (building and deploying a release takes 20-40 minutes, so a
  30-minute window made the live page report "Snapshot out of date" for part of
  every cycle).
- Code-only changes also force a matching certificate refresh.

## Safety behavior

- The exporter reads one frozen database state, so a release cannot mix database
  states while ingestion continues. The publisher pins a read transaction on the
  live file (opened read-only) and, on APFS, clones the database and its WAL
  copy-on-write (`clonefile`) inside it: the clone shares every block with the
  live file, so a release no longer writes a multi-gigabyte copy. The pin keeps
  checkpoints from moving newer state into the main file and keeps the WAL from
  being reset between the two clones, so the pair always recovers to one committed
  state. The clone's WAL is then recovered into it, it is converted to a standalone
  rollback-journal file, and `PRAGMA quick_check` must pass. Where cloning is not
  possible, or the clone fails its check, the SQLite backup API copies the pinned
  state instead (`CTAS_SNAPSHOT_METHOD=backup` forces this). The snapshot lives in
  the runtime checkout's `.git` directory (same volume as the database) and is
  removed at the end of every run; one left by a killed run is removed after an hour.
- Only named public metadata/research files and validated manifest-listed catalog pages, detail roots, and overflow parts are staged. Every requested detail file stays within 4 MiB; overflow parts preserve all original JSON and are verified by size and SHA-256 before reconstruction.
- Dirty files outside the generated-artifact allowlist stop the job.
- A rejected push keeps the release as `refs/pending/ctas-data` in the store and the next run pushes it before exporting again. The runtime checkout never commits to `main`; it only fast-forwards to it. (A checkout that still holds an automatic `CTAS data:` commit on `main` from before the data branch existed is recovered as before: the commit is preserved under `refs/ctas-recovery/` and the checkout is reset to `origin/main`.) `main` is never force-pushed.
- Superseded overflow parts and catalog pages disappear with the release they belonged to: every release is built from an empty generated tree and lists only what the current manifest declares.
- Recursive safety checks reject credentials, private paths, malformed public
  records, and unverified link hosts.
- Insecure source URLs are retained as non-clickable provenance rather than
  being rendered publicly.
- Every published release binds the interface, exporter, source universe,
  compact index, all detail roots and parts, tests, and automation contract to checksums.

Static-catalog assurance verifies release integrity and claim boundaries. It is
not peer review, scientific truth, classification validation, discovery
authority, or a managed-service uptime claim.

## Install or replace the service

Run once:

```bash
bash scripts/install_ctas_mirror.sh
```

The installer verifies the database, Python, SQLite, and unattended SSH push;
creates or fast-forwards the dedicated runtime checkout; installs the
120-second LaunchAgent; and requires its first run to exit successfully.

No GitHub token is stored. The job uses the existing non-interactive SSH key
with `BatchMode=yes` and `IdentitiesOnly=yes`.

## Verify

```bash
bash scripts/diagnose_ctas_mirror.sh
```

A healthy result shows:

- label `io.github.jackmcguireastro.ctas-mirror` loaded;
- `StartInterval` equal to 120;
- last exit code 0;
- a readable local database and runtime checkout;
- runtime Git state synchronized with `origin/main`;
- no current errors in `launchd.err.log`; and
- a successful unattended push dry-run.

The public release can be independently reproduced with
`supernova_watch.static_catalog_certification.build_static_catalog_certificate`
from the primary CTAS project.

## Disable

```bash
bash scripts/install_ctas_mirror.sh --uninstall
```

Uninstalling removes the LaunchAgent but deliberately leaves the runtime
checkout and logs for recovery and audit.

## Disk use on this Mac

Two things keep the publisher from ever crowding the disk. Before each export the
runner checks free space: the export copies the whole database into a temporary
snapshot, so it needs that much plus a floor left for everything else
(`PUBLISHER_MIN_FREE_GB`, default 25 GB). Below that it logs `paused: …` and skips
the run; the site keeps its last release and the freshness watchdog reports the
pause. Once a day `scripts/publisher_housekeeping.sh` keeps the runtime checkout
bounded: only the newest 100 commits of history are kept (the exporter reads only
`HEAD` and `origin/main`), recovery references older than 14 days are dropped,
automatic "generated recovery" stash entries are cleared, and Git garbage is
collected. Without it every data release stays in the checkout forever (about
95 MB a day in September 2026). The authoring checkout under Documents is not
touched.

## Local database retention

The source data CTAS receives (alert envelopes, observations) is small; almost all
database growth was derived bookkeeping. `scripts/ctas_db_retention.py` keeps it
bounded without touching source data or anything read as current:
`science-interest` and `population-anomaly` analysis runs (recomputed whenever the
population shifts) keep the newest run per candidate, everything from the last
three days, and every run cited by a benchmark, publication or certification file;
certification runs would keep the newest ten per scope, but the backend declares
them immutable (a `BEFORE DELETE` trigger), and any table protected that way is only
reported, never pruned. Other analysis types are never pruned. `preview` reports without writing; `prune --archive … --vacuum` archives
every removed row to gzip NDJSON first and compacts the file (run with the backend
stopped); `daily` prunes in small batches while the backend runs and reclaims pages
with incremental vacuum. The publisher checks its optional `CTAS_MIN_INTERVAL`
floor before taking the frozen database snapshot, so a waiting run never takes one.

## Freshness watchdog

The publisher fails closed and stays failed until someone looks, and GitHub Pages
keeps serving the last snapshot meanwhile. `.github/workflows/freshness-watchdog.yml`
therefore runs hourly in GitHub Actions, independent of this Mac, reads the live
`ctas/data/status.json` and `worldsindex/data/manifest.json`, and maintains one
issue labelled `freshness-watchdog`: opened (with an @mention) when a publisher has
stalled, updated when the picture changes or every 12 hours while it persists,
closed automatically on recovery. Its strongest signal is the cross-check: if
WorldsIndex published within two hours but CTAS has not for three, the cloud runs
work and this publisher is stuck. Absolute limits are 4 h for CTAS and 6 h for
WorldsIndex (both publishers run in the cloud now). Limits live in the workflow's
`env:` block; the checks are `scripts/watchdog_freshness.py`, tested by
`scripts/test_watchdog_freshness.py` in the release validation.

Since October 2026 it also reads what the data alone cannot show. The recent runs of
`ctas-cloud.yml` and `worldsindex-cloud.yml` (`gh run list`, and `gh run view --json
jobs` for the failed step's name): three failed completed runs in a row, or more than
half of at least four completed runs in 24 h, is an alert even while the site is
fresh (2026-10-14: every WorldsIndex run published and then failed saving its state).
Each run also publishes a marker beside its release, `ctas/data/cloud-run.json` and
`worldsindex/data/cloud-run.json` (`scripts/cloud_run_marker.py`): the state copy it
restored and when that copy was saved, the run id, and whether GitHub accepted the
`CTAS_STATE_TOKEN` key and when it expires (date only; the key never appears). The
marker is not part of the certified snapshot: `status.json` stays exactly as the
exporter writes it, the certificate binds an explicit file set, and the WorldsIndex
static test leaves the marker out of its manifest comparison.

### Where the alert arrives

The issue (label `freshness-watchdog`) @-mentions the owner, which GitHub turns into an
email; issue #5 (October 2026) showed that an email alone can go unseen for 40 hours.
Optionally the same summary is also POSTed to one webhook when the issue opens, when
the set of alerts changes, and when it closes (`scripts/watchdog_issue.sh`): plain text
with a `Title` header for an ntfy.sh topic (the ntfy app on a phone shows it as a push
notification), JSON `{"text", "content"}` for a Slack or Discord incoming webhook. Set
it once from a checkout, and remove it the same way:

```sh
gh secret set WATCHDOG_NOTIFY_WEBHOOK --repo JackMcGuireAstro/JackMcGuireAstro.github.io \
  --body "https://ntfy.sh/<a-long-private-topic-name>"
gh secret delete WATCHDOG_NOTIFY_WEBHOOK --repo JackMcGuireAstro/JackMcGuireAstro.github.io
```

The format is guessed from the address (`ntfy` in it means text); the workflow's
`WATCHDOG_NOTIFY_FORMAT` (`text` or `json`) overrides the guess for a self-hosted
server. Nothing else is notified, and a failed delivery never stops the issue.

### When an alert fires

The issue's first line says what to do; the alert codes mean:

- `ctas-runs-failing-after-publish`, `worldsindex-runs-failing-after-publish`,
  `ctas-state-not-saved`, `worldsindex-state-not-saved`: the runs publish, then fail,
  so every run restores an old working state (the collector re-collects the same
  alerts; WorldsIndex re-runs the same promotions). Open the newest failed run named
  in the issue, save its log and `ctas-publish-log` / `worldsindex-publish-log`
  artifact (kept five days), and read the lines after "saving ... to the state
  repository". A `gh release upload` refusal means the key lost write access to the
  state repository or the release `state` is missing; a check failure (`quick_check`,
  "holds no frozen snapshots") means the working data is damaged and the last good
  copy was rightly kept.
- `ctas-runs-failing`, `worldsindex-runs-failing`: the runs fail before publishing.
  Open the newest failed run and read the failed step the issue names. A failure at
  the private checkout or the restore means the key: run `Renew cloud key.command`.
- `state-token-rejected`: GitHub answered 401 to the key. Run `Renew cloud key.command`
  on the Mac (it copies a fresh `gh auth token` into the `CTAS_STATE_TOKEN` secret and
  the relay's `GH_DISPATCH_TOKEN`), then start each cloud workflow by hand once.
- `state-token-expiring`: the key expires within ten days (`gh auth token` keys from an
  OAuth sign-in carry no date and never raise this). Run `Renew cloud key.command`
  before the date in the issue.
- `ctas-stale`, `worldsindex-stale`, `*-stalled-while-*-publishes`: nothing published
  for too long. Open the newest run of the stalled workflow, save its log, and start
  the workflow by hand (Actions -> Run workflow). If no run started at all, check the
  relay's keeper (`relay/worker.js`) and its `GH_DISPATCH_TOKEN`.
- `ctas-unavailable`, `worldsindex-unavailable`: the status file could not be read
  from the site; look at the last "Validate and deploy site" run and GitHub's status.
- `deploy-failed`: pushes land but the site is frozen at the previous green commit.
  Open the failed deploy run, fix or revert the commit, or re-run the last green one.

## Public artifacts

- `ctas/data/live-summary.json`: the sub-2 MiB first-screen data used by the public interface. The obsolete `catalog-bootstrap.json` is retired.
- `ctas/data/catalog-index.json`: the complete compact candidate table and canonical event-UUID order. Complete-catalog browsing also uses manifest-listed bounded pages under `catalog-pages/`.
- `ctas/data/alias-index.json`: provider-scoped aliases, kept out of the initial page load and fetched for alias search or routes.
- `ctas/data/candidate-chunks/manifest.json`: the authoritative complete-catalog download contract. It checksum-binds the complete index, all 4096 stable root files, and every overflow part, proves counts, and specifies exact reconstruction in index UUID order.
- `ctas/data/candidate-chunks/000.json` through `fff.json`: stable UUID-derived dossier roots. Small roots contain complete candidate JSON directly; an oversized root contains a descriptor listing bounded `xxx.part-000001.json` files. Each part contains an ordered JSON text fragment. Concatenating the fragments exactly reproduces the original root, including an individually large candidate, without omitting measurements or provenance.
- Every overflow descriptor verifies its reconstructed byte length and SHA-256. The top manifest lists every part exactly once, and every listed part must be reachable through its root.
- A record whose content and score did not change keeps byte-identical dossier files from one release to the next, so a release commit carries only the dossiers that changed. The score is still recomputed for every release (recency and observation age decay with time), but the release clock it was computed against is published once, as `score_clock` in the complete-catalog manifest (and as `score_as_of` in every compact row); each `score_model` refers to it (`score_clock`) and names its own clock inputs as timestamps (`reference_time`, `newest_evidence_at`) rather than as ticking ages. Likewise the AstroEvidence `sourceUniverseVersion` in each dossier is taken from the source contracts' `definition_checksum_sha256`, which leaves out each source's polling state (last attempt, running counts). Before this (until October 2026) every release rewrote all 4096 roots, about 700 MB (83 MB gzipped), even with an unchanged database; now only records whose score or evidence moved change (about 165-400 roots, 45-95 MB per 30-minute cycle). The snapshot-verification gate recomputes every published score model against the release clock and requires it to match exactly.
- `ctas/data/research/manifest.json` and its listed CSV/VOTable/TOM files: normalized, checksum-bound reuse tables for scripts, TOPCAT, and target-management tools.
- `ctas/data/status.json`: freshness, source health, counts, and publication state.
- `ctas/data/source-universe.json`: maintained source and survey contracts.
- `ctas/data/release-history.json`: checksum-addressed catalog changes.
- `ctas/data/link-health.json`: recursive URL roles and structural checks.
- `ctas/data/certification.json`: checksum-bound static-catalog assurance report.

The former single-file `ctas/data/candidates.json` download exceeded GitHub's
enforced object-size limit and is no longer published. Download the compact
index plus the complete-catalog manifest and its listed parts instead. Verify
the declared byte lengths and SHA-256 values, map part records by `event_id`,
reconstruct descriptor roots from their ordered fragments when needed, then emit them in the `event_id` column order declared by `catalog-index.json`'s
`candidate_columns` and `candidate_rows` table. The
manifest includes the checksum of that canonical reconstructed array.

## Recover an interrupted generated-data publication

Stop the LaunchAgent before inspecting an in-progress rebase. Preserve the old
commit using a recovery ref or Git bundle before synchronizing the dedicated
runtime. Do not reset an authoring checkout. The runner now performs this
recovery automatically only after its generated-data-only checks pass.

Recovery references remain available locally with:

```bash
git for-each-ref refs/ctas-recovery/
```

Two kinds of reference appear there. `<stamp>-<commit>` preserves an unpublished
generated-data commit that had to be replaced by current `origin/main`.
`<stamp>-unfinished-<commit>` preserves regenerated files that an interrupted or
refused export left in the working tree; the runner snapshots them through a
temporary index (never through a patch, which Git refuses above 1 GiB for a full
catalog) and returns the checkout to `HEAD` before syncing. The publisher itself
returns every generated file under `ctas/data` to `HEAD` on any exit that does
not commit, so these references are expected to be rare. Both kinds are safe to
delete once inspected; the next export reproduces the data.

A code update must be pushed and deployed before restarting a repaired runtime;
its next run fetches current `main`, exports the current local database, validates
the full release, and publishes it. Check the successful publisher log and live
catalog count rather than assuming a loaded LaunchAgent has published data.

## Live provider data (fetched in the visitor's browser)

A dossier's "Live data from the sources" panel (`ctas/live-sources.js`) fetches the
object's current light curves and spectra from the providers only when the visitor
asks: ZTF through ALeRCE (by ZTF name, else by position within 2″) and Rubin through
Fink directly, and Gaia Science Alerts, Pan-STARRS DR2, TNS public spectrum files,
Lasair (Rubin light curve and Sherlock host context) and NASA ADS papers through the
pass-through relay in `relay/` (address in `/live-config.json`; Lasair and ADS use
free keys stored only as relay secrets). Nothing
is stored by CTAS or on the publishing computer, and these values are labelled as
live provider data outside the checksum-verified snapshot. In the Spectra panel, each
record with a public TNS file, or a WISeREP record, also has a "Plot spectrum" control
that fetches just that file through the same relay on request; `ctas/spectrum-ascii.js`
reads the common ASCII dialects (tested by `scripts/test_ctas_spectrum_ascii.js` with the
samples in `tests/fixtures/spectra/`) and `ctas/spectrum-fits.js` reads FITS spectra
(1-D images with linear or log WCS keywords, IRAF multispec arrays taking the first
spectrum, binary tables with wavelength/flux columns including SDSS loglam/ivar; tested by
`scripts/test_ctas_spectrum_fits.js` with files written by
`scripts/make_spectrum_fits_fixtures.py`). gzip-compressed files are inflated in the
browser. For a WISeREP record the page first fetches the object's public listing that the
record names (`public_download_url`), reads the file's address from it, then fetches the
file; a 401/403 or a sign-in page instead of a file is shown as "proprietary on WISeREP".
No credentials are asked for or stored.
