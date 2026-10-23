# Resilience of the CTAS and WorldsIndex releases

Both data products publish and deploy entirely in GitHub Actions. In the two weeks before
2026-10-22 every outage had the same shape: a code change, or a new check, that passed on
the 24-event test fixture met real data for the first time and stopped everything for 7 to
41 hours while the data itself was fine. This document describes the rules that keep the
releases and the deploy going in that situation without anyone stepping in, flag what
happened, and still stop on real data-integrity failures.

| Date | What stopped | For |
|---|---|---|
| 2026-10-18 | WorldsIndex restore refused hard links in the state archive | 20 h |
| 2026-10-19 | a static test looked a pinned event up by TNS name; AT2026zxx became SN2026zxx; every CTAS release refused | 7 h |
| 2026-10-21 | a new CTAS release-time schema check required a `links` list that 1,083 real events lack | 23 h |
| 2026-10-21 | new deploy browser tests (status page overflow; a first-screen byte budget calibrated on the fixture) blocked every Pages deploy; the site froze while the data branches kept updating, and nothing alerted | 41 h |
| 2026-10-21 | a new WorldsIndex schema check rejected the monitor's failed-probe state (`changed: null`) | 32 h |

In one paragraph: every check now has a class; new checks (and changed schema files) only
warn until they have passed real releases; a publish that a check blocks is retried once
with the code that produced the release currently online; the deploy picks the first of
main, that code, or the last deployed code whose blocking checks pass; the watchdog
compares the live site with the data branches; and every failure or fallback leaves a
sanitized report anyone can read without signing in.

## 1. Every check has a class

`scripts/check-classes.json` (schema `check-classes@2`) lists every release-time check of
both publishers and of "Validate and deploy site" (131 checks, including the 44 CTAS
certificate gates) with one of three classes:

* **always_block**: the hard core. A failure blocks on every run, from the first one; it is
  never in watch mode. Exactly seven checks: `ctas.gate.public-export-safety` (banned keys
  and secrets in the export), `site.credential-scan` and `worldsindex.credential-scan`
  (credential-shaped files or text), `ctas.release-layout` and `worldsindex.artifact-guard`
  (unmanifested files: the published set must be exactly the checksum-bound set),
  `ctas.checksums` and `worldsindex.checksums` (checksum self-consistency: every file the
  certificate or manifest lists has exactly the bytes and sha256 it states). The
  undeclared-file part of the WorldsIndex schema validator is always_block too.
* **integrity**: the published data, or the code that loads and verifies it, is wrong:
  manifest closure, certificate self-consistency, provenance and receipt closure,
  provider-label accounting, missing or duplicated records, catastrophic data loss
  (`ctas.count-drop`, `worldsindex.count-drop`: the candidate or object count may not drop
  by more than 20% against the published release), front-end scripts that do not parse.
  A failure blocks once the check is out of watch mode (section 2).
* **advisory**: everything that cannot make the published data wrong: browser layout,
  payload budgets, accessibility, page copy, tests that pin specific real objects by name,
  interop round trips of derived exports (ECSV, VOTable, VOParquet: the JSON release they
  are derived from is verified by integrity checks), feed and changelog formatting, tooling
  tests. A failure never blocks a release or a deploy: it is a `WARN` line in the run log,
  a row in the run's results, and one GitHub issue labelled `check-warning` per check,
  which closes itself when the check passes again.

**The rule for a future check** (also in the file): classify it by what its failure
proves. If a failure can only mean that bytes readers download are wrong, missing, unsafe
or unverifiable, it is integrity (always_block only for the hard core above, and only by
a deliberate decision). If the published data could be right while it fails (a layout, a
size budget, a pinned object, a derived export's formatting), it is advisory. When unsure,
integrity: watch mode covers its first day. A test that looks a real object up by name or
by a value that may change upstream is always advisory.

Parts. A check can name a **parser** and **part rules**, so one suite can be mostly
integrity and still carry advisory parts. The parser reads the check's output (`unittest`:
the `FAIL:`/`ERROR:` test names; the schema validators: the failing schema or file); each
failing part takes the class of the first matching rule; when no part can be attributed
(an import error, a crash, a missing validator) the check's own class applies. In
`ctas.test_ctas_static` the test that pins real regression events by name and three tests
that read page copy are advisory; the rest, including the test that compares the
certificate's bound code with the tree, is integrity.

Schema files. The two schema validators (`ctas.schemas`, `worldsindex.schemas`) are also
split into one sub-check per schema file, `<id>:<file>` (26 CTAS, 18 WorldsIndex). Each
has its own class (CTAS: status, certification, catalog index, the manifests, release
history, source universe and common are integrity, the rest advisory; WorldsIndex: the
manifest, catalog index, detail shards and common are integrity, the rest advisory) and
its own watch period.

The machinery: publishers and the deploy run each check through `check <id> <command>`
(`scripts/release_checks.sh`), which keeps the output and calls `scripts/release_checks.py
record`. It returns 0 when the check passed or failed without blocking (a `WARN` line says
so) and 3 when the failure blocks. Anything that goes wrong while classifying also returns
3, so a broken classifier can never wave a release through; an id that is not in the file
is treated as integrity. `scripts/test_release_checks.py` keeps the file and the scripts in
step: every id a script runs is classified for that scope, and every classified id is run.

A published CTAS certificate still says `verification-failed` and names the gate when an
advisory gate (a byte budget, page copy) fails; the release goes out with that certificate.
Nothing is hidden; the status page says what failed.

## 2. New checks start in watch mode

An integrity check, and each schema file of the schema validators, is in **watch mode**
until it was **first seen at least 24 hours ago AND passed N consecutive real releases**:
N = 4 for CTAS (about two hours of releases), 3 for WorldsIndex (about three hours), 3 for
the deploy's own checks. In watch mode a failure is a warning (and a `check-warning`
issue) whatever the class, except always_block. Then the check is promoted and blocks.
Promotion is automatic, needs no code change or commit, is recorded (`promoted_at`) and is
**permanent**: a promoted check that fails later blocks (a failure resets its count, not
its promotion). Without that, one failure of an established guard would put it back into
watch mode and let the next bad release through.

The history lives in a small public file each publisher writes next to its release,
`ctas/data/check-history.json` and `worldsindex/data/check-history.json` (schema
`check-history@1` in both schema folders and both OpenAPI documents; in `PUBLIC_FILES`
when written; 15 to 25 KB). One row per check id and per schema file: `first_seen`,
`consecutive_passes`, `last_result`, `last_failure_at`, `last_failure_detail` (a short
sanitized line), `promoted_at`, `class`, and for schema files `sha256`. The publisher reads
the previous release's copy from its data branch, so promotion travels with the releases.
The deploy's own checks keep theirs in `history/deploy.json` on the `run-reports` branch.

* **A new check** is one the history does not know: it starts in watch mode the first time
  it runs.
* **A changed schema file** starts a new watch period: the row records the sha256 of the
  schema together with every schema it references (so a change to `common.schema.json`
  restarts all of them); a different digest is a new row. That is the 2026-10-21 shape: a
  stricter schema lands, warns for a day on real data, and only then blocks.
* **Bootstrap.** Checks marked `baseline` ran on real releases before the history existed
  (every check older than the Track 1 batch). While no history of their scope is readable
  (the first release after this lands, or a lost file) they count as promoted, so landing
  this machinery never turns an established guard into a warning. The Track 1 checks (the
  schema validators, the round trips, the changelog and feed tests, the Track 1 certificate
  gates, the WorldsIndex real-shape schema tests) and the new count-drop checks are not
  baseline: they start counting at the first release after landing. To put a rewritten
  check back into watch mode, give it a new id.
* Rows of checks the current code does not run (written by newer or older code) are carried
  over unchanged and dropped after 30 days without a run.

The history file is written after the checks that judged the release, so it is not part of
the checksum-bound release: not in the CTAS certificate, not a WorldsIndex manifest
artifact (like the cloud-run marker; `scripts/test_worldsindex_static.py` and
`scripts/validate_worldsindex_schemas.py` know it as a publisher file).

## 3. Last-good code for publishing

### 3a. What is recorded

* The CTAS certificate already records the commit whose code built the release
  (`publication_relationship.local_head`, also `ctas:site_commit` in provenance.json).
* WorldsIndex records it in provenance.json (`wi:siteCommit` of the static build, a manifest
  artifact).
* Every cloud-run marker (`ctas/data/cloud-run.json`, `worldsindex/data/cloud-run.json`)
  now records `site_commit` as well.
* A release built by the fallback code carries, in its marker, the additive key
  `code_fallback: {failed_commit, failed_check, failed_checks, detail, used_commit}`. It is
  in the marker rather than in status.json or the manifest because those are checksum-bound
  and written by the code being fallen back to, which may predate the key; the marker is
  written by the current runner and is outside the certified bytes.

### 3b. The one fallback attempt

When a publisher stops at a blocking check it exits 3 ("blocked by a release check";
every other failure still exits 1). The cloud runner reads the commit that produced the
release currently on the data branch (`scripts/release_commit.py`, from the publisher's
already-synced data store, nothing downloaded) and asks the one pure decision function,
`scripts/release_checks.py decide`:

| Outcome | When |
|---|---|
| `proceed` | no blocking failure (warnings are recorded) |
| `block` (`not-fallback-eligible`) | a blocking check has `fallback: false`: the restore safety checks, the export-safety gate, the credential scans, and the builder-side WorldsIndex checks (monitor, ExoNexus gates, atlas) that site code cannot change |
| `block` (`no-release-commit`, `release-commit-is-current`) | the release records no commit, or it was built by this very code (then the data or the environment changed, and older code is no answer) |
| `block` (`release-commit-not-on-main`) | that commit is not an ancestor of main (GitHub compare API): a release built from another branch by a manual run never becomes the fallback |
| `block` (`new-check-not-in-fallback-code`) | the blocking check does not exist in the fallback code, which could only skip it, never answer it (code older than the classification is taken to know the baseline checks) |
| `try-fallback` | otherwise: the release step runs once more with that code on the same data |

The retry (`code_fallback_run` in `scripts/code_fallback.sh`, called exactly once by each
runner; it never calls itself) runs from a standalone checkout of that commit: fetched
explicitly because Actions checkouts are shallow, borrowing the site's objects, with the
site's origin for the data-branch push, the run's data store linked in (no second
download), and `origin/main` set to that commit so the CTAS exporter certifies the release
against the code that actually built it. (A `git worktree` would share `origin/main` with
the site checkout, and the code-alignment gates would fail.)

* **CTAS** re-runs `publish_ctas.sh` of that commit on **the same frozen database**: the
  blocked run keeps its snapshot (`CTAS_KEEP_FROZEN_DB`), the retry uses it as is
  (`CTAS_FROZEN_DB`), and the runner deletes it afterwards. No second 4 GB copy is made.
* **WorldsIndex** rebuilds the static release with that commit's code from **the same
  builder outputs** (`publish_worldsindex.sh --fast --force`; the monitor, the ExoNexus
  gates and the atlas are not run again).

Then `decide` runs again with the retry's results: `use-fallback` when it passed (the
release is published, its marker carries `code_fallback`, and one issue per product is
opened or updated: **"CTAS published with fallback code"** / **"WorldsIndex published with
fallback code"**, naming the failing check, its last lines and the commit range), `block`
with `data-integrity` when the fallback code reports the same blocking failure (the data is
bad: nothing is published, a `release-blocked` issue says so), otherwise `block` with
`fallback-failed` or `fallback-error`. A run that published with fallback code ends green;
the issue is the alert, and it closes when a run publishes with the current code again.
A fallback run that ends cleanly but publishes nothing (the published release already
matches what it built, publication paused, the lock held) is not reported as published:
the decision becomes `block` with reason `fallback-published-nothing` and says what
happened (`fallback_result`), the run stays red and a `release-blocked` issue opens.
The state is saved after this step exactly as before, whatever happened.

Transitional limits, until the releases and the last deployment are all built by this
batch's code (one release of each product after landing): a fallback to older code cannot
pass the frozen snapshot to that publisher (it takes its own snapshot of the same database)
and records no check results (its exit code decides); a release built by such code drops
`check-history.json` (the next release bootstraps it; baseline checks stay promoted); and
older WorldsIndex code treats the new `worldsindex/data/check-history.json` as a file the
manifest does not declare, so an older deploy candidate fails on WorldsIndex data that
carries it (main is unaffected).

## 4. Deploy: which site code goes live

"Validate and deploy site" keeps every integrity validation of the data, but chooses the
site code to deploy among up to three candidates (`scripts/deploy_candidates.sh`), in
order, skipping a commit already tried:

1. **main**: the check groups run as before, one workflow step each, recording results
   instead of failing;
2. **ctas-release**: the commit that produced the current CTAS release (the HEAD whose code
   the CTAS certificate binds);
3. **last-deployment**: the commit of the last successful github-pages deployment
   (deployments API with the workflow token; `deployments: read`).

A fallback candidate is checked out as a worktree (fetched explicitly), the very same data
commits are extracted into it, and every check group runs there with main's runner and
classification; advisory checks are skipped for fallback candidates (they cannot change the
choice), and a test the older tree lacks is recorded as skipped. The first candidate with
no blocking failure (always_block, or promoted integrity) is deployed; advisory failures
are warnings. A candidate that is not an ancestor of main is never used. If no candidate
passes, nothing is deployed and the previous deployment stays live, as before. When the
deployed code is not main's, a **"Site deployed with fallback code"** issue names the
checks main failed; it closes on the next deploy of main.

**Step 12 ("CTAS static release") and the guarantee.** `scripts/test_ctas_static.py`
compares the sha256 of every code file the live CTAS certificate binds with the files of
the candidate tree, exactly as before (it is integrity). So the guarantee is unchanged:
**every deployed site's CTAS-bound code is byte-identical to the code that certified the
live CTAS release**; a deploy never pairs a CTAS release with front-end code its
certificate does not bind. What changed is what happens when main differs, which is the
case after every CTAS code landing until the next CTAS release re-certifies main (30 to
60 minutes): before, the whole deploy failed and the public site froze, also for unrelated
WorldsIndex data; now the ctas-release candidate is deployed with the fresh data, which
matches the certificate by construction, and main follows on the first deploy after the
CTAS release that certifies it. That **landing window** (main's commit younger than 3
hours, deployed candidate = the CTAS release's commit) opens no issue; longer, the issue
opens. During the window the WorldsIndex front end of main also waits, when a landing
touches CTAS-bound code.

Time: main's checks as before (about 12 minutes on real data); each fallback candidate
adds its blocking checks only (about 6 minutes; browser tests are advisory and skipped);
at most two fallback candidates; the job limit is 50 minutes.

## 5. The public site against the data branches

The hourly freshness watchdog (`scripts/watchdog_freshness.py`, workflow "Publisher
freshness watchdog") now also reads `ctas/data/status.json` and
`worldsindex/data/manifest.json` from the data branches on raw.githubusercontent.com and
compares them with the same files on the live site. When the live CTAS
`last_successful_update` or WorldsIndex `generatedAt` lags its data branch by more than 60
minutes, it raises **`deploy-stale`** through the existing issue mechanism: the
`freshness-watchdog` issue is titled "Public site is not deploying" and names the last
successful deploy run and the step the newest failed deploy run failed in. The 2026-10-21
freeze would have been an issue within the hour.

## 6. Failure reports anyone can read

A failed or fallback publish run (ctas-cloud, worldsindex-cloud) and a failed or fallback
deploy run write one sanitized JSON report (`scripts/run_report.py`, schema
`run-report@2`): workflow, run id and URL, commit, the failing phase and check ids, the
failing check's class, mode, parts and output tail, every non-passing check, the decision,
and at most 200 lines of evidence (the publisher's own FAIL, WARN and phase lines and the
validators' error lines). It is pushed to the public branch **`run-reports`**:

    reports/<workflow>/latest.json      the newest report of that workflow
    reports/<workflow>/<run_id>.json    the newest 50 per workflow are kept

The branch is a single parentless commit replaced with `--force-with-lease`, so concurrent
writers retry instead of losing a report and the branch never grows. Read it at
`https://raw.githubusercontent.com/JackMcGuireAstro/JackMcGuireAstro.github.io/run-reports/reports/ctas-cloud/latest.json`
(or `worldsindex-cloud`, `worldsindex-release`).

The repository is public, so everything that becomes public (reports, issue bodies, check
results) passes one sanitizer: the values of every secret-bearing environment variable the
workflows set (`SECRET_ENV_NAMES`: STATE_TOKEN, DEPLOY_TOKEN, GH_TOKEN, PUSH_TOKEN,
BACKEND_ENV, BUILDER_ENV, AAVSO_AID_API_TOKEN_OVERRIDE, WATCHDOG_NOTIFY_WEBHOOK and the
release tokens; `scripts/test_run_report.py` reads every workflow file and fails when one
sets a secret variable not in the list), each value of a multi-line settings secret, their
base64 forms and the `x-access-token:` form git sends, the values of the collector's and
builder's settings files, GitHub token shapes (ghp_, gho_, ghs_, ghu_, ghr_, github_pat_),
Authorization headers, Bearer tokens, AWS access keys and secret keys, Cloudflare API keys
and tokens, `*_TOKEN=`/`*_SECRET=`/`*PASSWORD=` assignments, private keys and credentials
inside URLs become `***`. Then a looser scan runs over the result; if any credential shape
or secret value survives, **the report is refused** and nothing is written or pushed.

## 7. Staging rehearsal (designed, not built)

Not built in this batch; the design, for when the five behaviours above have run for a
while:

* Landing scripts push to a `staging` branch instead of main.
* A workflow on `staging` pushes runs, with the same restored real state the cloud runners
  use (`ctas_cloud_state.py restore`, `worldsindex_cloud_state.py restore`, read-only): a
  CTAS publisher dry run (`publish_ctas.sh --dry-run` on the restored database, no
  collection, no push) and the WorldsIndex static build and checks (`publish_worldsindex.sh
  --fast --dry-run` from the restored builder outputs), then the deploy's check groups on
  the staging tree with the current data branches.
* Green: the workflow fast-forwards main to the staging commit (`git push origin
  staging:main`, refusing anything but a fast-forward). Red: a run report and an issue; main
  is untouched.
* Cost: about 40 minutes of runner time per landing (the CTAS export needs 16 GB; the
  ubuntu-24.04 runner has it), and the state token must be readable by that workflow.
  With watch mode, fallback and the candidate deploy in place, the rehearsal mostly moves
  the "new check meets real data" moment before the landing instead of after it.

## How the five outages would have gone

* **2026-10-18, restore refused hard links (20 h).** The restore is a classified integrity
  check (`worldsindex.restore`) with no fallback: older code must never accept an archive
  the current safety rules refuse. The run would still stop, but the first failing run
  would open a `release-blocked` issue naming the check with the restore's output, and push
  a run report readable without signing in. What shortens this outage is the time to
  diagnose, not an automatic recovery; that is deliberate.
* **2026-10-19, AT2026zxx renamed to SN2026zxx (7 h).** The failing test is an advisory
  part of `ctas.test_ctas_static`: the release publishes on schedule with a
  `check-warning` issue for the test.
* **2026-10-21, `links` required in the dossier schema (23 h).** `candidate-chunk.schema.json`
  is an advisory schema file, and the changed schema was in watch mode anyway: publishing
  and deploying continue, with a `check-warning` issue.
* **2026-10-21, deploy browser tests (41 h).** Both browser checks are advisory: the deploy
  goes out with two `check-warning` issues, and the watchdog's `deploy-stale` alert would
  have caught any other reason for a frozen site within the hour.
* **2026-10-21, WorldsIndex schema rejected `changed: null` (32 h).** The failure is in
  `source-monitor.schema.json`, an advisory schema file (and changed, so in watch mode):
  the release goes out with a `check-warning` issue.

And the pattern behind them, a fresh code change that breaks on real data in a promoted
integrity check (an exporter that crashes, a manifest the new builder writes wrongly): the
release is published by the code that produced the previous release, within the same run,
and the "published with fallback code" issue names the commit range and the check.

## Trade-offs and limits

* **Advisory failures ship.** A layout regression or broken page copy reaches the public
  site; the warning issue is the safeguard. That is the price of never freezing the data
  on cosmetics.
* **Watch mode delays new integrity checks.** A new integrity check cannot block for at
  least 24 hours and N releases. A check that must guard something serious from day one
  should be written as a part of an existing promoted check, or made always_block by a
  deliberate decision (and then verified on real data first).
* **Fallback runs older code.** For the length of a fallback, a bug fixed on main can come
  back. Security and privacy checks never fall back, and a blocking check unknown to the
  fallback code blocks.
* **An environment failure that hits both passes reads as bad data.** If the runner runs out
  of memory in both passes, `decide` says `data-integrity`. The run report shows the exit
  code (137 for an out-of-memory kill).
* **Time.** A CTAS publish that falls back runs the export twice on the same snapshot; a
  deploy that falls back validates up to two more trees (blocking checks only).
* **Public reports.** The reports are public. They are sanitized and refused when a
  credential shape survives, and the publish logs were already public artifacts, but a
  secret printed in a form no pattern knows would be published. Secrets must never be
  printed in the first place.
* **Issue noise.** Failures outside the classified checks (a failed push, a failed save, a
  cancelled step) open no `release-blocked` issue; the watchdog's run-result alerts cover
  them.

## Binding

The CTAS certificate binds the files the CTAS publisher executes while building a release:
`scripts/release_checks.py`, `scripts/release_checks.sh`, `scripts/check-classes.json`,
`scripts/run_report.py` (the sanitizer release_checks.py imports) and
`ctas/schemas/check-history.schema.json` are in `BOUND_CODE_PATHS`, so the certificate
records which checks were allowed to warn for that release. Everything else in this batch is
declared unbound, each with its reason, in `UNBOUND_RESILIENCE_PATHS` in
`scripts/export_ctas_snapshot.py`; workflows are never bound. Landing this batch therefore
re-binds: until the first CTAS release built by it, main's bound code differs from the live
certificate, and the deploy serves the ctas-release candidate (the landing window of
section 4).

## Files

| File | Role |
|---|---|
| `scripts/check-classes.json` | the classification, the watch settings, the rule for new checks |
| `scripts/release_checks.py` | classify, watch mode, history, the publish decision, the deploy choice, checksums, count drop |
| `scripts/release_checks.sh` | `check <id> <command>` for the bash publishers and the deploy |
| `scripts/code_fallback.sh` | the cloud runners' one fallback attempt |
| `scripts/release_commit.py` | which commit produced a release; on-main check; last Pages deployment |
| `scripts/deploy_checks.sh` | the deploy's check groups |
| `scripts/deploy_candidates.sh` | the deploy's choice of site code |
| `scripts/release_issues.py` | `check-warning`, `fallback-active`, `release-blocked` issues |
| `scripts/run_report.py` | the sanitizer, the report, the `run-reports` branch |
| `scripts/test_release_checks.py`, `test_run_report.py`, `test_release_issues.py`, `test_release_commit.py` | tests |
