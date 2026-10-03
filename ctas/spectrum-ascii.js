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
 * that, from the range of the values.
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
  function tnsFile(url) {
    var value = String(url || "");
    if (!TNS_FILE.test(value)) return null;
    var last = value.split("/").pop() || "", name = last;
    try { name = decodeURIComponent(last); } catch (_) { name = last; }
    var format = /\.(fits?|fts|fz)(\.gz)?$/i.test(name) ? "fits"
      : /\.(gz|zip|tar|tgz|bz2|xz|7z|rar)$/i.test(name) ? "archive"
      : /\.(jpe?g|png|gif|pdf|ps|eps|docx?)$/i.test(name) ? "document" : "ascii";
    return {url: value, fileName: name, format: format};
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

    var wavelengths = raw.map(function (point) { return point.wavelength; });
    var headerText = unitLines.join(" \n ");
    var logScale = /log.{0,12}(wave|lambda)|(wave|lambda).{0,12}\blog/i.test(headerText) &&
      Math.max.apply(null, wavelengths) < 6 && Math.min.apply(null, wavelengths) > 2;
    if (logScale) wavelengths = wavelengths.map(function (value) { return Math.pow(10, value); });
    var unit = logScale ? "Å" : wavelengthUnit(unitLines), basis = unit ? "header" : "values";
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
      columns: width, columnNames: columns.named ? names : null, hasError: points.some(function (point) { return point.error !== null; }),
      errorFromVariance: columns.variance, skipped: skipped, comments: comments, headers: headers, reordered: !sorted,
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
  function failureMessage(problem) {
    problem = problem || {};
    var status = Number(problem.status || 0);
    if (problem.kind === "no-relay") return "This site’s live-data relay is not configured, so the file cannot be fetched from here. Use the link to open the original file at TNS.";
    if (problem.kind === "timeout") return "The live-data relay did not answer in time. TNS or the relay may be slow or down; try again later, or open the original file at TNS.";
    if (problem.kind === "network") return "The live-data relay could not be reached (it may be down, or a network or browser extension blocked it). Try again later, or open the original file at TNS.";
    if (problem.kind === "http") {
      if (status === 400) return "The relay does not accept this file address, so it cannot be fetched from here. Open the original file at TNS instead.";
      if (status === 401 || status === 403) return "TNS refused the file (HTTP " + status + "). It is probably not public; open it at TNS, signed in if you have an account.";
      if (status === 404 || status === 410) return "TNS has no file at this address any more (HTTP " + status + "). It may have been renamed, moved or withdrawn.";
      if (status === 413) return "The file is larger than the relay’s 40 MB limit, so it is not plotted here. Open the original file at TNS.";
      if (status === 429) return "TNS is limiting requests right now (HTTP 429). Try again in a few minutes.";
      if (status >= 500) return "TNS or the relay had a problem (HTTP " + status + "). Try again later, or open the original file at TNS.";
      return "The file could not be fetched (HTTP " + (status || "error") + "). Open the original file at TNS.";
    }
    var reason = problem.reason;
    if (reason === "html") return "TNS sent back a web page instead of the data file, which usually means the file is not public (or TNS asked for a sign-in).";
    if (reason === "fits") return "This is a FITS file. Plotting FITS spectra isn’t supported here yet; open the original file at TNS.";
    if (reason === "binary") return "The file is binary (perhaps compressed or FITS), not a text table, so it cannot be plotted here yet.";
    if (reason === "empty") return "The file arrived empty, so there is nothing to plot.";
    if (reason === "too-few") return "The file was read, but only " + (problem.count || "a few") + " wavelength–flux rows were found, too few to plot.";
    if (reason === "implausible") return "The first column does not look like wavelengths in Å, nm or µm, so the file is not plotted.";
    return "The file arrived but could not be read as an ASCII spectrum: no rows of wavelength and flux numbers were found.";
  }

  var api = {
    LINES: LINES, MIN_POINTS: MIN_POINTS, tnsFile: tnsFile, parse: parse, normalise: normalise, toFrame: toFrame,
    decimate: decimate, bin: bin, robustRange: robustRange, ticks: ticks, lineMarkers: lineMarkers, labelRows: labelRows,
    failureMessage: failureMessage, median: median,
  };
  root.CTASSpectrum = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
