# Live-data relay

A tiny Cloudflare Worker that lets the CTAS and WorldsIndex pages read public
astronomy data from archives that do not allow other websites to read them
directly (TNS public spectrum files, Gaia Science Alerts light curves, Pan-STARRS
detections at MAST, TESS/Kepler/K2 light-curve files at MAST (mission files and the
TESS-SPOC full-frame light curves), IRSA ZTF light
curves, ExoFOP-TESS, the NASA Exoplanet Archive's TAP service and spectrum files,
NASA ADS and Lasair). It fetches one allow-listed public address when a visitor
asks, and passes the answer straight back. It keeps nothing; Cloudflare may hold
an identical response for up to an hour so providers are not asked twice.

Rules (see `worker.js`): GET only; only the listed hosts and paths; only requests
from pages on jackmcguireastro.github.io (or a local preview on port 8000); no
cookies or credentials either way; responses over 40 MB are refused.

Deploy (free Cloudflare account, from this folder):

    npx wrangler login
    npx wrangler deploy

Then put the printed `https://…workers.dev` address into `/live-config.json`
as `"relay"`.

Two sources need a free access key, stored as a Worker secret so it never reaches
the page (run from this folder, paste the key when asked):

    npx wrangler secret put ADS_TOKEN      # https://ui.adsabs.harvard.edu → Account → API Token
    npx wrangler secret put LASAIR_TOKEN   # https://lasair.lsst.ac.uk → sign up → My Profile → API token

Without them the relay answers 503 for those two sources and the pages say a key
is needed. Tests: `node scripts/test_live_relay.mjs`.

## Keeper (scheduled restarts)

GitHub's own schedule fires only every few hours for the site repository, so the Worker
also has a cron trigger (`wrangler.toml`, every 20 minutes) that calls `keep()` in
`worker.js`. For `ctas-cloud.yml` (75 min), `worldsindex-cloud.yml` (55 min) and
`freshness-watchdog.yml` (55 min) it starts a run when none is queued or going and the
last real run (success or failure) started longer ago than that. It needs one secret, the
owner's GitHub key:

    gh auth token | npx wrangler secret put GH_DISPATCH_TOKEN

Without it the keeper does nothing. `npx wrangler tail` shows its decisions.
