// Tests for relay/worker.js with a stand-in for the network.
import assert from "node:assert/strict";
import worker, { matchRoute, SITE_ORIGINS, keep, KEEP, fitsContentType } from "../relay/worker.js";

// A minimal FITS file: one header block (SIMPLE, BITPIX -32, NAXIS 1) and one data block.
const FITS_BYTES = (() => {
  const card = (k, v) => (k.padEnd(8) + "= " + String(v).padStart(20)).padEnd(80);
  const header = (card("SIMPLE", "T") + card("BITPIX", -32) + card("NAXIS", 1) + card("NAXIS1", 4) + "END".padEnd(80)).padEnd(2880);
  const out = new Uint8Array(5760);
  for (let i = 0; i < 2880; i += 1) out[i] = header.charCodeAt(i);
  new DataView(out.buffer).setFloat32(2880, 1.5, false);
  return out;
})();

let upstreamCalls = [];
globalThis.fetch = async (url, init) => {
  upstreamCalls.push({ url, init });
  if (String(url).includes("big")) return new Response("x", { headers: { "Content-Length": String(50 * 1024 * 1024) } });
  if (/\.fits$/.test(String(url))) return new Response(FITS_BYTES, { status: 200, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  if (/\.fits\.gz$/.test(String(url))) return new Response(new Uint8Array([0x1f, 0x8b, 8, 0]), { status: 200, headers: {} });
  return new Response("1,2\n3,4\n", { status: 200, headers: { "Content-Type": "text/plain", "Set-Cookie": "a=b" } });
};
const call = (target, headers = { Origin: SITE_ORIGINS[0] }, method = "GET") =>
  worker.fetch(new Request("https://relay.example/?url=" + encodeURIComponent(target), { method, headers }));

const allowed = [
  "https://www.wis-tns.org/system/files/uploaded/GOTO/tns_2026pel_2461206.634757_NOT_ALFOSC_GOTO.dat",
  "https://gsaweb.ast.cam.ac.uk/alerts/alert/Gaia15adl/lightcurve.csv",
  "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.json?ra=250.29&dec=39.29&radius=0.0006",
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:TESS/product/tess2018206045859-s0001-0000000100100827-0120-s_lc.fits",
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HLSP/tess-spoc/s0040/target/0000/0002/6113/6679/hlsp_tess-spoc_tess_phot_0000000261136679-s0040_tess_v1_lc.fits",
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:Kepler/url/missions/kepler/lightcurves/0114/011446443/kplr011446443-2009131105131_llc.fits",
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:K2/url/missions/k2/lightcurves/c1/201300000/67000/ktwo201367065-c01_llc.fits",
  "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves?POS=CIRCLE%20250.29%2039.29%200.0004&FORMAT=csv",
  "https://exofop.ipac.caltech.edu/tess/target.php?id=261136679&json",
  "https://www.wis-tns.org/system/files/uploaded/ZTF/tns_2026abc_P60_SEDM_ZTF.fits",
  "https://www.wiserep.org/search/spectra?name=2026abht&public=yes&format=json&files_type=ascii&num_page=25&page=0",
  "https://www.wiserep.org/search/spectra?name=2026abht&public=yes&format=tsv",
  "https://www.wiserep.org/system/files/uploaded/TNS/SN2026abht_2026-09-12_10-29-45_P60_SEDM_TNS.ascii",
  "https://www.wiserep.org/system/files/uploaded/ePESSTO%2B/SN2026abc_EFOSC2.fits",
];
const refused = [
  "http://www.wis-tns.org/system/files/uploaded/a.dat",                       // not https
  "https://www.wis-tns.org/api/get/object",                                    // TNS API, not a public file
  "https://www.wis-tns.org/system/files/uploaded/a.dat?x=1",                   // extra query
  "https://evil.example/system/files/uploaded/a.dat",                          // other host
  "https://www.wiserep.org/search/spectra?name=2026abht&format=json",          // WISeREP listing without public=yes
  "https://www.wiserep.org/search/spectra?name=2026abht&public=no&format=json", // or asking for non-public rows
  "https://www.wiserep.org/search/spectra?name=2026abht&public=yes&format=html", // or a web page
  "https://www.wiserep.org/search/spectra?name=2026abht&public=yes&format=json&api_key=x", // or with extra parameters
  "https://www.wiserep.org/form-edit/spectrum/92931",                          // WISeREP record pages (sign-in)
  "https://www.wiserep.org/object/12345",                                      // WISeREP object pages
  "https://www.wiserep.org/system/files/uploaded/TNS/a.ascii?download=1",      // a file with a query
  "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.json?ra=1&dec=2&radius=5", // huge cone
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HST/product/x_drz.fits", // not a light curve
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HLSP/qlp/s0001/0000/0000/2515/5310/hlsp_qlp_tess_ffi_s0001-0000000025155310_tess_v01_llc.fits", // other HLSP
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HLSP/tess-spoc/s0040/hlsp_tess-spoc_tess_phot_0000000261136679-s0040_tess_v1_tp.fits", // pixels
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:TESS/product/../x_lc.fits", // path games
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:TESS/product/a_lc.fits&extra=1", // extra parameter
  "https://user:pw@gsaweb.ast.cam.ac.uk/alerts/alert/Gaia15adl/lightcurve.csv",
  "https://exofop.ipac.caltech.edu/tess/target.php?id=../../etc&json",
  "not a url",
];
for (const url of allowed) assert.ok(matchRoute(url), "should allow " + url);
for (const url of refused) assert.equal(matchRoute(url), null, "should refuse " + url);

let r = await call(allowed[0]);
assert.equal(r.status, 200);
assert.equal(r.headers.get("Access-Control-Allow-Origin"), SITE_ORIGINS[0]);
assert.equal(r.headers.get("Set-Cookie"), null, "provider cookies are never passed on");
assert.equal(await r.text(), "1,2\n3,4\n");
assert.equal(upstreamCalls.at(-1).init.method, "GET");
assert.ok(!("cookie" in (upstreamCalls.at(-1).init.headers || {})), "no visitor cookies go upstream");

r = await call(allowed[0], { Origin: "https://someone-else.example" });
assert.equal(r.status, 403);
r = await call(allowed[0], {});
assert.equal(r.status, 403, "requests without the site's Origin or Referer are refused");
r = await call(allowed[0], { Referer: SITE_ORIGINS[0] + "/ctas.html" });
assert.equal(r.status, 200, "a same-site Referer is accepted when no Origin is sent");
r = await call(refused[1]);
assert.equal(r.status, 400);
r = await call(allowed[0], { Origin: SITE_ORIGINS[0] }, "POST");
assert.equal(r.status, 405);
r = await worker.fetch(new Request("https://relay.example/", { method: "OPTIONS", headers: { Origin: SITE_ORIGINS[0] } }));
assert.equal(r.status, 204);
assert.equal(r.headers.get("Access-Control-Allow-Methods"), "GET");

const calls = upstreamCalls.length;
r = await call("https://gsaweb.ast.cam.ac.uk/alerts/alert/Gaia15big/lightcurve.csv");
assert.equal(r.status, 413, "oversized responses are refused");
assert.equal(upstreamCalls.length, calls + 1);

// FITS files: bytes pass through untouched, labelled application/fits even when the
// source calls them text; the same cache and size rules as every other file.
r = await call("https://www.wis-tns.org/system/files/uploaded/ZTF/tns_2026abc_P60_SEDM_ZTF.fits");
assert.equal(r.status, 200);
assert.equal(r.headers.get("Content-Type"), "application/fits");
assert.equal(r.headers.get("Cache-Control"), "public, max-age=3600");
const fitsBack = new Uint8Array(await r.arrayBuffer());
assert.equal(fitsBack.length, FITS_BYTES.length);
assert.deepEqual(Array.from(fitsBack.slice(0, 9)), Array.from(FITS_BYTES.slice(0, 9)), "the FITS header starts as sent");
assert.equal(new DataView(fitsBack.buffer).getFloat32(2880, false), 1.5, "the big-endian data is byte-identical");
assert.deepEqual(upstreamCalls.at(-1).init.cf, { cacheTtl: 3600, cacheEverything: true });
r = await call("https://www.wiserep.org/system/files/uploaded/TNS/SN2026abc_X.fits.gz");
assert.equal(r.headers.get("Content-Type"), "application/gzip");
assert.equal(fitsContentType("/system/files/uploaded/a/b.fits", "application/fits"), null, "a correct source label is kept");
assert.equal(fitsContentType("/system/files/uploaded/a/b.fits", "image/fits"), null);
assert.equal(fitsContentType("/system/files/uploaded/a/b.fits", "application/octet-stream"), null);
assert.equal(fitsContentType("/system/files/uploaded/a/b.FIT", null), "application/fits");
assert.equal(fitsContentType("/system/files/uploaded/a/b%2Bc.fts", "text/html"), "application/fits");
assert.equal(fitsContentType("/system/files/uploaded/a/b.ascii", "text/plain"), null, "text files keep their type");
// WISeREP: the listing and files are plain GETs with no credentials; a sign-in page
// for a proprietary file is passed back as the source sent it (the page reads it as such)
r = await call(allowed[allowed.length - 4]);
assert.equal(r.status, 200);
assert.ok(!("Authorization" in upstreamCalls.at(-1).init.headers) && !("cookie" in upstreamCalls.at(-1).init.headers), "no WISeREP credentials exist to send");
// NASA Exoplanet Archive TAP: SELECT on public tables only
const tap = (qy, fmt = "json") => "https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=" + encodeURIComponent(qy) + "&format=" + fmt;
assert.ok(matchRoute(tap("select pl_name,spec_type,spec_path from spectra where pl_name = 'WASP-18 b'")));
assert.ok(matchRoute(tap("select * from stellarhosts where hostname = 'WASP-18'")));
assert.equal(matchRoute(tap("select * from secret_table where 1=1")), null);
assert.equal(matchRoute(tap("select * from ps where 1=1; drop table ps")), null);
assert.equal(matchRoute(tap("select * from ps where 1=1") + "&maxrec=5"), null);
// atmosphere spectrum files: the relay opens the archive's page, then reads the file from its workspace
const spec = "https://exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly?atmospheres&spec_path=80/70/32/30/WASP_39_b_3.11466_3868_2.tbl";
assert.ok(matchRoute(spec));
assert.equal(matchRoute(spec.replace("80/70", "../70")), null);
assert.equal(matchRoute(spec + "&planet=x"), null);
assert.equal(matchRoute("https://exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly?atmospheres"), null);
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {
  upstreamCalls.push({ url, init });
  if (String(url).endsWith("nph-firefly?atmospheres")) return new Response(`<body onload="FF_InitPage ('/work/TMP_Ab_12/atmospheres/tab1', '/workspace/TMP_Ab_12', '/exodata/FDL', 'x')">`);
  return new Response("|CENTRALWAVELNG|PL_TRANDEP|\n 1.0 2.0\n", { status: 200 });
};
r = await call(spec);
assert.equal(r.status, 200);
assert.equal(upstreamCalls.at(-1).url, "https://exoplanetarchive.ipac.caltech.edu/workspace/TMP_Ab_12/atmospheres/tab1/data/80/70/32/30/WASP_39_b_3.11466_3868_2.tbl");
assert.ok((await r.text()).includes("CENTRALWAVELNG"));
globalThis.fetch = async (url, init) => { upstreamCalls.push({ url, init }); return new Response("<html>maintenance</html>"); };
r = await call(spec);
assert.equal(r.status, 502, "no workspace on the page means the source is unavailable");
globalThis.fetch = realFetch;
assert.ok(matchRoute("https://exofop.ipac.caltech.edu/tess/target.php?toi=1000.01&json"));

// keyed sources: the key comes from the Worker's secrets, never from the page
const ads = "https://api.adsabs.harvard.edu/v1/search/query?q=" + encodeURIComponent('full:"2026pel"') + "&fl=bibcode,title,author,pubdate&rows=20&sort=date+desc";
assert.ok(matchRoute(ads));
assert.equal(matchRoute(ads.replace("fl=bibcode", "fl=body,bibcode")), null, "only listed fields may be requested");
r = await call(ads);
assert.equal(r.status, 503, "without ADS_TOKEN the relay says the key is missing");
const withKey = (target, env) => worker.fetch(new Request("https://relay.example/?url=" + encodeURIComponent(target), { headers: { Origin: SITE_ORIGINS[0] } }), env);
r = await withKey(ads, { ADS_TOKEN: "ads-secret" });
assert.equal(r.status, 200);
assert.equal(upstreamCalls.at(-1).init.headers.Authorization, "Bearer ads-secret");
assert.ok(!(await r.text()).includes("ads-secret"));
const lasair = "https://api.lasair.lsst.ac.uk/api/cone/?ra=10.5&dec=-20.1&radius=2&requestType=nearest";
r = await withKey(lasair, { LASAIR_TOKEN: "lasair-secret" });
assert.equal(r.status, 200);
assert.equal(upstreamCalls.at(-1).url, "https://api.lasair.lsst.ac.uk/api/cone/");
assert.equal(upstreamCalls.at(-1).init.method, "POST");
assert.equal(upstreamCalls.at(-1).init.body, "ra=10.5&dec=-20.1&radius=2&requestType=nearest");
assert.equal(upstreamCalls.at(-1).init.headers.Authorization, "Token lasair-secret");
assert.equal(matchRoute("https://api.lasair.lsst.ac.uk/api/query/?selected=*&tables=objects"), null, "free-form Lasair queries are not relayed");
assert.equal(matchRoute("https://api.lasair.lsst.ac.uk/api/cone/?ra=1&dec=2&radius=900"), null);
assert.equal(matchRoute(tap("select pl_name from spectra where pl_name='x'")) && (await withKey(tap("select pl_name from spectra where pl_name='x'"), {})).status, 200, "public NASA queries need no key");
// keeper: restarts a workflow only when nothing is running and its last real run is old
{
  const now = Date.parse("2026-10-03T12:00:00Z");
  const ago = (m) => new Date(now - m * 60000).toISOString();
  const runsFor = {
    "ctas-cloud.yml": [{ status: "completed", conclusion: "cancelled", created_at: ago(5) }, { status: "completed", conclusion: "success", created_at: ago(120) }],
    "worldsindex-cloud.yml": [{ status: "in_progress", conclusion: null, created_at: ago(3) }],
    "freshness-watchdog.yml": [{ status: "completed", conclusion: "success", created_at: ago(20) }],
  };
  const calls = [];
  const fetcher = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", auth: init.headers && init.headers.Authorization, body: init.body });
    const wf = url.match(/workflows\/([^/]+)\//)[1];
    if (init.method === "POST") return new Response(null, { status: 204 });
    return new Response(JSON.stringify({ workflow_runs: runsFor[wf] }), { status: 200 });
  };
  const results = await keep({ GH_DISPATCH_TOKEN: "gh-secret" }, fetcher, now);
  assert.deepEqual(results.map((r) => r.action), ["started", "running", "recent (20 min)"],
    "a cancelled run does not count as recent; a running one is left alone");
  const posts = calls.filter((c) => c.method === "POST");
  assert.equal(posts.length, 1);
  assert.ok(posts[0].url.endsWith("/actions/workflows/ctas-cloud.yml/dispatches"));
  assert.equal(JSON.parse(posts[0].body).ref, "main");
  assert.ok(calls.every((c) => c.auth === "Bearer gh-secret"));
  assert.equal(KEEP.length, 3);
  assert.deepEqual(await keep({}, fetcher, now), [{ workflow: "*", action: "skipped: no GH_DISPATCH_TOKEN secret" }]);
  const broken = await keep({ GH_DISPATCH_TOKEN: "x" }, async () => new Response("no", { status: 401 }), now);
  assert.ok(broken.every((r) => r.action.includes("HTTP 401")));
}
console.log("live relay: all checks passed");
