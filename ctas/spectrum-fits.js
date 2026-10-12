/* CTAS FITS spectrum reader: a small, dependency-free reader for the FITS files TNS
 * and WISeREP host, producing the same {points, unit, …} result as the ASCII reader so
 * the page plots both alike. Pure functions; unit-tested in
 * scripts/test_ctas_spectrum_fits.js with generated files in tests/fixtures/spectra/.
 *
 * Read: the 2880-byte header blocks (strings, logicals, numbers, CONTINUE cards); the
 * primary HDU and IMAGE / BINTABLE extensions; BITPIX 8, 16, 32, 64, -32, -64 with
 * BZERO / BSCALE and BLANK, big-endian as the standard requires.
 *
 * Layouts: (1) a 1-D image whose wavelengths come from the linear WCS keywords CRVAL1,
 * CDELT1 or CD1_1, CRPIX1, CUNIT1, with log10 dispersion when DC-FLAG = 1, CTYPE1 names
 * a log axis or SDSS COEFF0 / COEFF1 are given, and IRAF "multispec" spec1 dispersion
 * when it is linear or log; (2) 2-D or 3-D arrays (IRAF multispec, SDSS spSpec), where
 * the first row of the first plane is the flux (the result says so) and a band named
 * sigma/error in BANDIDn is the error; a 2–4 row array with no WCS is read as rows of
 * wavelength, flux, error; (3) a BINTABLE with wavelength-like and flux-like columns
 * (WAVE, LAMBDA, WAVELENGTH, LOGLAM, FLUX, FLUX_DENSITY, IVAR, ERR, …), either one row
 * per pixel or one row of vector cells. Air vs vacuum is reported as a label only.
 * Anything else is refused with a plain-words message (reason "fits-…").
 */
(function (root) {
  "use strict";

  var BLOCK = 2880, CARD = 80, MAX_HDUS = 24;
  var WAVE_NAME = /^(wave|wavelength|wavelen|lambda|lam|wl|awav|loglam|log_?lambda|wave_?(air|vac)|wavelength_?(air|vac)|lambda_?(air|vac)|opt_wave|obs_wave|spectral_axis)$/i;
  var FLUX_NAME = /^(flux|flux_density|fluxdensity|flux_lambda|f_?lambda|flam|spec|spectrum|sci|science|counts?|intensity|data|fnu|flux_?cal(ibrated)?|flux_?corr)$/i;
  var ERROR_NAME = /^(err|error|flux_?err(or)?|fluxerror|e_?flux|sigma|sig|sigma_flux|uncertainty|unc|ivar|variance|var|noise|stddev|std|dflux)$/i;
  var SHOW = [["OBJECT", "object"], ["DATE-OBS", "dateObs"], ["TELESCOP", "telescope"], ["INSTRUME", "instrument"], ["EXPTIME", "exptime"], ["BUNIT", "bunit"]];

  function sharedLib() {
    if (root.CTASSpectrum) return root.CTASSpectrum;
    try { return typeof require === "function" ? require("./spectrum-ascii.js") : null; } catch (_) { return null; }
  }
  function toBytes(input) {
    if (input instanceof Uint8Array) return input;
    if (input && input.buffer instanceof ArrayBuffer) return new Uint8Array(input.buffer, input.byteOffset || 0, input.byteLength);
    if (input instanceof ArrayBuffer) return new Uint8Array(input);
    if (typeof input === "string") { var out = new Uint8Array(input.length); for (var i = 0; i < input.length; i += 1) out[i] = input.charCodeAt(i) & 255; return out; }
    return new Uint8Array(0);
  }
  function ascii(bytes, start, end) {
    var text = "";
    for (var i = start; i < end && i < bytes.length; i += 1) text += String.fromCharCode(bytes[i]);
    return text;
  }
  function isFits(input) { var bytes = toBytes(input); return bytes.length >= 9 && ascii(bytes, 0, 9) === "SIMPLE  ="; }
  function isGzip(input) { var bytes = toBytes(input); return bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b; }
  function fail(reason, message, extra) {
    var problem = {ok: false, reason: reason, message: message};
    if (extra) Object.keys(extra).forEach(function (key) { problem[key] = extra[key]; });
    return problem;
  }

  // ------------------------------------------------------------------ header cards
  function cardValue(text) {
    // text: the 70 characters after "KEYWORD = "
    var trimmed = text.replace(/\s+$/, "");
    if (/^\s*'/.test(trimmed)) {
      var start = trimmed.indexOf("'"), out = "", i = start + 1;
      for (; i < trimmed.length; i += 1) {
        if (trimmed[i] === "'") { if (trimmed[i + 1] === "'") { out += "'"; i += 1; continue; } break; }
        out += trimmed[i];
      }
      return {value: out.replace(/\s+$/, ""), string: true, continued: /&$/.test(out.replace(/\s+$/, ""))};
    }
    var bare = trimmed.split("/")[0].trim();
    if (bare === "T") return {value: true};
    if (bare === "F") return {value: false};
    var number = Number(bare.replace(/(\d|\.)[dD]([+-]?\d)/, "$1e$2"));
    if (bare !== "" && isFinite(number)) return {value: number};
    return {value: bare};
  }
  function parseHeader(bytes, offset) {
    var cards = {}, order = [], position = offset, lastKey = null;
    while (position + CARD <= bytes.length) {
      var card = ascii(bytes, position, position + CARD), key = card.slice(0, 8).trim();
      position += CARD;
      if (key === "END") {
        var end = offset + Math.ceil((position - offset) / BLOCK) * BLOCK;
        return end <= bytes.length ? {cards: cards, order: order, end: end} : null;
      }
      if (key === "CONTINUE" && lastKey && typeof cards[lastKey] === "string") {
        var more = cardValue(card.slice(10));
        if (more.string) cards[lastKey] = cards[lastKey].replace(/&$/, "") + more.value;
        continue;
      }
      if (!key || key === "COMMENT" || key === "HISTORY" || card.slice(8, 10) !== "= ") { lastKey = null; continue; }
      var parsed = cardValue(card.slice(10));
      if (!(key in cards)) { cards[key] = parsed.value; order.push(key); }
      lastKey = parsed.string ? key : null;
    }
    return null;
  }
  function number(cards, key, fallback) {
    var value = cards[key];
    return typeof value === "number" && isFinite(value) ? value : fallback;
  }
  function text(cards, key) { var value = cards[key]; return value === undefined || value === null ? "" : String(value).trim(); }
  function axes(cards) {
    var count = number(cards, "NAXIS", 0), out = [];
    for (var i = 1; i <= count; i += 1) out.push(number(cards, "NAXIS" + i, 0));
    return out;
  }
  function dataBytes(cards) {
    var size = axes(cards), bitpix = Math.abs(number(cards, "BITPIX", 0)) / 8;
    if (!size.length || !bitpix) return 0;
    var cells = size.reduce(function (product, length) { return product * length; }, 1);
    return bitpix * number(cards, "GCOUNT", 1) * (number(cards, "PCOUNT", 0) + cells);
  }
  function readHdus(bytes) {
    var hdus = [], offset = 0;
    while (offset + BLOCK <= bytes.length && hdus.length < MAX_HDUS) {
      var header = parseHeader(bytes, offset);
      if (!header) return {hdus: hdus, truncated: true};
      var size = dataBytes(header.cards);
      hdus.push({index: hdus.length, cards: header.cards, dataStart: header.end, dataBytes: size,
        type: hdus.length ? text(header.cards, "XTENSION").toUpperCase() : "PRIMARY", name: text(header.cards, "EXTNAME")});
      offset = header.end + Math.ceil(size / BLOCK) * BLOCK;
    }
    return {hdus: hdus, truncated: false};
  }

  // ------------------------------------------------------------------ numbers from bytes
  function reader(bitpix) {
    switch (bitpix) {
      case 8: return {size: 1, get: function (view, at) { return view.getUint8(at); }};
      case 16: return {size: 2, get: function (view, at) { return view.getInt16(at, false); }};
      case 32: return {size: 4, get: function (view, at) { return view.getInt32(at, false); }};
      case 64: return {size: 8, get: function (view, at) { return Number(view.getBigInt64(at, false)); }};
      case -32: return {size: 4, get: function (view, at) { return view.getFloat32(at, false); }};
      case -64: return {size: 8, get: function (view, at) { return view.getFloat64(at, false); }};
      default: return null;
    }
  }
  // `count` values starting `element` elements into the HDU's data, scaled by BZERO/BSCALE.
  function readArray(bytes, hdu, element, count) {
    var cards = hdu.cards, kind = reader(number(cards, "BITPIX", 0));
    if (!kind) return null;
    var start = hdu.dataStart + element * kind.size;
    if (start + count * kind.size > bytes.length) return null;
    var view = new DataView(bytes.buffer, bytes.byteOffset + start, count * kind.size);
    var zero = number(cards, "BZERO", 0), scale = number(cards, "BSCALE", 1), blank = cards.BLANK, hasBlank = typeof blank === "number" && number(cards, "BITPIX", 0) > 0;
    var out = new Array(count);
    for (var i = 0; i < count; i += 1) {
      var raw = kind.get(view, i * kind.size);
      out[i] = hasBlank && raw === blank ? NaN : zero + scale * raw;
    }
    return out;
  }

  // ------------------------------------------------------------------ dispersion (wavelength axis)
  function unitWord(value) {
    var lower = String(value || "").trim().toLowerCase();
    if (!lower) return "";
    if (/^(a|aa|ang|angstrom|angstroms|0\.1 ?nm|1e-10 ?m|10\*\*-10 ?m)$/.test(lower) || /angstr/.test(lower)) return "Å";
    if (/^(nm|nanomet(er|re)s?)$/.test(lower)) return "nm";
    if (/^(um|micron|microns|micromet(er|re)s?|mum)$/.test(lower) || lower === "µm" || lower === "μm") return "µm";
    if (/^(m|meter|meters|metre|metres)$/.test(lower)) return "m";
    return "";
  }
  function watText(cards, prefix) {
    var joined = "";
    for (var i = 1; i < 1000; i += 1) {
      var key = prefix + "_" + ("00" + i).slice(-3);
      if (!(key in cards)) break;
      var piece = String(cards[key]);
      while (piece.length < 68) piece += " ";
      joined += piece;
    }
    return joined.replace(/\s+/g, " ").trim();
  }
  function dispersion(cards) {
    var ctype = text(cards, "CTYPE1").toUpperCase(), cunit = unitWord(text(cards, "CUNIT1"));
    var wat1 = watText(cards, "WAT1"), wat2 = watText(cards, "WAT2");
    var watUnit = (/units?=([a-z]+)/i.exec(wat1) || [])[1];
    if (!cunit && watUnit) cunit = unitWord(watUnit);
    var crval = number(cards, "CRVAL1", NaN), cdelt = number(cards, "CDELT1", number(cards, "CD1_1", NaN)), crpix = number(cards, "CRPIX1", 1);
    var dcflag = number(cards, "DC-FLAG", NaN), kind = "", source = "";
    if (/^MULTISPE/.test(ctype) || /multispec/i.test(watText(cards, "WAT0"))) {
      // spec1 = "ap beam dtype w1 dw nw z aplow aphigh": dtype 0 linear, 1 log10, 2 non-linear
      var spec1 = /spec1\s*=\s*"([^"]+)"/i.exec(wat2);
      var fields = spec1 ? spec1[1].trim().split(/\s+/).map(Number) : [];
      if (fields.length >= 6 && (fields[2] === 0 || fields[2] === 1) && isFinite(fields[3]) && fields[4]) {
        crval = fields[3]; cdelt = fields[4]; crpix = 1; kind = fields[2] === 1 ? "log10" : "linear"; source = "IRAF multispec spec1";
        if (isFinite(fields[6]) && fields[6] > 0) source += " (its z = " + fields[6] + " is not applied)";
      } else return {error: "the IRAF multispec dispersion is non-linear (dtype 2) or incomplete, which is not supported"};
    } else if (isFinite(crval) && isFinite(cdelt) && cdelt !== 0) {
      if (crval === 1 && cdelt === 1 && crpix === 1 && !cunit) return null; // a pixel axis, not wavelengths
      source = "CRVAL1/CDELT1";
      kind = dcflag === 1 ? "log10" : /-LOG$/.test(ctype) ? (crval > 10 ? "ln" : "log10") : /LOG/.test(ctype) ? "log10" : "linear";
      if (kind === "linear" && crval > 2 && crval < 6 && Math.abs(cdelt) < 0.01 && !cunit) kind = "log10";
    } else if (isFinite(number(cards, "COEFF0", NaN)) && isFinite(number(cards, "COEFF1", NaN))) {
      crval = number(cards, "COEFF0", 0); cdelt = number(cards, "COEFF1", 0); crpix = 1; kind = "log10"; source = "COEFF0/COEFF1";
    }
    if (!kind) return null;
    var medium = /^AWAV/.test(ctype) ? "air" : /^WAVE/.test(ctype) ? "vacuum" : "";
    return {kind: kind, crval: crval, cdelt: cdelt, crpix: crpix, unit: cunit, medium: medium, source: source,
      wavelength: function (index) {
        var x = (index + 1 - crpix) * cdelt;
        return kind === "log10" ? Math.pow(10, crval + x) : kind === "ln" ? crval * Math.exp(x / crval) : crval + x;
      }};
  }
  function headerMedium(cards) {
    var vac = cards.VACUUM, airorvac = text(cards, "AIRORVAC").toLowerCase(), wavmed = text(cards, "WAVMED").toLowerCase();
    if (vac === true || airorvac === "vac" || airorvac === "vacuum" || /vac/.test(wavmed)) return "vacuum";
    if (vac === false || airorvac === "air" || /air/.test(wavmed)) return "air";
    return "";
  }

  // ------------------------------------------------------------------ images
  function monotonic(values) {
    var up = 0, down = 0;
    for (var i = 1; i < values.length; i += 1) {
      if (!isFinite(values[i]) || values[i] <= 0) return false;
      if (values[i] > values[i - 1]) up += 1; else if (values[i] < values[i - 1]) down += 1;
    }
    return (up === 0 || down === 0) && values.length > 1;
  }
  function readImage(bytes, hdu) {
    var cards = hdu.cards, size = axes(cards), n1 = size[0] || 0, n2 = size[1] || 1, n3 = size[2] || 1;
    if (size.length < 1 || n1 < 2) return null;
    if (size.slice(3).some(function (length) { return length !== 1; })) return fail("fits-unsupported", "The FITS image has " + size.length + " axes, which is not a spectrum layout this page can read.");
    var disp = dispersion(cards), notes = [], raw = [], error = null, wavelengths = null, i;
    if (disp && disp.error) return fail("fits-unsupported", disp.error + ".");
    var flux = readArray(bytes, hdu, 0, n1);
    if (!flux) return fail("fits-truncated", "The FITS file ends before its data array does, so the spectrum cannot be read.");
    if (!disp) {
      // No WCS: rows (or columns) of wavelength, flux, error written straight from arrays.
      if (size.length === 2 && n2 >= 2 && n2 <= 4 && monotonic(flux)) {
        wavelengths = flux; flux = readArray(bytes, hdu, n1, n1);
        if (n2 >= 3) error = readArray(bytes, hdu, 2 * n1, n1);
        notes.push("no wavelength keywords, so the " + n2 + " rows were read as wavelength, flux" + (n2 >= 3 ? ", error" : ""));
      } else if (size.length === 2 && n1 >= 2 && n1 <= 4 && n2 >= 10) {
        var all = readArray(bytes, hdu, 0, n1 * n2);
        if (!all) return fail("fits-truncated", "The FITS file ends before its data array does, so the spectrum cannot be read.");
        wavelengths = []; flux = []; error = n1 >= 3 ? [] : null;
        for (i = 0; i < n2; i += 1) { wavelengths.push(all[i * n1]); flux.push(all[i * n1 + 1]); if (error) error.push(all[i * n1 + 2]); }
        if (!monotonic(wavelengths)) return fail("fits-no-wavelength", "The FITS image has no wavelength keywords (CRVAL1/CDELT1) and its first column is not a wavelength axis.");
        notes.push("no wavelength keywords, so the " + n1 + " columns were read as wavelength, flux" + (n1 >= 3 ? ", error" : ""));
      } else {
        return fail("fits-no-wavelength", "The FITS image has no wavelength keywords (CRVAL1 and CDELT1 or CD1_1), so its pixels cannot be placed on a wavelength axis.");
      }
    } else {
      wavelengths = []; for (i = 0; i < n1; i += 1) wavelengths.push(disp.wavelength(i));
      if (n2 > 1 || n3 > 1) {
        notes.push("the file holds " + (n3 > 1 ? n3 + " planes of " : "") + n2 + " spectra (rows); the first row of the first plane is plotted");
        for (var band = 1; band <= n3; band += 1) {
          if (/sigma|err|unc|noise/i.test(text(cards, "BANDID" + band)) && band > 1) {
            error = readArray(bytes, hdu, (band - 1) * n1 * n2, n1);
            notes.push("errors from band " + band + " (" + text(cards, "BANDID" + band).replace(/\s+/g, " ").trim() + ")");
            break;
          }
        }
      }
      if (disp.source !== "CRVAL1/CDELT1") notes.push("wavelengths from " + disp.source);
      if (disp.kind !== "linear") notes.push("the dispersion axis is " + (disp.kind === "ln" ? "logarithmic (FITS -LOG)" : "log10 wavelength"));
    }
    var skipped = 0;
    for (i = 0; i < flux.length; i += 1) {
      var w = wavelengths[i], f = flux[i], e = error ? error[i] : NaN;
      if (!isFinite(w) || !isFinite(f) || w <= 0) { skipped += 1; continue; }
      raw.push({wavelength: w, flux: f, error: isFinite(e) && e >= 0 ? e : null});
    }
    var unit = disp ? disp.unit : "", factor = 1;
    if (unit === "m") { factor = 1e10; unit = "Å"; notes.push("wavelengths converted from metres"); }
    if (factor !== 1) raw.forEach(function (point) { point.wavelength *= factor; });
    return {raw: raw, unit: unit, skipped: skipped, notes: notes, layout: size.length === 1 ? "image-1d" : "image-" + size.length + "d",
      logAxis: Boolean(disp && disp.kind !== "linear"), medium: (disp && disp.medium) || headerMedium(cards), columnNames: null};
  }

  // ------------------------------------------------------------------ binary tables
  var CELL = {L: 1, X: 0, B: 1, I: 2, J: 4, K: 8, A: 1, E: 4, D: 8, C: 8, M: 16, P: 8, Q: 16};
  function tableColumns(cards) {
    var count = number(cards, "TFIELDS", 0), columns = [], offset = 0;
    for (var i = 1; i <= count; i += 1) {
      var form = /^(\d*)([LXBIJKAEDCMPQ])/.exec(text(cards, "TFORM" + i).toUpperCase()) || [null, "", ""];
      var repeat = form[1] === "" ? 1 : Number(form[1]), code = form[2], width = CELL[code] || 0;
      columns.push({index: i, name: text(cards, "TTYPE" + i), unit: text(cards, "TUNIT" + i), code: code, repeat: repeat, width: width, offset: offset,
        scale: number(cards, "TSCAL" + i, 1), zero: number(cards, "TZERO" + i, 0), nullValue: cards["TNULL" + i]});
      offset += code === "X" ? Math.ceil(repeat / 8) : repeat * width;
    }
    return columns;
  }
  function cellReader(column) {
    var bitpix = {B: 8, I: 16, J: 32, K: 64, E: -32, D: -64}[column.code];
    var kind = bitpix ? reader(bitpix) : null;
    if (!kind) return null;
    return function (view, rowStart, k) {
      var raw = kind.get(view, rowStart + column.offset + k * kind.size);
      return typeof column.nullValue === "number" && bitpix > 0 && raw === column.nullValue ? NaN : column.zero + column.scale * raw;
    };
  }
  function findColumn(columns, pattern, taken) {
    for (var i = 0; i < columns.length; i += 1) if (taken.indexOf(columns[i]) === -1 && pattern.test(columns[i].name)) return columns[i];
    return null;
  }
  function readTable(bytes, hdu) {
    var cards = hdu.cards, rowBytes = number(cards, "NAXIS1", 0), rows = number(cards, "NAXIS2", 0), columns = tableColumns(cards);
    if (!rowBytes || !rows || !columns.length) return fail("fits-unsupported", "The FITS table has no rows.");
    var wave = findColumn(columns, WAVE_NAME, []) || findColumn(columns, /wave|lambda|^wl$|^lam/i, []);
    var flux = findColumn(columns, FLUX_NAME, [wave]) || findColumn(columns, /flux|spec|count|intens|flam/i, [wave]);
    var error = findColumn(columns, ERROR_NAME, [wave, flux]) || findColumn(columns, /err|sig|unc|ivar|var|noise/i, [wave, flux]);
    if (!flux) return fail("fits-unsupported", "The FITS table has no flux-like column (its columns are " + columns.map(function (c) { return c.name || "?"; }).join(", ") + ").");
    var disp = wave ? null : dispersion(cards);
    if (!wave && !disp) return fail("fits-no-wavelength", "The FITS table has a flux column but no wavelength-like column and no wavelength keywords (its columns are " + columns.map(function (c) { return c.name || "?"; }).join(", ") + ").");
    if (disp && disp.error) return fail("fits-unsupported", disp.error + ".");
    var readers = {wave: wave && cellReader(wave), flux: cellReader(flux), error: error && cellReader(error)};
    if ((wave && !readers.wave) || !readers.flux) return fail("fits-unsupported", "The FITS table stores the spectrum in a column type this page cannot read (" + (wave && !readers.wave ? wave : flux).code + ").");
    if (hdu.dataStart + rowBytes * rows > bytes.length) return fail("fits-truncated", "The FITS file ends before its table does, so the spectrum cannot be read.");
    var view = new DataView(bytes.buffer, bytes.byteOffset + hdu.dataStart, rowBytes * rows);
    var vector = flux.repeat > 1, count = vector ? Math.min(flux.repeat, wave ? wave.repeat : Infinity) : rows, notes = [], raw = [], skipped = 0;
    if (vector && rows > 1) notes.push("the table has " + rows + " rows of spectra; the first row is plotted");
    if (!vector && (wave && wave.repeat !== 1)) return fail("fits-unsupported", "The FITS table mixes vector and scalar columns in a way this page cannot read.");
    var errorKind = error ? (/^ivar/i.test(error.name) ? "ivar" : /^var/i.test(error.name) ? "variance" : "sigma") : "";
    for (var i = 0; i < count; i += 1) {
      var rowStart = vector ? 0 : i * rowBytes, k = vector ? i : 0;
      var w = wave ? readers.wave(view, rowStart, k) : disp.wavelength(i), f = readers.flux(view, rowStart, k);
      var e = readers.error && (vector ? i < error.repeat : true) ? readers.error(view, rowStart, k) : NaN;
      if (errorKind === "ivar") e = e > 0 ? 1 / Math.sqrt(e) : NaN;
      else if (errorKind === "variance") e = e >= 0 ? Math.sqrt(e) : NaN;
      if (!isFinite(w) || !isFinite(f) || w <= 0) { skipped += 1; continue; }
      raw.push({wavelength: w, flux: f, error: isFinite(e) && e >= 0 ? e : null});
    }
    var logWavelength = Boolean(wave && /^log/i.test(wave.name));
    var unit = wave ? unitWord(wave.unit.replace(/^log\d*\s*\(?|\)?$/gi, "")) : disp.unit, medium = "";
    if (wave && /vac/i.test(wave.name)) medium = "vacuum"; else if (wave && /air/i.test(wave.name)) medium = "air";
    if (!medium) medium = (disp && disp.medium) || headerMedium(cards);
    if (unit === "m") { raw.forEach(function (point) { point.wavelength *= 1e10; }); unit = "Å"; notes.push("wavelengths converted from metres"); }
    if (logWavelength) notes.push("wavelengths are log10 in column " + wave.name);
    if (errorKind === "ivar") notes.push("errors are 1/√" + error.name);
    if (!wave) notes.push("wavelengths from the table header's " + disp.source);
    return {raw: raw, unit: unit, logWavelength: logWavelength, logAxis: Boolean(disp && disp.kind !== "linear"), skipped: skipped, notes: notes, layout: vector ? "bintable-vector" : "bintable-rows",
      medium: medium, columnNames: [wave ? wave.name : "(" + disp.source + ")", flux.name].concat(error ? [error.name] : [])};
  }

  // ------------------------------------------------------------------ putting it together
  function keywords(hdus, chosen) {
    var out = {};
    SHOW.forEach(function (pair) {
      var primary = hdus[0].cards[pair[0]], own = chosen.cards[pair[0]];
      var value = own !== undefined && own !== "" ? own : primary;
      if (value !== undefined && value !== null && String(value).trim() !== "") out[pair[1]] = typeof value === "number" ? value : String(value).trim();
    });
    return out;
  }
  function parse(input) {
    var lib = sharedLib();
    var bytes = toBytes(input);
    if (!bytes.length) return fail("empty", "The file arrived empty, so there is nothing to plot.");
    if (isGzip(bytes)) return fail("gzip", "The file is gzip-compressed.");
    if (!isFits(bytes)) return fail("fits-not-fits", "The file does not start with a FITS header (SIMPLE = T), so it is not a FITS file.");
    var found = readHdus(bytes);
    if (!found.hdus.length) return fail("fits-truncated", "The FITS header is incomplete (no END card), so the file cannot be read.");
    if (found.hdus[0].cards.SIMPLE !== true) return fail("fits-unsupported", "The file says it does not conform to the FITS standard (SIMPLE is not T).");
    if (found.hdus[0].cards.GROUPS === true) return fail("fits-unsupported", "The FITS file uses random groups, which is not a spectrum layout this page can read.");
    var attempts = [], result = null, chosen = null;
    for (var i = 0; i < found.hdus.length && !result; i += 1) {
      var hdu = found.hdus[i], read = null;
      if (hdu.type === "PRIMARY" || hdu.type === "IMAGE") read = readImage(bytes, hdu);
      else if (hdu.type === "BINTABLE") read = readTable(bytes, hdu);
      else if (hdu.type === "TABLE") read = fail("fits-unsupported", "ASCII-table (TABLE) extensions are not supported.");
      if (!read) continue;
      if (read.ok === false) { attempts.push(read); continue; }
      result = read; chosen = hdu;
    }
    if (!result) {
      if (found.truncated) return fail("fits-truncated", "The FITS file is cut short, so no complete spectrum could be read from it.");
      if (attempts.length) return attempts[0];
      return fail("fits-unsupported", "The FITS file holds no image or table extension with spectrum data.");
    }
    if (!lib) return fail("fits-unsupported", "The spectrum helpers are not loaded.");
    var assembled = lib.assemble(result.raw, {unit: result.unit, logWavelength: result.logWavelength === true});
    if (!assembled.ok) {
      if (assembled.reason === "too-few") return fail("too-few", "", {count: assembled.count});
      if (assembled.reason === "implausible") return fail("implausible", "", {unit: assembled.unit});
      return assembled;
    }
    assembled.format = "fits"; assembled.skipped = result.skipped; assembled.layout = result.layout; assembled.notes = result.notes;
    assembled.logWavelength = assembled.logWavelength || result.logAxis;
    assembled.hdu = {index: chosen.index, type: chosen.type, name: chosen.name};
    assembled.header = keywords(found.hdus, chosen); assembled.medium = result.medium || headerMedium(found.hdus[0].cards);
    assembled.columns = result.columnNames ? result.columnNames.length : 1; assembled.columnNames = result.columnNames;
    assembled.hdus = found.hdus.length; assembled.truncated = found.truncated;
    return assembled;
  }

  var api = {parse: parse, isFits: isFits, isGzip: isGzip, readHdus: readHdus, parseHeader: parseHeader, dispersion: dispersion};
  root.CTASSpectrumFits = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
