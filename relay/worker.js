/*
 * Live-data relay for jackmcguireastro.github.io (CTAS and WorldsIndex).
 *
 * Some astronomy archives serve public data but do not let a web page on another
 * site read it (no CORS header). This Cloudflare Worker fetches one allow-listed
 * public URL at a visitor's request and passes the response straight back with a
 * CORS header for the site. It stores nothing (Cloudflare's edge may keep a
 * response for up to an hour to spare the providers repeated identical requests).
 *
 * Request:  GET https://<worker>/?url=<encoded https URL>
 * Only GET, only the hosts and paths below, only from the site's own pages, no
 * visitor cookies or credentials in either direction, responses capped at 40 MB.
 *
 * Two sources need a free access key (ADS papers, Lasair for Rubin alerts). Those
 * keys are stored as Worker secrets (`npx wrangler secret put ADS_TOKEN`,
 * `... LASAIR_TOKEN`), added only to requests for their own host, and never sent to
 * the page. Their routes are narrow read-only queries; without the key the relay
 * answers 503 for them.
 */
export const SITE_ORIGINS = [
  "https://jackmcguireastro.github.io",
  "http://localhost:8000",
  "http://127.0.0.1:8000",
];
export const MAX_BYTES = 40 * 1024 * 1024;
export const CACHE_SECONDS = 3600;

// Each rule: exact host, a path pattern, and an optional check on the query string.
export const ROUTES = [
  { // TNS public uploads: spectra and reports attached to public object pages
    host: "www.wis-tns.org",
    path: /^\/system\/files\/uploaded\/[A-Za-z0-9_\-./%+]+$/,
    query: (q) => q.toString() === "",
  },
  { // Gaia Science Alerts light curves
    host: "gsaweb.ast.cam.ac.uk",
    path: /^\/alerts\/alert\/Gaia\d{2}[a-z]{1,4}\/lightcurve\.csv$/,
    query: (q) => q.toString() === "",
  },
  { // Pan-STARRS DR2 detections (cone search) from MAST catalogs
    host: "catalogs.mast.stsci.edu",
    path: /^\/api\/v0\.1\/panstarrs\/dr2\/(detection|mean)\.(json|csv)$/,
    query: (q) => q.has("ra") && q.has("dec") && Number(q.get("radius")) > 0 && Number(q.get("radius")) <= 0.01,
  },
  { // TESS / Kepler / K2 light-curve files from MAST
    host: "mast.stsci.edu",
    path: /^\/api\/v0\.1\/Download\/file$/,
    query: (q) => /^mast:(TESS|Kepler|K2)\/[A-Za-z0-9_\-./]+_(lc|llc|slc)\.fits$/.test(q.get("uri") || ""),
  },
  { // ZTF data-release light curves from IRSA (cone search)
    host: "irsa.ipac.caltech.edu",
    path: /^\/cgi-bin\/ZTF\/nph_light_curves$/,
    query: (q) => /^CIRCLE [-\d.]+ [-\d.]+ 0?\.\d+$/.test(q.get("POS") || ""),
  },
  { // ExoFOP-TESS target overviews, by TIC number or TOI
    host: "exofop.ipac.caltech.edu",
    path: /^\/tess\/target\.php$/,
    query: (q) => (/^\d{1,12}$/.test(q.get("id") || "") || /^\d{1,6}(\.\d{2})?$/.test(q.get("toi") || "")) && q.has("json"),
  },
  { // NASA Exoplanet Archive TAP: read-only SELECTs on public tables
    host: "exoplanetarchive.ipac.caltech.edu",
    path: /^\/TAP\/sync$/,
    query: (q) => /^select\s[\w\s,*().]{1,400}\sfrom\s(spectra|ml|stellarhosts|ps|pscomppars|toi|k2pandc)\s+where\s[^;]{1,600}$/i.test(q.get("query") || "")
      && ["json", "csv"].includes(q.get("format") || "") && [...q.keys()].every((k) => k === "query" || k === "format"),
  },
  { // NASA Exoplanet Archive atmospheric-spectrum data files
    host: "exoplanetarchive.ipac.caltech.edu",
    path: /^\/data\/ExoData\/[A-Za-z0-9_\-./%+]+\.(tbl|txt|csv|dat)$/,
    query: (q) => q.toString() === "",
  },
  { // NASA ADS search: papers about an object (key: ADS_TOKEN)
    host: "api.adsabs.harvard.edu",
    path: /^\/v1\/search\/query$/,
    query: (q) => (q.get("q") || "").length > 0 && (q.get("q") || "").length <= 400
      && (q.get("fl") || "").split(",").every((f) => ["bibcode", "title", "author", "pubdate", "doctype", "citation_count", "doi"].includes(f))
      && Number(q.get("rows") || 10) <= 50 && [...q.keys()].every((k) => ["q", "fl", "rows", "sort"].includes(k)),
    auth: { secret: "ADS_TOKEN", scheme: "Bearer" },
  },
  { // Lasair (Rubin/LSST alerts): cone search, object with light curve, Sherlock context (key: LASAIR_TOKEN)
    host: "api.lasair.lsst.ac.uk",
    path: /^\/api\/(object|cone|sherlock\/position)\/$/,
    query: (q) => [...q.keys()].length > 0 && [...q.keys()].every((k) => ["objectId", "ra", "dec", "radius", "requestType", "lite", "lasair_added"].includes(k))
      && (!q.has("radius") || Number(q.get("radius")) <= 60) && (!q.has("objectId") || /^\d{1,25}$/.test(q.get("objectId"))),
    auth: { secret: "LASAIR_TOKEN", scheme: "Token" },
    postForm: true,
  },
];

export function findRoute(target) {
  let url;
  try { url = new URL(target); } catch { return null; }
  if (url.protocol !== "https:" || url.username || url.password || url.port) return null;
  const route = ROUTES.find((r) => r.host === url.hostname && r.path.test(url.pathname) && r.query(url.searchParams));
  return route ? { url, route } : null;
}
export function matchRoute(target) {
  const found = findRoute(target);
  return found ? found.url : null;
}

function allowedOrigin(request) {
  const origin = request.headers.get("Origin");
  if (origin) return SITE_ORIGINS.includes(origin) ? origin : null;
  const referer = request.headers.get("Referer") || "";
  const match = SITE_ORIGINS.find((o) => referer === o || referer.startsWith(o + "/"));
  return match || null;
}

function reply(status, message, origin) {
  const headers = { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" };
  if (origin) { headers["Access-Control-Allow-Origin"] = origin; headers["Vary"] = "Origin"; }
  return new Response(message + "\n", { status, headers });
}

export default {
  async fetch(request, env = {}) {
    const origin = allowedOrigin(request);
    if (request.method === "OPTIONS") {
      if (!origin) return reply(403, "This relay only serves jackmcguireastro.github.io.", null);
      return new Response(null, { status: 204, headers: {
        "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET",
        "Access-Control-Max-Age": "86400", "Vary": "Origin" } });
    }
    if (request.method !== "GET") return reply(405, "Only GET is relayed.", origin);
    if (!origin) return reply(403, "This relay only serves jackmcguireastro.github.io.", null);
    const target = new URL(request.url).searchParams.get("url") || "";
    const found = findRoute(target);
    if (!found) return reply(400, "That address is not on the relay's list of public sources.", origin);
    const { url: upstream, route } = found;
    const headers0 = { "User-Agent": "jackmcguireastro.github.io live-data relay (https://jackmcguireastro.github.io)", "Accept": "*/*" };
    if (route.auth) {
      const key = env[route.auth.secret];
      if (!key) return reply(503, "This source needs an access key that has not been added to the relay yet.", origin);
      headers0.Authorization = route.auth.scheme + " " + key;
    }
    let init = { method: "GET", headers: headers0, redirect: "follow", cf: { cacheTtl: CACHE_SECONDS, cacheEverything: true } };
    let address = upstream.toString();
    if (route.postForm) {
      address = upstream.origin + upstream.pathname;
      init = { ...init, method: "POST", body: upstream.searchParams.toString(),
               headers: { ...headers0, "Content-Type": "application/x-www-form-urlencoded" } };
      delete init.cf;
    }

    let response;
    try {
      response = await fetch(address, init);
    } catch (error) {
      return reply(502, "The source could not be reached: " + String(error && error.message || error).slice(0, 200), origin);
    }
    // Redirects are followed: the request itself was allow-listed, and archives such as
    // MAST hand file downloads to their own storage hosts.
    const length = Number(response.headers.get("Content-Length") || 0);
    if (length > MAX_BYTES) return reply(413, "The source file is larger than the relay's 40 MB limit.", origin);

    let seen = 0;
    const limiter = new TransformStream({
      transform(chunk, controller) {
        seen += chunk.byteLength;
        if (seen > MAX_BYTES) { controller.error(new Error("response exceeded 40 MB")); return; }
        controller.enqueue(chunk);
      },
    });
    const headers = new Headers();
    for (const name of ["Content-Type", "Content-Length", "Last-Modified", "ETag"]) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Access-Control-Expose-Headers", "X-Relay-Source, Content-Length");
    headers.set("Vary", "Origin");
    headers.set("Cache-Control", "public, max-age=" + CACHE_SECONDS);
    headers.set("X-Relay-Source", upstream.hostname);
    return new Response(response.body ? response.body.pipeThrough(limiter) : null, { status: response.status, headers });
  },
};
