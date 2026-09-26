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
        | sub-2 MiB live summary + 4096 stable detail roots with bounded parts + research tables
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
The authoring checkout remains under `~/Documents/Codex/JackMcGuireAstro Website/Development`; it is separate from the background runtime.

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
- Only named public metadata/research files and validated manifest-listed catalog pages, detail roots, and overflow parts are staged. Every requested detail file stays within 4 MiB; overflow parts preserve all original JSON and are verified by size and SHA-256 before reconstruction.
- Dirty files outside the generated-artifact allowlist stop the job.
- A rejected push remains local. If it later diverges from the remote, the runner verifies every unpublished commit: each must be an ordinary automatic `CTAS data:` commit changing only known generated public artifacts. It preserves the old tip under `refs/ctas-recovery/`, synchronizes this dedicated runtime checkout, and regenerates against current code and a fresh database snapshot. Code changes, other files, merge commits, and unrecognized commits stop recovery with the checkout preserved. Published history is never force-pushed.
- Superseded tracked overflow parts and catalog pages are removed in the same publication commit; only narrowly matched generated filenames may be retired.
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

## Public artifacts

- `ctas/data/live-summary.json`: the sub-2 MiB first-screen data used by the public interface. The obsolete `catalog-bootstrap.json` is retired.
- `ctas/data/catalog-index.json`: the complete compact candidate table and canonical event-UUID order. Complete-catalog browsing also uses manifest-listed bounded pages under `catalog-pages/`.
- `ctas/data/alias-index.json`: provider-scoped aliases, kept out of the initial page load and fetched for alias search or routes.
- `ctas/data/candidate-chunks/manifest.json`: the authoritative complete-catalog download contract. It checksum-binds the complete index, all 4096 stable root files, and every overflow part, proves counts, and specifies exact reconstruction in index UUID order.
- `ctas/data/candidate-chunks/000.json` through `fff.json`: stable UUID-derived dossier roots. Small roots contain complete candidate JSON directly; an oversized root contains a descriptor listing bounded `xxx.part-000001.json` files. Each part contains an ordered JSON text fragment. Concatenating the fragments exactly reproduces the original root, including an individually large candidate, without omitting measurements or provenance.
- Every overflow descriptor verifies its reconstructed byte length and SHA-256. The top manifest lists every part exactly once, and every listed part must be reachable through its root.
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
