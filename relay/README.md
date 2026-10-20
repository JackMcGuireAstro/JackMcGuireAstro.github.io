# Live-data relay

A tiny Cloudflare Worker that lets the CTAS and WorldsIndex pages read public
astronomy data from archives that do not allow other websites to read them
directly (TNS public spectrum files, ASCII or FITS; WISeREP public-spectra listings
and the public files they name; Gaia Science Alerts light curves, Pan-STARRS
detections at MAST, TESS/Kepler/K2 light-curve files at MAST (mission files and the
TESS-SPOC full-frame light curves), IRSA ZTF light
curves, ExoFOP-TESS, the NASA Exoplanet Archive's TAP service and spectrum files,
NED, VizieR (Gaia DR3, SWEET-Cat), HEASARC, AAVSO VSX, NASA ADS and Lasair). It fetches one allow-listed public address when a visitor
asks, and passes the answer straight back. It keeps nothing; Cloudflare may hold
an identical response for up to an hour so providers are not asked twice.

Rules (see `worker.js`): GET only; only the listed hosts and paths; only requests
from pages on jackmcguireastro.github.io (or a local preview on port 8000); no
cookies or credentials either way; responses over 40 MB are refused. Files pass
through byte for byte; a `.fits` file is labelled `application/fits` (a `.fits.gz`
`application/gzip`) when the source labels it as text or not at all. WISeREP is
asked only for its public listing (`/search/spectra?…&public=yes&format=json|tsv|csv`)
and for files under `/system/files/uploaded/`; a proprietary spectrum comes back as a
sign-in page or a 401/403, which the CTAS page reports as proprietary. No WISeREP
account is used or asked for.

## Routes

Each route is one host, one path pattern and a check on the query string (`ROUTES` in
`worker.js`). What each one is for:

| Host and path | Used by | Allowed |
|---|---|---|
| `www.wis-tns.org/system/files/uploaded/…` | CTAS "Plot spectrum" | public upload files, no query |
| `www.wiserep.org/search/spectra` | CTAS spectrum lookup and the public-spectra count | `public=yes`, `format=json|tsv|csv`, a name ≤ 80 chars |
| `www.wiserep.org/system/files/uploaded/…` | CTAS "Plot spectrum" | public upload files, no query |
| `gsaweb.ast.cam.ac.uk/alerts/alert/<name>/lightcurve.csv` | CTAS Gaia Alerts by name | no query |
| `gsaweb.ast.cam.ac.uk/alerts/alerts.csv` | CTAS Gaia Alerts by position (the whole public index, matched in the browser) | no query |
| `catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/(detection|mean)` | CTAS Pan-STARRS cone | radius ≤ 0.01° |
| `mast.stsci.edu/api/v0.1/Download/file` | WorldsIndex light curves | TESS/Kepler/K2 `_lc`/`_llc`/`_slc` FITS and TESS-SPOC light curves only |
| `irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves` | CTAS IRSA ZTF cone | `POS=CIRCLE ra dec r`, r < 1° |
| `exofop.ipac.caltech.edu/tess/target.php` | WorldsIndex ExoFOP | `id=<TIC>` or `toi=<TOI>` with `json` |
| `exoplanetarchive.ipac.caltech.edu/TAP/sync` | WorldsIndex archive rows | one `SELECT … FROM spectra|ml|stellarhosts|ps|pscomppars|toi|k2pandc|cumulative WHERE …`, csv or json |
| `exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly` | WorldsIndex spectrum files | `atmospheres&spec_path=…` (resolved through the archive's workspace) |
| `ned.ipac.caltech.edu/tap/sync` | CTAS NED cone | `SELECT TOP n … FROM NEDTAP.objdir WHERE CONTAINS(… CIRCLE(… r))=1`, r ≤ 0.05° |
| `tapvizier.cds.unistra.fr/TAPVizieR/tap/sync` | CTAS Gaia DR3 cone; WorldsIndex Gaia DR3 by source id and SWEET-Cat by host | `"I/355/gaiadr3"` (cone ≤ 0.05° or `Source=<id>`) and `"J/A+A/691/A53/catalog"` (the SWEET-Cat table, August 2024 copy; `Name='…'`; the earlier `"J/A+A/656/A53/sweetcat"` stays allowed for pages already open but CDS does not serve that designation) |
| `heasarc.gsfc.nasa.gov/xamin/vo/tap/sync` | CTAS HEASARC pointed observations | `swiftmastr|xmmmaster|chanmaster|numaster|nicermastr`, cone ≤ 0.25° |
| `www.aavso.org/vsx/index.php` | CTAS known variables near the position | `view=api.list`, radius ≤ 0.1°, json |
| `api.adsabs.harvard.edu/v1/search/query` | both pages' paper lists | `q` ≤ 400 chars, listed `fl` fields, ≤ 50 rows; key `ADS_TOKEN` |
| `api.lasair.lsst.ac.uk/api/(object|cone|sherlock/position)/` | CTAS Rubin via Lasair | listed parameters, radius ≤ 60″; key `LASAIR_TOKEN` |

TAP routes (NED, VizieR, HEASARC) accept only `REQUEST=doQuery`, `LANG=ADQL`, `FORMAT=csv|json`,
an optional `MAXREC` ≤ 999 and one `QUERY` matching the route's pattern; no `UPLOAD`, no
semicolons, parameter names case-insensitive but each at most once (`tapQuery`).

The pages' "Check all sources now" control re-runs every source for one target; a second press
passes `fresh`, which only bypasses the visitor's browser cache (`cache: "reload"`). The relay
address does not change, so this edge cache still stands between visitors and the providers.

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
