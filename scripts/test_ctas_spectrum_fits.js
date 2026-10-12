// Tests for ctas/spectrum-fits.js: FITS spectra in the layouts TNS and WISeREP host
// (tests/fixtures/spectra/*.fits, written by scripts/make_spectrum_fits_fixtures.py with
// the standard library, not by this reader), small files built here for the remaining
// BITPIX values and header forms, the rejected cases, and the fetch path that tells FITS
// from ASCII and inflates gzip (stubbed network).
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");
const fits = require("../ctas/spectrum-fits.js");
const spectrum = require("../ctas/spectrum-ascii.js");

const FIXTURES = path.join(__dirname, "..", "tests", "fixtures", "spectra");
const read = (name) => fs.readFileSync(path.join(FIXTURES, name));
const close = (a, b, tolerance, message) => assert.ok(Math.abs(a - b) <= tolerance, `${message}: ${a} vs ${b}`);
const first = (parsed) => parsed.points[0], last = (parsed) => parsed.points[parsed.points.length - 1];

// --- a tiny FITS writer for the inline cases -------------------------------------------
function card(key, value, comment) {
  let text;
  if (typeof value === "boolean") text = (value ? "T" : "F").padStart(20);
  else if (typeof value === "number") text = String(value).padStart(20);
  else text = "'" + String(value).replace(/'/g, "''").padEnd(8) + "'";
  return (key.padEnd(8) + "= " + text + (comment ? " / " + comment : "")).slice(0, 80).padEnd(80);
}
function block(text) { return text.padEnd(Math.ceil(text.length / 2880) * 2880); }
function header(cards) { return block(cards.join("") + "END".padEnd(80)); }
function bytesOf(text) { const out = new Uint8Array(text.length); for (let i = 0; i < text.length; i += 1) out[i] = text.charCodeAt(i); return out; }
function pad(data) { const out = new Uint8Array(Math.ceil(data.length / 2880) * 2880); out.set(data); return out; }
function concat(parts) {
  const total = parts.reduce((n, p) => n + p.length, 0), out = new Uint8Array(total);
  let at = 0; for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}
function image(bitpix, values, extra) {
  const cards = [card("SIMPLE", true), card("BITPIX", bitpix), card("NAXIS", 1), card("NAXIS1", values.length)].concat(extra || []);
  const size = Math.abs(bitpix) / 8, data = new Uint8Array(values.length * size), view = new DataView(data.buffer);
  values.forEach((v, i) => {
    if (bitpix === 8) view.setUint8(i, v); else if (bitpix === 16) view.setInt16(i * 2, v, false); else if (bitpix === 32) view.setInt32(i * 4, v, false);
    else if (bitpix === 64) view.setBigInt64(i * 8, BigInt(v), false); else if (bitpix === -32) view.setFloat32(i * 4, v, false); else view.setFloat64(i * 8, v, false);
  });
  return concat([bytesOf(header(cards)), pad(data)]);
}
const n = 40, linear = [card("CRVAL1", 4000), card("CDELT1", 10), card("CRPIX1", 1), card("CUNIT1", "Angstrom")];
const ramp = Array.from({length: n}, (_, i) => i + 1);

// --- generated fixtures -----------------------------------------------------------------
{ // primary 1-D float32 image, AWAV in Angstrom, NaN pixels dropped, header keywords shown
  const parsed = fits.parse(read("linear-1d.fits"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.format, "fits");
  assert.equal(parsed.layout, "image-1d");
  assert.equal(parsed.points.length, 398, "400 pixels minus two NaN");
  assert.equal(parsed.skipped, 2);
  assert.equal(parsed.unit, "Å");
  assert.equal(parsed.unitBasis, "header");
  assert.equal(first(parsed).wavelength, 3600);
  assert.equal(last(parsed).wavelength, 3600 + 14 * 399);
  assert.equal(parsed.medium, "air", "AWAV means air wavelengths");
  assert.equal(parsed.hasError, false);
  assert.deepEqual(parsed.header, {object: "SN 2026fit", dateObs: "2026-09-12T10:29:45", telescope: "P60", instrument: "SEDM", exptime: 2700, bunit: "erg/cm2/s/A"});
  assert.deepEqual(parsed.hdu, {index: 0, type: "PRIMARY", name: ""});
  const trough = parsed.points.reduce((low, p) => (p.wavelength > 5900 && p.wavelength < 6400 && p.flux < low.flux ? p : low), parsed.points[0]);
  close(trough.wavelength, 6150, 15, "the absorption trough sits where it was written");
}
{ // scaled int16 with BLANK, IRAF DC-FLAG = 1 log10 dispersion, units from WAT1
  const parsed = fits.parse(read("log-wave-int16.fits"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.logWavelength, true);
  assert.equal(parsed.points.length, 299, "the BLANK pixel is dropped");
  close(first(parsed).wavelength, Math.pow(10, 3.55), 1e-6, "10^CRVAL1");
  close(parsed.points[1].wavelength, Math.pow(10, 3.5501), 1e-6, "log10 steps");
  close(first(parsed).flux, 2e-16 * (1 + 0.3 * Math.sin(0)), 2e-19, "BZERO + BSCALE * value");
  assert.equal(parsed.unit, "Å");
  assert.ok(parsed.notes.some((note) => /log10/.test(note)));
}
{ // IRAF multispec 3-D: first aperture of the first band, sigma band as the error
  const parsed = fits.parse(read("multispec-3d.fits"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.layout, "image-3d");
  assert.equal(parsed.points.length, 320);
  assert.equal(first(parsed).wavelength, 4000);
  assert.equal(last(parsed).wavelength, 4000 + 3 * 319);
  assert.equal(parsed.hasError, true);
  close(first(parsed).error / first(parsed).flux, 0.05, 1e-6, "band 4 (sigma) is the error");
  assert.ok(parsed.notes.some((note) => /first row of the first plane/.test(note)), "the reader says it took the first spectrum");
  assert.ok(parsed.notes.some((note) => /multispec/.test(note)));
  close(parsed.points[5].flux, 2e-16 * Math.exp(-Math.pow((4015 - 5000) / 2500, 2)) * (1 - 0.45 * Math.exp(-Math.pow((4015 - 6150) / 80, 2))), 1e-20, "aperture 1, not aperture 2 (half flux)");
}
{ // SDSS-like table: one row per pixel, loglam → Å, 1/sqrt(ivar) errors, a zero-ivar pixel has none
  const parsed = fits.parse(read("bintable-rows-sdss.fits"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.layout, "bintable-rows");
  assert.deepEqual(parsed.hdu, {index: 2, type: "BINTABLE", name: "COADD"}, "the empty primary and IMAGE extension are walked past");
  assert.equal(parsed.points.length, 500);
  assert.equal(parsed.logWavelength, true);
  close(first(parsed).wavelength, Math.pow(10, 3.58), 1e-6, "10^loglam");
  assert.equal(parsed.medium, "vacuum", "VACUUM = T in the primary header");
  assert.deepEqual(parsed.columnNames, ["loglam", "flux", "ivar"]);
  close(parsed.points[8].error, 0.2, 1e-9, "1/sqrt(25)");
  assert.equal(parsed.points[7].error, null, "ivar = 0 gives no error");
  assert.equal(parsed.header.telescope, "SDSS 2.5-M");
}
{ // ESO-style one-row table of vectors with WAVE in nm, FLUX, ERR and an ignored QUAL column
  const parsed = fits.parse(read("bintable-vector-eso.fits"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.layout, "bintable-vector");
  assert.equal(parsed.points.length, 450);
  assert.equal(parsed.unit, "nm");
  assert.equal(parsed.unitBasis, "header");
  close(first(parsed).wavelength, 3700, 1e-9, "nm converted to Å");
  close(last(parsed).wavelength, 3700 + 12 * 449, 1e-9, "last pixel");
  close(first(parsed).error / first(parsed).flux, 0.04, 1e-6, "ERR column");
  assert.deepEqual(parsed.header, {object: "AT 2026eso", dateObs: "2026-10-01T03:12:00.000", telescope: "ESO-VLT-U1", instrument: "FORS2", exptime: 900, bunit: "erg.cm**(-2).s**(-1).angstrom**(-1)"},
    "keywords come from the primary header, filled in from the extension");
}
{ // rows of wavelength, flux, error with no WCS at all
  const parsed = fits.parse(read("pairs-2d.fits"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.points.length, 260);
  assert.equal(first(parsed).wavelength, 3800);
  assert.equal(parsed.unitBasis, "values");
  close(first(parsed).error / first(parsed).flux, 0.07, 1e-9, "third row is the error");
  assert.ok(parsed.notes.some((note) => /read as wavelength, flux, error/.test(note)));
}
// rejected files say what is wrong in plain words
{
  const frame = fits.parse(read("image-2d-noaxis.fits"));
  assert.equal(frame.ok, false);
  assert.equal(frame.reason, "fits-no-wavelength");
  assert.match(spectrum.failureMessage(frame), /no wavelength keywords/);
  const cut = fits.parse(read("truncated.fits"));
  assert.equal(cut.reason, "fits-truncated");
  assert.match(spectrum.failureMessage(cut), /ends before its data array/);
  assert.equal(fits.parse(read("linear-1d.fits.gz")).reason, "gzip");
  assert.equal(fits.isGzip(read("linear-1d.fits.gz")), true);
  assert.equal(fits.isFits(read("linear-1d.fits")), true);
  assert.equal(fits.isFits(read("sedm-ztf.ascii")), false);
  assert.equal(fits.parse(read("sedm-ztf.ascii")).reason, "fits-not-fits");
  assert.equal(fits.parse(new Uint8Array(0)).reason, "empty");
}

// --- inline files: every BITPIX, header forms, dispersion forms ----------------------------
for (const bitpix of [8, 16, 32, 64, -32, -64]) {
  const parsed = fits.parse(image(bitpix, ramp, linear));
  assert.equal(parsed.ok, true, "BITPIX " + bitpix);
  assert.equal(parsed.points.length, n);
  assert.equal(parsed.points[3].flux, 4, "BITPIX " + bitpix + " value");
  assert.equal(parsed.points[3].wavelength, 4030);
}
{ // BZERO/BSCALE on an unsigned-style int32, BLANK dropped
  const parsed = fits.parse(image(32, ramp.map((v) => (v === 7 ? -99 : v)), linear.concat([card("BZERO", 100), card("BSCALE", 0.5), card("BLANK", -99)])));
  assert.equal(parsed.points.length, n - 1);
  assert.equal(parsed.points[0].flux, 100.5);
  assert.equal(parsed.skipped, 1);
}
{ // CD1_1 instead of CDELT1, CRPIX1 not 1, metres as the unit
  const parsed = fits.parse(image(-64, ramp, [card("CRVAL1", 5e-7), card("CD1_1", 1e-9), card("CRPIX1", 11), card("CUNIT1", "m")]));
  assert.equal(parsed.ok, true);
  close(parsed.points[10].wavelength, 5000, 1e-6, "pixel 11 is CRVAL1 (converted to Å)");
  close(parsed.points[0].wavelength, 4900, 1e-6, "ten pixels before it");
  assert.equal(parsed.unit, "Å");
}
{ // FITS standard WAVE-LOG axis: λ = λr exp(x / λr); and a 'LINEAR' axis holding log10 values
  const parsed = fits.parse(image(-32, ramp, [card("CTYPE1", "WAVE-LOG"), card("CRVAL1", 5000), card("CDELT1", 5), card("CRPIX1", 1), card("CUNIT1", "Angstrom")]));
  assert.equal(parsed.ok, true);
  close(parsed.points[1].wavelength, 5000 * Math.exp(5 / 5000), 1e-6, "WAVE-LOG");
  assert.equal(parsed.medium, "vacuum", "WAVE means vacuum");
  const log10 = fits.parse(image(-32, ramp, [card("CTYPE1", "LINEAR"), card("CRVAL1", 3.6), card("CDELT1", 0.001), card("CRPIX1", 1)]));
  assert.equal(log10.ok, true);
  close(log10.points[0].wavelength, Math.pow(10, 3.6), 1e-6, "small CRVAL1 with a tiny step is log10 wavelength");
  const coeff = fits.parse(image(-32, ramp, [card("COEFF0", 3.6), card("COEFF1", 0.001)]));
  close(coeff.points[0].wavelength, Math.pow(10, 3.6), 1e-6, "SDSS COEFF0/COEFF1");
}
{ // nm declared with CUNIT1 but values plainly Angstrom: the values win, as for ASCII files
  const parsed = fits.parse(image(-32, ramp, [card("CRVAL1", 4000), card("CDELT1", 10), card("CUNIT1", "nm")]));
  assert.equal(parsed.unit, "Å");
  assert.equal(parsed.unitBasis, "values");
}
{ // strings with embedded quotes, CONTINUE cards, comments after values, AIRORVAC
  const long = "'" + "a very long object name ".repeat(3).trim().slice(0, 60) + "&'";
  const parsed = fits.parse(image(-32, ramp, linear.concat([
    card("OBJECT", "O'Brien's SN"), ("TELESCOP= " + long).padEnd(80), ("CONTINUE  '" + " continued'").padEnd(80),
    card("EXPTIME", 120, "seconds"), card("AIRORVAC", "air")])));
  assert.equal(parsed.header.object, "O'Brien's SN");
  assert.ok(parsed.header.telescope.endsWith(" continued"), "CONTINUE joins the string: " + parsed.header.telescope);
  assert.equal(parsed.header.exptime, 120);
  assert.equal(parsed.medium, "air");
}
{ // a pixel axis (CRVAL1 = CDELT1 = CRPIX1 = 1) is not a wavelength axis
  const parsed = fits.parse(image(-32, ramp, [card("CRVAL1", 1), card("CDELT1", 1), card("CRPIX1", 1), card("CTYPE1", "PIXEL")]));
  assert.equal(parsed.reason, "fits-no-wavelength");
}
{ // too few pixels, and a header with no END card
  assert.equal(fits.parse(image(-32, [1, 2, 3], [card("CRVAL1", 4000), card("CDELT1", 10)])).reason, "too-few");
  assert.equal(fits.parse(bytesOf(card("SIMPLE", true) + card("BITPIX", -32))).reason, "fits-truncated");
  assert.equal(fits.parse(bytesOf(header([card("SIMPLE", false), card("BITPIX", 8), card("NAXIS", 0)]))).reason, "fits-unsupported");
  assert.equal(fits.parse(bytesOf(header([card("SIMPLE", true), card("BITPIX", 8), card("NAXIS", 0)]))).reason, "fits-unsupported", "no data anywhere");
}
{ // a table whose columns are not a spectrum, and one whose wavelength is a string column
  const table = (cols, rowBytes, rows) => concat([bytesOf(header([card("SIMPLE", true), card("BITPIX", 8), card("NAXIS", 0), card("EXTEND", true)])),
    bytesOf(header([card("XTENSION", "BINTABLE"), card("BITPIX", 8), card("NAXIS", 2), card("NAXIS1", rowBytes), card("NAXIS2", rows), card("PCOUNT", 0), card("GCOUNT", 1), card("TFIELDS", cols.length / 2)]
      .concat(cols.map((c, i) => card((i % 2 ? "TFORM" : "TTYPE") + (Math.floor(i / 2) + 1), c))))), pad(new Uint8Array(rowBytes * rows))]);
  const photometry = fits.parse(table(["TIME", "D", "SAP_FLUX", "E"], 12, 20));
  assert.equal(photometry.reason, "fits-no-wavelength");
  assert.match(spectrum.failureMessage(photometry), /no wavelength-like column/);
  assert.equal(fits.parse(table(["WAVE", "10A", "FLUX", "E"], 14, 20)).reason, "fits-unsupported");
  assert.equal(fits.parse(table(["RA", "D", "DEC", "D"], 16, 20)).reason, "fits-unsupported");
}
// every FITS problem has a plain-words message that points at the provider
for (const reason of ["fits-unsupported", "fits-no-wavelength", "fits-truncated", "fits-not-fits", "gzip"]) {
  const tns = spectrum.failureMessage({reason, message: "The FITS file could not be read."});
  assert.ok(tns.length > 30 && /TNS/.test(tns) && !/undefined/.test(tns), reason);
  assert.match(spectrum.failureMessage({reason, message: "x.", provider: "WISeREP"}), /WISeREP/);
}

// --- the fetch path: FITS and ASCII through one call, gzip inflated, WISeREP two-step ---------
(async () => {
  const live = require("../ctas/live-sources.js");
  let answer = {status: 200, body: read("linear-1d.fits"), type: "application/fits"};
  const calls = [];
  global.fetch = async (url) => {
    calls.push(String(url));
    if (String(url) === "live-config.json") return new Response(JSON.stringify({relay: "https://relay.example/"}), {status: 200});
    return new Response(answer.body, {status: answer.status, headers: {"Content-Type": answer.type}});
  };
  const tns = "https://www.wis-tns.org/system/files/uploaded/ZTF/tns_2026abc_P60_SEDM_ZTF.fits";
  let fetched = await live.fetchViaRelay(tns);
  assert.equal(fetched.binary, true);
  assert.equal(fetched.text, "", "FITS bytes are not decoded as text");
  assert.equal(fetched.bytes.byteLength, read("linear-1d.fits").length);
  let parsed = await live.fetchSpectrumFile(tns);
  assert.equal(parsed.format, "fits");
  assert.equal(parsed.points.length, 398);
  answer = {status: 200, body: read("linear-1d.fits.gz"), type: "application/gzip"};
  parsed = await live.fetchSpectrumFile(tns + ".gz");
  assert.equal(parsed.format, "fits");
  assert.equal(parsed.points.length, 398, "gzip is inflated before reading");
  answer = {status: 200, body: read("sprat-lt.txt"), type: "text/plain"};
  parsed = await live.fetchSpectrumFile(tns.replace(".fits", ".txt"));
  assert.equal(parsed.format, "ascii");
  assert.equal(parsed.points.length, 133, "ASCII still goes to the ASCII reader");
  answer = {status: 200, body: read("image-2d-noaxis.fits"), type: "application/fits"};
  await assert.rejects(live.fetchSpectrumFile(tns), (problem) => problem.reason === "fits-no-wavelength");
  answer = {status: 200, body: zlib.gzipSync(Buffer.from("<html><body>sign in</body></html>")), type: "application/gzip"};
  await assert.rejects(live.fetchSpectrumFile(tns + ".gz"), (problem) => problem.reason === "html", "an inflated web page is still recognised");
  answer = {status: 403, body: "forbidden", type: "text/plain"};
  await assert.rejects(live.fetchSpectrumFile(tns), (problem) => problem.kind === "http" && problem.status === 403);

  // WISeREP: the record's listing is read first, then the file it names
  const record = spectrum.wiserepRecord({provider: "wiserep", provider_spectrum_id: "92931", file_name: "SN2026abht_2026-09-12_10-29-45_P60_SEDM_TNS.ascii",
    public_download_url: "https://www.wiserep.org/search/spectra?name=2026abht&public=yes&format=json&files_type=ascii&num_page=25&page=0",
    source_url: "https://www.wiserep.org/form-edit/spectrum/92931"});
  assert.equal(record.provider, "WISeREP");
  const listing = JSON.stringify({data: [{"Spec. ID": "92931", "Obj. Name": "SN2026abht", "Ascii file": "SN2026abht_2026-09-12_10-29-45_P60_SEDM_TNS.ascii", "FITS file": "", "Groups": "TNS", "WL Medium": "Air", "Public": "Yes"}]});
  answer = {status: 200, body: listing, type: "application/json"};
  const found = spectrum.wiserepFile((await live.fetchViaRelay(record.listingUrl)).text, record);
  assert.equal(found.ok, true);
  assert.equal(found.url, "https://www.wiserep.org/system/files/uploaded/TNS/SN2026abht_2026-09-12_10-29-45_P60_SEDM_TNS.ascii");
  answer = {status: 200, body: read("sedm-ztf.ascii"), type: "text/plain"};
  parsed = await live.fetchSpectrumFile(found.url);
  assert.equal(parsed.points.length, 137);
  assert.ok(calls.every((url) => !/password|token|login/i.test(url)), "no credentials anywhere in the requests");
  console.log("Spectrum FITS reading (6 generated layouts, 6 BITPIX values, log/CD/COEFF dispersions, tables), 9 rejected cases, and the FITS/ASCII/gzip/WISeREP fetch path passed.");
})().catch((error) => { console.error(error); process.exit(1); });
