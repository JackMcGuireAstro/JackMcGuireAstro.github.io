/* CTAS spectrum helpers: read a public ASCII spectrum file (as TNS and WISeREP host
 * them) into wavelength / flux / error points, and the small amount of arithmetic a
 * plot needs (normalisation, redshift, line markers, axis ticks). Pure functions only:
 * nothing here fetches, stores or touches the page, so every rule is unit-tested in
 * scripts/test_ctas_spectrum_ascii.js.
 *
 * Files arrive in many dialects: '#' (or '!', ';', '%', '//') comment headers, a bare
 * column-name line, whitespace / comma / semicolon / tab separators, two or three
 * columns (wavelength, flux, optional error or variance), Fortran 'D' exponents, NaN
 * and Inf placeholders, descending order, Windows line endings. Wavelengths are
 * returned in Angstrom; nm and micron files are recognised from the header or, failing
 * that, from the range of the values. FITS files are read by ctas/spectrum-fits.js,
 * which hands its rows to assemble() here so both formats get the same treatment.
 * Also here: which TNS and WISeREP records can be fetched (tnsFile, wiserepRecord) and
 * how a WISeREP file address is read from the site's public listing (wiserepFile).
 */
(function (root) {
  "use strict";

  var MIN_POINTS = 10;
  var TNS_FILE = /^https:\/\/www\.wis-tns\.org\/system\/files\/uploaded\/[A-Za-z0-9_\-./%+]+$/;
  // Rest wavelengths in Angstrom (air), as conventionally quoted for transient spectra.
  var LINES = [
    {id: "ca-hk", label: "Ca II H&K", rest: [3934, 3969]},
    {id: "h-gamma", label: "Hγ", rest: [4340]},
    {id: "h-beta", label: "Hβ", rest: [4861]},
    {id: "fe-ii", label: "Fe II", rest: [5169]},
    {id: "s-ii", label: "S II ‘W’", rest: [5454, 5640]},
    {id: "he-i-5876", label: "He I", rest: [5876]},
    {id: "na-i-d", label: "Na I D", rest: [5890, 5896]},
    {id: "si-ii", label: "Si II", rest: [6355]},
    {id: "h-alpha", label: "Hα", rest: [6563]},
    {id: "he-i-6678", label: "He I", rest: [6678]},
    {id: "o-i", label: "O I", rest: [7774]},
    {id: "ca-nir", label: "Ca II NIR", rest: [8498, 8542, 8662]},
  ];
  var UNIT_FACTOR = {"Å": 1, "nm": 10, "µm": 10000};

  // ------------------------------------------------------------------ files
  function fileFormat(name) {
    return /\.(fits?|fts|fz)(\.gz)?$/i.test(name) ? "fits"
      : /\.(gz|zip|tar|tgz|bz2|xz|7z|rar)$/i.test(name) ? "archive"
      : /\.(jpe?g|png|gif|pdf|ps|eps|docx?)$/i.test(name) ? "document" : "ascii";
  }
  function lastSegment(url) {
    var last = String(url || "").split("?")[0].split("/").pop() || "";
    try { return decodeURIComponent(last); } catch (_) { return last; }
  }
  function tnsFile(url) {
    var value = String(url || "");
    if (!TNS_FILE.test(value)) return null;
    var name = lastSegment(value);
    return {url: value, fileName: name, format: fileFormat(name), provider: "TNS"};
  }

  // WISeREP records in the CTAS data carry the object's public-spectra listing
  // (public_download_url: /search/spectra?name=…&public=yes&format=json…), the record
  // page (source_url: /form-edit/spectrum/<id>), the spectrum id and the file name. The
  // listing is fetched first and the file's own address is read from it (wiserepFile).
  var WISEREP_LISTING = /^https:\/\/www\.wiserep\.org\/search\/spectra\?(?=.*(?:^|&)public=yes(?:&|$))(?=.*(?:^|&)format=(?:json|tsv|csv)(?:&|$))[A-Za-z0-9_\-=&.%+]*$/;
  var WISEREP_FILE = /^https:\/\/www\.wiserep\.org\/system\/files\/uploaded\/[A-Za-z0-9_\-./%+]+$/;
  function wiserepRecord(row) {
    row = row || {};
    var listing = String(row.public_download_url || "");
    if (!WISEREP_LISTING.test(listing)) return null;
    var name = String(row.file_name || "").trim();
    var id = String(row.provider_spectrum_id || "").trim();
    var record = /^https:\/\/www\.wiserep\.org\/form-edit\/spectrum\/\d+\/?$/.test(String(row.source_url || "")) ? String(row.source_url) : "";
    return {provider: "WISeREP", listingUrl: listing, recordUrl: record, spectrumId: id, fileName: name, format: name ? fileFormat(name) : "ascii"};
  }
  // The file address for one record, read from the listing WISeREP answered with. The
  // listing is JSON (an object or array of rows) or TSV/CSV; its rows name the file in
  // an "Ascii file" / "FITS file" field and the owning group in a "Group(s)" field. An
  // absolute https://www.wiserep.org/… address on the row is used as given; a bare file
  // name is joined to WISeREP's uploads path with the row's group, the same layout TNS
  // uses for its uploads. Returns {url, fileName, group, medium, fluxUnit, public} or a
  // problem {reason: "proprietary" | "not-listed" | "html" | "no-file" | "listing-unreadable"}.
  function wiserepFile(listingText, record) {
    var body = String(listingText === null || listingText === undefined ? "" : listingText).replace(/^﻿/, "").trim();
    record = record || {};
    if (!body) return {ok: false, reason: "not-listed"};
    if (/^\s*(<!doctype\s+html|<html|<head|<body)/i.test(body.slice(0, 500)) || /<html[\s>]/i.test(body.slice(0, 3000))) return {ok: false, reason: /log\s*-?in|sign\s*-?in|password/i.test(body) ? "proprietary" : "html"};
    var rows = listingRows(body);
    if (!rows) return {ok: false, reason: "listing-unreadable"};
    var wanted = rows.filter(function (row) { return rowMatchesRecord(row, record); });
    if (!wanted.length && rows.length === 1 && !record.spectrumId && !record.fileName) wanted = rows;
    if (!wanted.length) return {ok: false, reason: "not-listed"};
    var row = wanted[0], fileName = "", group = "", url = "", medium = "", fluxUnit = "", isPublic = null;
    Object.keys(row).forEach(function (key) {
      var value = row[key] === null || row[key] === undefined ? "" : String(row[key]).trim(), label = key.toLowerCase();
      if (!value) return;
      if (WISEREP_FILE.test(value) && (!record.fileName || lastSegment(value) === record.fileName || /file/.test(label))) { if (!url || lastSegment(value) === record.fileName) url = value; }
      else if (/file/.test(label) && !/related|size|type/.test(label) && !/^https?:/i.test(value)) {
        if (!fileName || value === record.fileName || (!record.fileName && /ascii/.test(label))) fileName = value;
      }
      if (/^groups?$/.test(label) && !group) group = value.split(/[,;]/)[0].trim();
      if (/medium|air|vac/.test(label)) medium = /vac/i.test(value) ? "vacuum" : /air/i.test(value) ? "air" : "";
      if (/flux\s*unit/.test(label)) fluxUnit = value;
      if (/^public$/.test(label)) isPublic = /^(y|yes|1|true|public)$/i.test(value) ? true : /^(n|no|0|false|private|proprietary)$/i.test(value) ? false : null;
    });
    if (isPublic === false) return {ok: false, reason: "proprietary"};
    if (!url && fileName && group && /^[A-Za-z0-9_\-. +]+$/.test(group) && /^[^/\\]+$/.test(fileName)) {
      url = "https://www.wiserep.org/system/files/uploaded/" + encodeURIComponent(group) + "/" + encodeURIComponent(fileName);
    }
    if (!url || !WISEREP_FILE.test(url)) return {ok: false, reason: "no-file", fileName: fileName, group: group};
    return {ok: true, url: url, fileName: fileName || lastSegment(url), group: group, medium: medium, fluxUnit: fluxUnit, public: isPublic, format: fileFormat(fileName || lastSegment(url))};
  }
  function listingRows(body) {
    var rows = null;
    if (/^[[{]/.test(body)) {
      try { rows = JSON.parse(body); } catch (_) { return null; }
      if (rows && !Array.isArray(rows) && typeof rows === "object") {
        if (Array.isArray(rows.data)) rows = rows.data;
        else if (rows.data && typeof rows.data === "object") rows = Object.keys(rows.data).map(function (key) { return rows.data[key]; });
        else rows = Object.keys(rows).map(function (key) { return rows[key]; });
      }
      return Array.isArray(rows) ? rows.filter(function (row) { return row && typeof row === "object"; }) : null;
    }
    var lines = body.split(/\r\n|\r|\n/).filter(function (line) { return line.trim(); });
    if (lines.length < 2) return [];
    var separator = lines[0].indexOf("\t") !== -1 ? "\t" : ",";
    var cells = function (line) { return line.split(separator).map(function (cell) { return cell.trim().replace(/^"|"$/g, ""); }); };
    var names = cells(lines[0]);
    return lines.slice(1).map(function (line) {
      var values = cells(line), row = {};
      names.forEach(function (name, index) { row[name] = values[index] === undefined ? "" : values[index]; });
      return row;
    });
  }
  function rowMatchesRecord(row, record) {
    var byId = false, byName = false;
    Object.keys(row).forEach(function (key) {
      var value = row[key] === null || row[key] === undefined ? "" : String(row[key]).trim(), label = key.toLowerCase();
      if (record.spectrumId && /spec.*id|^id$|spectrum/.test(label) && value === record.spectrumId) byId = true;
      if (record.fileName && /file/.test(label) && (value === record.fileName || lastSegment(value) === record.fileName)) byName = true;
    });
    return byId || byName;
  }

  // ------------------------------------------------------------------ parsing
  var COMMENT = /^(#|!|;|%|\/\/|\\)/;
  var NONFINITE = /^[+-]?(nan|inf|infinity|null|none|-+)$/i;
  function token(value) {
    var cleaned = String(value).replace(/^["']|["']$/g, "").replace(/(\d|\.)[dD]([+-]?\d)/, "$1e$2");
    if (cleaned === "" || NONFINITE.test(cleaned)) return {numeric: true, value: NaN};
    var number = Number(cleaned);
    return isFinite(number) || /^[+-]?\d*\.?\d+(e[+-]?\d+)?$/i.test(cleaned) ? {numeric: true, value: number} : {numeric: false, value: NaN};
  }
  function splitLine(line) {
    return line.split(/[\s,;|]+/).filter(function (part) { return part !== ""; });
  }
  function findColumn(names, pattern, skip) {
    for (var i = 0; i < names.length; i += 1) if (skip.indexOf(i) === -1 && pattern.test(names[i])) return i;
    return -1;
  }
  function columnMap(names, width) {
    // Unnamed columns: a third column is taken as the error only in a three-column file
    // (four or more unnamed columns often hold sky or a second flux) and only if it looks
    // like one (see errorsLookReal).
    var map = {wavelength: 0, flux: 1, error: width === 3 ? 2 : -1, variance: false, named: false};
    if (!names || names.length !== width) return map;
    var wl = findColumn(names, /wave|lambda|^wl$|^wav|angstrom|^lam/i, []);
    var err = findColumn(names, /err|sig|unc|noise|^var|variance|^e_?f/i, [wl]);
    var flux = findColumn(names, /flux|f_?lam|flam|^f$|count|intens|^fl$|^spec/i, [wl, err]);
    if (wl === -1) return map;
    if (flux === -1) flux = [0, 1, 2].filter(function (i) { return i !== wl && i !== err && i < width; })[0];
    if (flux === undefined) return map;
    return {wavelength: wl, flux: flux, error: err, variance: err !== -1 && /^var|variance/i.test(names[err]), named: true};
  }
  function median(values) {
    if (!values.length) return NaN;
    var sorted = values.slice().sort(function (a, b) { return a - b; }), middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  }
  function quantile(sorted, q) {
    if (!sorted.length) return NaN;
    var position = (sorted.length - 1) * q, low = Math.floor(position), high = Math.ceil(position);
    return sorted[low] + (sorted[high] - sorted[low]) * (position - low);
  }
  function plausible(wavelengths, low, high) {
    var middle = median(wavelengths);
    return isFinite(middle) && middle >= (low || 500) && middle <= (high || 60000);
  }
  function unitFromText(textValue) {
    if (/\bnm\b|nanomet/i.test(textValue)) return "nm";
    if (/micron|µm|μm|\bum\b/i.test(textValue)) return "µm";
    if (/angstr|Å|\bAA\b|\bang\b|\(A\)|\[A\]|\bA\s*$/i.test(textValue)) return "Å";
    return "";
  }
  // The unit written next to the wavelength keyword ("Wavelength (nm)", "CUNIT1 = 'Angstrom'",
  // "label=Wavelength units=angstroms"), so a flux unit such as erg/s/cm2/A elsewhere on
  // the line is not mistaken for it.
  var WAVELENGTH_WORD = /wave|lambda|\bwl\b|\blam\b|ctype1|cunit1|\bdisp/i;
  function wavelengthUnit(lines) {
    for (var i = 0; i < lines.length; i += 1) {
      var match = WAVELENGTH_WORD.exec(lines[i]);
      if (!match) continue;
      var unit = unitFromText(lines[i].slice(match.index, match.index + 48).split(/[,;]\s*(?=[A-Za-z])/)[0]);
      if (unit) return unit;
    }
    return "";
  }
  function errorsLookReal(raw) {
    var errors = raw.map(function (point) { return point.error; }).filter(function (value) { return value !== null; });
    if (errors.length < raw.length * 0.9) return false;
    var typicalFlux = median(raw.map(function (point) { return Math.abs(point.flux); }));
    return median(errors) <= Math.max(2 * typicalFlux, 1e-300);
  }
  function unitFromValues(raw) {
    var low = Math.min.apply(null, raw), high = Math.max.apply(null, raw);
    if (high < 30) return "µm";
    if (low >= 100 && high <= 2600) return "nm";
    return "Å";
  }

  function failure(reason) { return {ok: false, reason: reason}; }

  function parse(textValue) {
    var body = String(textValue === null || textValue === undefined ? "" : textValue).replace(/^﻿/, "");
    if (!body.trim()) return failure("empty");
    if (/^\s*SIMPLE\s*=\s*T/.test(body.slice(0, 200))) return failure("fits");
    if (/^\s*(<!doctype\s+html|<html|<\?xml|<head|<body)/i.test(body.slice(0, 500)) || /<html[\s>]/i.test(body.slice(0, 2000))) return failure("html");
    if (/[\u0000-\u0008\u000e-\u001f]/.test(body.slice(0, 4000))) return failure("binary");

    var lines = body.split(/\r\n|\r|\n/);
    var unitLines = [], names = null, rows = [], widths = {}, comments = 0, headers = 0, skipped = 0;
    lines.forEach(function (line) {
      var trimmed = line.trim();
      if (!trimmed) return;
      if (COMMENT.test(trimmed)) {
        comments += 1;
        var commentText = trimmed.replace(COMMENT, "").trim();
        if (WAVELENGTH_WORD.test(commentText)) unitLines.push(commentText);
        // a commented column-name line just before the data ("# wavelength flux error")
        var commentParts = splitLine(commentText);
        if (!rows.length && commentParts.length >= 2 && commentParts.length <= 6 && !commentParts.some(function (part) { return token(part).numeric; }) &&
            /wave|lambda|\bwl\b|\blam/i.test(commentParts[0] + " " + commentParts[1])) names = commentParts;
        return;
      }
      var parts = splitLine(trimmed);
      var first = parts.length >= 2 ? token(parts[0]) : {numeric: false}, second = parts.length >= 2 ? token(parts[1]) : {numeric: false};
      if (!first.numeric || !second.numeric) {
        headers += 1;
        if (!rows.length) {
          unitLines.push(trimmed);
          if (parts.length >= 2 && parts.length <= 8) names = parts;
        }
        return;
      }
      var values = parts.map(function (part) { return token(part).value; });
      widths[values.length] = (widths[values.length] || 0) + 1;
      rows.push(values);
    });
    if (!rows.length) return failure("no-numbers");

    var width = Number(Object.keys(widths).sort(function (a, b) { return widths[b] - widths[a] || b - a; })[0]);
    var columns = columnMap(names ? names.map(function (name) { return name.replace(/[()[\]]/g, " ").trim(); }) : null, width);
    var raw = [];
    rows.forEach(function (values) {
      var wavelength = values[columns.wavelength], flux = values[columns.flux];
      var error = columns.error >= 0 && columns.error < values.length ? values[columns.error] : NaN;
      if (columns.variance) error = error >= 0 ? Math.sqrt(error) : NaN;
      if (!isFinite(wavelength) || !isFinite(flux) || wavelength <= 0) { skipped += 1; return; }
      raw.push({wavelength: wavelength, flux: flux, error: isFinite(error) && error >= 0 ? error : null});
    });
    if (!raw.length) return failure("no-numbers");
    if (raw.length < MIN_POINTS) return {ok: false, reason: "too-few", count: raw.length};
    if (columns.error >= 0 && !columns.named && !errorsLookReal(raw)) raw.forEach(function (point) { point.error = null; });

    var headerText = unitLines.join(" \n ");
    var assembled = assemble(raw, {unit: wavelengthUnit(unitLines), logHint: /log.{0,12}(wave|lambda)|(wave|lambda).{0,12}\blog/i.test(headerText)});
    if (!assembled.ok) return assembled;
    assembled.format = "ascii"; assembled.columns = width; assembled.columnNames = columns.named ? names : null;
    assembled.errorFromVariance = columns.variance; assembled.skipped = skipped; assembled.comments = comments; assembled.headers = headers;
    return assembled;
  }

  // The steps every reader shares once it has raw {wavelength, flux, error} rows in the
  // file's own units: log10 wavelengths (declared by `logHint`, or `logWavelength: true`
  // when the format states it), the wavelength unit (`unit` as the header says, checked
  // against the values; otherwise judged from the values), conversion to Å, a
  // plausibility check and ascending order. `hints.unit` is "Å", "nm", "µm" or "".
  function assemble(raw, hints) {
    hints = hints || {};
    if (!raw.length) return failure("no-numbers");
    if (raw.length < MIN_POINTS) return {ok: false, reason: "too-few", count: raw.length};
    var wavelengths = raw.map(function (point) { return point.wavelength; });
    var logScale = hints.logWavelength === true ||
      (Boolean(hints.logHint) && Math.max.apply(null, wavelengths) < 6 && Math.min.apply(null, wavelengths) > 2);
    if (logScale) wavelengths = wavelengths.map(function (value) { return Math.pow(10, value); });
    var unit = logScale && !hints.unit ? "Å" : (UNIT_FACTOR[hints.unit] ? hints.unit : ""), basis = unit ? "header" : "values";
    if (unit && !plausible(wavelengths.map(function (value) { return value * UNIT_FACTOR[unit]; }), 800, 30000)) { unit = ""; basis = "values"; }
    if (!unit) unit = unitFromValues(wavelengths);
    var factor = UNIT_FACTOR[unit];
    var converted = wavelengths.map(function (value) { return value * factor; });
    if (!plausible(converted)) return {ok: false, reason: "implausible", unit: unit};

    var points = raw.map(function (point, index) {
      return {wavelength: converted[index], flux: point.flux, error: point.error};
    });
    var sorted = true;
    for (var i = 1; i < points.length; i += 1) if (points[i].wavelength < points[i - 1].wavelength) { sorted = false; break; }
    if (!sorted) points.sort(function (a, b) { return a.wavelength - b.wavelength; });
    return {
      ok: true, points: points, unit: unit, unitBasis: basis, logWavelength: logScale,
      hasError: points.some(function (point) { return point.error !== null; }), reordered: !sorted, skipped: 0,
    };
  }

  // ------------------------------------------------------------------ plot arithmetic
  function normalise(points) {
    var positive = points.filter(function (point) { return point.flux > 0; }).map(function (point) { return point.flux; });
    var scale = positive.length >= Math.max(5, points.length * 0.25) ? median(positive)
      : median(points.map(function (point) { return Math.abs(point.flux); }));
    if (!isFinite(scale) || scale <= 0) scale = 1;
    return {scale: scale, points: points.map(function (point) {
      return {wavelength: point.wavelength, flux: point.flux / scale, error: point.error === null ? null : point.error / scale};
    })};
  }
  function toFrame(points, z, frame) {
    var factor = frame === "rest" && isFinite(z) && z > 0 ? 1 + z : 1;
    if (factor === 1) return points;
    return points.map(function (point) { return {wavelength: point.wavelength / factor, flux: point.flux, error: point.error}; });
  }
  // Keep at most `limit` points while preserving narrow features: each bin keeps its
  // lowest and highest flux sample, in wavelength order.
  function decimate(points, limit) {
    limit = limit || 1600;
    if (points.length <= limit) return points;
    var bins = Math.max(1, Math.floor(limit / 2)), size = points.length / bins, out = [];
    for (var b = 0; b < bins; b += 1) {
      var start = Math.floor(b * size), end = Math.min(points.length, Math.floor((b + 1) * size));
      if (end <= start) continue;
      var low = start, high = start;
      for (var i = start; i < end; i += 1) {
        if (points[i].flux < points[low].flux) low = i;
        if (points[i].flux > points[high].flux) high = i;
      }
      if (low === high) out.push(points[low]);
      else if (low < high) out.push(points[low], points[high]);
      else out.push(points[high], points[low]);
    }
    return out;
  }
  // Binned means for a readable table of a long spectrum.
  function bin(points, count) {
    count = count || 200;
    if (points.length <= count) return points.slice();
    var size = points.length / count, out = [];
    for (var b = 0; b < count; b += 1) {
      var start = Math.floor(b * size), end = Math.min(points.length, Math.floor((b + 1) * size));
      if (end <= start) continue;
      var sumW = 0, sumF = 0;
      for (var i = start; i < end; i += 1) { sumW += points[i].wavelength; sumF += points[i].flux; }
      out.push({wavelength: sumW / (end - start), flux: sumF / (end - start), error: null, samples: end - start});
    }
    return out;
  }
  // A flux range that ignores the 0.5% most extreme samples (cosmic rays, edge spikes).
  function robustRange(values) {
    var finite = values.filter(function (value) { return isFinite(value); }).sort(function (a, b) { return a - b; });
    if (!finite.length) return [0, 1];
    var low = quantile(finite, 0.005), high = quantile(finite, 0.995);
    if (high === low) { low -= Math.abs(low || 1) * 0.1; high += Math.abs(high || 1) * 0.1; }
    var pad = (high - low) * 0.08;
    return [low - pad, high + pad];
  }
  function niceStep(span, count) {
    var raw = span / Math.max(1, count), power = Math.pow(10, Math.floor(Math.log10(raw))), unit = raw / power;
    return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10) * power;
  }
  function ticks(min, max, count) {
    if (!(max > min)) return [min];
    var step = niceStep(max - min, count || 5), out = [];
    for (var value = Math.ceil(min / step) * step; value <= max + step * 1e-9; value += step) out.push(Number(value.toPrecision(12)));
    return out;
  }
  function lineMarkers(min, max, z, frame) {
    var shift = frame === "observed" && isFinite(z) && z > 0 ? 1 + z : 1;
    return LINES.map(function (line) {
      var positions = line.rest.map(function (rest) { return rest * shift; }).filter(function (value) { return value >= min && value <= max; });
      return positions.length ? {id: line.id, label: line.label, rest: line.rest, positions: positions} : null;
    }).filter(Boolean);
  }
  // Stagger marker labels into rows so neighbours (He I 5876 and Na I D) do not overlap.
  // Each label starts just right of its first marker line, or is pulled left so it ends
  // at `rightEdge`; returns {row, x} per marker.
  function labelRows(markers, toX, minGap, charWidth, rightEdge) {
    var rowsEnd = [];
    return markers.map(function (marker) {
      var size = marker.label.length * (charWidth || 6.2), start = toX(marker.positions[0]) + 2, row = 0;
      if (isFinite(rightEdge)) start = Math.min(start, rightEdge - size);
      while (row < rowsEnd.length && rowsEnd[row] > start - minGap) row += 1;
      rowsEnd[row] = start + size;
      return {row: row, x: start};
    });
  }

  // ------------------------------------------------------------------ plain-words problems
  // `problem.provider` names the site the file lives on ("TNS" unless told otherwise;
  // "WISeREP" for its records), so the advice points at the right place.
  function failureMessage(problem) {
    problem = problem || {};
    var status = Number(problem.status || 0), site = problem.provider === "WISeREP" ? "WISeREP" : "TNS";
    var open = site === "WISeREP" ? "open its record at WISeREP" : "open the original file at TNS";
    var Open = open.charAt(0).toUpperCase() + open.slice(1);
    var proprietary = "This spectrum is proprietary on WISeREP (only its owners can download it), so it cannot be fetched or plotted here. " +
      "Its public metadata stays on this page; " + open + " to request access.";
    if (problem.kind === "no-relay") return "This site’s live-data relay is not configured, so the file cannot be fetched from here. Use the link to " + open + ".";
    if (problem.kind === "timeout") return "The live-data relay did not answer in time. " + site + " or the relay may be slow or down; try again later, or " + open + ".";
    if (problem.kind === "network") return "The live-data relay could not be reached (it may be down, or a network or browser extension blocked it). Try again later, or " + open + ".";
    if (problem.kind === "http") {
      if (status === 400) return "The relay does not accept this file address, so it cannot be fetched from here. " + Open + " instead.";
      if (status === 401 || status === 403) return site === "WISeREP" ? proprietary + " (HTTP " + status + ".)"
        : "TNS refused the file (HTTP " + status + "). It is probably not public; open it at TNS, signed in if you have an account.";
      if (status === 404 || status === 410) return site + " has no file at this address any more (HTTP " + status + "). It may have been renamed, moved or withdrawn.";
      if (status === 413) return "The file is larger than the relay’s 40 MB limit, so it is not plotted here. " + Open + ".";
      if (status === 429) return site + " is limiting requests right now (HTTP 429). Try again in a few minutes.";
      if (status >= 500) return site + " or the relay had a problem (HTTP " + status + "). Try again later, or " + open + ".";
      return "The file could not be fetched (HTTP " + (status || "error") + "). " + Open + ".";
    }
    var reason = problem.reason;
    if (reason === "proprietary") return proprietary;
    if (reason === "html") return site === "WISeREP" ? proprietary + " (WISeREP answered with a web page instead of the file.)"
      : "TNS sent back a web page instead of the data file, which usually means the file is not public (or TNS asked for a sign-in).";
    if (reason === "not-listed") return "WISeREP’s public listing for this object no longer names this spectrum, so its file cannot be found. It may have been withdrawn or made proprietary; " + open + ".";
    if (reason === "no-file") return "WISeREP’s public listing names this spectrum but gives no downloadable file address, so it is not plotted here. " + Open + ".";
    if (reason === "listing-unreadable") return "WISeREP’s public listing could not be read (it was not the JSON or table expected), so the file cannot be located from here. " + Open + ".";
    if (reason === "fits") return "This is a FITS file whose contents could not be read as a spectrum; " + open + ".";
    if (typeof reason === "string" && reason.indexOf("fits-") === 0) return (problem.message || "This FITS file could not be read as a spectrum.") + " " + Open + ".";
    if (reason === "gzip") return "The file is gzip-compressed and this browser cannot decompress it here. " + Open + ".";
    if (reason === "binary") return "The file is binary (perhaps a compressed archive or an image), not a text table or a FITS spectrum, so it cannot be plotted here.";
    if (reason === "empty") return "The file arrived empty, so there is nothing to plot.";
    if (reason === "too-few") return "The file was read, but only " + (problem.count || "a few") + " wavelength–flux rows were found, too few to plot.";
    if (reason === "implausible") return "The wavelengths do not look like Å, nm or µm, so the file is not plotted.";
    return "The file arrived but could not be read as an ASCII spectrum: no rows of wavelength and flux numbers were found.";
  }

  var api = {
    LINES: LINES, MIN_POINTS: MIN_POINTS, tnsFile: tnsFile, wiserepRecord: wiserepRecord, wiserepFile: wiserepFile, fileFormat: fileFormat,
    parse: parse, assemble: assemble, normalise: normalise, toFrame: toFrame,
    decimate: decimate, bin: bin, robustRange: robustRange, ticks: ticks, lineMarkers: lineMarkers, labelRows: labelRows,
    failureMessage: failureMessage, median: median,
  };
  root.CTASSpectrum = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
