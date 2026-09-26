# Live-data relay

A tiny Cloudflare Worker that lets the CTAS and WorldsIndex pages read public
astronomy data from archives that do not allow other websites to read them
directly (TNS public spectrum files, Gaia Science Alerts light curves, Pan-STARRS
detections at MAST, TESS/Kepler/K2 light-curve files at MAST, IRSA ZTF light
curves, ExoFOP-TESS). It fetches one allow-listed public address when a visitor
asks, and passes the answer straight back. It keeps nothing; Cloudflare may hold
an identical response for up to an hour so providers are not asked twice.

Rules (see `worker.js`): GET only; only the listed hosts and paths; only requests
from pages on jackmcguireastro.github.io (or a local preview on port 8000); no
cookies or credentials either way; responses over 40 MB are refused.

Deploy (free Cloudflare account, from this folder):

    npx wrangler login
    npx wrangler deploy

Then put the printed `https://…workers.dev` address into `/live-config.json`
as `"relay"`. Tests: `node scripts/test_live_relay.mjs`.
