// ctas/sky.js in a bare JavaScript runtime: the pure parts (coordinates, the approximate
// localisation region, the finder-chart sources and overlay, the print document, the layer
// list). The browser behaviour is covered by scripts/test_ctas_browser.py.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const listeners = {};
const stubElement = () => ({addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }});
const context = {
  console,
  URLSearchParams,
  encodeURIComponent,
  Promise,
  Number, Math, String, Array, Object, JSON, Date, isFinite, Error,
  window: null,
  document: {addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); }, createElement: stubElement, getElementById() { return null; }, querySelector() { return null; }, head: {appendChild() {}}},
  location: {origin: "https://example.test", pathname: "/ctas.html"},
  setTimeout,
};
context.window = context;
context.window.addEventListener = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "ctas", "sky.js"), "utf8"), context, {filename: "sky.js"});
const sky = context.CTASSky;
// objects come from the vm realm, so compare by value
const same = (actual, expected, message) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
assert.ok(sky, "sky.js exposes CTASSky");
assert.ok(listeners["ctas:candidate-opened"] && listeners["ctas:snapshot"], "it listens for the dossier and snapshot events");

// coordinates: complete, in range, numeric
same(sky.coordinates({ra_deg: 10.5, dec_deg: -20.25}), {ra: 10.5, dec: -20.25});
for (const bad of [{}, {ra_deg: 10}, {ra_deg: "x", dec_deg: 1}, {ra_deg: 360, dec_deg: 0}, {ra_deg: 1, dec_deg: 91}, {ra_deg: null, dec_deg: null}]) assert.equal(sky.coordinates(bad), null, JSON.stringify(bad));

// the pinned runtime and the four layers
assert.equal(sky.ALADIN_VERSION, "3.6.1");
assert.equal(sky.ALADIN_URL, "https://aladin.cds.unistra.fr/AladinLite/api/v3/3.6.1/aladin.js");
same(sky.LAYERS.map((l) => l.hips), ["CDS/P/DSS2/color", "CDS/P/DESI-Legacy-Surveys/DR10/color", "CDS/P/PanSTARRS/DR1/color-z-zg-g", "CDS/P/2MASS/color"]);
assert.match(sky.layerButtons(-45), /Pan-STARRS DR1 colour \(outside footprint\)/);
assert.doesNotMatch(sky.layerButtons(10), /outside footprint/);
assert.match(sky.SAMP_NOTE, /Chrome, Firefox and Edge/);
assert.match(sky.SAMP_NOTE, /not work in Safari/);

// localisation: 90% area wins, retracted notices are ignored, optical records get none
const optical = {ra_deg: 1, dec_deg: 2, primary_messenger: "electromagnetic", follow_up: {}};
assert.equal(sky.localisation(optical), null);
const gw = {ra_deg: 1, dec_deg: 2, primary_messenger: "gravitational-wave", record_role: "localization-region-alert",
  follow_up: {messenger_signals: [{sky_area_90_sq_deg: 1000, observed_at: "2026-09-01T00:00:00Z", retracted: true},
                                  {sky_area_90_sq_deg: 314.159265, observed_at: "2026-10-01T00:00:00Z", skymap_url: "https://gracedb.ligo.org/a.fits"},
                                  {sky_area_90_sq_deg: 50, observed_at: "2026-09-15T00:00:00Z", skymap_url: "http://insecure/x"}]}};
const region = sky.localisation(gw);
assert.ok(Math.abs(region.radius_deg - 10) < 1e-6, "a circle of equal area");
assert.equal(region.skymap_url, "https://gracedb.ligo.org/a.fits");
assert.equal(region.approximate, true);
assert.match(region.basis, /90% sky area/);
const neutrino = {ra_deg: 1, dec_deg: 2, primary_messenger: "neutrino", coordinate_error_arcsec: 1800, follow_up: {}};
assert.ok(Math.abs(sky.localisation(neutrino).radius_deg - 0.5) < 1e-9);
assert.equal(sky.localisation({ra_deg: 1, dec_deg: 2, primary_messenger: "electromagnetic", coordinate_error_arcsec: 1800, follow_up: {}}), null, "optical uncertainty alone draws nothing");
assert.equal(sky.localisation({primary_messenger: "neutrino", coordinate_error_arcsec: 1800}), null, "no coordinates, no region");

// finder sources: DR10, DR9, PS1 north of -30, DSS2 last
same(sky.finderSources(10, 5, 2).map((s) => s.id), ["ls-dr10", "ls-dr9", "ps1", "dss2"]);
same(sky.finderSources(10, -45, 2).map((s) => s.id), ["ls-dr10", "ls-dr9", "dss2"]);
const url = sky.legacyCutoutUrl(10.123456789, -5.5, 2, "ls-dr10");
assert.match(url, /^https:\/\/www\.legacysurvey\.org\/viewer\/jpeg-cutout\?ra=10\.123457&dec=-5\.500000&layer=ls-dr10&pixscale=0\.2344&width=512&height=512$/);
assert.match(sky.legacyCutoutUrl(10, 5, 10, "ls-dr9"), /pixscale=1\.1719/);
assert.match(sky.hips2fitsUrl(10, 5, 10, "CDS/P/DSS2/color"), /hips2fits\?hips=CDS%2FP%2FDSS2%2Fcolor&width=512&height=512&fov=0\.166667&projection=TAN/);
const listing = "projcell,subcell,ra,dec,filter,mjd,type,filename,shortname\n1,1,1,1,g,1,stack,/g.fits,g\n1,1,1,1,i,1,stack,/i.fits,i\n1,1,1,1,y,1,stack,/y.fits,y\n";
assert.match(sky.ps1CutoutUrl(10, 5, 2, listing), /fitscut\.cgi\?red=\/y\.fits&green=\/i\.fits&blue=\/g\.fits&format=jpeg&size=480&ra=10\.000000&dec=5\.000000&output_size=512/);
assert.match(sky.ps1CutoutUrl(10, 5, 10, "projcell,subcell,ra,dec,filter,mjd,type,filename,shortname\n1,1,1,1,r,1,stack,/r.fits,r\n"), /\?red=\/r\.fits&format=jpeg&size=2400/);
assert.equal(sky.ps1CutoutUrl(10, 5, 2, ""), null);
assert.equal(sky.ps1CutoutUrl(10, 5, 2, "no,header\n"), null);

// overlay: north up, east left, a round scale bar no longer than a quarter of the field
same(sky.scaleBar(2), {arcsec: 30, label: "30″"});
same(sky.scaleBar(10), {arcsec: 120, label: "2′"});
const svg = sky.finderOverlaySvg(2, 512);
assert.match(svg, /<text[^>]*>N<\/text>/);
assert.match(svg, /<text[^>]*>E<\/text>/);
assert.match(svg, />30″</);
assert.match(svg, /aria-hidden="true"/);

// panel and print document carry name, coordinates and epoch; nothing without coordinates
const candidate = {event_id: "123e4567-e89b-42d3-a456-426614174000", name: "AT2026<x>", ra_deg: 10.5, dec_deg: -20.25, event_type: "optical-transient", classification: "SN Ia", discovery_survey: "ZTF",
  astro_evidence: {target: {coordinateEpoch: "J2000"}}};
const panel = sky.finderPanel(candidate);
assert.match(panel, /data-finder="123e4567-e89b-42d3-a456-426614174000"/);
assert.match(panel, /AT2026&lt;x&gt;/);
assert.match(panel, /epoch J2000/);
assert.match(panel, /data-finder-field="2"[\s\S]*data-finder-field="10"/);
assert.match(panel, /tools\/finder\/\?ra=10\.500000&amp;dec=-20\.250000&amp;name=AT2026%3Cx%3E&amp;fov=10/);
assert.equal(sky.finderPanel({name: "nowhere"}), "");
const printed = sky.printDocument(candidate, [{fov: 2, url: "https://img/2", label: "Legacy Surveys DR10"}, {fov: 10, url: "https://img/10", label: "DSS2"}]);
assert.match(printed, /<title>Finder chart: AT2026&lt;x&gt;<\/title>/);
assert.match(printed, /10\.500000, -20\.250000 \(degrees\), epoch J2000/);
assert.equal((printed.match(/<figure>/g) || []).length, 2);
assert.match(printed, /north up, east left · scale bar 30″/);
assert.match(printed, /Stable event id 123e4567-e89b-42d3-a456-426614174000/);
assert.doesNotMatch(printed, /<script/);

console.log("sky.js: coordinates, pinned runtime and layers, approximate localisation, finder sources and overlay, print document passed.");
