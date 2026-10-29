/* CTAS observer tools (Framework v2 Track 4): what each record still lacks and which observation
   would close it, prefilled requests (ATLAS and ZTF forced photometry, a Swift ToO), TNS report and
   AstroNote drafts, and the import of an observing log that measures how much of a localisation
   region the observer has covered (with an optional Treasure Map push).

   Everything is computed in the reader's browser from the published dossier. Nothing is sent
   anywhere unless the reader presses a button that says so, no credential is stored, and every
   request or report is a DRAFT the reader reviews and submits with their own account.
   No dependencies; the same file runs in the page and under node. */
(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASObserver = model;
  if (typeof document !== "undefined" && document.addEventListener) model.install(document);
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SCHEMA = "ctas.observer-tools.v1";
  var GAP_RULE = "ctas-information-gap.v1";
  var GAP_RULE_TEXT = "Each missing piece of evidence has a stated weight (classification spectrum 5, counterpart search of a localisation region 5, " +
    "redshift 4, a second band for colour 3, a recent photometric point 3, host context 2). The weight is multiplied by 1 when WIRO or Red Buttes " +
    "could take the resolving observation at the latest magnitude, by 0.5 when only a larger telescope could, and gaps are listed by that product " +
    "(ties in the order above). It ranks what the public record lacks; it is not a scientific priority or an observing request.";
  var CLAIM = "Drafts and estimates from the public record, for the reader to review. Nothing is submitted, stored or requested by CTAS.";
  var ATLAS_ENDPOINT = "https://fallingstar-data.com/forcedphot/queue/";
  var ATLAS_DOCS = "https://fallingstar-data.com/forcedphot/apiguide/";
  var ZTF_ENDPOINT = "https://ztfweb.ipac.caltech.edu/cgi-bin/requestForcedPhotometry.cgi";
  var ZTF_DOCS = "https://irsa.ipac.caltech.edu/data/ZTF/docs/ztf_zfps_userguide.pdf";
  var SWIFT_DOCS = "https://www.swift.psu.edu/too_api/";
  var TNS_BULK_DOCS = "https://www.wis-tns.org/content/tns-getting-started";
  var TREASURE_MAP_API = "https://treasuremap.space/api/v1/pointings";
  var TREASURE_MAP_DOCS = "https://treasuremap.space/documentation";
  var FORCED_DAYS_BEFORE = 50;
  var RECENT_POINT_DAYS = 7;
  var MAX_POINTINGS = 5000;
  var MAX_COVERAGE_CELLS = 400000;

  // ------------------------------------------------------------ helpers
  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c]; });
  }
  function finite(value) { return value !== null && value !== undefined && value !== "" && isFinite(Number(value)); }
  function num(value) { return finite(value) ? Number(value) : null; }
  function round(value, digits) { var f = Math.pow(10, digits); return Math.round(Number(value) * f) / f; }
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;
  function mjdFromMs(ms) { return ms / 86400000 + 40587; }
  function jdFromMs(ms) { return ms / 86400000 + 2440587.5; }
  function msFromIso(value) { var ms = Date.parse(text(value)); return isFinite(ms) ? ms : null; }
  function pad(n, w) { var s = String(Math.floor(Math.abs(n))); while (s.length < w) s = "0" + s; return s; }
  function sexagesimal(deg, hours) {
    var value = hours ? deg / 15 : deg, sign = value < 0 ? "-" : (hours ? "" : "+");
    value = Math.abs(value);
    var a = Math.floor(value), bFloat = (value - a) * 60, b = Math.floor(bFloat), c = (bFloat - b) * 60;
    var cText = c.toFixed(hours ? 3 : 2);
    if (Number(cText) >= 60) { cText = (0).toFixed(hours ? 3 : 2); b += 1; }
    if (b >= 60) { b -= 60; a += 1; }
    return sign + pad(a, 2) + ":" + pad(b, 2) + ":" + (Number(cText) < 10 ? "0" : "") + cText;
  }
  function tnsDateTime(ms) {
    var d = new Date(ms);
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1, 2) + "-" + pad(d.getUTCDate(), 2) + " " + pad(d.getUTCHours(), 2) + ":" + pad(d.getUTCMinutes(), 2) + ":" + pad(d.getUTCSeconds(), 2);
  }
  // The TNS name without its AT/SN prefix ("2026woo"), when the record carries a TNS designation.
  function tnsName(candidate) {
    var rows = (candidate && candidate.designations) || [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (String(row.source_key || row.source || "").toLowerCase() === "tns") {
        var m = /^(?:AT|SN|TDE|FRB|kilonova)?\s*(\d{4}[a-z]{2,4})$/i.exec(text(row.designation).trim());
        if (m) return m[1];
      }
    }
    var named = /^(?:AT|SN)\s*(\d{4}[a-z]{2,4})$/.exec(text(candidate && candidate.name).trim());
    return named ? named[1] : null;
  }

  // ------------------------------------------------------------ what the record holds
  var UNCLASSIFIED = {"": 1, "unclassified": 1, "unknown": 1, "at": 1, "candidate": 1};
  function latestMagnitude(candidate) {
    var block = (candidate && candidate.suggested_follow_up) || {};
    var latest = block.latest_magnitude || {};
    if (finite(latest.value)) return {value: Number(latest.value), band: latest.band || null, observedAt: latest.observed_at || null, basis: latest.basis || null};
    if (finite(candidate && candidate.discovery_magnitude)) return {value: Number(candidate.discovery_magnitude), band: null, observedAt: candidate.discovery_time || null, basis: "discovery"};
    return {value: null, band: null, observedAt: null, basis: null};
  }
  // Photometric bands seen, from the light-curve inputs (ZTF g/r/i, ATLAS c/o) or the detections by band.
  function bands(candidate) {
    var by = (((candidate || {}).lightcurve_science || {}).inputs || {}).detectionsByBand || {};
    var families = {};
    Object.keys(by).forEach(function (key) {
      if (!(Number(by[key]) > 0)) return;
      var m = /([ugrizyco])$/i.exec(key);
      families[m ? m[1].toLowerCase() : key] = (families[m ? m[1].toLowerCase() : key] || 0) + Number(by[key]);
    });
    return families;
  }
  function lastPhotometryMs(candidate) {
    var inputs = ((candidate || {}).lightcurve_science || {}).inputs || {};
    if (finite(inputs.lastMjd)) return (Number(inputs.lastMjd) - 40587) * 86400000;
    var latest = latestMagnitude(candidate);
    return latest.basis === "latest-retained-detection" ? msFromIso(latest.observedAt) : null;
  }
  function redshiftOf(candidate) {
    if (finite(candidate && candidate.redshift)) return {value: Number(candidate.redshift), kind: "transient"};
    if (finite(candidate && candidate.host_redshift)) return {value: Number(candidate.host_redshift), kind: "host"};
    var props = (((candidate || {}).comparison || {}).properties) || [];
    for (var i = 0; i < props.length; i++) {
      if (props[i].code !== "redshift") continue;
      var row = (props[i].rows || []).find(function (r) { return r.state === "active" && finite(r.normalized !== undefined ? r.normalized : r.value); });
      if (row) return {value: Number(row.normalized !== undefined ? row.normalized : row.value), kind: "comparison"};
    }
    var host = hostOf(candidate);
    if (host && finite(host.redshift)) return {value: Number(host.redshift), kind: "probable host"};
    return null;
  }
  function hostOf(candidate) {
    var value = candidate && candidate.host_association;
    var runs = value && Array.isArray(value.runs) ? value.runs : [];
    var run = runs.find(function (r) { return r.analysisType === "host-association" && r.status === "complete" && r.result; });
    if (run && run.result.verdict === "probable" && run.result.probableHost) {
      var best = (run.result.candidates || [])[0] || {};
      var z = (best.redshifts || []).find(function (row) { return finite(row.value); });
      return {name: run.result.probableHost, redshift: z ? Number(z.value) : null, verdict: "probable"};
    }
    if (run) return {name: null, redshift: null, verdict: run.result.verdict};
    if (candidate && candidate.host_name) return {name: candidate.host_name, redshift: num(candidate.host_redshift), verdict: "named"};
    var counts = (candidate && candidate.follow_up_counts) || {};
    return Number(counts.host_context) > 0 ? {name: null, redshift: null, verdict: "context"} : null;
  }
  function isLocalisation(candidate) {
    var mm = candidate && candidate.multimessenger;
    if (mm && mm.schema === "ctas.multimessenger@1") return true;
    var type = text(candidate && candidate.event_type).toLowerCase();
    return /gravitational|neutrino|frb|radio-burst|gamma|x-ray/.test(type);
  }

  // ------------------------------------------------------------ the information-gap ranking
  // `reach(kind)` says who could take the resolving observation: "jack" (WIRO or Red Buttes) or
  // "larger". Without an exposure model, a stated magnitude threshold decides (r ≤ 21.5 imaging,
  // ≤ the spectroscopy limit for a spectrum).
  function reachFor(candidate, ccd) {
    var mag = latestMagnitude(candidate).value;
    var spectroscopy = (ccd && ccd.SPECTROSCOPY) || {wiro: {limit_mag: 19.0, placeholder: true}};
    var specLimit = Math.max.apply(null, Object.keys(spectroscopy).map(function (k) { return finite(spectroscopy[k].limit_mag) ? Number(spectroscopy[k].limit_mag) : -99; }));
    var imagingLimit = 21.5;
    if (ccd && Array.isArray(ccd.TELESCOPES) && ccd.FILTERS && ccd.SKY && typeof ccd.solve === "function") {
      // The faintest r magnitude any profile reaches at S/N 20 within 3 x 600 s, airmass 1.3, grey sky.
      var filter = ccd.FILTERS.find(function (f) { return f.id === "r"; });
      imagingLimit = Math.max.apply(null, ccd.TELESCOPES.map(function (t) {
        var out = ccd.solve("maglim", {exptime: 1800, snr: 20, airmass: 1.3, sky: ccd.SKY.r.grey, diameter: t.diameter, obstruction: t.obstruction,
          throughput: t.throughput.r, extinction: t.extinction.r, plateScale: t.plateScale, seeing: t.seeing, apertureFactor: 1, gain: t.gain,
          readNoise: t.readNoise, dark: t.dark, fullWell: t.fullWell, zeroPoint: filter.zeroPoint});
        return finite(out.mag) ? out.mag : -99;
      }));
    }
    return {
      magnitude: mag, imagingLimit: round(imagingLimit, 2), spectroscopyLimit: specLimit > -99 ? specLimit : null,
      imaging: mag === null ? "unknown" : mag <= imagingLimit ? "jack" : "larger",
      spectrum: mag === null || specLimit <= -99 ? "unknown" : mag <= specLimit ? "jack" : "larger"
    };
  }
  function gaps(candidate, options) {
    options = options || {};
    var now = options.now === undefined ? Date.now() : Number(options.now);
    var reach = reachFor(candidate, options.ccd);
    var out = [];
    function add(id, weight, gap, resolves, kind) {
      var who = kind === "region" ? "jack" : reach[kind] || "unknown";
      var factor = who === "jack" ? 1 : who === "larger" ? 0.5 : 0.75;
      out.push({id: id, weight: weight, score: round(weight * factor, 2), gap: gap, resolves: resolves,
        reach: who, reachText: who === "jack" ? "WIRO or Red Buttes" : who === "larger" ? "a larger telescope" : "depends on the current brightness (none retained)"});
    }
    if (isLocalisation(candidate)) {
      add("counterpart-search", 5, "No optical counterpart is established inside the localisation region.",
        "Tiled imaging of the most probable part of the region (the tiling drafts in Suggested follow-up), then import the observing log below to measure coverage.", "region");
    } else {
      var classification = text(candidate && candidate.classification).trim().toLowerCase();
      var counts = (candidate && candidate.follow_up_counts) || {};
      if (UNCLASSIFIED[classification] !== undefined || !(Number(counts.spectra) > 0)) {
        add("classification-spectrum", 5, UNCLASSIFIED[classification] !== undefined ? "No public classification is retained." : "No public spectrum is retained.",
          "A classification spectrum" + (reach.spectroscopyLimit !== null ? " (practical to about " + reach.spectroscopyLimit.toFixed(1) + " mag with the current WIRO profile)" : "") + ".", "spectrum");
      }
      if (!redshiftOf(candidate)) {
        add("redshift", 4, "No redshift is retained (transient, host or probable host).",
          "A spectrum of the transient (redshift from its lines) or of the probable host galaxy.", "spectrum");
      }
      var seen = Object.keys(bands(candidate));
      if (seen.length < 2) {
        add("second-band", 3, seen.length ? "Only one photometric band (" + seen.join(", ") + ") is retained, so there is no colour." : "No retained photometry has a band the light-curve inputs accept.",
          "Imaging in two bands (g and r) on the same night for a colour.", "imaging");
      }
      var last = lastPhotometryMs(candidate);
      var discovered = msFromIso(candidate && candidate.discovery_time);
      var young = discovered === null || (now - discovered) / 86400000 <= 180;
      if (young && (last === null || (now - last) / 86400000 > RECENT_POINT_DAYS)) {
        add("recent-point", 3, last === null ? "No retained photometric point." : "The newest retained photometric point is " + Math.round((now - last) / 86400000) + " days old.",
          "Photometry tonight to extend the light curve (current brightness and decline rate).", "imaging");
      }
      if (!hostOf(candidate)) {
        add("host-context", 2, "No host galaxy or environment is retained.", "Deep imaging of the field to look for a host, or a host spectrum for its redshift.", "imaging");
      }
    }
    var order = ["classification-spectrum", "counterpart-search", "redshift", "second-band", "recent-point", "host-context"];
    out.sort(function (a, b) { return b.score - a.score || order.indexOf(a.id) - order.indexOf(b.id); });
    out.forEach(function (row, i) { row.rank = i + 1; });
    return {schema: SCHEMA, rule: GAP_RULE, ruleText: GAP_RULE_TEXT, reach: reach, gaps: out};
  }

  // ------------------------------------------------------------ prefilled requests
  function forcedPhotometry(candidate, options) {
    options = options || {};
    var now = options.now === undefined ? Date.now() : Number(options.now);
    var ra = num(candidate && candidate.ra_deg), dec = num(candidate && candidate.dec_deg);
    if (ra === null || dec === null) return null;
    var discovered = msFromIso(candidate.discovery_time);
    var start = (discovered === null ? now - 120 * 86400000 : discovered - FORCED_DAYS_BEFORE * 86400000);
    var atlasParams = {ra: round(ra, 6), dec: round(dec, 6), mjd_min: round(mjdFromMs(start), 3), mjd_max: round(mjdFromMs(now), 3), send_email: "False"};
    var ztfParams = {ra: round(ra, 6), dec: round(dec, 6), jdstart: round(jdFromMs(start), 3), jdend: round(jdFromMs(now), 3), email: "YOUR_ZFPS_EMAIL", userpass: "YOUR_ZFPS_PASSWORD"};
    function query(params) { return Object.keys(params).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); }).join("&"); }
    return {
      window: {start: new Date(start).toISOString(), end: new Date(now).toISOString(), basis: discovered === null ? "the last 120 days" : FORCED_DAYS_BEFORE + " days before discovery to now"},
      atlas: {endpoint: ATLAS_ENDPOINT, docs: ATLAS_DOCS, params: atlasParams,
        curl: 'curl -X POST -H "Authorization: Token $ATLAS_TOKEN" -H "Accept: application/json" --data "' + query(atlasParams) + '" ' + ATLAS_ENDPOINT,
        note: "Set ATLAS_TOKEN to your own ATLAS forced-photometry API token first (see the API guide)."},
      ztf: {endpoint: ZTF_ENDPOINT, docs: ZTF_DOCS, params: ztfParams, url: ZTF_ENDPOINT + "?" + query(ztfParams),
        note: "Replace the e-mail and password with your own ZFPS account; the service's shared HTTP login is in its user guide."}
    };
  }
  function swiftToo(candidate) {
    var ra = num(candidate && candidate.ra_deg), dec = num(candidate && candidate.dec_deg);
    if (ra === null || dec === null) return null;
    var mag = latestMagnitude(candidate);
    var classification = text(candidate.classification).trim() || "Unclassified";
    var name = text(candidate.name || candidate.event_id);
    var request = {
      source_name: name, source_type: UNCLASSIFIED[classification.toLowerCase()] !== undefined ? "Optical transient" : classification,
      ra: round(ra, 5), dec: round(dec, 5), poserr: finite(candidate.coordinate_error_arcsec) ? round(Number(candidate.coordinate_error_arcsec) / 60, 4) : 0.0,
      instrument: "XRT", urgency: 3, obs_type: "Light Curve", opt_mag: mag.value === null ? null : round(mag.value, 2), opt_filt: mag.band || "",
      exp_time_per_visit: 2000, num_of_visits: 1, monitoring_freq: "", uvot_mode: "0x9999", xrt_mode: "PC",
      immediate_objective: "X-ray and UV photometry of " + name + ".",
      science_just: name + " (" + classification + ", discovered " + text(candidate.discovery_time).slice(0, 10) + (mag.value === null ? "" : ", latest " + mag.value.toFixed(2) + " mag" + (mag.band ? " " + mag.band : "")) + "). [Write the scientific justification.]",
      exp_time_just: "[Explain the requested exposure.]"
    };
    return {request: request, docs: SWIFT_DOCS,
      notes: ["Field names follow the Swift TOO request form (swifttools Swift_TOO); review every value, choose the urgency and modes, and submit with your own Swift TOO account.",
        "poserr is in arcminutes; uvot_mode 0x9999 means 'filter of the day' and is a placeholder choice."]};
  }

  // ------------------------------------------------------------ TNS drafts
  function photometryGroup(candidate) {
    var mag = latestMagnitude(candidate);
    return {"0": {obsdate: "", flux: "", flux_error: "", limiting_flux: "", flux_units: "", filter_value: "", instrument_value: "", exptime: "", observer: "",
      comments: mag.value === null ? "" : "Latest public magnitude in the CTAS record: " + mag.value.toFixed(2) + (mag.band ? " (" + mag.band + ")" : "") + "; replace with your own measurement."}};
  }
  function tnsAtReport(candidate) {
    var ra = num(candidate && candidate.ra_deg), dec = num(candidate && candidate.dec_deg);
    if (ra === null || dec === null) return null;
    if (tnsName(candidate)) return {registered: tnsName(candidate), report: null,
      notes: ["Already registered on TNS as " + tnsName(candidate) + ": report new photometry on its TNS page, or send a classification report after a spectrum."]};
    var discovered = msFromIso(candidate.discovery_time);
    var host = hostOf(candidate);
    var report = {at_report: {"0": {
      ra: {value: sexagesimal(ra, true), error: finite(candidate.coordinate_error_arcsec) ? round(Number(candidate.coordinate_error_arcsec), 2) : "", units: "arcsec"},
      dec: {value: sexagesimal(dec, false), error: finite(candidate.coordinate_error_arcsec) ? round(Number(candidate.coordinate_error_arcsec), 2) : "", units: "arcsec"},
      reporting_group_id: "", discovery_data_source_id: "", reporter: "", discovery_datetime: discovered === null ? "" : tnsDateTime(discovered),
      at_type: "1", host_name: host && host.name ? text(host.name) : "", host_redshift: host && finite(host.redshift) ? String(host.redshift) : "",
      transient_redshift: finite(candidate.redshift) ? String(candidate.redshift) : "", internal_name: text(candidate.name), remarks: "",
      non_detection: {obsdate: "", limiting_flux: "", flux_units: "", filter_value: "", instrument_value: "", exptime: "", observer: "", comments: "", archiveid: "", archival_remarks: ""},
      photometry: {photometry_group: photometryGroup(candidate)}
    }}};
    return {registered: null, report: report, docs: TNS_BULK_DOCS,
      notes: ["Bulk-report JSON of the TNS AT report: fill reporting_group_id, discovery_data_source_id, reporter, the non-detection and your photometry (IDs from the TNS lists), then submit with your own TNS bot or test it on the TNS sandbox first.",
        "at_type 1 is PSN (possible supernova); change it if needed."]};
  }
  function tnsClassificationReport(candidate) {
    var name = tnsName(candidate);
    var z = redshiftOf(candidate);
    var report = {classification_report: {"0": {
      name: name || "", classifier: "", objtypeid: "", redshift: z && z.kind === "transient" ? String(z.value) : "", groupid: "", remarks: "",
      spectra: {spectra_group: {"0": {obsdate: "", instrumentid: "", exptime: "", observer: "", reducer: "", spectypeid: "1", ascii_file: "", fits_file: "", remarks: "", spec_proprietary_period: "0"}}}
    }}};
    return {report: report, docs: TNS_BULK_DOCS,
      notes: [name ? "For TNS object " + name + "." : "This record has no TNS name yet: register it with an AT report first.",
        "Fill the classifier, objtypeid and the spectrum's instrument and files from the TNS lists after reducing your spectrum."]};
  }
  function astroNoteDraft(candidate) {
    var name = text(candidate && candidate.name);
    var mag = latestMagnitude(candidate);
    var z = redshiftOf(candidate);
    var host = hostOf(candidate);
    var facts = [];
    if (finite(candidate.ra_deg) && finite(candidate.dec_deg)) facts.push(name + " (RA " + sexagesimal(Number(candidate.ra_deg), true) + ", Dec " + sexagesimal(Number(candidate.dec_deg), false) + ")");
    if (candidate.discovery_time) facts.push("discovered " + text(candidate.discovery_time).slice(0, 10) + (candidate.discovery_survey ? " by " + candidate.discovery_survey : ""));
    if (mag.value !== null) facts.push("latest public magnitude " + mag.value.toFixed(2) + (mag.band ? " (" + mag.band + ")" : ""));
    if (z) facts.push("redshift " + z.value + " (" + z.kind + ")");
    if (host && host.name) facts.push("probable host " + host.name);
    return {schema: "ctas.astronote-draft@1", title: "[Observations of " + name + "]", type: "[Classification / Follow-up / Other]", authors: "[Your name(s), affiliation]",
      related_objects: tnsName(candidate) ? [tnsName(candidate)] : [], body: "Public record (CTAS, " + new Date().toISOString().slice(0, 10) + "): " + facts.join("; ") + ". [Describe your observations, reduction and result.]",
      notes: ["A draft to paste into the TNS AstroNotes form. Only the public facts above are filled in; the observations and the result are yours to write."]};
  }

  // ------------------------------------------------------------ observing log
  var COLUMN_ALIASES = {
    ra: ["ra", "ra_deg", "raj2000", "ra_j2000"], dec: ["dec", "dec_deg", "dej2000", "decj2000", "dec_j2000"],
    time: ["time", "date", "obs_time", "utc", "date_obs", "mjd", "jd"], filter: ["filter", "band"],
    fov: ["fov", "fov_deg", "field_deg"], fov_w: ["fov_w", "fov_width_deg", "width_deg"], fov_h: ["fov_h", "fov_height_deg", "height_deg"],
    radius: ["radius", "radius_deg"], pa: ["pa", "pa_deg", "position_angle"], depth: ["depth", "limmag", "lim_mag", "limiting_mag", "maglim"],
    telescope: ["telescope", "instrument", "site"]
  };
  function splitCsvLine(line) {
    var out = [], cur = "", quoted = false;
    for (var i = 0; i < line.length; i++) {
      var c = line[i];
      if (quoted) { if (c === "\"" && line[i + 1] === "\"") { cur += "\""; i++; } else if (c === "\"") quoted = false; else cur += c; }
      else if (c === "\"") quoted = true; else if (c === ",") { out.push(cur); cur = ""; } else cur += c;
    }
    out.push(cur);
    return out.map(function (s) { return s.trim(); });
  }
  function parseAngle(value, hours) {
    var s = text(value).trim();
    if (s === "") return null;
    if (/^[+-]?\d+(\.\d+)?$/.test(s)) return Number(s);
    var m = /^([+-]?)(\d{1,3})[:\sh°d]\s*(\d{1,2})[:\sm′']\s*(\d{1,2}(?:\.\d+)?)\s*[s″"]?$/.exec(s);
    if (!m) return null;
    var value_ = Number(m[2]) + Number(m[3]) / 60 + Number(m[4]) / 3600;
    return (m[1] === "-" ? -1 : 1) * value_ * (hours ? 15 : 1);
  }
  function parseTime(value) {
    var s = text(value).trim();
    if (s === "") return null;
    if (/^\d+(\.\d+)?$/.test(s)) { var n = Number(s); if (n > 2400000) return (n - 2440587.5) * 86400000; if (n > 30000 && n < 100000) return (n - 40587) * 86400000; return null; }
    var ms = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s.replace(" ", "T") + "Z");
    return isFinite(ms) ? ms : null;
  }
  // CSV with a header row; returns {pointings, errors, columns}. Field-of-view defaults come from the
  // telescope column (wiro/rbo) and the instrument profiles when the row gives no footprint.
  function parseObservationLog(csvText, options) {
    options = options || {};
    var telescopes = options.telescopes || [];
    var lines = text(csvText).replace(/\r/g, "").split("\n").filter(function (l) { return l.trim() !== "" && !/^\s*#/.test(l); });
    if (!lines.length) return {pointings: [], errors: ["The file is empty."], columns: {}};
    var header = splitCsvLine(lines[0]).map(function (h) { return h.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^_+|_+$/g, ""); });
    var columns = {};
    Object.keys(COLUMN_ALIASES).forEach(function (key) {
      var i = header.findIndex(function (h) { return COLUMN_ALIASES[key].indexOf(h) >= 0; });
      if (i >= 0) columns[key] = i;
    });
    if (columns.ra === undefined || columns.dec === undefined) return {pointings: [], errors: ["The header needs ra and dec columns (degrees or sexagesimal)."], columns: columns};
    var pointings = [], errors = [];
    for (var r = 1; r < lines.length && pointings.length < MAX_POINTINGS; r++) {
      var cells = splitCsvLine(lines[r]);
      function cell(key) { return columns[key] === undefined ? "" : cells[columns[key]]; }
      var ra = parseAngle(cell("ra"), /[:hms\s]/.test(cell("ra"))), dec = parseAngle(cell("dec"), false);
      if (ra === null || dec === null || ra < 0 || ra >= 360 || Math.abs(dec) > 90) { errors.push("Row " + (r + 1) + ": ra/dec not understood."); continue; }
      var telescopeId = text(cell("telescope")).toLowerCase();
      var profile = telescopes.find(function (t) { return t.id === telescopeId || (telescopeId && text(t.name).toLowerCase().indexOf(telescopeId) === 0); }) || null;
      var w = num(cell("fov_w")), h = num(cell("fov_h")), side = num(cell("fov")), radius = num(cell("radius"));
      var footprint;
      if (radius !== null && radius > 0) footprint = {kind: "circle", radiusDeg: radius, basis: "row"};
      else if (w !== null && h !== null && w > 0 && h > 0) footprint = {kind: "box", widthDeg: w, heightDeg: h, basis: "row"};
      else if (side !== null && side > 0) footprint = {kind: "box", widthDeg: side, heightDeg: side, basis: "row"};
      else if (profile && profile.fieldOfViewDeg) footprint = {kind: "box", widthDeg: profile.fieldOfViewDeg[0], heightDeg: profile.fieldOfViewDeg[1], basis: profile.id + " profile" + (profile.placeholderFields && profile.placeholderFields.indexOf("fieldOfViewDeg") >= 0 ? " (placeholder field of view)" : "")};
      else { errors.push("Row " + (r + 1) + ": no field of view (give fov, fov_w/fov_h or radius in degrees, or a telescope column with wiro or rbo)."); continue; }
      pointings.push({ra: ra, dec: dec, timeMs: parseTime(cell("time")), filter: cell("filter") || null, depth: num(cell("depth")), pa: num(cell("pa")) || 0, footprint: footprint, row: r + 1});
    }
    if (lines.length - 1 > MAX_POINTINGS) errors.push("Only the first " + MAX_POINTINGS + " rows were read.");
    return {pointings: pointings, errors: errors, columns: columns};
  }

  // ------------------------------------------------------------ HEALPix (NESTED) cell centres
  var JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4], JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];
  // Centre of NESTED cell `ipix` at `order` (≤ 20) as {ra, dec} in degrees (healpix_base nest2xyf + xyf2loc).
  function nestPix2Ang(order, ipix) {
    var nside = Math.pow(2, order), npface = nside * nside;
    var face = Math.floor(ipix / npface), ipf = ipix - face * npface;
    var ix = 0, iy = 0, bit = 1, rest = ipf;
    for (var b = 0; b < order; b++) {
      var two = rest % 4; rest = Math.floor(rest / 4);
      if (two & 1) ix += bit;
      if (two & 2) iy += bit;
      bit *= 2;
    }
    var jr = JRLL[face] * nside - ix - iy - 1, nr, z, kshift;
    if (jr < nside) { nr = jr; z = 1 - (nr * nr) / (3 * npface); kshift = 0; }
    else if (jr > 3 * nside) { nr = 4 * nside - jr; z = (nr * nr) / (3 * npface) - 1; kshift = 0; }
    else { nr = nside; z = ((2 * nside - jr) * 2) / (3 * nside); kshift = (jr - nside) & 1; }
    var jp = Math.floor((JPLL[face] * nr + ix - iy + 1 + kshift) / 2);
    if (jp > 4 * nside) jp -= 4 * nside;
    if (jp < 1) jp += 4 * nside;
    var phi = (jp - (kshift + 1) * 0.5) * ((Math.PI / 2) / nr);
    return {ra: ((phi * R2D) % 360 + 360) % 360, dec: 90 - Math.acos(Math.max(-1, Math.min(1, z))) * R2D};
  }
  function cellAreaSqDeg(order) { return (4 * Math.PI * R2D * R2D) / (12 * Math.pow(4, order)); }

  // ------------------------------------------------------------ coverage
  function makeInside(pointing) {
    var a0 = pointing.ra * D2R, d0 = pointing.dec * D2R, s0 = Math.sin(d0), c0 = Math.cos(d0);
    var fp = pointing.footprint, cosPa = Math.cos((pointing.pa || 0) * D2R), sinPa = Math.sin((pointing.pa || 0) * D2R);
    var reach = fp.kind === "circle" ? fp.radiusDeg : Math.sqrt(fp.widthDeg * fp.widthDeg + fp.heightDeg * fp.heightDeg) / 2;
    var cosReach = Math.cos(Math.min(90, reach * 1.01) * D2R);
    return function (raDeg, decDeg) {
      var a = raDeg * D2R, d = decDeg * D2R, sd = Math.sin(d), cd = Math.cos(d), dra = a - a0;
      var cosc = s0 * sd + c0 * cd * Math.cos(dra);
      if (cosc < cosReach) return false;
      if (fp.kind === "circle") return true;
      var xi = (cd * Math.sin(dra)) / cosc, eta = (c0 * sd - s0 * cd * Math.cos(dra)) / cosc;
      var x = xi * cosPa - eta * sinPa, y = xi * sinPa + eta * cosPa;
      return Math.abs(x) <= Math.tan((fp.widthDeg / 2) * D2R) && Math.abs(y) <= Math.tan((fp.heightDeg / 2) * D2R);
    };
  }
  function anyInside(tests, ra, dec) { for (var i = 0; i < tests.length; i++) if (tests[i](ra, dec)) return true; return false; }
  // The region a multimessenger block publishes: the 90 % MOC of a gravitational-wave sky map, or a circle.
  function regionOf(candidate) {
    var mm = candidate && candidate.multimessenger;
    if (!mm || mm.schema !== "ctas.multimessenger@1") return null;
    if (mm.kind === "gravitational-wave") {
      var regions = (((mm.localization || {}).result || {}).regions) || [];
      var r90 = regions.find(function (r) { return Number(r.credible_level) === 0.9 && r.moc_json; });
      if (!r90) return null;
      var moc = typeof r90.moc_json === "string" ? JSON.parse(r90.moc_json) : r90.moc_json;
      return {kind: "moc", moc: moc, label: "90% credible region", areaSqDeg: num(r90.covering_area_sq_deg)};
    }
    var c = mm.region;
    return c && finite(c.centerRaDeg) && finite(c.centerDecDeg) && finite(c.radiusDeg) ? {kind: "circle", ra: Number(c.centerRaDeg), dec: Number(c.centerDecDeg), radiusDeg: Number(c.radiusDeg), label: "published circle", areaSqDeg: num(c.areaSqDeg)} : null;
  }
  // Fraction of the region inside at least one pointing's footprint. A MOC is refined to equal-area
  // cells small next to the smallest footprint (capped at MAX_COVERAGE_CELLS); a circle is sampled
  // on its tangent plane with area weights. Footprints are boxes (optionally rotated) or circles.
  function coverage(pointings, region) {
    if (!region || !pointings || !pointings.length) return null;
    var tests = pointings.map(makeInside);
    var smallest = Math.min.apply(null, pointings.map(function (p) { return p.footprint.kind === "circle" ? 2 * p.footprint.radiusDeg : Math.min(p.footprint.widthDeg, p.footprint.heightDeg); }));
    if (region.kind === "moc") {
      var orders = Object.keys(region.moc).map(Number).filter(function (o) { return o >= 0 && o <= 20; });
      var baseCells = orders.reduce(function (n, o) { return n + region.moc[o].length * Math.pow(4, Math.max.apply(null, orders) - o); }, 0);
      var maxOrder = Math.max.apply(null, orders);
      var target = maxOrder;
      while (target < 20 && Math.sqrt(cellAreaSqDeg(target)) > smallest / 10 && baseCells * Math.pow(4, target + 1 - maxOrder) <= MAX_COVERAGE_CELLS) target++;
      var total = 0, covered = 0;
      orders.forEach(function (order) {
        var shift = Math.pow(4, Math.max(0, target - order));
        region.moc[order].forEach(function (ipix) {
          for (var k = 0; k < shift; k++) {
            var c = nestPix2Ang(Math.max(order, target), ipix * shift + k);
            total += 1;
            if (anyInside(tests, c.ra, c.dec)) covered += 1;
          }
        });
      });
      var area = total * cellAreaSqDeg(target);
      return {method: "MOC refined to order " + target + " (" + total.toLocaleString("en-US") + " cells of " + round(cellAreaSqDeg(target) * 3600, 1) + " arcmin²)",
        fraction: total ? covered / total : 0, coveredSqDeg: round(covered * cellAreaSqDeg(target), 3), regionSqDeg: round(area, 2), pointings: pointings.length};
    }
    var n = 81, r = region.radiusDeg * D2R, a0 = region.ra * D2R, d0 = region.dec * D2R, s0 = Math.sin(d0), c0 = Math.cos(d0);
    var tanR = Math.tan(r), wTot = 0, wIn = 0;
    for (var i = 0; i < n; i++) {
      for (var j = 0; j < n; j++) {
        var x = (2 * (i + 0.5) / n - 1) * tanR, y = (2 * (j + 0.5) / n - 1) * tanR, rho = Math.sqrt(x * x + y * y);
        var cang = Math.atan(rho);
        if (cang > r) continue;
        var dec = Math.asin(Math.cos(cang) * s0 + (rho ? (y * Math.sin(cang) * c0) / rho : 0));
        var ra = a0 + Math.atan2(x * Math.sin(cang), rho * c0 * Math.cos(cang) - y * s0 * Math.sin(cang));
        var weight = Math.pow(Math.cos(cang), 3);
        wTot += weight;
        if (anyInside(tests, ((ra * R2D) % 360 + 360) % 360, dec * R2D)) wIn += weight;
      }
    }
    var regionArea = 2 * Math.PI * (1 - Math.cos(r)) * R2D * R2D;
    return {method: "circle sampled on its tangent plane (" + n + " × " + n + " grid, area-weighted)", fraction: wTot ? wIn / wTot : 0,
      coveredSqDeg: round((wTot ? wIn / wTot : 0) * regionArea, 3), regionSqDeg: round(regionArea, 3), pointings: pointings.length};
  }

  // Treasure Map pointings (v1 API) for a gravitational-wave event; the token is added only when sent.
  function treasureMapPayload(candidate, pointings, options) {
    options = options || {};
    var mm = candidate && candidate.multimessenger;
    if (!mm || mm.kind !== "gravitational-wave") return null;
    return {graceid: text(candidate.name), pointings: (pointings || []).filter(function (p) { return p.timeMs !== null; }).map(function (p) {
      return {ra: round(p.ra, 6), dec: round(p.dec, 6), band: p.filter || "other", instrumentid: options.instrumentId || "YOUR_INSTRUMENT_ID",
        depth: p.depth, depth_unit: "ab_mag", time: new Date(p.timeMs).toISOString().replace(/\.\d{3}Z$/, ""), status: "completed", pos_angle: p.pa || 0};
    })};
  }

  // ------------------------------------------------------------ the panel
  var current = {};
  function gapsSection(result) {
    if (!result.gaps.length) return "<p>The public record holds a classification, a redshift, colour, a recent point and host context: no gap is listed.</p>";
    return '<ol class="ctas-observer__gaps">' + result.gaps.map(function (row) {
      return '<li data-gap="' + esc(row.id) + '"><strong>' + esc(row.gap) + "</strong> " + esc(row.resolves) + " <small>(" + esc(row.reachText) + "; weight " + row.weight + ", score " + row.score + ")</small></li>";
    }).join("") + "</ol>";
  }
  function panel(candidate, options) {
    options = options || {};
    if (!candidate || !candidate.event_id) return "";
    var ccd = options.ccd || (typeof window !== "undefined" ? window.CTASExposureModel : null);
    var result = gaps(candidate, {now: options.now, ccd: ccd});
    var id = esc(candidate.event_id), base = options.base || "";
    current[candidate.event_id] = {candidate: candidate};
    var hasCoords = finite(candidate.ra_deg) && finite(candidate.dec_deg);
    var tns = tnsName(candidate);
    var region = regionOf(candidate);
    var actions = hasCoords ? '<ul class="ctas-observer__actions">' +
      '<li><a href="' + esc(base) + "tonight/runsheet.html?site=wiro&amp;events=" + encodeURIComponent(candidate.event_id) + '" data-observer-runsheet>Printable run sheet</a> (finder chart, airmass, exposure, standard-star note)</li>' +
      '<li><button type="button" class="ctas-link-button" data-observer-copy="atlas" data-event="' + id + '">copy the ATLAS forced-photometry request (curl)</button> · ' +
      '<button type="button" class="ctas-link-button" data-observer-copy="ztf" data-event="' + id + '">copy the ZTF forced-photometry request (URL)</button></li>' +
      '<li><button type="button" class="ctas-link-button" data-observer-download="swift" data-event="' + id + '">Swift ToO request (JSON)</button> · ' +
      (tns ? "" : '<button type="button" class="ctas-link-button" data-observer-download="tns-at" data-event="' + id + '">TNS AT report draft (JSON)</button> · ') +
      '<button type="button" class="ctas-link-button" data-observer-download="tns-classification" data-event="' + id + '">TNS classification report draft (JSON)</button> · ' +
      '<button type="button" class="ctas-link-button" data-observer-download="astronote" data-event="' + id + '">AstroNote draft (JSON)</button></li></ul>' +
      (tns ? "<p><small>Registered on TNS as " + esc(tns) + ", so no AT report is offered.</small></p>" : "") : "<p>No retained coordinates, so no request can be prefilled.</p>";
    var log = region ? '<section class="ctas-observer__log"><h5>Your observing log</h5>' +
      "<p>Import a CSV of your pointings (header row with <code>ra</code>, <code>dec</code> and optionally <code>time</code>, <code>filter</code>, <code>depth</code>, <code>fov</code> or <code>fov_w</code>/<code>fov_h</code> or <code>radius</code> in degrees, <code>pa</code>, <code>telescope</code> = wiro or rbo) to see how much of the " + esc(region.label) + " you covered. The file is read in this browser only.</p>" +
      '<p><label>Observing log (CSV) <input type="file" accept=".csv,text/csv" data-observer-log="' + id + '"></label></p><div data-observer-coverage="' + id + '" role="status" aria-live="polite"></div></section>' : "";
    return '<section class="ctas-observer" data-observer-panel="' + id + '" aria-labelledby="ctas-observer-title-' + id + '">' +
      '<h5 id="ctas-observer-title-' + id + '">What the record lacks, and which observation closes it</h5>' + gapsSection(result) +
      '<details><summary>How the gaps are ranked</summary><p><small>' + esc(GAP_RULE_TEXT) + " Reach: imaging to r ≈ " + esc(result.reach.imagingLimit) + " mag (S/N 20 in 3 × 600 s, best profile)" +
      (result.reach.spectroscopyLimit !== null ? ", spectrum to " + esc(result.reach.spectroscopyLimit) + " mag" : "") + ", from the instrument profiles (some placeholders).</small></p></details>" +
      "<h5>Requests and reports (drafts)</h5>" + actions + log + '<p class="ctas-claim-boundary">' + esc(CLAIM) + "</p></section>";
  }

  function payloadFor(candidate, kind, options) {
    var safe = String(candidate.name || candidate.event_id).replace(/[^A-Za-z0-9_.-]+/g, "_");
    if (kind === "swift") { var s = swiftToo(candidate); return s ? {name: safe + "-swift-too.json", type: "application/json", body: JSON.stringify(s.request, null, 2) + "\n"} : null; }
    if (kind === "tns-at") { var a = tnsAtReport(candidate); return a && a.report ? {name: safe + "-tns-at-report.json", type: "application/json", body: JSON.stringify(a.report, null, 2) + "\n"} : null; }
    if (kind === "tns-classification") return {name: safe + "-tns-classification-report.json", type: "application/json", body: JSON.stringify(tnsClassificationReport(candidate).report, null, 2) + "\n"};
    if (kind === "astronote") return {name: safe + "-astronote-draft.json", type: "application/json", body: JSON.stringify(astroNoteDraft(candidate), null, 2) + "\n"};
    if (kind === "treasure-map") { var t = treasureMapPayload(candidate, options.pointings, options); return t ? {name: safe + "-treasure-map-pointings.json", type: "application/json", body: JSON.stringify(t, null, 2) + "\n"} : null; }
    return null;
  }
  function download(document_, payload) {
    var link = document_.createElement("a");
    link.href = URL.createObjectURL(new Blob([payload.body], {type: payload.type}));
    link.download = payload.name;
    document_.body.appendChild(link); link.click(); link.remove();
  }
  function coverageHtml(candidate, parsed, result) {
    var tm = candidate.multimessenger && candidate.multimessenger.kind === "gravitational-wave" ? '<p><button type="button" class="ctas-link-button" data-observer-download="treasure-map" data-event="' + esc(candidate.event_id) + '">Treasure Map pointings (JSON)</button> ' +
      '<label>Treasure Map API token <input type="password" autocomplete="off" data-observer-tm-token="' + esc(candidate.event_id) + '"></label> <label>instrument id <input type="text" size="6" data-observer-tm-instrument="' + esc(candidate.event_id) + '"></label> ' +
      '<button type="button" data-observer-tm-send="' + esc(candidate.event_id) + '">Send to Treasure Map</button> <small>(sent straight from this browser to treasuremap.space; the token is not stored)</small></p>' : "";
    return "<p><strong>" + (100 * result.fraction).toFixed(1) + "%</strong> of the " + esc(regionOf(candidate).label) + " (" + esc(result.regionSqDeg) + " deg²) lies inside at least one of your " +
      esc(result.pointings) + " pointings (" + esc(result.coveredSqDeg) + " deg²). <small>" + esc(result.method) + ".</small></p>" +
      (parsed.errors.length ? "<details><summary>" + parsed.errors.length + " row(s) skipped</summary><ul>" + parsed.errors.slice(0, 20).map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("") + "</ul></details>" : "") + tm;
  }
  function install(document_, options) {
    options = options || {};
    if (!document_ || document_.__ctasObserverInstalled) return;
    document_.__ctasObserverInstalled = true;
    var logs = {};
    document_.addEventListener("click", function (event) {
      var target = event.target && event.target.closest ? event.target : null;
      if (!target) return;
      var copy = target.closest("[data-observer-copy]");
      if (copy) {
        var entry = current[copy.getAttribute("data-event")]; if (!entry) return;
        var fp = forcedPhotometry(entry.candidate); if (!fp) return;
        var value = copy.getAttribute("data-observer-copy") === "atlas" ? fp.atlas.curl : fp.ztf.url;
        var done = function () { copy.textContent = "copied — " + (copy.getAttribute("data-observer-copy") === "atlas" ? fp.atlas.note : fp.ztf.note); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(value).then(done, function () { window.prompt("Copy this request:", value); });
        else window.prompt("Copy this request:", value);
        return;
      }
      var dl = target.closest("[data-observer-download]");
      if (dl) {
        var entry2 = current[dl.getAttribute("data-event")]; if (!entry2) return;
        var kind = dl.getAttribute("data-observer-download");
        var instrument = document_.querySelector('[data-observer-tm-instrument="' + dl.getAttribute("data-event") + '"]');
        var payload = payloadFor(entry2.candidate, kind, {pointings: (logs[dl.getAttribute("data-event")] || {}).pointings, instrumentId: instrument && instrument.value ? instrument.value : undefined});
        if (payload) download(document_, payload);
        return;
      }
      var send = target.closest("[data-observer-tm-send]");
      if (send) {
        var eventId = send.getAttribute("data-observer-tm-send"), entry3 = current[eventId], log = logs[eventId];
        var token = document_.querySelector('[data-observer-tm-token="' + eventId + '"]'), inst = document_.querySelector('[data-observer-tm-instrument="' + eventId + '"]');
        if (!entry3 || !log || !token || !token.value || !inst || !inst.value) { send.textContent = "Import a log and enter your token and instrument id first"; return; }
        var body = treasureMapPayload(entry3.candidate, log.pointings, {instrumentId: inst.value});
        body.api_token = token.value;
        send.disabled = true; send.textContent = "Sending…";
        fetch(TREASURE_MAP_API, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}).then(function (r) {
          send.textContent = r.ok ? "Sent (HTTP " + r.status + ")" : "Treasure Map answered HTTP " + r.status + "; use the JSON download instead";
        }).catch(function () { send.textContent = "Could not reach Treasure Map from the browser; use the JSON download (see " + TREASURE_MAP_DOCS + ")"; })
          .then(function () { token.value = ""; send.disabled = false; });
      }
    });
    document_.addEventListener("change", function (event) {
      var input = event.target && event.target.matches && event.target.matches("[data-observer-log]") ? event.target : null;
      if (!input || !input.files || !input.files[0]) return;
      var eventId = input.getAttribute("data-observer-log"), entry = current[eventId];
      var out = document_.querySelector('[data-observer-coverage="' + eventId + '"]');
      if (!entry || !out) return;
      var reader = new FileReader();
      reader.onload = function () {
        var telescopes = ((typeof window !== "undefined" && window.CTASExposureModel) || {}).TELESCOPES || options.telescopes || [];
        var parsed = parseObservationLog(String(reader.result || ""), {telescopes: telescopes});
        if (!parsed.pointings.length) { out.innerHTML = "<p>" + esc(parsed.errors[0] || "No usable pointing.") + "</p>"; return; }
        logs[eventId] = parsed;
        out.innerHTML = coverageHtml(entry.candidate, parsed, coverage(parsed.pointings, regionOf(entry.candidate)));
      };
      reader.readAsText(input.files[0]);
    });
  }

  return {SCHEMA: SCHEMA, GAP_RULE: GAP_RULE, GAP_RULE_TEXT: GAP_RULE_TEXT, CLAIM: CLAIM, gaps: gaps, reachFor: reachFor, latestMagnitude: latestMagnitude,
    bands: bands, redshiftOf: redshiftOf, hostOf: hostOf, tnsName: tnsName, sexagesimal: sexagesimal, forcedPhotometry: forcedPhotometry, swiftToo: swiftToo,
    tnsAtReport: tnsAtReport, tnsClassificationReport: tnsClassificationReport, astroNoteDraft: astroNoteDraft, parseObservationLog: parseObservationLog,
    parseTime: parseTime, parseAngle: parseAngle, nestPix2Ang: nestPix2Ang, cellAreaSqDeg: cellAreaSqDeg, coverage: coverage, regionOf: regionOf,
    treasureMapPayload: treasureMapPayload, panel: panel, payloadFor: payloadFor, install: install};
}));
