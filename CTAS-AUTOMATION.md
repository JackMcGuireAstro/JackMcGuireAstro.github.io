# CTAS public-catalog automation

CTAS is published as a public static catalog. The scientific database and
Python ingestion pipeline run locally; a user LaunchAgent checks every 120
seconds and pushes only an explicit allowlist of public artifacts to this
GitHub Pages repository.

No local dashboard, managed database, secret manager, or human approval step is
part of this publication path.

## Architecture

```text
local CTAS SQLite database
        |
        | transactionally consistent SQLite backup
        v
Python public exporter and validators
        |
        | compact index + 32 lazy shards + full download + assurance artifacts
        v
dedicated runtime checkout (public repository only)
        |
        | allowlisted commit, ordinary SSH push
        v
GitHub Pages /ctas.html
```

The runtime checkout is kept at
`~/Library/Application Support/CTASPublisher/site`. It is intentionally outside
`Documents`, because macOS can deny background jobs access to protected folders.
The authoring checkout in `Documents/GitHub` remains available for normal site
work and is not the background runtime.

## Schedule and freshness

- `launchd` runs the short job every 120 seconds and once at login/load.
- It runs while the Mac is awake, online, and the user is logged in. Sleep or
  power-off delays publication; the existing static snapshot remains online.
- Candidate or durable source-state changes publish immediately on the next
  check.
- Unchanged state does not create a commit every two minutes. A bounded
  15-minute heartbeat refreshes the certificate before its 30-minute validity
  window expires.
- Code-only changes also force a matching certificate refresh.

## Safety behavior

- The exporter reads a frozen SQLite backup, so a release cannot mix database
  states while ingestion continues.
- Only the 40 explicit public data artifacts in `publish_ctas.sh` are staged.
- Dirty non-data files in the runtime checkout stop the job.
- A rejected push remains local and is amended on the next run; divergence is
  rebased only when Git can do so cleanly, otherwise publication stops without
  forcing history.
- Recursive safety checks reject credentials, private paths, malformed public
  records, and unverified link hosts.
- Insecure source URLs are retained as non-clickable provenance rather than
  being rendered publicly.
- Every published release binds the interface, exporter, source universe,
  compact index, all shards, tests, and automation contract to checksums.

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

## Public artifacts

- `ctas/data/catalog-index.json`: compact initial catalog index.
- `ctas/data/candidate-chunks/manifest.json`: checksums for 32 lazy detail shards.
- `ctas/data/candidate-chunks/*.json`: complete candidate workspaces.
- `ctas/data/candidates.json`: full-catalog download.
- `ctas/data/status.json`: freshness, source health, counts, and publication state.
- `ctas/data/source-universe.json`: maintained source and survey contracts.
- `ctas/data/release-history.json`: checksum-addressed catalog changes.
- `ctas/data/link-health.json`: recursive URL roles and structural checks.
- `ctas/data/certification.json`: checksum-bound static-catalog assurance report.
