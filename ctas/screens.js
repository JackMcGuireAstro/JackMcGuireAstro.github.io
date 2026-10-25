/* CTAS collector screens in the dossier (Framework v2 Track 3a).
   Draws the candidate's analysis_screens block (scripts/ctas_analysis_screens.py): the
   population-deviation badge with the features that drive it, the multiband light-curve and
   colour/temperature screen on the photometry panel, and, for a gravitational-wave event,
   the ranked optical candidates and the WIRO / Red Buttes tiling drafts inside the
   Suggested follow-up card. Every panel is absent when its run is absent. No dependencies,
   so the same file runs in the page and under node. */
(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASScreens = model;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SCHEMA = "ctas.analysis-screens@1";
  var FEATURE_LABELS = {
    discovery_magnitude: "discovery magnitude",
    discovery_to_ingest_hours: "hours from discovery to CTAS receipt",
    log_coordinate_error_arcsec: "position uncertainty (log arcsec)",
    log_distance_mpc: "distance (log Mpc)",
    messenger_count: "number of messengers",
    observation_count: "number of observations"
  };

  function esc(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function finite(value) { return typeof value === "number" && isFinite(value); }
  function fixed(value, digits) { return finite(value) ? value.toFixed(digits) : "—"; }
  function signed(value, digits) { return finite(value) ? (value >= 0 ? "+" : "−") + Math.abs(value).toFixed(digits) : "—"; }
  function sci(value) { return finite(value) ? value.toExponential(2) : "—"; }

  function runs(candidate) {
    var block = candidate && candidate.analysis_screens;
    return block && block.schema === SCHEMA && Array.isArray(block.runs) ? block.runs : [];
  }
  function run(candidate, type) {
    return runs(candidate).find(function (row) { return row.analysisType === type; }) || null;
  }
  function runsOf(candidate, type) {
    return runs(candidate).filter(function (row) { return row.analysisType === type; });
  }

  // The method line every panel carries: what ran, which version, which inputs, which result.
  function provenance(row) {
    return '<p class="ctas-screen__provenance"><small>' + esc(row.methodName) + " v" + esc(row.methodVersion) +
      " · inputs sha256 <code>" + esc(String(row.inputChecksumSha256 || "").slice(0, 12)) + "…</code> · result sha256 <code>" +
      esc(String(row.resultChecksumSha256 || "").slice(0, 12)) + "…</code>" + (row.completedAt ? " · completed " + esc(row.completedAt.slice(0, 16).replace("T", " ")) + " UTC" : "") +
      ((row.omittedResultFields || []).length ? " · not drawn here: " + esc(row.omittedResultFields.join(", ")) : "") + "</small></p>";
  }
  function boundary(row) {
    return '<p class="ctas-claim-boundary">' + esc(row.claimBoundary) + "</p>" +
      ((row.warnings || []).length ? '<ul class="ctas-screen__warnings">' + row.warnings.map(function (text) { return "<li>" + esc(text) + "</li>"; }).join("") + "</ul>" : "");
  }

  // ------------------------------------------------------------ population deviation badge
  function badge(candidate) {
    var row = run(candidate, "population-anomaly");
    if (!row || !row.badge) return "";
    var b = row.badge, features = (b.drivingFeatures || []).map(function (item) {
      return esc(FEATURE_LABELS[item.feature] || item.feature) + " " + signed(item.robustZ, 2) + "σ";
    });
    return '<section class="ctas-screen ctas-screen--anomaly" data-screen="population-anomaly"><p><span class="pill ctas-screen__badge' +
      (b.unusual ? " ctas-screen__badge--unusual" : "") + '" data-unusual="' + (b.unusual ? "true" : "false") + '">' +
      (b.unusual ? "Unusual" : "Not unusual") + " · population deviation " + esc(fixed(b.score, 2)) + "</span> " +
      (features.length ? "Driving features: " + features.join("; ") + "." : "No feature deviation is retained.") +
      " <small>Rule: " + esc(b.rule) + ".</small></p>" + provenance(row) +
      '<details class="ctas-screen__more"><summary>What this score is</summary>' + boundary(row) + "</details></section>";
  }

  // ------------------------------------------------------------ multiband fit and colour screen
  function colourScreen(candidate) {
    var row = run(candidate, "multiband-light-curve");
    if (!row) return "";
    var r = row.result || {}, sed = r.sed_screen || {}, peak = r.peak_ab_magnitudes || {}, colours = r.model_peak_colors_mag || {};
    var unc = r.parameter_uncertainties || {};
    var bands = Object.keys(peak).sort();
    var peakRows = bands.map(function (band) {
      var params = (r.band_parameters || {})[band] || {};
      return "<tr><th scope=\"row\">" + esc(band) + "</th><td>" + esc(fixed(peak[band], 2)) + "</td><td>" + esc(params.point_count === undefined ? "—" : params.point_count) + "</td></tr>";
    }).join("");
    var colourText = Object.keys(colours).sort().map(function (key) { return esc(key) + " " + esc(signed(colours[key], 2)) + " mag"; }).join(" · ");
    var temperature = finite(sed.temperature_k)
      ? Math.round(sed.temperature_k).toLocaleString("en-US") + " K" + (finite(sed.temperature_uncertainty_k) ? " ± " + Math.round(sed.temperature_uncertainty_k).toLocaleString("en-US") + " K" : "")
      : null;
    return '<section class="ctas-screen ctas-screen--colour" data-screen="multiband-light-curve"><h5>Colour and temperature screen</h5>' +
      "<p>Shared-shape fit across " + esc(bands.length) + " band" + (bands.length === 1 ? "" : "s") + ": rise " + esc(fixed(r.tau_rise_days, 1)) +
      (finite(unc.tau_rise_days) ? " ± " + esc(fixed(unc.tau_rise_days, 1)) : "") + " d, fall " + esc(fixed(r.tau_fall_days, 1)) +
      (finite(unc.tau_fall_days) ? " ± " + esc(fixed(unc.tau_fall_days, 1)) : "") + " d, model peak MJD " + esc(fixed(r.model_peak_mjd, 2)) +
      ", reduced χ² " + esc(fixed(r.reduced_chi_square, 2)) + ".</p>" +
      (colourText ? "<p><strong>Model peak colours:</strong> " + colourText + "</p>" : "") +
      (temperature ? "<p><strong>Colour temperature:</strong> " + esc(temperature) + (finite(sed.epoch_mjd) ? " at MJD " + esc(fixed(sed.epoch_mjd, 2)) : "") +
        ((sed.bands || []).length ? " from " + esc(sed.bands.join(", ")) : "") + " <small>(" + esc(sed.temperature_semantics || "observer-frame colour screen") + ")</small></p>"
        : "<p><strong>Colour temperature:</strong> not computed" + (sed.status ? " (" + esc(sed.status) + ")" : "") + ".</p>") +
      (peakRows ? '<div class="ctas-evidence-table-wrap" role="region" aria-label="Model peak magnitudes by band" tabindex="0"><table class="ctas-evidence-table"><caption>Model peak AB magnitude per band (point estimates; no extinction or K-correction).</caption><thead><tr><th scope="col">Band</th><th scope="col">Peak AB mag</th><th scope="col">Points</th></tr></thead><tbody>' + peakRows + "</tbody></table></div>" : "") +
      provenance(row) + boundary(row) + "</section>";
  }

  // ------------------------------------------------------------ gravitational-wave follow-up
  function gwFollowup(candidate) {
    var ranking = run(candidate, "gw-counterpart-ranking"), tilings = runsOf(candidate, "gw-tiling-plan");
    if (!ranking && !tilings.length) return "";
    var retracted = (ranking && ranking.eventRetracted) || tilings.some(function (row) { return row.eventRetracted; });
    var html = '<section class="ctas-screen ctas-screen--gw" data-screen="gw"><h5>Gravitational-wave localization</h5>' +
      (retracted ? '<p class="ctas-screen__retracted" data-gw-retracted><strong>Retracted:</strong> this event is marked retracted; the ranking and tiling below are kept for the record, not for follow-up.</p>' : "");
    if (ranking) {
      var rows = (ranking.result || {}).rankings || [];
      html += "<h6>Optical candidates in the sky map</h6><p>" + esc(rows.length) + " localized record" + (rows.length === 1 ? "" : "s") +
        " in the declared window" + ((ranking.result || {}).ranking_truncated ? " (list truncated by the collector)" : "") + ".</p>" +
        (rows.length ? '<div class="ctas-evidence-table-wrap" role="region" aria-label="Ranked optical candidates" tabindex="0"><table class="ctas-evidence-table" data-gw-ranking><caption>2-D: sky probability density; 3-D: density times the map distance likelihood (records with a retained distance only).</caption><thead><tr><th scope="col">2-D rank</th><th scope="col">3-D rank</th><th scope="col">Record</th><th scope="col">State</th><th scope="col">Credible level</th><th scope="col">Density /sr</th><th scope="col">Distance likelihood</th></tr></thead><tbody>' +
          rows.map(function (row) {
            var state = row.candidateState === "retracted" ? "retracted" : row.candidateState === "public" ? "public" : "not in the public catalog";
            var name = row.candidateState === "not-in-public-catalog" ? esc(row.name || row.eventId)
              : '<button type="button" class="ctas-link-button" data-open-event="' + esc(row.eventId) + '">' + esc(row.name || row.eventId) + "</button>";
            return "<tr" + (row.candidateState === "retracted" ? ' class="is-retracted"' : "") + "><td>" + esc(row.rank2d === null || row.rank2d === undefined ? "—" : row.rank2d) +
              "</td><td>" + esc(row.rank3d === null || row.rank3d === undefined ? "—" : row.rank3d) + "</td><td>" + name + "</td><td>" + esc(state) +
              "</td><td>" + esc(fixed(row.credibleLevel, 3)) + "</td><td>" + esc(sci(row.probabilityDensityPerSr)) + "</td><td>" + esc(sci(row.distanceLikelihood)) + "</td></tr>";
          }).join("") + "</tbody></table></div>" : "") + provenance(ranking) + boundary(ranking);
    }
    tilings.forEach(function (row) {
      var r = row.result || {}, field = (row.parameters || {}).field || {}, tiles = r.tiles || [];
      html += '<h6 data-gw-tiling="' + esc(field.id || "") + '">Tiling draft: ' + esc(field.name || field.id || "field") +
        (field.placeholder ? ' <a class="ctas-followup__badge" href="tools/exposure/#placeholder-note">placeholder field of view</a>' : "") + "</h6>" +
        "<p>" + esc(tiles.length) + " tile" + (tiles.length === 1 ? "" : "s") + " of " + esc(fixed((row.parameters || {}).field_of_view_sq_deg, 3)) +
        " deg² cover " + esc(fixed(100 * (r.cumulative_probability || 0), 1)) + "% of the map probability; target " +
        esc(fixed(100 * ((row.parameters || {}).target_probability || 0), 0)) + "% " + (r.target_probability_reached ? "reached" : "not reached") + "." +
        (field.basis ? " <small>" + esc(field.basis) + ".</small>" : "") + "</p>" +
        (tiles.length ? '<details class="ctas-screen__more"><summary>Tile centres</summary><div class="ctas-evidence-table-wrap" role="region" aria-label="Tile centres" tabindex="0"><table class="ctas-evidence-table"><thead><tr><th scope="col">#</th><th scope="col">RA (deg)</th><th scope="col">Dec (deg)</th><th scope="col">Tile probability</th><th scope="col">Cumulative</th></tr></thead><tbody>' +
          tiles.map(function (tile, index) {
            return "<tr><td>" + (index + 1) + "</td><td>" + esc(fixed(tile.ra_deg, 4)) + "</td><td>" + esc(fixed(tile.dec_deg, 4)) + "</td><td>" +
              esc(fixed(tile.incremental_probability, 4)) + "</td><td>" + esc(fixed(tile.cumulative_probability, 4)) + "</td></tr>";
          }).join("") + "</tbody></table></div></details>" : "") + provenance(row) + boundary(row);
    });
    return html + "</section>";
  }

  return {SCHEMA: SCHEMA, runs: runs, badge: badge, colourScreen: colourScreen, gwFollowup: gwFollowup, esc: esc};
}));
