# WorldsIndex local refresh and GitHub publication

WorldsIndex uses the same operating pattern as CTAS: the research checks run on
this Mac, a dedicated local checkout commits only generated public artifacts,
and GitHub Pages serves the site. ChatGPT-hosted Sites and Codex scheduled tasks
are not part of this path.

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

## Scientific boundary

A provider change marker is evidence that a source may have changed. It is not
permission to mix unreviewed upstream rows into the public catalog. The monitor
receipt updates automatically, while measurements stay on the last reconciled
snapshot until that source's fetch, normalization, identity reconciliation,
provenance, and regression gates pass. Failed and quarantined runs leave the
last-good catalog measurements unchanged; their typed failure state remains
publicly visible instead of being mistaken for freshness.

The publisher can stage only:

- `worldsindex/data/manifest.json`
- `worldsindex/data/registry.json.gz`
- `worldsindex/data/sky-detections.json.gz`
- `worldsindex/data/source-monitor.json`
- `worldsindex/data/details/00.json.gz` through `ff.json.gz`

No token, `.env` file, source checkout, raw private receipt, or arbitrary site
file is included.

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
