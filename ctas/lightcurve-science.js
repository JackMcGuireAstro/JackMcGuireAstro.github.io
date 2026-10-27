/* CTAS light-curve science and broker classes in the dossier (Framework v2 Track 3c, part 1).
   Draws the candidate's lightcurve_science block (scripts/ctas_lightcurve_science.py) on the
   photometry panel, and its broker_classes block (scripts/ctas_broker_classes.py) as a
   "Broker classes" panel: every broker's own outputs side by side, CTAS's stated reading of
   each, and a consensus line that compares labels and averages nothing. Every panel is absent
   when its block is absent. No dependencies, so the same file runs in the page and under node. */
(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASLightcurveScience = model;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var LC_SCHEMA = "ctas.lightcurve-science@1";
  var BROKER_SCHEMA = "ctas.broker-classes@1";
  var BAND_LABELS = {ztfg: "ZTF g", ztfr: "ZTF r", ztfi: "ZTF i", atlasc: "ATLAS c", atlaso: "ATLAS o"};
  var RUN_LABELS = {
    "multiband-bazin": "Multiband Bazin fit", "multiband-villar": "Multiband Villar fit",
    "lightcurve-features": "Light-curve features", "absolute-magnitude": "Peak absolute magnitude",
    "jump-detector": "Jump detector", periodogram: "Lomb–Scargle periodogram", salt3: "SALT3 fit",
    screens: "Screens"
  };
  var OUTCOME_LABELS = {
    "searched-with-data": "answered", "searched-no-match": "no object", ambiguous: "ambiguous",
    "temporarily-unavailable": "unavailable", "provider-failure": "provider failure"
  };

  function esc(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function finite(value) { return typeof value === "number" && isFinite(value); }
  function fixed(value, digits) { return finite(value) ? value.toFixed(digits) : "—"; }
  function pm(value, sigma, digits) {
    return finite(value) ? value.toFixed(digits) + (finite(sigma) ? " ± " + sigma.toFixed(digits) : "") : "—";
  }
  function mjdDate(mjd) {
    if (!finite(mjd)) return "—";
    var date = new Date((mjd - 40587) * 86400000);
    return isNaN(date.getTime()) ? "—" : date.toISOString().slice(0, 10) + " (MJD " + mjd.toFixed(2) + ")";
  }
  function bandLabel(key) { return BAND_LABELS[key] || key; }

  function lcRuns(candidate) {
    var block = candidate && candidate.lightcurve_science;
    return block && block.schema === LC_SCHEMA && Array.isArray(block.runs) ? block.runs : [];
  }
  function run(candidate, type) {
    return lcRuns(candidate).find(function (row) { return row.analysisType === type; }) || null;
  }
  function complete(row) { return row && row.status === "complete" && row.result; }

  function provenance(row) {
    return '<small class="ctas-screen__provenance">' + esc(row.methodName) + " v" + esc(row.methodVersion) +
      " · inputs sha256 <code>" + esc(String(row.inputChecksumSha256 || "").slice(0, 12)) + "…</code>" +
      (row.resultChecksumSha256 ? " · result sha256 <code>" + esc(String(row.resultChecksumSha256).slice(0, 12)) + "…</code>" : "") + "</small>";
  }
  function warnings(row) {
    return (row.warnings || []).length ? '<ul class="ctas-screen__warnings">' + row.warnings.map(function (text) {
      return "<li>" + esc(text) + "</li>";
    }).join("") + "</ul>" : "";
  }

  // ------------------------------------------------------------ light-curve science
  function featuresTable(features, absolute) {
    var bands = Object.keys(features.bands || {}).sort();
    var absBands = (absolute && absolute.bands) || {};
    var rows = bands.map(function (band) {
      var entry = features.bands[band], abs = absBands[band] || {};
      return "<tr><th scope=\"row\">" + esc(bandLabel(band)) + "</th><td>" + pm(entry.peakMagnitude, entry.peakMagnitudeSigma, 2) +
        (entry.peakBracketedByData ? "" : " <small>(extrapolated)</small>") + "</td><td>" +
        (finite(abs.peakAbsoluteMagnitude) ? pm(abs.peakAbsoluteMagnitude, abs.peakAbsoluteMagnitudeSigma, 2) + " <small>A = " + fixed(abs.extinctionMag, 2) + "</small>" : "—") +
        "</td></tr>";
    }).join("");
    return '<div class="ctas-evidence-table-wrap" role="region" aria-label="Peak magnitudes by band" tabindex="0"><table class="ctas-evidence-table">' +
      "<caption>Peak magnitudes from the " + esc(String(features.model || "").replace("multiband-", "")) + " fit (shared shape across bands)</caption>" +
      '<thead><tr><th scope="col">Band</th><th scope="col">Peak apparent (AB)</th><th scope="col">Peak absolute</th></tr></thead><tbody>' +
      rows + "</tbody></table></div>";
  }

  function panel(candidate) {
    var all = lcRuns(candidate);
    if (!all.length) return "";
    var features = run(candidate, "lightcurve-features"), absolute = run(candidate, "absolute-magnitude");
    var jump = run(candidate, "jump-detector"), period = run(candidate, "periodogram");
    var salt = run(candidate, "salt3"), screens = run(candidate, "screens");
    var parts = [];
    if (complete(features)) {
      var f = features.result;
      parts.push('<dl class="ctas-detail__facts">' +
        "<div><dt>Peak</dt><dd>" + esc(mjdDate(f.peakMjd)) + (finite(f.peakMjdSigma) ? " ± " + fixed(f.peakMjdSigma, 1) + " d" : "") + "</dd></div>" +
        "<div><dt>Half-maximum rise</dt><dd>" + pm(f.halfMaxRiseDays, f.halfMaxRiseDaysSigma, 1) + " d</dd></div>" +
        "<div><dt>Half-maximum duration</dt><dd>" + pm(f.halfMaxDurationDays, f.halfMaxDurationDaysSigma, 1) + " d</dd></div>" +
        "<div><dt>Decline over 15 d</dt><dd>" + pm(f.declineMagPerDay15, f.declineMagPerDay15Sigma, 3) + " mag/day</dd></div></dl>" +
        featuresTable(f, complete(absolute) ? absolute.result : null) + provenance(features) + warnings(features));
    }
    if (complete(absolute)) {
      parts.push("<p><small>Absolute magnitude: z = " + esc(absolute.result.redshift) + " (" + esc(absolute.inputs.redshiftSource || "") +
        "), μ = " + fixed(absolute.result.distanceModulus, 2) + " (Planck 2018), E(B−V) = " + fixed(absolute.result.ebvRecalibrated, 3) +
        " (SFD × 0.86, Fitzpatrick 1999), k-term +" + fixed(absolute.result.kCorrectionTerm, 3) + " mag only.</small></p>");
    }
    if (complete(salt)) {
      var p = salt.result.parameters || {};
      parts.push("<p><strong>SALT3</strong>: x1 = " + pm((p.x1 || {}).value, (p.x1 || {}).sigma, 2) + ", c = " + pm((p.c || {}).value, (p.c || {}).sigma, 3) +
        ", peak B = " + fixed(salt.result.peakRestFrameBMagnitudeAB, 2) + " AB, χ²/dof = " + fixed(salt.result.reducedChi2, 2) +
        (salt.result.redshiftFitted ? " (redshift fitted)" : "") + " · <small>" + esc(salt.inputs.selectedBecause || "") + "</small></p>" + warnings(salt));
    }
    if (complete(jump)) {
      parts.push("<p><strong>Jump detector</strong>: " + (jump.result.jump ? "brightening above the 10–70 day baseline" : "no jump") + " · " +
        Object.keys(jump.result.bands).sort().map(function (band) {
          return esc(bandLabel(band)) + " " + fixed(jump.result.bands[band].significance, 1) + "σ";
        }).join(", ") + "</p>");
    }
    if (complete(period)) {
      parts.push("<p><strong>Periodogram</strong>: " + Object.keys(period.result.bands).sort().map(function (band) {
        var entry = period.result.bands[band];
        return esc(bandLabel(band)) + " " + fixed(entry.bestPeriodDays, 3) + " d (FAP " + (finite(entry.falseAlarmProbability) ? entry.falseAlarmProbability.toExponential(1) : "—") +
          (entry.periodicCandidate ? ", periodic candidate" : "") + ")";
      }).join("; ") + "</p>");
    }
    if (complete(screens)) {
      var fast = screens.result.fastTransient || {}, nuclear = screens.result.nuclearTransient || {};
      var flags = [];
      if (fast.flag) flags.push('<span class="pill">Fast-evolving screen</span> ' + esc((fast.drivers || []).join("; ")));
      if (nuclear.flag) flags.push('<span class="pill">Nuclear screen</span> host offset ' + fixed(nuclear.nearestHostOffsetArcsec, 2) + "″");
      if (flags.length) parts.push("<p>" + flags.join(" · ") + ' <small>(screens, not claims)</small></p>');
    }
    var notComputed = all.filter(function (row) { return row.status !== "complete"; });
    var details = '<details class="ctas-lc-science__runs"><summary>All ' + all.length + " runs (" + notComputed.length + " not computed)</summary><ul>" +
      all.map(function (row) {
        return "<li><strong>" + esc(RUN_LABELS[row.analysisType] || row.analysisType) + "</strong> — " +
          (row.status === "complete" ? "computed" : "not computed: " + esc(row.notComputedReason)) + " " + provenance(row) + "</li>";
      }).join("") + "</ul></details>";
    return '<section class="ctas-lc-science" aria-labelledby="ctas-lc-science-title"><h4 id="ctas-lc-science-title">Light-curve science</h4>' +
      (parts.length ? parts.join("") : "<p>No light-curve run could be computed for this event; each run below says why.</p>") + details +
      '<p class="ctas-claim-boundary">' + esc((features || all[0]).claimBoundary) + "</p></section>";
  }

  // ------------------------------------------------------------ broker classes
  function brokerBlock(candidate) {
    var block = candidate && candidate.broker_classes;
    return block && block.schema === BROKER_SCHEMA && Array.isArray(block.brokers) ? block : null;
  }

  function brokerOutput(entry) {
    var output = entry.output;
    if (!output) return entry.dataReceipt && entry.dataReceipt.unreadable ? "kept response unreadable: " + esc(entry.dataReceipt.unreadable) : "—";
    if (entry.source === "alerce-classifiers") {
      return (output.classifiers || []).map(function (classifier) {
        var top = (classifier.classes || []).slice(0, 3).map(function (row) { return esc(row.class) + " " + fixed(row.probability, 2); }).join(", ");
        return "<div><small>" + esc(classifier.classifier) + " " + esc(classifier.version) + "</small>: " + top + "</div>";
      }).join("");
    }
    if (entry.source === "fink-science-scores") {
      return "<div><small>newest of " + esc(output.alerts) + " alerts</small></div>" + (output.scores || []).filter(function (row) {
        return finite(row.value);
      }).map(function (row) { return "<div>" + esc(row.label) + ": " + fixed(row.value, 3) + "</div>"; }).join("");
    }
    var props = output.classifierProperties || {};
    return "<div><small>locus " + esc(output.locusId) + "</small></div><div>tags: " + esc((output.tags || []).join(", ") || "none") + "</div>" +
      Object.keys(props).sort().map(function (key) { return "<div>" + esc(key) + ": " + esc(props[key]) + "</div>"; }).join("");
  }

  function brokerPanel(candidate) {
    var block = brokerBlock(candidate);
    if (!block) return "";
    var consensus = block.consensus || {};
    var stateText = {agree: "readings agree", disagree: "readings disagree", "single-reading": "one reading only",
      "no-broker-reading": "no broker reading"}[consensus.state] || consensus.state;
    var rows = block.brokers.map(function (entry) {
      var reading = entry.reading || {};
      return "<tr><th scope=\"row\">" + esc(entry.broker) + "</th><td>" + esc(OUTCOME_LABELS[entry.currentOutcome] || entry.currentOutcome) +
        "</td><td>" + (reading.coarseClass ? esc(reading.coarseClass) + (reading.brokerClass ? " <small>(" + esc(reading.brokerClass) +
        (finite(reading.probability) ? " " + fixed(reading.probability, 2) : "") + ")</small>" : "") : "—") + "</td><td>" + brokerOutput(entry) + "</td></tr>";
    }).join("");
    return '<section class="ctas-broker-classes" aria-labelledby="ctas-broker-classes-title"><h4 id="ctas-broker-classes-title">Broker classes</h4>' +
      "<p><strong>Consensus:</strong> " + esc(stateText) + (consensus.coarseClassesSeen && consensus.coarseClassesSeen.length ? " — " + esc(consensus.coarseClassesSeen.join(" · ")) : "") +
      (consensus.tnsLabel ? " · TNS: " + esc(consensus.tnsLabel) : "") + "</p>" +
      '<div class="ctas-evidence-table-wrap" role="region" aria-label="Broker classifications side by side" tabindex="0"><table class="ctas-evidence-table">' +
      "<caption>Each broker's own outputs for the retained ZTF object; the reading is CTAS's stated rule, not the broker's word</caption>" +
      '<thead><tr><th scope="col">Broker</th><th scope="col">Latest query</th><th scope="col">Reading</th><th scope="col">Broker outputs</th></tr></thead><tbody>' +
      rows + "</tbody></table></div>" +
      '<details><summary>Rules and credits</summary><p><small>' + esc(consensus.rule) + "</small></p><ul>" +
      block.brokers.map(function (entry) {
        return "<li><small>" + esc(entry.broker) + ": " + esc((entry.reading || {}).rule || "no reading") + " — " + esc(entry.attribution) + "</small></li>";
      }).join("") + "</ul></details>" +
      '<p class="ctas-claim-boundary">' + esc(block.claimBoundary) + "</p></section>";
  }

  return {panel: panel, brokerPanel: brokerPanel, mjdDate: mjdDate};
}));
