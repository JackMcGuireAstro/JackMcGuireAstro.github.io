# WorldsIndex local refresh and GitHub publication

WorldsIndex uses the same operating pattern as CTAS: the research checks run on
this Mac, a dedicated local checkout commits only generated public artifacts,
and GitHub Pages serves the site. ChatGPT-hosted Sites and Codex scheduled tasks
are not part of this path.

## What runs

`io.github.jackmcguireastro.worldsindex-mirror` runs every six hours while the
Mac is awake, online, and the user is logged in. It:

1. refreshes the dedicated public-site checkout from `origin/main`;
2. runs every implemented ExoNexus provider monitor locally;
3. publishes typed quarantined provider outcomes as status only, while rejecting
   monitor crashes that cannot produce a valid receipt;
4. runs ExoNexus type checking, tests, lint, production build, and atlas build;
5. creates and validates the GitHub-native static release;
6. stages only the explicit WorldsIndex data allowlist; and
7. pushes the resulting commit to `main` over unattended SSH.

GitHub then runs the WorldsIndex release-validation workflow. GitHub Pages
publishes the repository; it does not query scientific providers or hold their
credentials.

Because macOS blocks background launch agents from reading Documents, the
installer makes a launchd-readable operational copy of ExoNexus at
`~/Library/Application Support/WorldsIndexPublisher/source`. The editable
canonical project remains under Documents. Rerun the installer after changing
source code or frozen inputs; provider receipts continue advancing in the
operational copy between installs. `.env.local` remains local with mode 0600 and
is never copied into the public-site checkout.

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

Run a non-publishing end-to-end build in a suitable clean checkout:

```sh
./scripts/publish_worldsindex.sh --dry-run
```

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
