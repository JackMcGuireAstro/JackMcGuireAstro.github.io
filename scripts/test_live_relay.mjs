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
console.log("live relay: all checks passed");
