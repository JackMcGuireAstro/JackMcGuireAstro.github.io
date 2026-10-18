/* CTAS live source data: fetched from the providers in the visitor's browser when a
 * dossier asks for it. Nothing here is stored by CTAS or on the publishing computer,
 * and none of it is part of the checksum-verified snapshot; every value is labelled
 * with the provider it came from at the moment it was fetched.
 *
 * Sources that allow web pages to read them (ALeRCE for ZTF, Fink for Rubin, SIMBAD) are
 * fetched directly. Sources that do not (TNS and WISeREP files, Gaia Alerts, Pan-STARRS
 * at MAST, IRSA, NED, VizieR, HEASARC, VSX, Lasair, ADS) go through a pass-through relay
 * whose address is read from /live-config.json; without it those sources are reported as
 * unavailable, never guessed. Spectrum files come back as bytes and are read as FITS
 * (ctas/spectrum-fits.js) or text (ctas/spectrum-ascii.js), gzip inflated first
 * (fetchSpectrumFile).
 *
 * The dossier panel lists every source (SOURCES) with what the release already holds for
 * it; "Check all sources now" runs them a few at a time (pool) and marks each answer as
 * new, the same or fewer than the retained evidence (compareWithRelease). A repeat run
 * passes fresh = true, which only bypasses this browser's HTTP cache; the relay address is
 * unchanged, so its one-hour edge cache still stands between visitors and the providers.
 */
(function (root) {
  "use strict";

  var ALERCE = "https://api.alerce.online/ztf/v1";
  var FINK_LSST = "https://api.lsst.fink-portal.org/api/v1";
  var MATCH_ARCSEC = 2;
  var TIMEOUT_MS = 25000;
  var ZTF_BANDS = {1: "g", 2: "r", 3: "i"};
  var PS1_BANDS = {1: "g", 2: "r", 3: "i", 4: "z", 5: "y"};

  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }
  function mjdToIso(mjd) { return new Date((Number(mjd) - 40587) * 86400000).toISOString(); }
  function jdToIso(jd) { return mjdToIso(Number(jd) - 2400000.5); }
  function separationArcsec(ra1, dec1, ra2, dec2) {
    var r = Math.PI / 180, d1 = dec1 * r, d2 = dec2 * r;
    var s = Math.sin((d2 - d1) / 2), t = Math.sin((ra2 - ra1) * r / 2);
    return 2 * Math.asin(Math.sqrt(s * s + Math.cos(d1) * Math.cos(d2) * t * t)) / r * 3600;
  }
  function designations(candidate) {
    return (candidate.designations || []).map(function (row) { return String(row.designation || ""); })
      .concat(candidate.name ? [String(candidate.name)] : []);
  }
  function findName(candidate, pattern) {
    var hit = designations(candidate).filter(function (name) { return pattern.test(name); })[0];
    return hit || null;
  }

  // ---------------------------------------------------------------- parsers
  function parseAlerceLightcurve(json, oid) {
    var rows = [];
    (json && json.detections || []).forEach(function (d) {
      var positive = d.isdiffpos === undefined || d.isdiffpos === 1 || d.isdiffpos === true || d.isdiffpos === "t" || d.isdiffpos === "1";
      if (!positive || finite(d.magpsf) === null || finite(d.mjd) === null) return;
      rows.push({observed_at: mjdToIso(d.mjd), mjd: Number(d.mjd), band: ZTF_BANDS[d.fid] || String(d.fid || ""),
        magnitude: Number(d.magpsf), magnitude_error: finite(d.sigmapsf), detection: 1, difference_photometry: 1,
        magnitude_system: "AB", photometry_method: "ZTF difference-image PSF", provider: "ALeRCE (ZTF " + oid + ")", source_key: "alerce-ztf"});
    });
    (json && json.non_detections || []).forEach(function (d) {
      if (finite(d.diffmaglim) === null || finite(d.mjd) === null) return;
      rows.push({observed_at: mjdToIso(d.mjd), mjd: Number(d.mjd), band: ZTF_BANDS[d.fid] || String(d.fid || ""),
        limiting_magnitude: Number(d.diffmaglim), detection: 0, magnitude_system: "AB",
        photometry_method: "ZTF difference-image limit", provider: "ALeRCE (ZTF " + oid + ")", source_key: "alerce-ztf"});
    });
    return rows;
  }
  function nearestAlerceObject(json, ra, dec) {
    var best = null;
    (json && json.items || []).forEach(function (item) {
      var sep = separationArcsec(ra, dec, Number(item.meanra), Number(item.meandec));
      if (isFinite(sep) && sep <= MATCH_ARCSEC && (!best || sep < best.sep)) best = {oid: item.oid, sep: sep};
    });
    return best;
  }
  function parseFinkSources(list, ra, dec) {
    var rows = [];
    (Array.isArray(list) ? list : []).forEach(function (s) {
      var sra = finite(s["r:ra"]), sdec = finite(s["r:dec"]), mjd = finite(s["r:midpointMjdTai"]);
      var flux = finite(s["r:psfFlux"]), err = finite(s["r:psfFluxErr"]);
      if (sra === null || sdec === null || mjd === null || flux === null) return;
      if (ra !== null && separationArcsec(ra, dec, sra, sdec) > MATCH_ARCSEC) return;
      var row = {observed_at: mjdToIso(mjd), mjd: mjd, band: String(s["r:band"] || ""), flux: flux, flux_error: err, flux_unit: "nJy",
        difference_photometry: 1, magnitude_system: "AB", photometry_method: "Rubin difference-image PSF (TAI midpoint)",
        provider: "Fink (Rubin/LSST)", source_key: "fink-lsst"};
      if (flux > 0 && err !== null && flux / err >= 3) {
        row.magnitude = 31.4 - 2.5 * Math.log10(flux);
        row.magnitude_error = 1.0857 * err / flux;
        row.detection = 1;
      } else if (err !== null && err > 0) {
        row.limiting_magnitude = 31.4 - 2.5 * Math.log10(3 * err);
        row.detection = 0;
      } else return;
      rows.push(row);
    });
    return rows;
  }
  function parseGaiaCsv(textValue, name) {
    var rows = [];
    String(textValue || "").split(/\r?\n/).forEach(function (line) {
      if (!line || line.charAt(0) === "#") return;
      var parts = line.split(",");
      if (parts.length < 3) return;
      var jd = finite(parts[1]), mag = finite(parts[2]);
      if (jd === null || mag === null || jd < 2400000) return;
      rows.push({observed_at: jdToIso(jd), band: "G", magnitude: mag, detection: 1, magnitude_system: "Vega (Gaia G)",
        photometry_method: "Gaia Science Alerts averaged G", provider: "Gaia Science Alerts (" + name + ")", source_key: "gaia-alerts"});
    });
    return rows;
  }
  function parsePanstarrs(json) {
    var data = json && (json.data || json) || [];
    var rows = [];
    (Array.isArray(data) ? data : []).forEach(function (d) {
      var mjd = finite(d.obsTime), flux = finite(d.psfFlux), err = finite(d.psfFluxErr);
      if (mjd === null || flux === null || flux <= 0) return;
      rows.push({observed_at: mjdToIso(mjd), mjd: mjd, band: PS1_BANDS[d.filterID] || String(d.filterID || ""),
        magnitude: -2.5 * Math.log10(flux / 3631), magnitude_error: err === null ? null : 1.0857 * err / flux,
        detection: 1, magnitude_system: "AB", photometry_method: "Pan-STARRS1 DR2 single-epoch PSF",
        provider: "Pan-STARRS1 DR2 (MAST)", source_key: "panstarrs-dr2"});
    });
    return rows;
  }
  // ctas/spectrum-ascii.js reads the many TNS / WISeREP dialects (headers, separators,
  // nm or micron wavelengths, error columns); this simple reader is the fallback.
  function spectrumLib() {
    if (root.CTASSpectrum) return root.CTASSpectrum;
    try { return typeof require === "function" ? require("./spectrum-ascii.js") : null; } catch (_) { return null; }
  }
  function parseAsciiSpectrum(textValue) {
    var lib = spectrumLib();
    if (lib) {
      var parsed = lib.parse(textValue);
      return parsed.ok ? parsed.points.map(function (point, index) { return {index: index + 1, wavelength: point.wavelength, flux: point.flux}; }) : [];
    }
    var points = [];
    String(textValue || "").split(/\r?\n/).forEach(function (line) {
      var trimmed = line.trim();
      if (!trimmed || /^[#!;%]/.test(trimmed)) return;
      var parts = trimmed.split(/[\s,;]+/);
      if (parts.length < 2) return;
      var wavelength = Number(parts[0]), flux = Number(parts[1]);
      if (isFinite(wavelength) && isFinite(flux) && wavelength > 0) points.push({index: points.length + 1, wavelength: wavelength, flux: flux});
    });
    return points.length >= 10 ? points : [];
  }

  // Lasair (Rubin): find the object's source list wherever the response keeps it.
  function lasairSourceArrays(json) {
    var found = [];
    (function walk(value, depth) {
      if (!value || typeof value !== "object" || depth > 3) return;
      if (Array.isArray(value)) {
        if (value.length && value[0] && typeof value[0] === "object" && "midpointMjdTai" in value[0] && ("psfFlux" in value[0] || "scienceFlux" in value[0])) found.push(value);
        return;
      }
      Object.keys(value).forEach(function (key) { walk(value[key], depth + 1); });
    })(json, 0);
    return found;
  }
  function parseLasairObject(json, objectId) {
    var rows = [], seen = {};
    lasairSourceArrays(json).forEach(function (list) {
      list.forEach(function (s) {
        var mjd = finite(s.midpointMjdTai), flux = finite(s.psfFlux !== undefined ? s.psfFlux : s.scienceFlux);
        var err = finite(s.psfFluxErr !== undefined ? s.psfFluxErr : s.scienceFluxErr);
        if (mjd === null || flux === null) return;
        var key = mjd + ":" + (s.band || "");
        if (seen[key]) return;
        seen[key] = 1;
        var forced = "diaForcedSourceId" in s;
        var row = {observed_at: mjdToIso(mjd), mjd: mjd, band: String(s.band || ""), flux: flux, flux_error: err, flux_unit: "nJy",
          difference_photometry: 1, magnitude_system: "AB", photometry_method: forced ? "Rubin forced difference PSF" : "Rubin difference-image PSF",
          provider: "Lasair (Rubin " + objectId + ")", source_key: "lasair-lsst"};
        if (flux > 0 && err !== null && flux / err >= 3) { row.magnitude = 31.4 - 2.5 * Math.log10(flux); row.magnitude_error = 1.0857 * err / flux; row.detection = 1; }
        else if (err !== null && err > 0) { row.limiting_magnitude = 31.4 - 2.5 * Math.log10(3 * err); row.detection = 0; }
        else return;
        rows.push(row);
      });
    });
    return rows;
  }
  // Lasair answers with bare NaN values (not valid JSON) and with 18-digit object ids
  // that lose digits as JavaScript numbers; repair both before parsing.
  function parseLasairText(text) {
    return JSON.parse(String(text)
      .replace(/"(object|objectId|diaObjectId|diaSourceId|diaForcedSourceId)"\s*:\s*(-?\d{16,})/g, '"$1":"$2"')
      .replace(/:\s*-?(NaN|Infinity)\b/g, ":null"));
  }
  function lasairNearest(json) {
    if (json && !Array.isArray(json) && json.nearest) json = json.nearest;
    var hit = Array.isArray(json) ? json[0] : json;
    if (!hit || typeof hit !== "object") return null;
    var id = hit.object || hit.objectId || hit.diaObjectId;
    return id === undefined || id === null ? null : {objectId: String(id), sep: finite(hit.separation)};
  }
  function sherlockSummary(json) {
    var node = json && json.sherlock ? json.sherlock : json;
    if (Array.isArray(node)) node = node[0];
    if (node && Array.isArray(node.classifications)) node = node.classifications[0];
    else if (node && node.classifications && typeof node.classifications === "object") {
      var first = node.classifications[Object.keys(node.classifications)[0]];
      if (Array.isArray(first)) node = {classification: first[0], description: first[1]};
    }
    if (!node || typeof node !== "object") return null;
    var label = node.classification || node.sherlock_classification || node["class"];
    var text = String(node.description || node.annotator || "").replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
    return label ? {label: String(label), text: text} : null;
  }
  // NASA ADS: search names the way papers write them.
  function adsQuery(candidate) {
    var terms = {};
    designations(candidate).forEach(function (name) {
      var tns = /^(?:AT|SN|TDE|FRB|FBOT|LRN|ILRT|Nova)\s?(\d{4}[a-z]{2,4})$/i.exec(name.trim());
      if (tns) terms['"' + tns[1] + '"'] = 1;
      else if (/^[A-Za-z][\w+\-. ]{2,30}$/.test(name.trim())) terms['"' + name.trim().replace(/"/g, "") + '"'] = 1;
    });
    var list = Object.keys(terms).slice(0, 6);
    return list.length ? "full:(" + list.join(" OR ") + ")" : "";
  }
  function parseAds(json) {
    return ((json && json.response && json.response.docs) || []).map(function (doc) {
      var authors = doc.author || [];
      return {bibcode: doc.bibcode, title: (doc.title || [""])[0], authors: authors.slice(0, 3).join("; ") + (authors.length > 3 ? " et al." : ""),
        date: doc.pubdate || "", type: doc.doctype || "", url: "https://ui.adsabs.harvard.edu/abs/" + encodeURIComponent(doc.bibcode) + "/abstract"};
    });
  }

  // ---------------------------------------------------------------- fetching
  function timed(url, options) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, TIMEOUT_MS) : null;
    var opts = Object.assign({credentials: "omit", referrerPolicy: "strict-origin"}, options || {});
    if (opts.fresh) opts.cache = "reload";
    delete opts.fresh;
    if (controller) opts.signal = controller.signal;
    return fetch(url, opts).finally(function () { if (timer) clearTimeout(timer); });
  }
  function getJson(url, options) {
    return timed(url, options).then(function (r) {
      if (r.status === 404) return null;
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function getLasair(url) {
    return timed(url).then(function (r) {
      if (r.status === 404) return null;
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text().then(parseLasairText);
    });
  }
  function relayUrl(relay, target) { return relay.replace(/\/+$/, "") + "/?url=" + encodeURIComponent(target); }
  var configPromise = null;
  function loadConfig() {
    if (!configPromise) {
      configPromise = timed("live-config.json", {cache: "no-cache"}).then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return configPromise;
  }
  function relayFrom(config) {
    return config && typeof config.relay === "string" && /^https:\/\//.test(config.relay) ? config.relay : "";
  }
  // One public file through the relay, on request (the "Plot spectrum" control). Resolves
  // with {bytes, text, …}: the raw bytes always, and the text unless the bytes are a FITS
  // file or gzip (then text is ""). Rejects with an Error whose `kind` is no-relay,
  // timeout, network or http (with `status`), so the page can say what went wrong.
  function relayProblem(kind, status) {
    var error = new Error(kind + (status ? " " + status : ""));
    error.kind = kind; error.status = status || 0;
    return error;
  }
  function fitsLib() {
    if (root.CTASSpectrumFits) return root.CTASSpectrumFits;
    try { return typeof require === "function" ? require("./spectrum-fits.js") : null; } catch (_) { return null; }
  }
  function looksBinary(bytes) {
    var fits = fitsLib();
    if (fits && (fits.isFits(bytes) || fits.isGzip(bytes))) return true;
    var view = new Uint8Array(bytes), limit = Math.min(view.length, 4000);
    for (var i = 0; i < limit; i += 1) if (view[i] === 0 || (view[i] < 9 && view[i] > 0) || (view[i] > 13 && view[i] < 32 && view[i] !== 27)) return true;
    return false;
  }
  function decodeText(bytes) {
    if (typeof TextDecoder !== "undefined") return new TextDecoder("utf-8").decode(bytes);
    var view = new Uint8Array(bytes), out = "";
    for (var i = 0; i < view.length; i += 1) out += String.fromCharCode(view[i]);
    return out;
  }
  function fetchViaRelay(target, options) {
    return loadConfig().then(function (config) {
      var relay = relayFrom(config);
      if (!relay) throw relayProblem("no-relay");
      return timed(relayUrl(relay, target), options && options.fresh ? {fresh: true} : {}).then(function (r) {
        if (!r.ok) throw relayProblem("http", r.status);
        return r.arrayBuffer().then(function (bytes) {
          var binary = looksBinary(bytes);
          return {bytes: bytes, text: binary ? "" : decodeText(bytes), binary: binary, status: r.status, relay: relay,
            contentType: (r.headers && r.headers.get("Content-Type")) || ""};
        });
      }, function (error) { throw relayProblem(error && error.name === "AbortError" ? "timeout" : "network"); });
    });
  }
  // gzip-compressed files (TNS accepts .fits.gz) are inflated in the browser when it can.
  function inflate(bytes) {
    if (typeof DecompressionStream === "undefined" || typeof Response === "undefined") return Promise.reject({reason: "gzip"});
    try {
      return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer()
        .catch(function () { throw {reason: "gzip"}; });
    } catch (_) { return Promise.reject({reason: "gzip"}); }
  }
  // Bytes from the relay → a parsed spectrum (ctas/spectrum-fits.js for FITS, else
  // ctas/spectrum-ascii.js). Rejects with the reader's own problem object.
  function readSpectrumBytes(response) {
    var lib = spectrumLib(), fits = fitsLib(), bytes = response.bytes;
    var ready = fits && fits.isGzip(bytes) ? inflate(bytes) : Promise.resolve(bytes);
    return ready.then(function (plain) {
      var parsed = fits && fits.isFits(plain) ? fits.parse(plain)
        : lib ? lib.parse(plain === bytes && response.text !== "" ? response.text : decodeText(plain)) : null;
      if (!parsed) throw {reason: "no-reader"};
      if (!parsed.ok) throw parsed;
      return parsed;
    });
  }
  // The whole path for one provider file: fetch through the relay, inflate, read.
  function fetchSpectrumFile(target, options) {
    return fetchViaRelay(target, options).then(readSpectrumBytes);
  }

  // ---------------------------------------------------------------- small readers
  // CSV as TAP services and IRSA return it: quoted fields, a header row, "#" comments.
  function splitCsvLine(line) {
    var out = [], cur = "", quoted = false;
    for (var i = 0; i < line.length; i += 1) {
      var c = line.charAt(i);
      if (quoted) {
        if (c === '"' && line.charAt(i + 1) === '"') { cur += '"'; i += 1; }
        else if (c === '"') quoted = false;
        else cur += c;
      } else if (c === '"') quoted = true;
      else if (c === ",") { out.push(cur); cur = ""; }
      else cur += c;
    }
    out.push(cur);
    return out;
  }
  function parseCsv(textValue) {
    var lines = String(textValue || "").replace(/^﻿/, "").split(/\r?\n/).filter(function (line) { return line.trim() && line.charAt(0) !== "\\" && !/^#(?![A-Za-z])/.test(line); });
    if (!lines.length) return {columns: [], rows: []};
    if (/^\s*</.test(lines[0])) return {columns: [], rows: [], markup: true};
    var columns = splitCsvLine(lines[0].replace(/^#/, "")).map(function (name) { return name.trim(); });
    var rows = lines.slice(1).map(function (line) {
      var cells = splitCsvLine(line), row = {};
      columns.forEach(function (name, index) { row[name] = cells[index] === undefined ? "" : cells[index].trim(); });
      return row;
    });
    return {columns: columns, rows: rows};
  }
  function column(table, pattern) {
    return (table.columns || []).filter(function (name) { return pattern.test(name); })[0] || null;
  }
  // SIMBAD and VizieR TAP JSON: {metadata: [{name}], data: [[…]]}; NASA-style JSON: [{…}].
  function tapRows(json) {
    if (Array.isArray(json)) return json;
    if (json && Array.isArray(json.data) && Array.isArray(json.metadata)) {
      var names = json.metadata.map(function (m) { return m.name; });
      return json.data.map(function (values) { var row = {}; names.forEach(function (n, i) { row[n] = values[i]; }); return row; });
    }
    return [];
  }
  // IRSA ZTF data-release light curves (persistent-source PSF photometry, not difference imaging)
  function parseIrsaZtf(textValue) {
    var table = parseCsv(textValue), rows = [];
    var mjdCol = column(table, /^mjd$/i), magCol = column(table, /^mag$/i), errCol = column(table, /^magerr$/i), bandCol = column(table, /^filtercode$/i), flagCol = column(table, /^catflags$/i);
    if (!mjdCol || !magCol) return rows;
    table.rows.forEach(function (r) {
      var mjd = finite(r[mjdCol]), mag = finite(r[magCol]);
      if (mjd === null || mag === null) return;
      if (flagCol && finite(r[flagCol]) !== null && Number(r[flagCol]) >= 32768) return;
      rows.push({observed_at: mjdToIso(mjd), mjd: mjd, band: String(r[bandCol] || "").replace(/^z/, ""), magnitude: mag, magnitude_error: errCol ? finite(r[errCol]) : null,
        detection: 1, difference_photometry: 0, magnitude_system: "AB", photometry_method: "ZTF data-release PSF (persistent source, not difference imaging)", provider: "IRSA ZTF data release", source_key: "ztf-irsa"});
    });
    return rows;
  }
  // The Gaia Science Alerts index: the alert nearest to a position, within `arcsec`.
  function nearestGaiaAlert(textValue, ra, dec, arcsec) {
    var table = parseCsv(textValue);
    var nameCol = column(table, /^#?name$/i), raCol = column(table, /^ra(deg)?$/i), decCol = column(table, /^dec(deg)?$/i), dateCol = column(table, /^date$/i);
    if (!nameCol || !raCol || !decCol) return null;
    var best = null;
    table.rows.forEach(function (r) {
      var sra = finite(r[raCol]), sdec = finite(r[decCol]);
      if (sra === null || sdec === null) return;
      var sep = separationArcsec(ra, dec, sra, sdec);
      if (sep <= (arcsec || MATCH_ARCSEC) && (!best || sep < best.sep)) best = {name: String(r[nameCol]).trim(), sep: sep, date: dateCol ? r[dateCol] : ""};
    });
    return best && /^Gaia\d{2}[a-z]{1,4}$/.test(best.name) ? best : null;
  }
  // AAVSO VSX api.list answers {"VSXObjects": {"VSXObject": [ … ]}} or {"VSXObjects": ""}.
  function parseVsx(json) {
    var node = json && json.VSXObjects;
    var list = node && node.VSXObject ? node.VSXObject : [];
    if (list && !Array.isArray(list)) list = [list];
    return (list || []).map(function (o) { return {name: o.Name, type: o.VariabilityType || "", period: o.Period || "", sep: o.Distance !== undefined ? Number(o.Distance) : null}; });
  }
  function latestOf(rows, key) {
    var latest = null;
    (rows || []).forEach(function (r) { var v = r[key || "observed_at"]; if (v && (!latest || v > latest)) latest = v; });
    return latest;
  }

  // ---------------------------------------------------------------- release baseline
  // What the published record already holds for each source family, so a live answer can
  // be compared with it: retained counts per source id (from the record's source
  // accounting), when the release last asked each source, and the latest retained
  // observation date.
  function releaseBaseline(candidate) {
    var counts = candidate.follow_up_counts || {}, bySource = {};
    (candidate.source_coverage || []).forEach(function (sc) {
      var id = sc.source_id; if (!id) return;
      var cur = bySource[id] || {count: 0, checkedAt: null, dispositions: []};
      cur.count = Math.max(cur.count, Number(sc.retained_record_count || 0));
      if (sc.checked_at && (!cur.checkedAt || sc.checked_at > cur.checkedAt)) cur.checkedAt = sc.checked_at;
      if (sc.disposition) cur.dispositions.push(sc.disposition);
      bySource[id] = cur;
    });
    var observations = (candidate.follow_up || {}).observations || [];
    return {counts: counts, bySource: bySource, latestObserved: latestOf(observations), observations: observations.length,
      spectra: Number(counts.spectra || 0), publications: Number(counts.publications || 0), catalogCounterparts: Number(counts.catalog_counterparts || 0),
      archiveProducts: Number(counts.archive_products || 0), releasedAt: candidate.updated_at || null};
  }
  function familyBaseline(base, family) {
    var retained = null, checkedAt = null, dispositions = [];
    (family || []).forEach(function (id) {
      var b = base.bySource[id]; if (!b) return;
      retained = (retained || 0) + b.count;
      if (b.checkedAt && (!checkedAt || b.checkedAt > checkedAt)) checkedAt = b.checkedAt;
      dispositions = dispositions.concat(b.dispositions);
    });
    return {retained: retained, checkedAt: checkedAt, dispositions: dispositions};
  }
  // The comparison shown on a row. `kind`: photometry (counts and latest observation),
  // spectra, papers, catalog, archive (counts only). Returns {state, text} where state is
  // new | same | fewer | none | unknown.
  function compareWithRelease(source, result, base) {
    if (!result || (result.status !== "ok" && result.status !== "none")) return {state: "unknown", text: ""};
    var fam = familyBaseline(base, source.family);
    var count = Number(result.count || 0);
    var retained = fam.retained;
    if (source.kind === "spectra") retained = Math.max(retained || 0, base.spectra);
    if (source.kind === "papers") retained = Math.max(retained || 0, base.publications);
    var parts = [];
    if (source.kind === "photometry" && result.latest && base.latestObserved && Date.parse(result.latest) > Date.parse(base.latestObserved) + 43200000) {
      parts.push("newer measurements upstream (latest " + result.latest.slice(0, 10) + ", release holds up to " + base.latestObserved.slice(0, 10) + ")");
    }
    if (retained === null) {
      if (count > 0) return {state: "new", text: (parts[0] ? parts[0] + "; " : "") + count.toLocaleString() + " upstream, none of this source retained in the release"};
      return {state: parts.length ? "new" : "none", text: parts.join("; ")};
    }
    if (count > retained) parts.push(count.toLocaleString() + " upstream vs " + retained.toLocaleString() + " retained (+" + (count - retained).toLocaleString() + ")");
    if (parts.length) return {state: "new", text: parts.join("; ")};
    if (count === retained) return {state: "same", text: count ? "matches the release (" + count.toLocaleString() + " retained)" : "none here, none retained"};
    return {state: "fewer", text: "release holds more (" + retained.toLocaleString() + " retained, " + count.toLocaleString() + " answered now)"};
  }

  // ---------------------------------------------------------------- the sources
  // Each entry: key, label, kind, family (the release's source ids it is compared with),
  // applies(candidate) → false | reason, and run(candidate, ctx) resolving to a result
  // {status: ok | none | unavailable | no-relay | needs-key | link, count, latest, rows,
  // how, link, note, context}. ctx has relay, fresh, json(url, opts), text(url, opts).
  var SOURCES = [];
  function source(spec) { SOURCES.push(spec); return spec; }
  function needsPosition(candidate) { return finite(candidate.ra_deg) !== null && finite(candidate.dec_deg) !== null ? true : "No coordinates in the record"; }
  function tnsSuffix(candidate) {
    var hit = null;
    designations(candidate).forEach(function (name) { var m = /^(?:AT|SN|TDE|FRB|FBOT|LRN|ILRT|Nova)\s?(\d{4}[a-z]{2,4})$/i.exec(name.trim()); if (m && !hit) hit = m[1]; });
    return hit;
  }
  function pos(candidate) { return {ra: finite(candidate.ra_deg), dec: finite(candidate.dec_deg)}; }
  function deg(arcsec) { return (arcsec / 3600).toFixed(6); }
  function okOrNone(result) { result.status = result.count > 0 ? "ok" : "none"; return result; }
  function relayText(ctx, target) {
    if (!ctx.relay) return Promise.reject({noRelay: true});
    return ctx.text(relayUrl(ctx.relay, target));
  }
  function relayJson(ctx, target) {
    if (!ctx.relay) return Promise.reject({noRelay: true});
    return ctx.json(relayUrl(ctx.relay, target));
  }
  function tap(base, query, format) {
    return base + "?REQUEST=doQuery&LANG=ADQL&FORMAT=" + (format || "csv") + "&QUERY=" + encodeURIComponent(query);
  }

  source({key: "alerce", label: "ZTF via ALeRCE", kind: "photometry", family: ["ztf-alerce"], unit: "measurements",
    applies: function (c) { return findName(c, /^ZTF\d{2}[a-z]{7}$/) ? true : needsPosition(c); },
    run: function (candidate, ctx) {
      var oid = findName(candidate, /^ZTF\d{2}[a-z]{7}$/), p = pos(candidate);
      var byPosition = function (prefix) {
        if (p.ra === null || p.dec === null) return Promise.resolve(null);
        return ctx.json(ALERCE + "/objects/?ra=" + p.ra + "&dec=" + p.dec + "&radius=" + MATCH_ARCSEC + "&page_size=5")
          .then(function (json) { var hit = nearestAlerceObject(json, p.ra, p.dec); if (hit) hit.how = (prefix || "") + "by position: " + hit.oid + " (" + hit.sep.toFixed(2) + "″)"; return hit; });
      };
      var curve = function (hit) {
        return ctx.json(ALERCE + "/objects/" + encodeURIComponent(hit.oid) + "/lightcurve").then(function (json) {
          if (!json) return null;
          var rows = parseAlerceLightcurve(json, hit.oid);
          return okOrNone({how: hit.how, rows: rows, count: rows.length, latest: latestOf(rows), link: "https://alerce.online/object/" + encodeURIComponent(hit.oid)});
        });
      };
      var first = oid ? curve({oid: oid, how: "by its ZTF name " + oid}) : Promise.resolve(null);
      return first.then(function (result) {
        if (result) return result;
        // ALeRCE may not know a TNS-reported ZTF name; the nearest ZTF object is the fallback
        return byPosition(oid ? oid + " not in ALeRCE; " : "").then(function (hit) {
          if (!hit) return {status: "none", count: 0, how: oid ? "by name " + oid + " and by position" : "by position (within " + MATCH_ARCSEC + "″)", note: "No ZTF object found"};
          return curve(hit).then(function (found) { return found || {status: "none", count: 0, how: hit.how, note: "ALeRCE has no light curve for " + hit.oid}; });
        });
      });
    }});
  source({key: "fink", label: "Rubin via Fink", kind: "photometry", family: ["rubin-fink", "rubin-fink-crossmatch"], unit: "measurements", applies: needsPosition,
    run: function (candidate, ctx) {
      var p = pos(candidate);
      return ctx.json(FINK_LSST + "/conesearch", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ra: p.ra, dec: p.dec, radius: 10, "output-format": "json"})}).then(function (list) {
        var rows = parseFinkSources(list || [], p.ra, p.dec);
        return okOrNone({how: "by position (within " + MATCH_ARCSEC + "″)", rows: rows, count: rows.length, latest: latestOf(rows), link: "https://lsst.fink-portal.org/"});
      });
    }});
  source({key: "lasair", label: "Rubin via Lasair", kind: "photometry", family: ["rubin-lasair"], unit: "measurements", applies: needsPosition, relay: true,
    run: function (candidate, ctx) {
      var p = pos(candidate), base = "https://api.lasair.lsst.ac.uk/api/";
      if (!ctx.relay) return Promise.reject({noRelay: true});
      var lasairJson = function (target) { return ctx.text(relayUrl(ctx.relay, target), {raw: true}).then(function (r) {
        if (r.status === 503) throw {needsKey: "Lasair"};
        if (r.status === 404) return null;
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text().then(parseLasairText);
      }); };
      var context = lasairJson(base + "sherlock/position/?ra=" + p.ra + "&dec=" + p.dec + "&lite=true").then(sherlockSummary, function () { return null; });
      return lasairJson(base + "cone/?ra=" + p.ra + "&dec=" + p.dec + "&radius=" + MATCH_ARCSEC + "&requestType=nearest").then(function (json) {
        var hit = lasairNearest(json);
        if (!hit) return {status: "none", count: 0, how: "by position (within " + MATCH_ARCSEC + "″)", note: "No Rubin object at this position"};
        return lasairJson(base + "object/?objectId=" + encodeURIComponent(hit.objectId)).then(function (obj) {
          var rows = obj ? parseLasairObject(obj, hit.objectId) : [];
          return okOrNone({how: "by position: " + hit.objectId + (hit.sep !== null ? " (" + hit.sep.toFixed(2) + "″)" : ""), rows: rows, count: rows.length, latest: latestOf(rows),
            link: "https://lasair.lsst.ac.uk/objects/" + encodeURIComponent(hit.objectId) + "/"});
        });
      }).then(function (result) { return context.then(function (sherlock) { result.context = sherlock; return result; }); });
    }});
  source({key: "gaia", label: "Gaia Science Alerts", kind: "photometry", family: ["gaia-alerts", "represented-survey-gaiaalerts"], unit: "measurements", relay: true,
    applies: function (c) { return findName(c, /^Gaia\d{2}[a-z]{1,4}$/) ? true : needsPosition(c); },
    run: function (candidate, ctx) {
      var name = findName(candidate, /^Gaia\d{2}[a-z]{1,4}$/), p = pos(candidate);
      var found = name ? Promise.resolve({name: name, how: "by its Gaia alert name " + name})
        : relayText(ctx, "https://gsaweb.ast.cam.ac.uk/alerts/alerts.csv").then(function (body) {
          var hit = nearestGaiaAlert(body, p.ra, p.dec, MATCH_ARCSEC);
          return hit ? {name: hit.name, how: "by position in the alerts index: " + hit.name + " (" + hit.sep.toFixed(2) + "″)"} : null;
        });
      return found.then(function (hit) {
        if (!hit) return {status: "none", count: 0, how: "by position in the public alerts index (within " + MATCH_ARCSEC + "″)", note: "No Gaia alert at this position"};
        return relayText(ctx, "https://gsaweb.ast.cam.ac.uk/alerts/alert/" + hit.name + "/lightcurve.csv").then(function (body) {
          var rows = parseGaiaCsv(body, hit.name);
          return okOrNone({how: hit.how, rows: rows, count: rows.length, latest: latestOf(rows), link: "https://gsaweb.ast.cam.ac.uk/alerts/alert/" + hit.name + "/"});
        });
      });
    }});
  source({key: "panstarrs", label: "Pan-STARRS1 DR2", kind: "photometry", family: ["pan-starrs"], unit: "measurements", applies: needsPosition, relay: true,
    run: function (candidate, ctx) {
      var p = pos(candidate);
      return relayJson(ctx, "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.json?ra=" + p.ra + "&dec=" + p.dec +
        "&radius=" + deg(MATCH_ARCSEC) + "&columns=%5BobsTime,filterID,psfFlux,psfFluxErr%5D&pagesize=5000").then(function (json) {
        var rows = parsePanstarrs(json);
        return okOrNone({how: "by position (within " + MATCH_ARCSEC + "″); archival 2009–2014 epochs", rows: rows, count: rows.length, latest: latestOf(rows), link: "https://catalogs.mast.stsci.edu/panstarrs/"});
      });
    }});
  source({key: "irsa-ztf", label: "ZTF data release via IRSA", kind: "photometry", family: ["ztf-irsa"], unit: "measurements", applies: needsPosition, relay: true, plot: false,
    run: function (candidate, ctx) {
      var p = pos(candidate);
      return relayText(ctx, "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves?POS=CIRCLE%20" + p.ra + "%20" + p.dec + "%20" + deg(MATCH_ARCSEC) + "&FORMAT=csv").then(function (body) {
        var rows = parseIrsaZtf(body);
        return okOrNone({how: "by position (within " + MATCH_ARCSEC + "″); data-release PSF photometry of a persistent source, kept out of the plot", rows: rows, count: rows.length, latest: latestOf(rows),
          link: "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-scan?mission=irsa&submit=Select&projshort=ZTF"});
      });
    }});
  source({key: "wiserep", label: "WISeREP public spectra", kind: "spectra", family: ["wiserep", "tns-public-reports"], unit: "public spectra", relay: true,
    applies: function (c) { return tnsSuffix(c) ? true : "No TNS designation to look up"; },
    run: function (candidate, ctx) {
      var name = tnsSuffix(candidate), lib = spectrumLib();
      return relayText(ctx, "https://www.wiserep.org/search/spectra?name=" + encodeURIComponent(name) + "&public=yes&format=json&num_page=50").then(function (body) {
        var text = String(body || "").trim();
        if (/^\s*(<!doctype\s+html|<html|<head|<body)/i.test(text.slice(0, 500))) return {status: /log\s*-?in|sign\s*-?in|password/i.test(text) ? "proprietary" : "unavailable", count: 0, note: "WISeREP answered with a web page instead of its public listing"};
        var rows = [];
        if (text && lib && lib.wiserepFile) {
          try { rows = JSON.parse(text); } catch (_) { rows = []; }
          if (rows && !Array.isArray(rows) && typeof rows === "object") rows = Array.isArray(rows.data) ? rows.data : Object.keys(rows.data || rows).map(function (k) { return (rows.data || rows)[k]; });
        } else if (text) { try { rows = JSON.parse(text); } catch (_) { rows = []; } }
        rows = Array.isArray(rows) ? rows.filter(function (r) { return r && typeof r === "object"; }) : [];
        return okOrNone({how: "public listing for " + name, count: rows.length, items: rows, link: "https://www.wiserep.org/search/spectra?name=" + encodeURIComponent(name) + "&public=yes"});
      });
    }});
  source({key: "ads", label: "Papers and reports (NASA ADS)", kind: "papers", family: ["ads"], unit: "papers", relay: true,
    applies: function (c) { return adsQuery(c) ? true : "No searchable name"; },
    run: function (candidate, ctx) {
      var q = adsQuery(candidate);
      if (!ctx.relay) return Promise.reject({noRelay: true});
      return ctx.text(relayUrl(ctx.relay, "https://api.adsabs.harvard.edu/v1/search/query?q=" + encodeURIComponent(q) + "&fl=bibcode,title,author,pubdate,doctype&rows=25&sort=" + encodeURIComponent("date desc")), {raw: true}).then(function (r) {
        if (r.status === 503) throw {needsKey: "NASA ADS"};
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json().then(function (json) {
          var papers = parseAds(json), total = json && json.response && finite(json.response.numFound);
          return okOrNone({how: "full-text search for " + q, count: total !== null && total !== undefined ? total : papers.length, papers: papers, query: q, latest: latestOf(papers, "date")});
        });
      });
    }});
  source({key: "simbad", label: "SIMBAD objects nearby", kind: "catalog", family: ["simbad"], unit: "objects", applies: needsPosition,
    run: function (candidate, ctx) {
      var p = pos(candidate), r = 10;
      var q = "SELECT TOP 10 main_id, otype, ra, dec, DISTANCE(POINT('ICRS', ra, dec), POINT('ICRS', " + p.ra + ", " + p.dec + ")) AS sep FROM basic WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', " + p.ra + ", " + p.dec + ", " + deg(r) + "))=1 ORDER BY sep";
      return ctx.json(tap("https://simbad.cds.unistra.fr/simbad/sim-tap/sync", q, "json")).then(function (json) {
        var rows = tapRows(json).map(function (o) { return {name: o.main_id, type: o.otype, sep: finite(o.sep) === null ? null : Number(o.sep) * 3600}; });
        return okOrNone({how: "within " + r + "″ (context, not a host association)", count: rows.length, items: rows, link: "https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=" + encodeURIComponent(p.ra + " " + p.dec) + "&Radius=" + r + "&Radius.unit=arcsec"});
      });
    }});
  source({key: "ned", label: "NED objects nearby", kind: "catalog", family: ["ned", "ned-cone"], unit: "objects", applies: needsPosition, relay: true,
    run: function (candidate, ctx) {
      var p = pos(candidate), r = 10;
      var q = "SELECT TOP 10 prefname, pretype, ra, dec, z FROM NEDTAP.objdir WHERE CONTAINS(POINT('J2000', ra, dec), CIRCLE('J2000', " + p.ra + ", " + p.dec + ", " + deg(r) + "))=1";
      return relayText(ctx, tap("https://ned.ipac.caltech.edu/tap/sync", q, "csv")).then(function (body) {
        var table = parseCsv(body);
        if (table.markup) throw new Error("NED answered in a format this page cannot read");
        var rows = table.rows.map(function (o) { return {name: o.prefname, type: o.pretype, z: o.z}; });
        return okOrNone({how: "within " + r + "″ (context, not a host association)", count: rows.length, items: rows, link: "https://ned.ipac.caltech.edu/conesearch?search_type=Near%20Position%20Search&coordinates=" + encodeURIComponent(p.ra + "d " + p.dec + "d") + "&radius=" + (r / 60).toFixed(3)});
      });
    }});
  source({key: "gaia-dr3", label: "Gaia DR3 sources nearby", kind: "catalog", family: ["gaia-dr3"], unit: "sources", applies: needsPosition, relay: true,
    run: function (candidate, ctx) {
      var p = pos(candidate), r = 5;
      var q = "SELECT TOP 10 Source, RA_ICRS, DE_ICRS, Gmag, Plx, PM FROM \"I/355/gaiadr3\" WHERE 1=CONTAINS(POINT('ICRS', RA_ICRS, DE_ICRS), CIRCLE('ICRS', " + p.ra + ", " + p.dec + ", " + deg(r) + "))";
      return relayText(ctx, tap("https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync", q, "csv")).then(function (body) {
        var table = parseCsv(body);
        if (table.markup) throw new Error("VizieR answered in a format this page cannot read");
        var rows = table.rows.map(function (o) { return {name: "Gaia DR3 " + o.Source, type: o.Gmag ? "G = " + o.Gmag : "", plx: o.Plx}; });
        return okOrNone({how: "within " + r + "″ via VizieR (a star at the position may be a foreground source or a host nucleus)", count: rows.length, items: rows,
          link: "https://vizier.cds.unistra.fr/viz-bin/VizieR-4?-source=I/355/gaiadr3&-c=" + encodeURIComponent(p.ra + " " + p.dec) + "&-c.rs=" + r});
      });
    }});
  var HEASARC_TABLES = [["swiftmastr", "Swift", "start_time"], ["xmmmaster", "XMM-Newton", "time"], ["chanmaster", "Chandra", "time"], ["numaster", "NuSTAR", "time"]];
  source({key: "heasarc", label: "HEASARC pointed X-ray observations", kind: "archive", family: ["heasarc"], unit: "observations", applies: needsPosition, relay: true,
    run: function (candidate, ctx) {
      var p = pos(candidate), r = 0.2;
      return Promise.all(HEASARC_TABLES.map(function (t) {
        var q = "SELECT TOP 50 name, obsid, " + t[2] + " FROM " + t[0] + " WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', " + p.ra + ", " + p.dec + ", " + r + "))=1";
        return relayText(ctx, tap("https://heasarc.gsfc.nasa.gov/xamin/vo/tap/sync", q, "csv")).then(function (body) {
          var table = parseCsv(body);
          if (table.markup) throw new Error("HEASARC answered in a format this page cannot read");
          return table.rows.map(function (o) { return {mission: t[1], name: o.name, obsid: o.obsid, time: o[t[2]]}; });
        }, function (error) { return {failed: t[1], error: error}; });
      })).then(function (lists) {
        var rows = [], failed = [];
        lists.forEach(function (list) { if (Array.isArray(list)) rows = rows.concat(list); else failed.push(list.failed); });
        if (failed.length === lists.length) throw lists[0].error;
        var byMission = {};
        rows.forEach(function (row) { byMission[row.mission] = (byMission[row.mission] || 0) + 1; });
        var summary = Object.keys(byMission).map(function (m) { return m + " " + byMission[m]; }).join(", ");
        return okOrNone({how: "within 12′ in the Swift, XMM-Newton, Chandra and NuSTAR master tables" + (summary ? ": " + summary : "") + (failed.length ? " (" + failed.join(", ") + " did not answer)" : ""),
          count: rows.length, items: rows, link: "https://heasarc.gsfc.nasa.gov/cgi-bin/W3Browse/w3browse.pl"});
      });
    }});
  source({key: "vsx", label: "Known variables (AAVSO VSX)", kind: "catalog", family: ["aavso", "aavso-aid"], unit: "known variables", applies: needsPosition, relay: true,
    run: function (candidate, ctx) {
      var p = pos(candidate), r = 60;
      return relayJson(ctx, "https://www.aavso.org/vsx/index.php?view=api.list&ra=" + p.ra + "&dec=" + p.dec + "&radius=" + deg(r) + "&format=json").then(function (json) {
        var rows = parseVsx(json);
        return okOrNone({how: "within " + r + "″ of the position (a known variable here can explain a transient report)", count: rows.length, items: rows.map(function (v) { return {name: v.name, type: v.type, sep: v.sep}; }),
          link: "https://www.aavso.org/vsx/index.php?view=results.get&coords=" + encodeURIComponent(p.ra + " " + p.dec) + "&format=d&size=" + r + "&unit=3"});
      });
    }});
  // Sources that cannot be read from a web page: shown as links so the reader can check them.
  source({key: "tns-page", label: "TNS object page", kind: "link", family: ["tns", "tns-public-reports"],
    applies: function (c) { return tnsSuffix(c) ? true : "No TNS designation"; },
    link: function (c) { return "https://www.wis-tns.org/object/" + tnsSuffix(c); },
    note: "The TNS API needs a registered bot key and its pages sit behind a browser check, so the page cannot be read live; open it for the current reports, photometry and classification."});
  source({key: "asassn", label: "ASAS-SN Sky Patrol", kind: "link", family: ["asas-sn"], applies: needsPosition,
    link: function (c) { return "https://asas-sn.osu.edu/photometry?ra=" + finite(c.ra_deg) + "&dec=" + finite(c.dec_deg) + "&radius=0.5"; },
    note: "Light curves are made on request behind a reCAPTCHA, so they cannot be fetched by a page; open the form with the position filled in."});
  source({key: "gcn", label: "GCN circulars", kind: "link", family: ["gcn-circulars"],
    applies: function (c) { return designations(c).length ? true : "No name to search"; },
    link: function (c) { return "https://gcn.nasa.gov/circulars?query=" + encodeURIComponent(designations(c)[0]); },
    note: "GCN's circular search is a web page without a public JSON form; open it to see every circular that names this object."});

  function sourceByKey(key) { return SOURCES.filter(function (s) { return s.key === key; })[0] || null; }

  // ---------------------------------------------------------------- running them
  function makeContext(relay, fresh) {
    var opts = fresh ? {fresh: true} : {};
    return {relay: relay, fresh: Boolean(fresh),
      json: function (url, options) { return getJson(url, Object.assign({}, opts, options || {})); },
      text: function (url, options) {
        options = Object.assign({}, opts, options || {});
        var raw = options.raw; delete options.raw;
        return timed(url, options).then(function (r) {
          if (raw) return r;
          if (r.status === 404) return "";
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.text();
        });
      }};
  }
  function problemResult(error) {
    if (error && error.noRelay) return {status: "no-relay", count: 0, note: "Needs the live-data relay"};
    if (error && error.needsKey) return {status: "needs-key", count: 0, note: "Needs a free " + error.needsKey + " key added to the relay"};
    var reason = error && error.name === "AbortError" ? "timed out" : error && error.name === "SyntaxError" ? "the provider’s answer could not be read"
      : error && error.name === "TypeError" && /fetch|network/i.test(error.message || "") ? "the provider could not be reached" : error && error.message ? error.message : String(error);
    return {status: "unavailable", count: 0, note: "Unavailable right now (" + reason + ")"};
  }
  // One source for one candidate, always resolving to a labelled result.
  function runSource(spec, candidate, ctx) {
    var applies = spec.applies ? spec.applies(candidate) : true;
    var started = Date.now();
    var finish = function (result) {
      result.key = spec.key; result.source = spec.label; result.kind = spec.kind; result.unit = spec.unit || "rows";
      result.fetchedAt = new Date().toISOString(); result.ms = Date.now() - started;
      if (result.rows && spec.plot === false) result.rows.forEach(function (row) { row.plot = false; });
      return result;
    };
    if (applies !== true) return Promise.resolve(finish({status: "not-applicable", count: 0, note: applies}));
    if (spec.kind === "link") return Promise.resolve(finish({status: "link", count: 0, link: spec.link(candidate), note: spec.note}));
    if (spec.relay && !ctx.relay) return Promise.resolve(finish(problemResult({noRelay: true})));
    return Promise.resolve().then(function () { return spec.run(candidate, ctx); }).then(function (result) {
      return finish(result || {status: "none", count: 0});
    }, function (error) { return finish(problemResult(error)); });
  }
  // Run `jobs` (functions returning promises) at most `limit` at a time, in order.
  function pool(jobs, limit, onEach) {
    var next = 0, results = new Array(jobs.length);
    function worker() {
      if (next >= jobs.length) return Promise.resolve();
      var index = next; next += 1;
      return jobs[index]().then(function (result) { results[index] = result; if (onEach) onEach(index, result); })
        .catch(function (error) { results[index] = problemResult(error); if (onEach) onEach(index, results[index]); })
        .then(worker);
    }
    var workers = [];
    for (var i = 0; i < Math.min(limit, jobs.length); i += 1) workers.push(worker());
    return Promise.all(workers).then(function () { return results; });
  }
  var CONCURRENCY = 4;
  function spectra(candidate, ctx) {
    var lib = spectrumLib();
    var rows = ((candidate.follow_up || {}).spectra || []).filter(function (row) {
      var file = lib ? lib.tnsFile(row.public_download_url) : null;
      return file ? file.format === "ascii" || file.format === "fits" : /^https:\/\/www\.wis-tns\.org\/system\/files\/uploaded\//.test(row.public_download_url || "") &&
        /\.(dat|txt|ascii|asci|asc|csv|flm|spec)$/i.test(row.public_download_url);
    });
    if (!rows.length) return Promise.resolve([]);
    if (!ctx.relay) return Promise.resolve(rows.map(function (row) { return {row: row, points: [], note: "Needs the live-data relay"}; }));
    return Promise.all(rows.slice(0, 8).map(function (row) {
      return fetchSpectrumFile(row.public_download_url, {fresh: ctx.fresh}).then(function (parsed) {
        return {row: row, points: parsed.points, parsed: parsed, note: ""};
      }).catch(function (error) {
        return {row: row, points: [], note: lib && error && (error.kind || error.reason) ? lib.failureMessage(error) : "Unavailable: " + (error && error.message || error)};
      });
    }));
  }
  // Every live source for one candidate. options: fresh (bypass this browser's cache only;
  // the relay address is unchanged so its edge cache still applies), onRow(result, index,
  // comparison) called as each source finishes, limit (parallel requests, default 4).
  function load(candidate, options) {
    options = options || {};
    return loadConfig().then(function (config) {
      var relay = relayFrom(config), ctx = makeContext(relay, options.fresh), base = releaseBaseline(candidate);
      var jobs = SOURCES.map(function (spec) { return function () { return runSource(spec, candidate, ctx); }; });
      var spectraJob = spectra(candidate, ctx).catch(function () { return []; });
      return pool(jobs, options.limit || CONCURRENCY, function (index, result) {
        result.comparison = compareWithRelease(SOURCES[index], result, base);
        result.releaseBaseline = familyBaseline(base, SOURCES[index].family);
        if (options.onRow) options.onRow(result, index);
      }).then(function (results) {
        return spectraJob.then(function (spectraResults) {
          var ads = results.filter(function (r) { return r.key === "ads"; })[0];
          return {fetchedAt: new Date().toISOString(), relay: relay, fresh: Boolean(options.fresh), results: results, baseline: base,
            photometry: results.filter(function (r) { return r.kind === "photometry" && r.status !== "not-applicable"; }),
            spectra: spectraResults, papers: ads ? {papers: ads.papers || [], note: ads.status === "ok" || ads.status === "none" ? "" : ads.note, query: ads.how} : null,
            candidate: candidate};
        });
      });
    });
  }

  // ---------------------------------------------------------------- memory (this browser only)
  // The last summary this browser saw for a target, so a later visit can say what changed.
  // A per-viewer convenience in localStorage; never sent anywhere.
  var SEEN_PREFIX = "ctas-live-seen:";
  function storage() { try { return root.localStorage || null; } catch (_) { return null; } }
  function readSeen(eventId) {
    try { var s = storage(); var raw = s && s.getItem(SEEN_PREFIX + eventId); return raw ? JSON.parse(raw) : null; } catch (_) { return null; }
  }
  function summarize(result) {
    var rows = {};
    (result.results || []).forEach(function (r) { rows[r.key] = {status: r.status, count: Number(r.count || 0), latest: r.latest || null}; });
    return {at: result.fetchedAt, rows: rows};
  }
  function writeSeen(eventId, result) {
    try { var s = storage(); if (s) s.setItem(SEEN_PREFIX + eventId, JSON.stringify(summarize(result))); } catch (_) { /* private mode or full: just no memory */ }
  }
  // Differences between a fresh row and the remembered one: "" when nothing changed.
  function changeSinceSeen(result, seen) {
    if (!seen || !seen.rows || !seen.rows[result.key]) return "";
    var was = seen.rows[result.key];
    if (was.status !== result.status && (result.status === "ok" || result.status === "none") && (was.status === "ok" || was.status === "none")) {
      return "changed since you last looked (" + seen.at.slice(0, 10) + "): " + (result.status === "ok" ? "now " + Number(result.count).toLocaleString() : "now none");
    }
    if (result.status !== "ok") return "";
    if (Number(result.count) !== was.count) return "changed since you last looked (" + seen.at.slice(0, 10) + "): was " + was.count.toLocaleString() + ", now " + Number(result.count).toLocaleString();
    if (result.latest && was.latest && result.latest > was.latest) return "newer since you last looked (" + seen.at.slice(0, 10) + "): latest now " + result.latest.slice(0, 10);
    return "";
  }

  // ---------------------------------------------------------------- page panel
  var candidates = {};
  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function plural(unit, n) {
    if (n === 1) return /^public spectra$/.test(unit) ? "public spectrum" : unit.replace(/ies$/, "y").replace(/s$/, "");
    return unit;
  }
  function clock(iso) { return iso ? iso.slice(11, 19) + " UTC" : ""; }
  function day(iso) { return iso ? String(iso).slice(0, 10) : ""; }
  function releaseAskedText(spec, base) {
    var fam = familyBaseline(base, spec.family);
    if (spec.kind === "link") return fam.checkedAt ? "release last asked " + day(fam.checkedAt) : "";
    if (!fam.checkedAt && fam.retained === null) return "the release never asked this source for this record";
    return "release last asked " + (fam.checkedAt ? day(fam.checkedAt) : "(date not recorded)") + (fam.retained !== null ? " · " + fam.retained.toLocaleString() + " retained" : "");
  }
  function summaryText(result) {
    var n = Number(result.count || 0);
    switch (result.status) {
      case "ok": return n.toLocaleString() + " " + plural(result.unit, n);
      case "none": return result.note || "none found";
      case "no-relay": case "needs-key": case "unavailable": case "not-applicable": return result.note;
      case "proprietary": return "Proprietary at the provider: " + result.note;
      case "link": return "Link only — " + result.note;
      default: return result.note || result.status;
    }
  }
  function rowHtml(spec, result, base, seen) {
    var body;
    if (!result) {
      body = '<span class="ctas-live__state">not checked yet</span> <small class="ctas-live__meta">' + esc(releaseAskedText(spec, base)) + "</small>";
    } else if (result.status === "pending") {
      body = '<span class="ctas-live__state">checking…</span>';
    } else {
      var cmp = result.comparison || {state: "unknown", text: ""};
      var changed = changeSinceSeen(result, seen);
      body = '<span class="ctas-live__state">' + esc(summaryText(result)) + "</span>" +
        (result.how ? " <small>(" + esc(result.how) + ")</small>" : "") +
        (result.link ? ' <a href="' + esc(result.link) + '" target="_blank" rel="noopener">open at source<span class="sr-only"> (opens in a new tab)</span></a>' : "") +
        (cmp.state === "new" ? ' <mark class="ctas-live__new">New since the release: ' + esc(cmp.text) + "</mark>" : cmp.text ? ' <small class="ctas-live__same">' + esc(cmp.text) + "</small>" : "") +
        (changed ? ' <mark class="ctas-live__changed">' + esc(changed) + "</mark>" : "") +
        (result.context ? "<br><small>Lasair Sherlock context: <strong>" + esc(result.context.label) + "</strong>" + (result.context.text ? " — " + esc(result.context.text) : "") + "</small>" : "") +
        (result.items && result.items.length && result.kind === "catalog" ? "<br><small>" + esc(result.items.slice(0, 4).map(function (it) { return [it.name, it.type, it.sep !== null && it.sep !== undefined && isFinite(it.sep) ? Number(it.sep).toFixed(1) + "″" : ""].filter(Boolean).join(" · "); }).join("; ")) + (result.items.length > 4 ? "; …" : "") + "</small>" : "") +
        (result.status !== "link" ? '<br><small class="ctas-live__meta">fetched <time datetime="' + esc(result.fetchedAt) + '">' + esc(clock(result.fetchedAt)) + "</time> · " + esc(releaseAskedText(spec, base)) + "</small>" : "");
    }
    return "<strong>" + esc(spec.label) + "</strong> — " + body;
  }
  function panel(candidate) {
    if (!candidate || !candidate.event_id) return "";
    candidates[candidate.event_id] = candidate;
    var base = releaseBaseline(candidate), seen = readSeen(candidate.event_id);
    var rows = SOURCES.map(function (spec) {
      return '<li data-live-row="' + esc(spec.key) + '" data-live-status="idle">' + rowHtml(spec, null, base, seen) + "</li>";
    }).join("");
    return '<details class="ctas-evidence-panel ctas-live" data-live-panel="' + esc(candidate.event_id) + '" data-dossier-view="live">' +
      "<summary>Live data from the sources <small>" + SOURCES.length + " sources, checked on request, not stored</small></summary><div class=\"ctas-evidence-panel__body\">" +
      "<p>Ask every public source this page can reach for this object’s current light curves, spectra, catalogue neighbours, X-ray pointings and papers: ZTF (ALeRCE and the IRSA data release), Rubin (Fink and Lasair), Gaia Science Alerts, " +
      "Pan-STARRS, WISeREP, SIMBAD, NED, Gaia DR3, HEASARC, AAVSO VSX and NASA ADS, with links for the sources no web page can read. Each row says when it was fetched, what came back, and whether that is <strong>new since the release</strong> " +
      "compared with the evidence retained in this record. Nothing is saved by CTAS; values are shown as the providers return them now and are <strong>not part of the checksum-verified snapshot</strong> above." +
      (seen && seen.at ? " This browser last checked this record on " + esc(day(seen.at)) + "; rows that differ are marked." : "") + "</p>" +
      '<div class="ctas-evidence-tools"><button type="button" data-live-fetch="' + esc(candidate.event_id) + '">Check all sources now</button>' +
      '<span class="ctas-live__progress" data-live-progress role="status" aria-live="polite"></span></div>' +
      '<div data-live-results><ol class="ctas-live__sources" data-live-rows>' + rows + "</ol><div data-live-extras></div></div></div></details>";
  }
  function csv(rows) {
    var keys = ["observed_at", "band", "magnitude", "magnitude_error", "limiting_magnitude", "flux", "flux_error", "flux_unit", "magnitude_system", "photometry_method", "provider"];
    return "# Live provider data fetched " + new Date().toISOString() + " by the CTAS page; not part of the verified snapshot\n" + keys.join(",") + "\n" +
      rows.map(function (row) { return keys.map(function (k) { var v = row[k]; return v === null || v === undefined ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : v; }).join(","); }).join("\n");
  }
  // The combined plot, spectra, paper list and footer, drawn once every source has answered.
  function renderExtras(result) {
    var render = root.CTASRender || {};
    var rows = [], plotted = [];
    result.photometry.forEach(function (item) { rows = rows.concat(item.rows || []); });
    plotted = rows.filter(function (row) { return row.plot !== false; });
    var html = "";
    if (plotted.length && render.photometrySvg) {
      var candidate = result.candidate || {};
      html += render.photometrySvg(plotted.sort(function (a, b) { return a.observed_at < b.observed_at ? -1 : 1; }),
        {discovery: candidate.discovery_time, key: "live-" + String(candidate.event_id || "").slice(0, 8), name: (candidate.name || "this candidate") + " (live provider data)"}) +
        '<button type="button" data-live-csv>Download these live measurements (CSV)</button>';
    }
    (result.spectra || []).forEach(function (item, index) {
      var label = item.row.file_name || item.row.provider_spectrum_id || "TNS spectrum";
      html += "<h5>Spectrum: " + esc(label) + " <small>(TNS public " + (item.parsed && item.parsed.format === "fits" ? "FITS " : "") + "file" + (item.row.instrument ? ", " + esc(item.row.instrument) : "") + ")</small></h5>" +
        (item.parsed && item.parsed.ok && render.liveSpectrum ? render.liveSpectrum(item.row, item.parsed, result.candidate, "live-" + index, result.fetchedAt)
          : item.points.length && render.spectrumSvg ? render.spectrumSvg(item.row, item.points, "live-" + index) : "<p>" + esc(item.note) + "</p>");
    });
    if (result.papers) {
      var p = result.papers;
      html += "<h5>Papers and reports (NASA ADS)</h5>" + (p.papers && p.papers.length
        ? "<ol class=\"ctas-live__papers\">" + p.papers.map(function (paper) {
            return '<li><a href="' + esc(paper.url) + '" target="_blank" rel="noopener">' + esc(paper.title || paper.bibcode) + '<span class="sr-only"> (opens in a new tab)</span></a> <small>' +
              esc([paper.authors, paper.date, paper.type].filter(Boolean).join(" · ")) + "</small></li>";
          }).join("") + "</ol><p><small>ADS " + esc(p.query) + "; a match means the name appears in the paper, not that the paper is about this object.</small></p>"
        : "<p>" + esc(p.note || "No papers found in ADS for this object’s names.") + "</p>");
    }
    if (!result.relay) html += "<p class=\"ctas-link-empty\">Gaia Science Alerts, Pan-STARRS, IRSA, WISeREP, NED, Gaia DR3, HEASARC, VSX, Lasair, ADS and TNS spectrum files need the site’s live-data relay, which is not configured yet.</p>";
    html += "<p><small>Fetched " + esc(result.fetchedAt) + (result.fresh ? " (this browser’s cache bypassed; the relay may still answer from its one-hour cache)" : "") +
      ". Provider values are unmodified apart from unit conversion where labelled (Rubin and Pan-STARRS fluxes to AB magnitudes). Matching by position uses a " + MATCH_ARCSEC + "″ radius for photometry and can pick up an unrelated neighbour; " +
      "“new since the release” compares counts and latest dates with the evidence retained in this record, not with the provider’s own history.</small></p>";
    return {html: html, rows: plotted};
  }
  // The whole "Check all sources now" cycle for one panel element.
  function checkAll(panelNode, candidate, fresh) {
    var button = panelNode.querySelector("[data-live-fetch]"), progress = panelNode.querySelector("[data-live-progress]");
    var extras = panelNode.querySelector("[data-live-extras]"), base = releaseBaseline(candidate), seen = readSeen(candidate.event_id);
    var done = 0, news = 0;
    var row = function (key) { return panelNode.querySelector('[data-live-row="' + key + '"]'); };
    button.disabled = true;
    if (extras) extras.innerHTML = "";
    SOURCES.forEach(function (spec) { var li = row(spec.key); if (li) { li.setAttribute("data-live-status", "pending"); li.innerHTML = rowHtml(spec, {status: "pending"}, base, seen); } });
    if (progress) progress.textContent = "Checking " + SOURCES.length + " sources…";
    return load(candidate, {fresh: fresh, onRow: function (result) {
      var spec = sourceByKey(result.key), li = row(result.key);
      done += 1;
      if (result.comparison && result.comparison.state === "new") news += 1;
      if (li && spec) { li.setAttribute("data-live-status", result.status); li.innerHTML = rowHtml(spec, result, base, seen); }
      if (progress) progress.textContent = "Checked " + done + " of " + SOURCES.length + " sources" + (news ? " · " + news + " with something new since the release" : "");
    }}).then(function (result) {
      var rendered = renderExtras(result);
      lastRows = rendered.rows;
      if (extras) extras.innerHTML = rendered.html;
      if (progress) progress.textContent = "Checked all " + SOURCES.length + " sources at " + clock(result.fetchedAt) + (news ? " · " + news + " with something new since the release" : " · nothing newer than the release was found");
      writeSeen(candidate.event_id, result);
      return result;
    }).catch(function (error) {
      if (extras) extras.innerHTML = "<p>Live data could not be fetched: " + esc(error && error.message || error) + "</p>";
      if (progress) progress.textContent = "The check did not finish.";
    }).finally(function () { button.disabled = false; button.textContent = "Check again (bypass this browser’s cache)"; button.setAttribute("data-live-fresh", "1"); });
  }
  var lastRows = [];
  if (root.document) {
    root.document.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest("[data-live-fetch]");
      if (button) {
        var candidate = candidates[button.getAttribute("data-live-fetch")];
        var panelNode = button.closest("[data-live-panel]");
        if (!candidate || !panelNode) return;
        checkAll(panelNode, candidate, button.getAttribute("data-live-fresh") === "1");
        return;
      }
      if (event.target.closest && event.target.closest("[data-live-csv]") && lastRows.length) {
        var url = URL.createObjectURL(new Blob([csv(lastRows)], {type: "text/csv"}));
        var a = root.document.createElement("a");
        a.href = url; a.download = "ctas-live-photometry.csv"; a.click();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      }
    });
  }

  var api = {panel: panel, load: load, SOURCES: SOURCES, runSource: runSource, pool: pool, releaseBaseline: releaseBaseline, compareWithRelease: compareWithRelease,
    parseCsv: parseCsv, parseIrsaZtf: parseIrsaZtf, nearestGaiaAlert: nearestGaiaAlert, parseVsx: parseVsx, tapRows: tapRows, changeSinceSeen: changeSinceSeen, summarize: summarize, rowHtml: rowHtml, checkAll: checkAll,
    parseAlerceLightcurve: parseAlerceLightcurve, nearestAlerceObject: nearestAlerceObject,
    parseFinkSources: parseFinkSources, parseGaiaCsv: parseGaiaCsv, parsePanstarrs: parsePanstarrs,
    parseAsciiSpectrum: parseAsciiSpectrum, fetchViaRelay: fetchViaRelay, fetchSpectrumFile: fetchSpectrumFile, readSpectrumBytes: readSpectrumBytes, parseLasairObject: parseLasairObject, parseLasairText: parseLasairText, lasairNearest: lasairNearest,
    sherlockSummary: sherlockSummary, adsQuery: adsQuery, parseAds: parseAds, separationArcsec: separationArcsec, mjdToIso: mjdToIso, relayUrl: relayUrl, csv: csv};
  root.CTASLive = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
