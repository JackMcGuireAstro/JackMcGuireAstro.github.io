/* CTAS live source data: fetched from the providers in the visitor's browser when a
 * dossier asks for it. Nothing here is stored by CTAS or on the publishing computer,
 * and none of it is part of the checksum-verified snapshot; every value is labelled
 * with the provider it came from at the moment it was fetched.
 *
 * Sources that allow web pages to read them (ALeRCE for ZTF, Fink for Rubin) are
 * fetched directly. Sources that do not (TNS files, Gaia Alerts, Pan-STARRS at MAST)
 * go through a pass-through relay whose address is read from /live-config.json;
 * without it those sources are reported as unavailable, never guessed.
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
  function parseAsciiSpectrum(textValue) {
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

  // ---------------------------------------------------------------- fetching
  function timed(url, options) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, TIMEOUT_MS) : null;
    var opts = Object.assign({credentials: "omit", referrerPolicy: "strict-origin"}, options || {});
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
  function relayUrl(relay, target) { return relay.replace(/\/+$/, "") + "/?url=" + encodeURIComponent(target); }
  var configPromise = null;
  function loadConfig() {
    if (!configPromise) {
      configPromise = timed("live-config.json", {cache: "no-cache"}).then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return configPromise;
  }

  function ztf(candidate) {
    var oid = findName(candidate, /^ZTF\d{2}[a-z]{7}$/);
    var ra = finite(candidate.ra_deg), dec = finite(candidate.dec_deg);
    var how = oid ? "by its ZTF name " + oid : "by position (within " + MATCH_ARCSEC + "″)";
    var lookup = oid ? Promise.resolve({oid: oid}) : ra === null ? Promise.resolve(null)
      : getJson(ALERCE + "/objects/?ra=" + ra + "&dec=" + dec + "&radius=" + MATCH_ARCSEC + "&page_size=5")
        .then(function (json) { return nearestAlerceObject(json, ra, dec); });
    return lookup.then(function (hit) {
      if (!hit) return {source: "ZTF via ALeRCE", how: how, rows: [], note: "No ZTF object found"};
      return getJson(ALERCE + "/objects/" + encodeURIComponent(hit.oid) + "/lightcurve").then(function (json) {
        var rows = json ? parseAlerceLightcurve(json, hit.oid) : [];
        return {source: "ZTF via ALeRCE", how: oid ? how : "by position: " + hit.oid + " (" + hit.sep.toFixed(2) + "″)", rows: rows,
          link: "https://alerce.online/object/" + encodeURIComponent(hit.oid), note: json ? "" : "ALeRCE has no record of " + hit.oid};
      });
    });
  }
  function rubin(candidate) {
    var ra = finite(candidate.ra_deg), dec = finite(candidate.dec_deg);
    if (ra === null || dec === null) return Promise.resolve({source: "Rubin via Fink", rows: [], note: "No coordinates"});
    return getJson(FINK_LSST + "/conesearch", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ra: ra, dec: dec, radius: 10, "output-format": "json"})}).then(function (list) {
      return {source: "Rubin via Fink", how: "by position (within " + MATCH_ARCSEC + "″)", rows: parseFinkSources(list || [], ra, dec),
        link: "https://lsst.fink-portal.org/"};
    });
  }
  function gaia(candidate, relay) {
    var name = findName(candidate, /^Gaia\d{2}[a-z]{1,4}$/);
    if (!name) return Promise.resolve(null);
    if (!relay) return Promise.resolve({source: "Gaia Science Alerts", rows: [], note: "Needs the live-data relay"});
    var target = "https://gsaweb.ast.cam.ac.uk/alerts/alert/" + name + "/lightcurve.csv";
    return timed(relayUrl(relay, target)).then(function (r) { return r.ok ? r.text() : ""; }).then(function (body) {
      return {source: "Gaia Science Alerts", how: "by its Gaia alert name " + name, rows: parseGaiaCsv(body, name),
        link: "https://gsaweb.ast.cam.ac.uk/alerts/alert/" + name + "/"};
    });
  }
  function panstarrs(candidate, relay) {
    var ra = finite(candidate.ra_deg), dec = finite(candidate.dec_deg);
    if (ra === null || dec === null) return Promise.resolve(null);
    if (!relay) return Promise.resolve({source: "Pan-STARRS1 DR2", rows: [], note: "Needs the live-data relay"});
    var target = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.json?ra=" + ra + "&dec=" + dec +
      "&radius=" + (MATCH_ARCSEC / 3600).toFixed(6) + "&columns=%5BobsTime,filterID,psfFlux,psfFluxErr%5D&pagesize=5000";
    return getJson(relayUrl(relay, target)).then(function (json) {
      return {source: "Pan-STARRS1 DR2", how: "by position (within " + MATCH_ARCSEC + "″); archival 2009–2014 epochs",
        rows: parsePanstarrs(json), link: "https://catalogs.mast.stsci.edu/panstarrs/"};
    });
  }
  function spectra(candidate, relay) {
    var rows = ((candidate.follow_up || {}).spectra || []).filter(function (row) {
      return /^https:\/\/www\.wis-tns\.org\/system\/files\/uploaded\//.test(row.public_download_url || "") &&
        /\.(dat|txt|ascii|asc|csv|flm)$/i.test(row.public_download_url);
    });
    if (!rows.length) return Promise.resolve([]);
    if (!relay) return Promise.resolve(rows.map(function (row) { return {row: row, points: [], note: "Needs the live-data relay"}; }));
    return Promise.all(rows.slice(0, 8).map(function (row) {
      return timed(relayUrl(relay, row.public_download_url)).then(function (r) { return r.ok ? r.text() : ""; })
        .then(function (body) {
          var points = parseAsciiSpectrum(body);
          return {row: row, points: points, note: points.length ? "" : "File could not be read as a two-column spectrum"};
        }).catch(function (error) { return {row: row, points: [], note: "Unavailable: " + error.message}; });
    }));
  }

  function settle(promise, label) {
    return promise.then(function (value) { return value; }, function (error) {
      return {source: label, rows: [], note: "Unavailable right now (" + (error && error.name === "AbortError" ? "timed out" : error.message) + ")"};
    });
  }
  function load(candidate) {
    return loadConfig().then(function (config) {
      var relay = config && typeof config.relay === "string" && /^https:\/\//.test(config.relay) ? config.relay : "";
      return Promise.all([
        settle(ztf(candidate), "ZTF via ALeRCE"), settle(rubin(candidate), "Rubin via Fink"),
        settle(gaia(candidate, relay), "Gaia Science Alerts"), settle(panstarrs(candidate, relay), "Pan-STARRS1 DR2"),
        spectra(candidate, relay).catch(function () { return []; }),
      ]).then(function (results) {
        return {fetchedAt: new Date().toISOString(), relay: relay, photometry: results.slice(0, 4).filter(Boolean), spectra: results[4]};
      });
    });
  }

  // ---------------------------------------------------------------- page panel
  var candidates = {};
  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function panel(candidate) {
    if (!candidate || !candidate.event_id) return "";
    candidates[candidate.event_id] = candidate;
    return '<details class="ctas-evidence-panel ctas-live" data-live-panel="' + esc(candidate.event_id) + '" data-dossier-view="live">' +
      "<summary>Live data from the sources <small>fetched on request, not stored</small></summary><div class=\"ctas-evidence-panel__body\">" +
      "<p>Fetch this object’s current light curves and spectra straight from ZTF (via ALeRCE), Rubin (via Fink), Gaia Science Alerts, " +
      "Pan-STARRS and TNS. Nothing is saved by CTAS; values are shown as the providers return them now, labelled by source, and are " +
      "<strong>not part of the checksum-verified snapshot</strong> above.</p>" +
      '<button type="button" data-live-fetch="' + esc(candidate.event_id) + '">Fetch live data</button>' +
      '<div data-live-results role="status" aria-live="polite"></div></div></details>';
  }
  function csv(rows) {
    var keys = ["observed_at", "band", "magnitude", "magnitude_error", "limiting_magnitude", "flux", "flux_error", "flux_unit", "magnitude_system", "photometry_method", "provider"];
    return "# Live provider data fetched " + new Date().toISOString() + " by the CTAS page; not part of the verified snapshot\n" + keys.join(",") + "\n" +
      rows.map(function (row) { return keys.map(function (k) { var v = row[k]; return v === null || v === undefined ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : v; }).join(","); }).join("\n");
  }
  function renderResults(result) {
    var render = root.CTASRender || {};
    var rows = [];
    var list = result.photometry.map(function (item) {
      rows = rows.concat(item.rows || []);
      var count = (item.rows || []).length;
      return "<li><strong>" + esc(item.source) + "</strong> — " + (count ? count.toLocaleString() + " measurements" : esc(item.note || "no measurements")) +
        (item.how ? " <small>(" + esc(item.how) + ")</small>" : "") + (item.link ? ' <a href="' + esc(item.link) + '" target="_blank" rel="noopener">open at source<span class="sr-only"> (opens in a new tab)</span></a>' : "") + "</li>";
    }).join("");
    var html = "<ul class=\"ctas-live__sources\">" + list + "</ul>";
    if (rows.length && render.photometrySvg) {
      html += render.photometrySvg(rows.sort(function (a, b) { return a.observed_at < b.observed_at ? -1 : 1; })) +
        '<button type="button" data-live-csv>Download these live measurements (CSV)</button>';
    }
    (result.spectra || []).forEach(function (item, index) {
      var label = item.row.file_name || item.row.provider_spectrum_id || "TNS spectrum";
      html += "<h5>Spectrum: " + esc(label) + " <small>(TNS public file" + (item.row.instrument ? ", " + esc(item.row.instrument) : "") + ")</small></h5>" +
        (item.points.length && render.spectrumSvg ? render.spectrumSvg(item.row, item.points, "live-" + index) : "<p>" + esc(item.note) + "</p>");
    });
    if (!result.relay) html += "<p class=\"ctas-link-empty\">Gaia Science Alerts, Pan-STARRS and TNS spectrum files need the site’s live-data relay, which is not configured yet.</p>";
    html += "<p><small>Fetched " + esc(result.fetchedAt) + ". Provider values are unmodified apart from unit conversion where labelled (Rubin and Pan-STARRS fluxes to AB magnitudes). Matching by position uses a " + MATCH_ARCSEC + "″ radius and can pick up an unrelated neighbour.</small></p>";
    return {html: html, rows: rows};
  }
  if (root.document) {
    var lastRows = [];
    root.document.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest("[data-live-fetch]");
      if (button) {
        var candidate = candidates[button.getAttribute("data-live-fetch")];
        var box = button.parentNode.querySelector("[data-live-results]");
        if (!candidate || !box) return;
        button.disabled = true;
        box.innerHTML = "<p>Contacting the providers…</p>";
        load(candidate).then(function (result) {
          var rendered = renderResults(result);
          lastRows = rendered.rows;
          box.innerHTML = rendered.html;
        }).catch(function (error) { box.innerHTML = "<p>Live data could not be fetched: " + esc(error.message) + "</p>"; })
          .finally(function () { button.disabled = false; button.textContent = "Fetch again"; });
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

  var api = {panel: panel, load: load, parseAlerceLightcurve: parseAlerceLightcurve, nearestAlerceObject: nearestAlerceObject,
    parseFinkSources: parseFinkSources, parseGaiaCsv: parseGaiaCsv, parsePanstarrs: parsePanstarrs,
    parseAsciiSpectrum: parseAsciiSpectrum, separationArcsec: separationArcsec, mjdToIso: mjdToIso, relayUrl: relayUrl, csv: csv};
  root.CTASLive = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
