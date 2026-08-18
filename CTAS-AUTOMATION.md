# CTAS automation

Two independent automatic systems. They do different things and must not be
confused with each other.

```
SOURCE CODE SYNC                      PUBLIC DATA REFRESH
your Mac, every ~30 min               GitHub Actions, every ~30 min
  ~/Documents/GitHub/ctas               runs CTAS headlessly
        |                                       |
   validate, commit                     sanitized public JSON
        |                                       |
   push to private CTAS repo            full-site Pages artifact
                                                |
                                    jackmcguireastro.github.io/ctas.html
```

The public refresh does **not** depend on your Mac. The source sync does.

---

## A. Public data refresh (GitHub Actions)

| | |
|---|---|
| Workflow | `.github/workflows/update-ctas.yml` |
| Schedule | `cron: "7,37 * * * *"` — approximately every 30 minutes |
| Also runs on | `workflow_dispatch` (manual) and any push to `main` |
| Required secret | `CTAS_REPO_TOKEN` |
| Optional variable | `CTAS_REPO` (defaults to `JackMcGuireAstro/ctas`) |
| Deployment | Pages **artifact** — no data commits are ever made |
| Concurrency | group `pages`, in-progress deploys are never cancelled |

GitHub does not guarantee the exact minute, and may skip scheduled runs under
load. The site therefore says "approximately every 30 minutes" rather than
promising a clock time.

### What it does

1. Checks out this website repository.
2. Checks out the **private** CTAS repository into `_ctas_src/` using
   `CTAS_REPO_TOKEN`.
3. Installs CTAS with pip.
4. Runs `python scripts/update_ctas.py --output-dir ctas/data`.
5. Validates the generated JSON (parses, plausible size, no local paths, no
   credential-shaped strings).
6. Assembles `_site/` — the complete website — excluding `_ctas_src/`,
   `scripts/`, `tools/`, `.github/` and `.git`.
7. Verifies the artifact still contains every page, the CV, all seven
   presentation PDFs and the images, and that nothing private leaked in.
8. Uploads and deploys the artifact.

### Failure behaviour

- A source that is unreachable is recorded in `status.json` and the run
  continues.
- If **no** source succeeds and a previous dataset exists, that dataset is kept
  and the status becomes `degraded`.
- If no source succeeds and there is no previous dataset, the script exits `1`
  and the job stops **before** the deploy step, so the currently published site
  stays exactly as it was.
- Any validation failure also stops the job before deployment.

### First run

The very first run has no previous dataset. If the sources happen to be quiet,
trigger it manually with **Run workflow → allow_empty = true** so an honest
empty dataset is published.

---

## B. Source-code sync (your Mac)

| | |
|---|---|
| Script | `~/Documents/GitHub/ctas/scripts/auto_git_sync.sh` |
| LaunchAgent | `~/Library/LaunchAgents/io.github.jackmcguireastro.ctas-sync.plist` |
| Interval | `StartInterval 1800` — every 30 minutes |
| Log | `~/Library/Logs/ctas-sync/auto_git_sync.log` (rotates at 1 MB) |

### Install

```bash
cd ~/Documents/GitHub/ctas
sed "s|REPLACE_WITH_HOME|$HOME|g" scripts/io.github.jackmcguireastro.ctas-sync.plist \
  > ~/Library/LaunchAgents/io.github.jackmcguireastro.ctas-sync.plist
mkdir -p ~/Library/Logs/ctas-sync
launchctl load ~/Library/LaunchAgents/io.github.jackmcguireastro.ctas-sync.plist
```

Disable with:

```bash
launchctl unload ~/Library/LaunchAgents/io.github.jackmcguireastro.ctas-sync.plist
```

Dry run (changes nothing):

```bash
~/Documents/GitHub/ctas/scripts/auto_git_sync.sh --dry-run --verbose
```

### Requirements

Your Mac must be **awake and online** for a sync to run. It does **not** need to
be unlocked — a LaunchAgent runs for the logged-in user whether or not the
screen is locked, but it does not run while the machine is asleep. launchd
fires a missed `StartInterval` once the Mac wakes, so sleeping delays a sync
rather than skipping it.

### Safety behaviour

- No changes → exits 0 without committing.
- Compile check fails → no commit, no push.
- `.env`, databases, logs, caches, backups, virtualenvs → never staged, and
  re-checked after `git add`; if any reached the index the script unstages
  everything and stops.
- Remote diverged → stops and logs. It never pulls, merges, rebases or forces.
- Push rejected or auth failed → the commit stays local; nothing is forced.

---

## Alert sources

### Enabled now (no credentials required)

| Source | Service | Note |
|---|---|---|
| `fink-lsst` | Fink / Rubin LSST public broker | Rubin alert packets are world-public with no proprietary period |
| `tns-astronotes` | TNS AstroNotes | Only notes explicitly marked `note-public` are retained |

Context and cross-match services used during enrichment (NED, SIMBAD, Gaia,
HEASARC, IRSA, NOIRLab, MAST, ESO, WiseRep) are anonymous public query
services and need no credentials.

### Disabled until secrets are added

| Source | Required secrets |
|---|---|
| TNS public object deltas | `TNS_BOT_ID`, `TNS_BOT_NAME`, `TNS_API_KEY` |
| NASA GCN public Kafka stream | `GCN_CLIENT_ID`, `GCN_CLIENT_SECRET` |
| ATLAS forced photometry | `ATLAS_API_TOKEN` |
| AAVSO AID photometry | `AAVSO_AID_API_TOKEN` |

The workflow already passes these through. Adding them under
**Settings → Secrets and variables → Actions** is all that is needed — no code
change, no redesign. `update_ctas.py` reports each source as `disabled` with
the exact variables it is waiting for.

### Limitations

- Each Actions run starts with an empty ephemeral database, so connectors work
  from their bounded lookback window rather than a durable cursor. Long-running
  local CTAS retains more history than the public snapshot shows.
- Local FITS detection, notification delivery and follow-up submission are not
  part of the public pipeline. They stay local and gated.
- The public export is an allowlist. Only the fields named in
  `PUBLIC_EVENT_FIELDS` in `scripts/update_ctas.py` can ever be published.

---

## Public output

| File | Contents |
|---|---|
| `ctas/data/candidates.json` | Allowlisted candidate records, schema v1 |
| `ctas/data/status.json` | Pipeline status, last successful update, candidate count, per-source states |

Consumed by `ctas/app.js`, rendered at `/ctas.html`.

## Local use

```bash
# refresh the public data locally (needs CTAS installed)
python scripts/update_ctas.py --output-dir ctas/data

# preview the whole site
python3 -m http.server 8000
```
