// Tests for relay/worker.js with a stand-in for the network.
import assert from "node:assert/strict";
import worker, { matchRoute, SITE_ORIGINS } from "../relay/worker.js";

let upstreamCalls = [];
globalThis.fetch = async (url, init) => {
  upstreamCalls.push({ url, init });
  if (String(url).includes("big")) return new Response("x", { headers: { "Content-Length": String(50 * 1024 * 1024) } });
  return new Response("1,2\n3,4\n", { status: 200, headers: { "Content-Type": "text/plain", "Set-Cookie": "a=b" } });
};
const call = (target, headers = { Origin: SITE_ORIGINS[0] }, method = "GET") =>
  worker.fetch(new Request("https://relay.example/?url=" + encodeURIComponent(target), { method, headers }));

const allowed = [
  "https://www.wis-tns.org/system/files/uploaded/GOTO/tns_2026pel_2461206.634757_NOT_ALFOSC_GOTO.dat",
  "https://gsaweb.ast.cam.ac.uk/alerts/alert/Gaia15adl/lightcurve.csv",
  "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.json?ra=250.29&dec=39.29&radius=0.0006",
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:TESS/product/tess2018206045859-s0001-0000000100100827-0120-s_lc.fits",
  "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves?POS=CIRCLE%20250.29%2039.29%200.0004&FORMAT=csv",
  "https://exofop.ipac.caltech.edu/tess/target.php?id=261136679&json",
];
const refused = [
  "http://www.wis-tns.org/system/files/uploaded/a.dat",                       // not https
  "https://www.wis-tns.org/api/get/object",                                    // TNS API, not a public file
  "https://www.wis-tns.org/system/files/uploaded/a.dat?x=1",                   // extra query
  "https://evil.example/system/files/uploaded/a.dat",                          // other host
  "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.json?ra=1&dec=2&radius=5", // huge cone
  "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HST/product/x_drz.fits", // not a light curve
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
console.log("live relay: all checks passed");
