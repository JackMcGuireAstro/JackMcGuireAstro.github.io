/* CTAS host association, population anomaly and alert stamps in the dossier (Framework v2
   Track 3c, part 2). Draws the candidate's host_association block
   (scripts/ctas_host_association.py): the Prost-style host posterior over retained catalogued
   galaxies with the host-missed hypothesis, prior activity before discovery, Galactic
   extinction and the Gaia point-source flag, a Gaia colour-magnitude point for a stellar
   counterpart, and a Blast link. On request it reads the release's population-anomaly.json
   (isolation-forest percentile, driving features, nearest neighbours) and shows ZTF alert
   stamps from ALeRCE or Fink through the site's relay (nothing stored). Panels are absent when
   their data are absent. No dependencies, so the same file runs in the page and under node. */
(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASHostAssociation = model;
  if (typeof document !== "undefined" && document.addEventListener) model.install(document);
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SCHEMA = "ctas.host-association@1";
  var POPULATION_SCHEMA = "ctas.population-anomaly@1";
  var POPULATION_PATH = "ctas/data/population-anomaly.json";
  var VERDICTS = {
    probable: "probable host", ambiguous: "ambiguous — no candidate dominates",
    "host-missed-most-probable": "a host not in the retained catalogues is most probable"
  };
  var BASIS = {
    "directional-light-radius": "DLR", "physical-scale": "kpc scale", "angular-scale": "angular scale"
  };
  var FEATURE_LABELS = {
    log10HalfMaxRiseDays: "rise time", log10HalfMaxDurationDays: "duration",
    asinhDecline15: "15-day decline", peakGMinusR: "g − r at peak"
  };
  var populationPromise = null;

  function esc(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function finite(value) { return typeof value === "number" && isFinite(value); }
  function fixed(value, digits) { return finite(value) ? value.toFixed(digits) : "—"; }

  function block(candidate) {
    var value = candidate && candidate.host_association;
    return value && value.schema === SCHEMA && Array.isArray(value.runs) ? value : null;
  }
  function run(value, type) {
    return (value.runs || []).find(function (row) { return row.analysisType === type; }) || null;
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

  function redshiftText(list) {
    if (!list || !list.length) return "—";
    return list.map(function (row) {
      return fixed(row.value, 5) + (finite(row.error) ? " ± " + fixed(row.error, 5) : "") + " <small>(" + esc(row.catalog) +
        (row.reference ? ", " + esc(row.reference) : "") + ")</small>";
    }).join("<br>");
  }

  function posteriorSection(row) {
    if (!row) return "";
    if (!complete(row)) return "<p><strong>Host posterior</strong>: not computed — " + esc(row.notComputedReason) + "</p>";
    var r = row.result;
    var rows = (r.candidates || []).slice(0, 8).map(function (c) {
      var name = c.sourceUrl ? '<a href="' + esc(c.sourceUrl) + '" rel="noopener" target="_blank">' + esc(c.name) + "</a>" : esc(c.name);
      return "<tr><td>" + esc(c.rank) + '</td><th scope="row">' + name + " <small>" + esc((c.types || []).join(", ")) + "</small></th><td>" +
        esc((c.catalogs || []).join(", ")) + "</td><td>" + fixed(c.separationArcsec, 2) + "″</td><td>" + fixed(c.fractionalOffset, 2) +
        " <small>(" + esc(BASIS[c.offsetBasis] || c.offsetBasis) + ")</small></td><td>" + redshiftText(c.redshifts) + "</td><td>" + fixed(c.posterior, 3) + "</td></tr>";
    }).join("");
    var more = (r.candidates || []).length > 8 ? "<p><small>" + ((r.candidates || []).length - 8) + " further candidates have smaller posteriors.</small></p>" : "";
    return "<p><strong>Host posterior</strong>: " + esc(VERDICTS[r.verdict] || r.verdict) +
      (r.probableHost ? " — <strong>" + esc(r.probableHost) + "</strong>" : "") + " · host not catalogued: " + fixed(r.hostMissedPosterior, 3) + "</p>" +
      '<div class="ctas-evidence-table-wrap" role="region" aria-label="Host galaxy candidates" tabindex="0"><table class="ctas-evidence-table">' +
      "<caption>Retained catalogued galaxies ranked by posterior; the offset is in units of the stated scale</caption>" +
      '<thead><tr><th scope="col">Rank</th><th scope="col">Galaxy</th><th scope="col">Catalogues</th><th scope="col">Separation</th>' +
      '<th scope="col">Fractional offset</th><th scope="col">Catalogue redshift</th><th scope="col">Posterior</th></tr></thead><tbody>' +
      rows + "</tbody></table></div>" + more + provenance(row) + warnings(row);
  }

  function priorSection(row) {
    if (!row) return "";
    if (!complete(row)) return "<p><strong>Prior activity</strong>: not computed — " + esc(row.notComputedReason) + "</p>";
    var r = row.result;
    return "<p><strong>Prior activity</strong>: " + (r.flag ? '<span class="pill">detected before discovery</span> ' : "none found · ") +
      esc(r.detectionCount) + " detections on " + esc(r.detectionNights) + " nights among " + esc(r.preDiscoveryEpochs) +
      " retained epochs over " + esc(r.preDiscoveryNights) + " nights more than 30 days before discovery (" + esc((r.providers || []).join(", ")) + ") " +
      provenance(row) + "</p>";
  }

  function extinctionSection(row) {
    if (!row) return "";
    if (!complete(row)) return "<p><strong>Galactic extinction</strong>: not computed — " + esc(row.notComputedReason) + "</p>";
    var r = row.result, point = r.pointSource;
    return "<p><strong>Galactic extinction</strong>: E(B−V) = " + fixed(r.ebvRecalibrated, 3) + " (SFD × 0.86)" +
      (r.highExtinction ? ' <span class="pill">high extinction</span>' : "") + " · <strong>Point source</strong>: " +
      (point ? '<span class="pill">star-like Gaia source</span> Gaia DR3 ' + esc(point.gaiaSourceId) + " at " + fixed(point.separationArcsec, 2) + "″ — " + esc((point.reasons || []).join("; ")) : "no star-like Gaia source at the position") +
      " " + provenance(row) + "</p>" + warnings(row);
  }

  // A small colour-magnitude diagram: the counterpart and, for scale, the Sun.
  function cmdSvg(r) {
    var x0 = -0.5, x1 = 4.0, y0 = -5, y1 = 17, w = 260, h = 200, pad = 34;
    function sx(v) { return pad + (Math.max(x0, Math.min(x1, v)) - x0) / (x1 - x0) * (w - pad - 8); }
    function sy(v) { return 8 + (Math.max(y0, Math.min(y1, v)) - y0) / (y1 - y0) * (h - pad - 8); }
    var ticksX = [0, 1, 2, 3, 4].map(function (v) { return '<text x="' + sx(v) + '" y="' + (h - 16) + '" text-anchor="middle">' + v + "</text>"; }).join("");
    var ticksY = [-5, 0, 5, 10, 15].map(function (v) { return '<text x="' + (pad - 4) + '" y="' + (sy(v) + 4) + '" text-anchor="end">' + v + "</text>"; }).join("");
    return '<figure class="ctas-host-cmd"><svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="Gaia colour-magnitude position of the counterpart: absolute G ' +
      esc(fixed(r.absoluteG, 2)) + ", BP minus RP " + esc(fixed(r.bpMinusRp, 2)) + '">' +
      '<rect x="' + pad + '" y="8" width="' + (w - pad - 8) + '" height="' + (h - pad - 8) + '" fill="none" stroke="currentColor" stroke-opacity="0.4"/>' +
      ticksX + ticksY + '<text x="' + (w / 2) + '" y="' + (h - 2) + '" text-anchor="middle">BP − RP (mag)</text>' +
      '<text x="10" y="' + (h / 2) + '" text-anchor="middle" transform="rotate(-90 10 ' + (h / 2) + ')">M_G (mag)</text>' +
      '<circle cx="' + sx(0.82) + '" cy="' + sy(4.67) + '" r="3" fill="none" stroke="currentColor"><title>The Sun (reference)</title></circle>' +
      '<circle cx="' + sx(r.bpMinusRp) + '" cy="' + sy(r.absoluteG) + '" r="5" fill="currentColor"><title>Counterpart</title></circle></svg>' +
      "<figcaption>Gaia DR3 " + esc(r.gaiaSourceId) + ": M_G = " + fixed(r.absoluteG, 2) + ", BP−RP = " + fixed(r.bpMinusRp, 2) + ", parallax " +
      fixed(r.parallaxMas, 3) + " ± " + fixed(r.parallaxErrorMas, 3) + " mas (" + fixed(r.distancePc, 0) + " pc), no extinction correction; open circle: the Sun.</figcaption></figure>";
  }

  function stellarSection(row) {
    if (!row) return "";
    if (!complete(row)) return "<p><strong>Stellar counterpart</strong>: not computed — " + esc(row.notComputedReason) + "</p>";
    return cmdSvg(row.result) + provenance(row);
  }

  function ztfObject(candidate) {
    var observations = ((candidate.follow_up || {}).observations) || [];
    var best = null;
    observations.forEach(function (row) {
      var match = /^alerce:(ZTF\d{2}[a-z]{7}):(\d{6,20})$/.exec(String(row.provider_observation_id || ""));
      if (match && row.detection && (!best || Number(row.mjd) > best.mjd)) best = {oid: match[1], candid: match[2], mjd: Number(row.mjd)};
    });
    if (best) return best;
    var designations = candidate.designations || [];
    var alias = designations.map(function (row) { return String(row.designation || ""); }).find(function (name) { return /^ZTF\d{2}[a-z]{7}$/.test(name); });
    return alias ? {oid: alias, candid: null, mjd: null} : null;
  }

  function stampsSection(candidate) {
    var ztf = ztfObject(candidate);
    if (!ztf) return "";
    var buttons = (ztf.candid ? '<button type="button" data-ctas-stamps="alerce" data-oid="' + esc(ztf.oid) + '" data-candid="' + esc(ztf.candid) + '">ALeRCE stamps of the latest ZTF alert</button> ' : "") +
      '<button type="button" data-ctas-stamps="fink" data-oid="' + esc(ztf.oid) + '">Fink cutouts of the latest ZTF alert</button>';
    return '<div class="ctas-host-stamps"><p><strong>Alert stamps</strong> (' + esc(ztf.oid) + "): " + buttons + "</p>" +
      '<p data-ctas-stamps-status role="status" aria-live="polite"><small>Fetched on request from the broker through the site’s relay; nothing is stored and the images are not part of the verified snapshot.</small></p>' +
      '<div class="ctas-host-stamps__images" data-ctas-stamps-images></div></div>';
  }

  function panel(candidate) {
    var value = block(candidate);
    var stamps = candidate ? stampsSection(candidate) : "";
    var unusual = candidate && candidate.lightcurve_science ? '<p><button type="button" data-ctas-population="' + esc(candidate.event_id) +
      '">How unusual is this light curve among CTAS events?</button></p><div data-ctas-population-result="' + esc(candidate.event_id) + '"></div>' : "";
    if (!value && !stamps && !unusual) return "";
    var parts = [];
    if (value) {
      parts.push(posteriorSection(run(value, "host-posterior")));
      parts.push(priorSection(run(value, "prior-activity")));
      parts.push(extinctionSection(run(value, "extinction-and-point-source")));
      parts.push(stellarSection(run(value, "stellar-counterpart")));
      (value.externalLinks || []).forEach(function (link) {
        parts.push('<p><a href="' + esc(link.url) + '" rel="noopener" target="_blank">' + esc(link.label) + "</a> <small>" + esc(link.note || "") + "</small></p>");
      });
    }
    var claim = value ? '<p class="ctas-claim-boundary">' + esc((run(value, "host-posterior") || value.runs[0]).claimBoundary) + "</p>" : "";
    return '<section class="ctas-host-association" aria-labelledby="ctas-host-association-title"><h4 id="ctas-host-association-title">Host galaxy and context</h4>' +
      parts.join("") + unusual + stamps + claim + "</section>";
  }

  // ------------------------------------------------------------ population anomaly
  function populationHtml(document_, eventId) {
    if (!document_ || document_.schema !== POPULATION_SCHEMA) return "<p>The release's population file could not be read.</p>";
    if (document_.status !== "complete") return "<p>Not computed for this release: " + esc(document_.notComputedReason) + "</p>";
    var entry = (document_.events || []).find(function (row) { return row.eventId === eventId; });
    if (!entry) return "<p>This event is not in the release's feature table (" + esc(document_.population) + " events have every feature: rise, duration, decline and ZTF g − r at peak).</p>";
    var drivers = (entry.drivers || []).map(function (row) {
      return esc(FEATURE_LABELS[row.feature] || row.feature) + " (" + (finite(row.robustZ) ? (row.robustZ > 0 ? "+" : "") + row.robustZ.toFixed(1) + " robust σ" : "—") + ")";
    }).join(", ");
    var neighbours = (entry.neighbours || []).map(function (row) {
      return '<li><button type="button" class="ctas-link-button" data-open-event="' + esc(row.eventId) + '">' + esc(row.name || row.eventId) + "</button> <small>" +
        esc(row.classification || "unclassified") + " · distance " + fixed(row.distance, 2) + "</small></li>";
    }).join("");
    return "<p><strong>Isolation forest</strong>: rank " + esc(entry.rank) + " of " + esc(document_.population) + " (percentile " + fixed(entry.percentile, 1) + ")" +
      (drivers ? " · driven by " + drivers : "") + '</p><p><strong>Objects like this</strong> (nearest in rise, duration, decline and colour):</p><ul class="ctas-host-neighbours">' + neighbours + "</ul>" +
      '<p><small class="ctas-screen__provenance">' + esc(document_.methodName) + " v" + esc(document_.methodVersion) + " · feature table sha256 <code>" +
      esc(String(document_.featureTableChecksumSha256 || "").slice(0, 12)) + "…</code> · " + esc(document_.claimBoundary) + "</small></p>";
  }

  function loadPopulation(fetcher) {
    if (!populationPromise) {
      populationPromise = fetcher(POPULATION_PATH, {cache: "no-cache"}).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      }).catch(function (error) { populationPromise = null; throw error; });
    }
    return populationPromise;
  }

  function stampUrls(kind, oid, candid) {
    if (kind === "alerce") {
      return ["science", "template", "difference"].map(function (type) {
        return {label: type, url: "https://avro.alerce.online/get_stamp?oid=" + encodeURIComponent(oid) + "&candid=" + encodeURIComponent(candid) + "&type=" + type + "&format=png"};
      });
    }
    return ["Science", "Template", "Difference"].map(function (type) {
      return {label: type.toLowerCase(), url: "https://api.ztf.fink-portal.org/api/v1/cutouts?objectId=" + encodeURIComponent(oid) + "&kind=" + type + "&output-format=PNG"};
    });
  }

  function install(document_) {
    if (!document_ || document_.__ctasHostInstalled) return;
    document_.__ctasHostInstalled = true;
    document_.addEventListener("click", function (event) {
      var target = event.target && event.target.closest ? event.target : null;
      if (!target) return;
      var populationButton = target.closest("[data-ctas-population]");
      if (populationButton) {
        var eventId = populationButton.getAttribute("data-ctas-population");
        var box = document_.querySelector('[data-ctas-population-result="' + eventId + '"]');
        if (!box) return;
        box.innerHTML = '<p role="status">Reading the release’s population file…</p>';
        loadPopulation(window.fetch.bind(window)).then(function (doc) { box.innerHTML = populationHtml(doc, eventId); })
          .catch(function (error) { box.innerHTML = "<p>The population file could not be read (" + esc(error.message) + ").</p>"; });
        return;
      }
      var stampButton = target.closest("[data-ctas-stamps]");
      if (stampButton) {
        var section = stampButton.closest(".ctas-host-stamps");
        var status = section.querySelector("[data-ctas-stamps-status]"), images = section.querySelector("[data-ctas-stamps-images]");
        var kind = stampButton.getAttribute("data-ctas-stamps");
        status.textContent = "Asking " + (kind === "alerce" ? "ALeRCE" : "Fink") + " through the relay…";
        window.fetch("live-config.json", {cache: "no-cache"}).then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; }).then(function (config) {
          var relay = config && typeof config.relay === "string" && /^https:\/\//.test(config.relay) ? config.relay.replace(/\/+$/, "") : "";
          if (!relay) { status.textContent = "The live-data relay is not configured, so stamps cannot be fetched."; return; }
          images.innerHTML = stampUrls(kind, stampButton.getAttribute("data-oid"), stampButton.getAttribute("data-candid")).map(function (row) {
            return '<figure><img loading="lazy" width="126" height="126" alt="ZTF ' + esc(row.label) + ' stamp from ' + (kind === "alerce" ? "ALeRCE" : "Fink") +
              '" src="' + esc(relay + "/?url=" + encodeURIComponent(row.url)) + '"><figcaption>' + esc(row.label) + "</figcaption></figure>";
          }).join("");
          status.textContent = (kind === "alerce" ? "ALeRCE (Förster et al. 2021)" : "Fink (Möller et al. 2021)") + " stamps of the latest ZTF alert; credit ZTF and the broker. A blank image means the broker had none.";
        });
      }
    });
  }

  return {panel: panel, populationHtml: populationHtml, stampUrls: stampUrls, ztfObject: ztfObject, install: install};
}));
