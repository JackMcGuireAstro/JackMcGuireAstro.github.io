/* CTAS multimessenger event page in the dossier (Framework v2 Track 3d). Draws the candidate's
   multimessenger block (scripts/ctas_multimessenger.py): for a gravitational-wave event the LVK
   alert fields, the credible-region MOCs (with an Aladin Lite view of the 10/50/90 % MOCs on
   request), the galaxy ranking and the counterpart registry with exports and revisions; for a
   neutrino, FRB or X-ray notice the published circle and the public optical events inside it.
   Absent when the block is absent. No dependencies, so the same file runs under node. */
(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASMultimessenger = model;
  if (typeof document !== "undefined" && document.addEventListener) model.install(document);
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SCHEMA = "ctas.multimessenger@1";
  var MOC_COLOURS = {"0.1": "#ff4d6d", "0.5": "#ff9f1c", "0.9": "#7bdff2"};
  var KIND_LABELS = {"gravitational-wave": "Gravitational-wave event", neutrino: "Neutrino alert", radio: "Fast radio burst", "x-ray": "X-ray localisation"};
  var PROPERTY_LABELS = {HasNS: "Has a neutron star", HasRemnant: "Has remnant matter", HasMassGap: "Has a mass-gap object", HasSSM: "Has a sub-solar-mass object"};
  var current = {};

  function esc(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function finite(value) { return typeof value === "number" && isFinite(value); }
  function fixed(value, digits) { return finite(value) ? value.toFixed(digits) : "—"; }
  function sci(value) { return finite(value) ? value.toExponential(2) : "—"; }
  function pct(value) { return finite(value) ? (100 * value).toFixed(value < 0.01 && value > 0 ? 2 : 0) + "%" : "not in alert"; }

  function block(candidate) {
    var value = candidate && candidate.multimessenger;
    return value && value.schema === SCHEMA ? value : null;
  }
  function provenance(row) {
    if (!row) return "";
    return '<small class="ctas-screen__provenance">' + esc(row.methodName) + " v" + esc(row.methodVersion) +
      " · inputs sha256 <code>" + esc(String(row.inputChecksumSha256 || "").slice(0, 12)) + "…</code>" +
      (row.resultChecksumSha256 ? " · result sha256 <code>" + esc(String(row.resultChecksumSha256).slice(0, 12)) + "…</code>" : "") +
      (row.completedAt ? " · " + esc(row.completedAt) : "") + "</small>";
  }
  function warnings(row) {
    return row && (row.warnings || []).length ? '<ul class="ctas-screen__warnings">' + row.warnings.map(function (text) {
      return "<li>" + esc(text) + "</li>";
    }).join("") + "</ul>" : "";
  }
  function table(caption, label, head, rows) {
    return '<div class="ctas-evidence-table-wrap" role="region" aria-label="' + esc(label) + '" tabindex="0"><table class="ctas-evidence-table"><caption>' +
      esc(caption) + "</caption><thead><tr>" + head.map(function (h) { return '<th scope="col">' + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + rows.join("") + "</tbody></table></div>";
  }

  // ------------------------------------------------------------ gravitational waves
  function lvkSection(lvk) {
    var classes = Object.keys(lvk.classification || {}).map(function (key) { return esc(key) + " " + pct(lvk.classification[key]); }).join(" · ");
    var props = Object.keys(lvk.properties || {}).map(function (key) {
      return "<li>" + esc(PROPERTY_LABELS[key] || key) + ": " + (finite(lvk.properties[key]) ? pct(lvk.properties[key]) : "not in alert") + "</li>";
    }).join("");
    var coinc = lvk.externalCoincidence ? Object.keys(lvk.externalCoincidence).map(function (key) {
      return esc(key.replace(/_/g, " ")) + ": " + esc(lvk.externalCoincidence[key]);
    }).join(" · ") : "none reported";
    var history = (lvk.noticeHistory || []).map(function (row) { return esc(row.alertType || "notice") + (row.issuedAt ? " (" + esc(row.issuedAt) + ")" : ""); }).join(" → ");
    return '<dl class="ctas-detail__facts">' +
      "<dt>Significant</dt><dd>" + (lvk.significant === true ? "yes" : lvk.significant === false ? "no (low-significance alert)" : "not stated") + " <small>(" + esc(lvk.significantBasis) + ")</small></dd>" +
      "<dt>False-alarm rate</dt><dd>" + sci(lvk.falseAlarmRateHz) + " Hz · " + (finite(lvk.falseAlarmRatePerYear) && lvk.falseAlarmRatePerYear > 0 ? "1 per " + (1 / lvk.falseAlarmRatePerYear >= 1e4 ? (1 / lvk.falseAlarmRatePerYear).toExponential(1) : fixed(1 / lvk.falseAlarmRatePerYear, 1)) + " yr" : "—") + "</dd>" +
      "<dt>Source classification</dt><dd>" + (classes || "not in alert") + "</dd>" +
      "<dt>Search</dt><dd>" + esc([lvk.group, lvk.search, lvk.pipeline].filter(Boolean).join(" · ") || "not stated") + " · " + esc((lvk.instruments || []).join(", ")) + "</dd>" +
      "<dt>External coincidence</dt><dd>" + coinc + (lvk.combinedSkyMapPublished ? " · combined sky map published" : "") + "</dd>" +
      "<dt>Chirp-mass bins</dt><dd>" + esc(lvk.chirpMassNote) + "</dd>" +
      "<dt>Notices</dt><dd>" + (history || "—") + (lvk.retracted ? ' <span class="pill">retracted</span>' : "") + "</dd>" +
      "</dl><ul>" + props + "</ul>" +
      (lvk.gracedbUrl ? '<p><a href="' + esc(lvk.gracedbUrl) + '" rel="noopener" target="_blank">GraceDB superevent page</a></p>' : "");
  }

  function localizationSection(loc, candidate) {
    if (!loc) return "<p><strong>Localization</strong>: no sky map has been retained for this event.</p>";
    var r = loc.result || {};
    var regions = (r.regions || []).map(function (row) {
      return "<li>" + esc(Math.round(100 * row.credible_level)) + "% region: " + fixed(row.covering_area_sq_deg, 1) + " deg² covered at MOC order " + esc(row.moc_order) +
        " (" + esc(row.cell_count) + " cells)</li>";
    }).join("");
    var revisions = (loc.revisions || []).map(function (row) {
      return "<li>" + esc(row.completedAt) + " · " + esc(row.alertType || "") + (row.combinedMap ? " · combined GW+external map" : "") + " · 90% " + fixed(row.area90SqDeg, 1) + " deg² · map <code>" + esc(String(row.skymapChecksumSha256 || "").slice(0, 10)) + "</code></li>";
    }).join("");
    var stmoc = r.space_time_moc ? "<p><small>Space-Time MOC of the 90% region over " + esc(r.space_time_moc.window_days) + " days from " + esc(r.space_time_moc.window_start) +
      ' — <button type="button" class="ctas-link-button" data-mm-download="stmoc" data-event="' + esc(candidate.event_id) + '">download STMOC (ASCII)</button></small></p>' : "";
    return "<p><strong>Localization</strong>" + (loc.combinedMap ? " (combined GW + external map)" : "") + ": 90% credible area " +
      fixed((r.credible_area_sq_deg || {})["90"], 1) + " deg², 50% " + fixed((r.credible_area_sq_deg || {})["50"], 1) + " deg²" +
      (finite(r.map_distance_mean_mpc) ? " · distance " + fixed(r.map_distance_mean_mpc, 0) + " ± " + fixed(r.map_distance_std_mpc, 0) + " Mpc" : "") + "</p><ul>" + regions + "</ul>" +
      '<p><button type="button" data-mm-aladin="' + esc(candidate.event_id) + '">Show the sky map in Aladin Lite (10/50/90% MOCs)</button> ' +
      '<button type="button" class="ctas-link-button" data-mm-download="moc90" data-event="' + esc(candidate.event_id) + '">download 90% MOC (ASCII)</button></p>' +
      '<div class="ctas-mm-aladin" data-mm-stage="' + esc(candidate.event_id) + '" hidden></div><p data-mm-status="' + esc(candidate.event_id) + '" role="status" aria-live="polite"></p>' +
      stmoc + (revisions ? "<details><summary>Sky-map revisions (" + (loc.revisions || []).length + ")</summary><ul>" + revisions + "</ul></details>" : "") +
      provenance(loc) + warnings(loc) + (loc.claimBoundary ? '<p class="ctas-claim-boundary">' + esc(loc.claimBoundary) + "</p>" : "");
  }

  function galaxySection(gal, candidate) {
    if (!gal) return "<p><strong>Galaxies in the localization</strong>: not computed (no significant sky map retained).</p>";
    var r = gal.result || {}, cat = r.catalogue || {};
    if (!r.galaxies || !r.galaxies.length) {
      return "<p><strong>Galaxies in the localization</strong>: no galaxy of " + esc(cat.name || "the catalogue") + " " + esc(cat.version || "") + " lies in the 90% region" +
        (cat.complete_subset ? "" : ' <span class="pill">incomplete: galaxies already on file</span>') + ".</p>" + provenance(gal) + warnings(gal);
    }
    var rows = r.galaxies.slice(0, 25).map(function (g) {
      return "<tr><td>" + esc(g.rank_3d === null || g.rank_3d === undefined ? "2-D " + g.rank_2d : g.rank_3d) + '</td><th scope="row">' + esc(g.name) + "</th><td>" + fixed(g.ra_deg, 4) + ", " + fixed(g.dec_deg, 4) +
        "</td><td>" + fixed(g.distance_mpc, 1) + "</td><td>" + fixed(g.credible_level, 3) + "</td><td>" + sci(g.score) + "</td></tr>";
    });
    return "<p><strong>Galaxies in the localization</strong>: " + esc(r.galaxies_in_region) + " in the 90% region (" + esc(r.galaxies_with_distance) + " with a distance) from " +
      esc(cat.name) + " " + esc(cat.version) + (cat.complete_subset ? "" : ' <span class="pill">incomplete: galaxies already on file</span>') + "</p>" +
      table("Galaxies ranked by volume density times " + (cat.weight || "weight"), "Galaxies in the localization", ["Rank", "Galaxy", "RA, Dec (deg)", "Distance (Mpc)", "Credible level", "Score"], rows) +
      '<p><button type="button" class="ctas-link-button" data-mm-download="galaxies" data-event="' + esc(candidate.event_id) + '">download galaxy list (CSV)</button> <small>' + esc(cat.citation || "") + "</small></p>" +
      provenance(gal) + warnings(gal);
  }

  function registrySection(reg, candidate) {
    if (!reg) return "";
    if (reg.status !== "complete") return "<p><strong>Counterpart registry</strong>: " + esc(reg.notComputedReason) + "</p>";
    var r = reg.result || {};
    var rows = (r.rows || []).slice(0, 25).map(function (row) {
      var name = row.state === "not-in-public-catalog" ? esc(row.name || row.eventId) :
        '<button type="button" class="ctas-link-button" data-open-event="' + esc(row.eventId) + '">' + esc(row.name || row.eventId) + "</button>";
      return "<tr><td>" + esc(row.rank2d) + (row.rank3d ? " / " + esc(row.rank3d) : "") + '</td><th scope="row">' + name + "</th><td>" + esc(row.state) +
        (row.priorActivity ? ' <span class="pill">prior activity</span>' : "") + "</td><td>" + fixed(row.credibleLevel, 3) + "</td><td>" + fixed(row.peakMagnitude, 2) +
        "</td><td>" + fixed(row.impliedAbsoluteMagnitude, 1) + "</td></tr>";
    });
    return "<p><strong>Counterpart registry</strong>: " + esc(r.rowCount) + " public optical events in the map's window.</p>" +
      (rows.length ? table("Optical candidates ranked by sky density (2-D) and with distance (3-D)", "Counterpart registry", ["Rank 2-D / 3-D", "Event", "State", "Credible level", "Peak mag", "Implied M at map distance"], rows) : "") +
      '<p><button type="button" class="ctas-link-button" data-mm-download="registry" data-event="' + esc(candidate.event_id) + '">download registry (CSV)</button> <small>' + esc(r.treasureMap) + "</small></p>" +
      provenance(reg) + warnings(reg) + '<p class="ctas-claim-boundary">' + esc(reg.claimBoundary) + "</p>";
  }

  // ------------------------------------------------------------ circles
  function circleSection(value, candidate) {
    var region = value.region, run = value.candidates || {}, r = run.result || {};
    var rows = (r.rows || []).slice(0, 25).map(function (row) {
      return '<tr><th scope="row"><button type="button" class="ctas-link-button" data-open-event="' + esc(row.eventId) + '">' + esc(row.name || row.eventId) + "</button></th><td>" +
        esc(row.state) + "</td><td>" + fixed(row.separationDeg, 3) + "</td><td>" + fixed(row.timeOffsetDays, 2) + "</td><td>" + fixed(row.peakMagnitude, 2) + "</td></tr>";
    });
    return "<p><strong>Published region</strong>: centre " + fixed(region.centerRaDeg, 4) + ", " + fixed(region.centerDecDeg, 4) + " · radius " + fixed(region.radiusDeg, 3) + "° (" +
      esc(region.containmentBasis) + (finite(region.containment) ? ", " + pct(region.containment) : "") + ")" +
      (region.ellipse ? " · published error ellipse " + fixed(region.ellipse.semiMajorDeg, 3) + "° × " + fixed(region.ellipse.semiMinorDeg, 3) + "°, drawn as the circle that contains it" : "") + " · area " + fixed(region.areaSqDeg, 2) + " deg² · " + esc(region.instrument || "") + "</p>" +
      '<p><button type="button" data-mm-aladin="' + esc(candidate.event_id) + '">Show the region in Aladin Lite</button></p>' +
      '<div class="ctas-mm-aladin" data-mm-stage="' + esc(candidate.event_id) + '" hidden></div><p data-mm-status="' + esc(candidate.event_id) + '" role="status" aria-live="polite"></p>' +
      "<p><strong>Public optical events inside the region</strong> (discovered " + esc((run.parameters || {}).windowBeforeDays) + " d before to " + esc((run.parameters || {}).windowAfterDays) + " d after): " + esc(r.rowCount) + "</p>" +
      (rows.length ? table("Public CTAS events inside the published circle", "Events inside the region", ["Event", "State", "Separation (deg)", "Δt (days)", "Peak mag"], rows) : "") +
      (rows.length ? '<p><button type="button" class="ctas-link-button" data-mm-download="region" data-event="' + esc(candidate.event_id) + '">download list (CSV)</button></p>' : "") +
      provenance(run) + warnings(run) + '<p class="ctas-claim-boundary">' + esc(run.claimBoundary) + "</p>";
  }

  function panel(candidate) {
    var value = block(candidate);
    if (!value) return "";
    current[candidate.event_id] = {candidate: candidate, block: value};
    var body = value.kind === "gravitational-wave"
      ? lvkSection(value.lvk) + localizationSection(value.localization, candidate) + galaxySection(value.galaxies, candidate) + registrySection(value.counterparts, candidate) +
        (value.refreshPolicy ? "<p><small>" + esc(value.refreshPolicy) + "</small></p>" : "")
      : circleSection(value, candidate);
    return '<section class="ctas-multimessenger" aria-labelledby="ctas-mm-title-' + esc(candidate.event_id) + '"><h4 id="ctas-mm-title-' + esc(candidate.event_id) + '">' +
      esc(KIND_LABELS[value.kind] || "Multimessenger") + "</h4>" + body + "<p><small>" + esc(value.coverage) + "</small></p></section>";
  }

  // ------------------------------------------------------------ exports
  function csv(rows, columns) {
    function cell(v) { var s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
    return columns.join(",") + "\n" + rows.map(function (row) { return columns.map(function (c) { return cell(row[c]); }).join(","); }).join("\n") + "\n";
  }
  function exportPayload(value, kind) {
    var loc = (value.localization || {}).result || {};
    if (kind === "moc90") {
      var region = (loc.regions || []).find(function (row) { return row.credible_level === 0.9; });
      return region ? {name: "moc90.txt", type: "text/plain", body: region.moc_ascii + "\n"} : null;
    }
    if (kind === "stmoc") return loc.space_time_moc ? {name: "stmoc90.txt", type: "text/plain", body: loc.space_time_moc.stmoc_ascii + "\n"} : null;
    if (kind === "galaxies") {
      var g = ((value.galaxies || {}).result || {}).galaxies || [];
      return {name: "galaxies.csv", type: "text/csv", body: csv(g, ["rank_3d", "rank_2d", "name", "source_id", "ra_deg", "dec_deg", "distance_mpc", "distance_error_mpc", "credible_level", "probability_density_per_sr", "volume_density_per_mpc3_sr", "weight", "score"])};
    }
    if (kind === "registry") {
      return {name: "counterpart-registry.csv", type: "text/csv", body: csv(((value.counterparts || {}).result || {}).rows || [],
        ["rank2d", "rank3d", "eventId", "name", "state", "credibleLevel", "probabilityDensityPerSr", "discoveryTime", "peakMagnitude", "impliedAbsoluteMagnitude", "priorActivity"])};
    }
    if (kind === "region") {
      return {name: "region-candidates.csv", type: "text/csv", body: csv(((value.candidates || {}).result || {}).rows || [],
        ["eventId", "name", "state", "separationDeg", "timeOffsetDays", "discoveryTime", "peakMagnitude"])};
    }
    return null;
  }

  function mocOverlays(value) {
    return (((value.localization || {}).result || {}).regions || []).filter(function (row) { return row.moc_json; }).map(function (row) {
      return {level: row.credible_level, json: row.moc_json, color: MOC_COLOURS[String(row.credible_level)] || "#ffffff"};
    });
  }

  function openAladin(document_, eventId) {
    var entry = current[eventId]; if (!entry) return;
    var stage = document_.querySelector('[data-mm-stage="' + eventId + '"]'), status = document_.querySelector('[data-mm-status="' + eventId + '"]');
    var sky = window.CTASSky;
    if (!stage || !sky || !sky.loadAladinRuntime) { if (status) status.textContent = "The sky viewer is not available on this page."; return; }
    stage.hidden = false; status.textContent = "Loading the CDS Aladin Lite runtime (pinned " + sky.ALADIN_VERSION + ")…";
    sky.loadAladinRuntime().then(function (A) {
      var value = entry.block, region = value.region, overlays = mocOverlays(value);
      var target = region ? region.centerRaDeg + " " + region.centerDecDeg : null;
      var fov = region ? Math.max(1, region.radiusDeg * 3) : 60;
      if (!target && overlays.length) {
        var best = ((value.galaxies || {}).result || {}).galaxies || [];
        target = best.length ? best[0].ra_deg + " " + best[0].dec_deg : "0 0";
        fov = Math.min(180, Math.max(5, 3 * Math.sqrt(((value.localization.result || {}).credible_area_sq_deg || {})["90"] || 100)));
      }
      var aladin = A.aladin(stage, {target: target || "0 0", fov: fov, cooFrame: "ICRSd", survey: sky.LAYERS[0].hips, showCooGrid: true});
      overlays.slice().reverse().forEach(function (moc) { aladin.addMOC(A.MOCFromJSON(moc.json, {color: moc.color, opacity: 0.25, lineWidth: 1, name: Math.round(100 * moc.level) + "% region"})); });
      if (region && A.graphicOverlay && A.circle) {
        var overlay = A.graphicOverlay({name: "Published region", color: "#7bdff2", lineWidth: 2}); aladin.addOverlay(overlay);
        overlay.add(A.circle(region.centerRaDeg, region.centerDecDeg, region.radiusDeg, {color: "#7bdff2"}));
      }
      status.textContent = overlays.length ? "MOCs of the 10% (red), 50% (orange) and 90% (blue) credible regions of the newest retained sky map." : "The published circular region.";
    }).catch(function (error) { status.textContent = "Aladin Lite could not be loaded (" + error.message + "). The tables and downloads remain usable."; stage.hidden = true; });
  }

  function install(document_) {
    if (!document_ || document_.__ctasMmInstalled) return;
    document_.__ctasMmInstalled = true;
    document_.addEventListener("click", function (event) {
      var target = event.target && event.target.closest ? event.target : null;
      if (!target) return;
      var aladinButton = target.closest("[data-mm-aladin]");
      if (aladinButton) { aladinButton.disabled = true; openAladin(document_, aladinButton.getAttribute("data-mm-aladin")); return; }
      var download = target.closest("[data-mm-download]");
      if (download) {
        var entry = current[download.getAttribute("data-event")]; if (!entry) return;
        var payload = exportPayload(entry.block, download.getAttribute("data-mm-download")); if (!payload) return;
        var link = document_.createElement("a");
        link.href = URL.createObjectURL(new Blob([payload.body], {type: payload.type}));
        link.download = String(entry.candidate.name || entry.candidate.event_id).replace(/[^A-Za-z0-9_.-]+/g, "_") + "-" + payload.name;
        document_.body.appendChild(link); link.click(); link.remove();
      }
    });
  }

  return {panel: panel, exportPayload: exportPayload, mocOverlays: mocOverlays, csv: csv, install: install};
}));
