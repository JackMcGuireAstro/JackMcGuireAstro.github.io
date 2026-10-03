// Checks for the finder-chart page (tools/finder/finder.js): the hips2fits and Sesame
// requests it builds, the Sesame parser, URL parameters, and the SVG overlay geometry.
import assert from "node:assert/strict";
import { SURVEYS, looksLikeCoordinates, hips2fitsUrl, sesameUrl, parseSesame, readUrlParams, scaleBar, overlaySvg, describeMove, captionHtml, IMAGE_SIZE } from "../tools/finder/finder.js";
import { offsetMove } from "../tools/lib/astrometry.js";

let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg ?? ""} ${a} vs ${b} (tol ${tol})`);

test("hips2fits URL follows the documented parameters", () => {
  const url = new URL(hips2fitsUrl({ hips: "CDS/P/DSS2/red", ra: 202.469575, dec: 47.195258, fovArcmin: 10 }));
  assert.equal(url.origin + url.pathname, "https://alasky.cds.unistra.fr/hips-image-services/hips2fits");
  const p = Object.fromEntries(url.searchParams);
  assert.deepEqual(p, { hips: "CDS/P/DSS2/red", width: "1000", height: "1000", fov: "0.166667", projection: "TAN", coordsys: "icrs",
    ra: "202.469575", dec: "47.195258", format: "jpg" });
  assert.equal(new URL(hips2fitsUrl({ hips: "CDS/P/2MASS/J", ra: 0, dec: -89.5, fovArcmin: 60 })).searchParams.get("fov"), "1.000000");
});

test("the five surveys, with Pan-STARRS limited to Dec > −30°", () => {
  assert.deepEqual(SURVEYS.map((s) => s.hips), ["CDS/P/DSS2/red", "CDS/P/DSS2/blue", "CDS/P/PanSTARRS/DR1/color-z-zg-g", "CDS/P/PanSTARRS/DR1/g", "CDS/P/2MASS/J"]);
  assert.ok(SURVEYS.filter((s) => s.hips.includes("PanSTARRS")).every((s) => s.decMin === -30));
});

test("Sesame URL and its plain-text answer", () => {
  assert.equal(sesameUrl(" M 51 "), "https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/A?M%2051");
  const text = [
    "# M 51\t#Q12345", "#=Sc=Simbad (CDS, via client/server):    1     4ms", "%@ 1522887", "%I.0 M  51", "%C.0 Sy2",
    "%J 202.469575 +47.195258 = 13:29:52.69 +47:11:42.9", "%J.E [6.27 4.47 90] A 2006AJ....131.1163S", "%I NAME Whirlpool Galaxy",
    "", "#====Done (2026-Oct-03,10:00:00z)====",
  ].join("\n");
  assert.deepEqual(parseSesame(text), { ra: 202.469575, dec: 47.195258, id: "M 51" });
  assert.equal(parseSesame("# xyzzy\n#! *** Nothing found *** \n#====Done====\n"), null);
  assert.equal(parseSesame(""), null);
  assert.equal(parseSesame("%J 05.5 -05.25 = ...").dec, -5.25);
});

test("names go to Sesame, coordinate-like text gets a parse error instead", () => {
  for (const name of ["M 51", "3C 273", "2MASS J12345678+1234567", "SN 2026abc", "Vega", "NGC 1300", "HD 209458"]) assert.equal(looksLikeCoordinates(name), false, name);
  for (const c of ["13 29 52.7 +47 11 43", "13h29m52.7s +47d11m43s", "25 00 00 +10", "202.47, 47.19", "−5 30"]) assert.equal(looksLikeCoordinates(c), true, c);
});

test("URL parameters clamp the field and ignore unknown surveys", () => {
  const p = readUrlParams("?ra=202.47&dec=47.195&name=M%2051&fov=90&survey=ps1-g");
  assert.deepEqual(p, { ra: "202.47", dec: "47.195", name: "M 51", fov: 60, survey: "ps1-g" });
  assert.equal(readUrlParams("?fov=1").fov, 2);
  assert.equal(readUrlParams("?fov=abc&survey=nope").fov, null);
  assert.equal(readUrlParams("?survey=nope").survey, null);
});

test("scale bars are round and at most a quarter of the field", () => {
  assert.deepEqual(scaleBar(10), { arcsec: 120, label: "2′" });
  assert.deepEqual(scaleBar(2), { arcsec: 30, label: "30″" });
  assert.deepEqual(scaleBar(60), { arcsec: 900, label: "15′" });
  for (let f = 2; f <= 60; f += 0.5) assert.ok(scaleBar(f).arcsec <= f * 15, String(f));
});

const coords = (svg, cls) => [...svg.matchAll(new RegExp(`<rect class="${cls}" x="([\\d.]+)" y="([\\d.]+)" width="([\\d.]+)"`, "g"))].map((m) => [Number(m[1]) + Number(m[3]) / 2, Number(m[2]) + Number(m[3]) / 2]);

test("overlay: N up, E left, the scale bar's length, and the offset star's position", () => {
  const svg = overlaySvg({ ra: 150, dec: 20, fovArcmin: 10 });
  assert.ok(svg.includes('viewBox="0 0 1000 1000"') && svg.includes('aria-hidden="true"'));
  assert.ok(/>N<\/text>/.test(svg) && /text-anchor="end">E<\/text>/.test(svg));
  // the E label sits left of the compass origin, the N label above it
  const n = /<text x="([\d.]+)" y="([\d.]+)" text-anchor="middle">N</.exec(svg), e = /<text x="([\d.]+)" y="([\d.]+)" text-anchor="end">E</.exec(svg);
  assert.ok(Number(e[1]) < Number(n[1]) && Number(n[2]) < Number(e[2]));
  // scale bar: 2′ = 120″ at 1000 px / 600″
  const bar = /<line x1="([\d.]+)" y1="940" x2="([\d.]+)" y2="940"\/>/.exec(svg);
  near(Number(bar[2]) - Number(bar[1]), 200, 1e-9);
  assert.ok(svg.includes(">10′ × 10′<"));
  // an offset star 60″ east and 120″ north of the target is drawn left of and above centre
  const dec = 20, ra = 150 + 60 / 3600 / Math.cos(dec * Math.PI / 180);
  const withStar = overlaySvg({ ra: 150, dec, fovArcmin: 10, offset: { ra, dec: dec + 120 / 3600, label: "offset star" } });
  const [[x, y]] = coords(withStar, "offset");
  near(x, 500 - 100, 0.3); near(y, 500 - 200, 0.3);
  const outside = overlaySvg({ ra: 150, dec, fovArcmin: 2, offset: { ra: 150, dec: dec + 0.2 } });
  assert.ok(outside.includes("offset star (outside)") && !outside.includes("<rect"));
});

test("offset move wording and caption", () => {
  const m = offsetMove(150, 20, 150 - 30 / 3600 / Math.cos(20 * Math.PI / 180), 20 - 15 / 3600);
  const d = describeMove(m);
  assert.equal(d.text, "30.0″ west, 15.0″ south");
  assert.ok(d.detail.startsWith("ΔRA·cos δ = −30.0″"), d.detail);
  const cap = captionHtml({ name: "SN <x>", ra: 202.469575, dec: -47.195258, fovArcmin: 10, survey: SURVEYS[0], date: "2026-10-03", move: d });
  assert.ok(cap.includes("SN &lt;x&gt;") && cap.includes("13:29:52.70") && cap.includes("−47:11:42.9") && cap.includes("north up, east left"));
  assert.ok(cap.includes("DSS2 red via CDS hips2fits") && cap.includes("2026-10-03") && cap.includes("30.0″ west"));
  assert.equal(IMAGE_SIZE, 1000);
});

console.log(`\n${passed} finder checks passed`);
