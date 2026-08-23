/* CTAS public catalog: compact index first, complete evidence on demand. */
(function () {
  "use strict";

  var DATA_DIR = "ctas/data/";
  var PAGE = 35;
  var CACHE_KEY = "ctas-public-index-v1";
  var PUBLIC_LINK_HOSTS = {
    "api.fink-portal.org": 1, "api.ztf.fink-portal.org": 1, "fink-portal.org": 1,
    "apps.aavso.org": 1, "archive.eso.org": 1, "archive.gemini.edu": 1,
    "archive.stsci.edu": 1, "asas-sn.osu.edu": 1, "blackgem.org": 1,
    "cgbm.calet.jp": 1, "chime-experiment.ca": 1, "doc.lsst.fink-broker.org": 1,
    "docs.aavso.org": 1, "ep.bao.ac.cn": 1, "fallingstar-data.com": 1,
    "gcn.gsfc.nasa.gov": 1, "gcn.nasa.gov": 1, "github.com": 1,
    "goto-observatory.org": 1, "heasarc.gsfc.nasa.gov": 1,
    "irsa.ipac.caltech.edu": 1, "lasair.readthedocs.io": 1, "mast.stsci.edu": 1,
    "maxi.riken.jp": 1, "ned.ipac.caltech.edu": 1, "observ.pereplet.ru": 1,
    "outerspace.stsci.edu": 1, "roc-2.icecube.wisc.edu": 1,
    "roc.icecube.wisc.edu": 1, "rubinobservatory.org": 1,
    "simbad.cds.unistra.fr": 1, "ui.adsabs.harvard.edu": 1,
    "vizier.cds.unistra.fr": 1, "wfst.bao.ac.cn": 1, "www.aavso.org": 1,
    "www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca": 1, "www.cosmos.esa.int": 1,
    "www.wis-tns.org": 1, "www.wiserep.org": 1, "yse.ucsc.edu": 1,
    "www.ivoa.net": 1, "tom-toolkit.readthedocs.io": 1,
    "ampelproject.github.io": 1, "antares.noirlab.edu": 1,
    "babamul.caltech.edu": 1, "pitt-broker.readthedocs.io": 1, "ztf.uw.edu": 1
  };

  var state = {
    candidates: [], snapshot: null, status: null, sourceUniverse: null, releaseHistory: null,
    chunks: {}, activeSummary: null, activeDetail: null, cachedSnapshot: false,
    sortKey: "ctas_score", sortDir: -1, preset: "all", q: "", cls: "", msg: "",
    stat: "", shown: PAGE, skyDays: 7, skyPoints: [], skySelected: null,
    skyKeyboardIndex: -1, photBand: {}
  };

  var el = {
    status: document.getElementById("ctas-status"),
    releaseAlert: document.getElementById("ctas-release-alert"),
    overviewSummary: document.getElementById("ctas-overview-summary"),
    metrics: document.getElementById("ctas-metrics"),
    eventStats: document.getElementById("ctas-event-stats"),
    messengerStats: document.getElementById("ctas-messenger-stats"),
    priorityStats: document.getElementById("ctas-priority-stats"),
    stream: document.getElementById("ctas-stream"),
    sources: document.getElementById("ctas-sources"),
    providerStats: document.getElementById("ctas-provider-stats"),
    surveys: document.getElementById("ctas-surveys"),
    sourceUniverseSummary: document.getElementById("ctas-source-universe-summary"),
    sourceUniverseGroups: document.getElementById("ctas-source-universe-groups"),
    releaseHistory: document.getElementById("ctas-release-history"),
    toolbar: document.getElementById("ctas-toolbar"),
    results: document.getElementById("ctas-results"),
    workspace: document.getElementById("candidate-workspace"),
    count: document.getElementById("ctas-count"),
    clear: document.getElementById("ctas-clear"),
    q: document.getElementById("ctas-q"),
    cls: document.getElementById("ctas-class"),
    msg: document.getElementById("ctas-messenger"),
    stat: document.getElementById("ctas-statusfilter"),
    skyStage: document.getElementById("ctas-sky-stage"),
    sky: document.getElementById("ctas-sky-canvas"),
    skyTip: document.getElementById("ctas-sky-tooltip"),
    skyCount: document.getElementById("ctas-sky-count"),
    skyAccessible: document.getElementById("ctas-sky-accessible")
  };
  if (!el.results) return;

  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character];
    });
  }
  function num(value, digits) {
    if (value === null || value === undefined || value === "") return "";
    var parsed = Number(value);
    return isFinite(parsed) ? parsed.toFixed(digits === undefined ? 2 : digits) : "";
  }
  function finiteNumber(value) {
    return value !== null && value !== undefined && value !== "" && isFinite(Number(value));
  }
  function parseDate(value) {
    if (!value) return null;
    var parsed = new Date(value);
    return isNaN(parsed.getTime()) ? null : parsed;
  }
  function absolute(value) {
    var parsed = parseDate(value);
    if (!parsed) return "unknown";
    return parsed.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC").replace("Z", " UTC");
  }
  function relative(value) {
    var parsed = parseDate(value);
    if (!parsed) return "";
    var minutes = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 60000));
    if (minutes < 1) return "just now";
    if (minutes === 1) return "1 minute ago";
    if (minutes < 60) return minutes + " minutes ago";
    var hours = Math.round(minutes / 60);
    if (hours === 1) return "1 hour ago";
    if (hours < 48) return hours + " hours ago";
    return Math.round(hours / 24) + " days ago";
  }
  function sexagesimal(ra, dec) {
    return window.CTASCatalogModel ? window.CTASCatalogModel.sexagesimal(ra, dec) : "";
  }
  function fact(label, value) {
    if (value === null || value === undefined || value === "") return "";
    return "<div><dt>" + esc(label) + "</dt><dd>" + esc(value) + "</dd></div>";
  }
  function humanKey(value) {
    return text(value).replace(/[_-]+/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
  }
  function shortHash(value) { return text(value).slice(0, 12); }
  function sourceObjectId(row) {
    var match = text(row && row.summary).match(/\b(ZTF\d{2}[a-z]+)\b/i);
    return match ? match[1] : null;
  }

  function classifyReference(url, hint, row) {
    if (!url) return null;
    var parsed;
    try { parsed = new URL(url, window.location.href); }
    catch (_) { return {unavailable: true, label: "Original link unavailable (malformed source URL)"}; }
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port) {
      return {unavailable: true, label: "Original link unavailable (not verified for public use)"};
    }
    var host = parsed.hostname.toLowerCase();
    if (!PUBLIC_LINK_HOSTS[host]) {
      return {unavailable: true, label: "Original link unavailable (unverified provider host)"};
    }
    var path = parsed.pathname;
    var label = hint || "Open provider source";
    var kind = "reference";
    if (host === "www.wis-tns.org" && /^\/object\/\d{4}[a-z]+$/i.test(path)) {
      label = "Open TNS object record"; kind = "object";
    } else if (host === "fink-portal.org" && /^\/ZTF\d{2}[a-z]+\/?$/i.test(path)) {
      label = "Open Fink object record"; kind = "object";
    } else if (host.indexOf("fink-portal.org") !== -1) {
      label = "Open Fink API index"; kind = "documentation";
    } else if ((host === "gcn.nasa.gov" || host === "gcn.gsfc.nasa.gov") &&
               (/^\/circulars\//.test(path) || /^\/other\//.test(path))) {
      label = "Open exact GCN notice or circular"; kind = "record";
    } else if (host === "gcn.nasa.gov" || host === "gcn.gsfc.nasa.gov") {
      label = "Open GCN provider documentation"; kind = "documentation";
    } else if (host === "www.wiserep.org" && /^\/form-edit\/spectrum\//.test(path)) {
      label = "Open WISeREP spectrum record"; kind = "record";
    } else if (host === "www.wiserep.org") {
      label = /search|spectra/i.test(parsed.href) ? "Open WISeREP public data query" : "Open WISeREP source";
      kind = "query";
    } else if (host === "irsa.ipac.caltech.edu") {
      label = "Re-run IRSA cone query"; kind = "query";
    } else if (host === "ned.ipac.caltech.edu") {
      label = /byname|objsearch/i.test(parsed.href) ? "Open NED object page" : "Open NED API record";
      kind = "record";
    } else if (host === "simbad.cds.unistra.fr") {
      label = "Open SIMBAD object record"; kind = "record";
    } else if (host === "vizier.cds.unistra.fr") {
      label = "Open VizieR catalog query"; kind = "query";
    } else if (host === "ui.adsabs.harvard.edu") {
      label = "Open ADS publication record"; kind = "record";
    } else if (/download|fits|\.csv$|\.json$|\.xml$/i.test(parsed.href) || hint === "Download source artifact") {
      label = hint || "Download source artifact"; kind = "artifact";
    } else if (/docs?|help|missions?|about|readthedocs/i.test(parsed.href)) {
      label = hint || "Open provider documentation"; kind = "documentation";
    }
    return {url: parsed.href, label: label, kind: kind, row: row};
  }

  function renderReference(reference) {
    if (!reference) return "";
    if (reference.unavailable) return '<span class="ctas-link-unavailable">' + esc(reference.label) + "</span>";
    return '<a class="ctas-source-link ctas-source-link--' + esc(reference.kind) + '" href="' + esc(reference.url) +
      '" target="_blank" rel="noopener">' + esc(reference.label) +
      '<span class="sr-only"> (opens in a new tab)</span></a>';
  }

  function referencesForRow(row, fields) {
    var references = [], seen = {};
    var finkId = sourceObjectId(row);
    if (finkId) {
      var exact = classifyReference("https://fink-portal.org/" + finkId, "Open Fink object record", row);
      references.push(exact); seen[exact.url] = true;
    }
    (fields || [
      ["url", null], ["source_url", null], ["citation_url", null], ["canonical_url", null],
      ["public_download_url", "Download source artifact"], ["skymap_url", "Open source sky map"],
      ["object_specific_result_url", null], ["query_evidence_url", "Open source query or evidence"],
      ["documentation_url", "Open provider documentation"]
    ]).forEach(function (field) {
      var value = row && row[field[0]];
      if (!value || seen[value]) return;
      var reference = classifyReference(value, field[1], row);
      if (reference && reference.url && seen[reference.url]) return;
      if (reference && reference.url) seen[reference.url] = true;
      references.push(reference);
    });
    return references;
  }

  function renderReferences(rows, fields, showEmpty) {
    var references = [], seen = {};
    (rows || []).forEach(function (row) {
      referencesForRow(row, fields).forEach(function (reference) {
        var key = reference && (reference.url || reference.label);
        if (!key || seen[key]) return;
        seen[key] = true; references.push(reference);
      });
    });
    if (showEmpty === undefined) showEmpty = true;
    return references.length
      ? '<div class="ctas-source-links">' + references.map(renderReference).join("") + "</div>"
      : showEmpty ? '<p class="ctas-link-empty">No verified object-specific source link is retained for this record.</p>' : "";
  }

  function downloadBlob(filename, content, type) {
    var blob = new Blob([content], {type: type || "application/octet-stream"});
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement("a");
    anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function rowsToCsv(rows) {
    var keys = {};
    (rows || []).forEach(function (row) { Object.keys(row || {}).forEach(function (key) { keys[key] = true; }); });
    var columns = Object.keys(keys).sort();
    function cell(value) {
      var rendered = value && typeof value === "object" ? JSON.stringify(value) : text(value);
      return '"' + rendered.replace(/"/g, '""') + '"';
    }
    return [columns.map(cell).join(",")].concat((rows || []).map(function (row) {
      return columns.map(function (column) { return cell(row[column]); }).join(",");
    })).join("\n") + "\n";
  }

  function renderScoreFactors(candidate) {
    var labels = {
      recency_points: "Recency", brightness_points: "Reported-brightness term",
      classification_gap_points: "Missing-classification term",
      classification_conflict_points: "Classification-conflict term",
      spectroscopy_gap_points: "Missing-spectrum term",
      coverage_reduction: "Existing-observation reduction",
      observation_gap_points: "Observation-age term", multimessenger_points: "Messenger-diversity term",
      status: "Status override"
    };
    var factors = candidate.score_factors || {};
    var rows = Object.keys(labels).filter(function (key) { return factors[key] !== undefined && factors[key] !== null; })
      .map(function (key) {
        var value = factors[key];
        if (key !== "status" && isFinite(Number(value))) {
          value = (key === "coverage_reduction" ? "−" : Number(value) > 0 ? "+" : "") + Math.abs(Number(value)).toFixed(2);
        }
        return "<div><dt>" + esc(labels[key]) + "</dt><dd>" + esc(value) + "</dd></div>";
      }).join("");
    return '<details class="ctas-score-factors"><summary>Why this candidate has this CTAS score</summary><p>' +
      esc(candidate.score_explanation || "The displayed terms reproduce the public follow-up ordering score.") +
      "</p><dl>" + rows + "</dl></details>";
  }

  function renderCompleteness(candidate) {
    var complete = candidate.record_completeness || {};
    var components = Array.isArray(complete.components) ? complete.components : [];
    return '<details class="ctas-completeness"><summary><span><strong>' + esc(complete.label || "Record not assessed") +
      "</strong><small>" + esc(complete.present || 0) + " of " + esc(complete.applicable || 0) +
      " applicable public-record components present</small></span></summary>" +
      "<p>Completeness describes retained fields. It is separate from priority, scientific importance, and validity.</p>" +
      (components.length ? "<ul>" + components.map(function (row) {
        return '<li class="is-' + esc(row.state) + '"><span>' + esc(row.label) + "</span><strong>" +
          esc(humanKey(row.state)) + "</strong></li>";
      }).join("") + "</ul>" : "") + "</details>";
  }

  function renderSourceCoverage(candidate) {
    var rows = Array.isArray(candidate.source_coverage) ? candidate.source_coverage : [];
    if (!rows.length) return '<details class="ctas-source-coverage"><summary>Source-by-source coverage</summary>' +
      "<p>No bounded target-specific searches are recorded. This means not searched—not no match.</p></details>";
    return '<details class="ctas-source-coverage"><summary>Source-by-source provenance <small>' + rows.length +
      " recorded evaluations</small></summary><p>Provider failure, unavailability, and unperformed searches remain distinct from a searched-no-match result.</p><ul>" +
      rows.map(function (row) {
        var sought = Array.isArray(row.data_types_sought) ? row.data_types_sought.slice(0, 5).join(", ") : "";
        var references = renderReferences([row], [
          ["object_specific_result_url", null], ["query_evidence_url", "Open source query or evidence"],
          ["documentation_url", "Open provider documentation"]
        ]);
        return "<li><div><strong>" + esc(row.source_name || row.source_id) + '</strong><span class="pill">' +
          esc(humanKey(row.disposition || "unknown")) + "</span></div>" + (sought ? "<p>Sought: " + esc(sought) + "</p>" : "") +
          "<small>" + esc(row.checked_at ? absolute(row.checked_at) : "No explicit query clock") +
          (row.reason_code ? " · " + esc(humanKey(row.reason_code)) : "") + "</small><p><strong>" +
          esc(Number(row.retained_record_count || 0).toLocaleString()) + "</strong> retained public record" +
          (Number(row.retained_record_count || 0) === 1 ? "" : "s") + "</p>" + references + "</li>";
      }).join("") + "</ul></details>";
  }

  function candidateSummary(candidate) {
    var summary = candidate.candidate_summary;
    if (!summary || typeof summary !== "object") {
      return {intro: text(summary) || "Public CTAS event record.", details: ""};
    }
    var intro = summary.why_in_ctas || summary.known || "Public CTAS event record.";
    var details = '<div class="ctas-known-missing">' +
      (summary.known ? "<p><strong>Known in this snapshot</strong><span>" + esc(summary.known) + "</span></p>" : "") +
      (summary.missing ? "<p><strong>Not currently retained</strong><span>" + esc(summary.missing) + "</span></p>" : "") +
      (summary.non_claim ? "<p><strong>Claim boundary</strong><span>" + esc(summary.non_claim) + "</span></p>" : "") +
      "</div>";
    return {intro: intro, details: details};
  }

  function renderTimeline(candidate) {
    var follow = candidate.follow_up || {}, entries = [];
    function add(rows, kind, timeKey, title, summary) {
      (rows || []).forEach(function (row) {
        entries.push({kind: typeof kind === "function" ? kind(row) : kind,
          time: row[timeKey] || row.source_published_at || row.published_at || row.ctas_received_at,
          title: title(row), summary: summary(row), provider: row.provider, row: row});
      });
    }
    add((follow.classifications || []).concat(follow.classification_history || []), function (row) {
      return row.retracted ? "Classification retraction" : row.superseded ? "Classification revision" : "Reported classification";
    }, "asserted_at", function (row) { return row.classification || "Unclassified"; }, function (row) {
      return [row.subtype, row.method, row.probability === undefined ? "" : num(100 * row.probability, 1) + "% reported probability"].filter(Boolean).join(" · ");
    });
    add(follow.spectra, "Spectrum", "observed_at", function (row) { return row.file_name || row.provider_spectrum_id || "Spectrum metadata"; },
      function (row) { return [row.telescope, row.instrument, row.calibration_state].filter(Boolean).join(" · "); });
    add(follow.messenger_signals, "Messenger notice", "observed_at", function (row) {
      return [row.messenger, row.alert_type || row.role].filter(Boolean).join(" · ") || "Messenger notice";
    }, function (row) { return row.summary || row.measurement || "Provider notice"; });
    add(follow.publications, "Public report", "published_at", function (row) { return row.title || row.publication_type || "Public report"; },
      function (row) { return row.abstract || row.authors_text || "Provider report"; });
    if (candidate.discovery_time) entries.push({kind: "Discovery record", time: candidate.discovery_time,
      title: candidate.name, summary: [candidate.discovery_survey, num(candidate.discovery_magnitude, 2) ? num(candidate.discovery_magnitude, 2) + " mag" : ""].filter(Boolean).join(" · "),
          provider: candidate.discovery_survey, row: null});
    entries.sort(function (a, b) {
      var at = parseDate(a.time), bt = parseDate(b.time);
      return (bt ? bt.getTime() : 0) - (at ? at.getTime() : 0);
    });
    var shown = entries.slice(0, 12);
    return '<details class="ctas-timeline"><summary>Concise scientific timeline <small>' + entries.length +
      " entries; 12 shown initially</small></summary><ol>" + shown.map(function (entry) {
        return '<li><div class="ctas-timeline__clocks"><span>' + esc(entry.time ? absolute(entry.time) : "Time not recorded") +
          '</span></div><div><span class="pill">' + esc(entry.kind) + "</span><strong>" + esc(entry.title) +
          "</strong><small>" + esc(entry.provider || "Provider not recorded") + "</small>" +
          (entry.summary ? "<p>" + esc(entry.summary) + "</p>" : "") + (entry.row ? renderReferences([entry.row], undefined, false) : "") + "</div></li>";
      }).join("") + "</ol>" + (entries.length > shown.length ? "<p>Download the candidate JSON for all timeline-bearing rows.</p>" : "") + "</details>";
  }

  function photometrySvg(rows) {
    var numeric = rows.filter(function (row) {
      return parseDate(row.observed_at) && (finiteNumber(row.magnitude) || finiteNumber(row.limiting_magnitude));
    });
    if (!numeric.length) return '<p class="ctas-link-empty">No plottable magnitude and time pairs are retained.</p>';
    var cap = 600;
    var plotted = numeric.length <= cap ? numeric : Array.from({length: cap}, function (_, index) {
      return numeric[Math.floor(index * numeric.length / cap)];
    });
    var times = numeric.map(function (row) { return parseDate(row.observed_at).getTime(); });
    var mags = numeric.map(function (row) { return Number(finiteNumber(row.magnitude) ? row.magnitude : row.limiting_magnitude); });
    var minT = Math.min.apply(null, times), maxT = Math.max.apply(null, times);
    var minM = Math.min.apply(null, mags) - 0.35, maxM = Math.max.apply(null, mags) + 0.35;
    if (minT === maxT) { minT -= 43200000; maxT += 43200000; }
    if (minM === maxM) { minM -= 0.5; maxM += 0.5; }
    var width = 820, height = 330, left = 62, right = 18, top = 18, bottom = 46;
    function x(row) { return left + (parseDate(row.observed_at).getTime() - minT) / (maxT - minT) * (width - left - right); }
    function y(row) {
      var magnitude = Number(finiteNumber(row.magnitude) ? row.magnitude : row.limiting_magnitude);
      return top + (magnitude - minM) / (maxM - minM) * (height - top - bottom);
    }
    var colors = {g: "#60d394", r: "#ff6b6b", i: "#d4a74f", z: "#a78bfa", u: "#63b3ed", y: "#f6e05e"};
    var points = plotted.map(function (row) {
      var upper = !finiteNumber(row.magnitude);
      var title = [absolute(row.observed_at), row.band || "band unavailable",
        upper ? "limit " + num(row.limiting_magnitude, 3) : num(row.magnitude, 3) + " ± " + num(row.magnitude_error, 3),
        row.magnitude_system, row.provider].filter(Boolean).join(" · ");
      var color = colors[text(row.band).toLowerCase()] || "#8ad5df";
      return upper
        ? '<path d="M ' + num(x(row), 1) + " " + num(y(row) - 5, 1) + " l -4 -6 m 4 6 l 4 -6 m -4 6 v 7" +
          '" stroke="' + color + '" fill="none"><title>' + esc(title) + "</title></path>"
        : '<circle cx="' + num(x(row), 1) + '" cy="' + num(y(row), 1) + '" r="3.2" fill="' + color +
          '" fill-opacity=".82"><title>' + esc(title) + "</title></circle>";
    }).join("");
    var date0 = new Date(minT).toISOString().slice(0, 10), date1 = new Date(maxT).toISOString().slice(0, 10);
    return '<div class="ctas-lightcurve"><svg viewBox="0 0 ' + width + " " + height + '" role="img" aria-label="Light curve with magnitude increasing downward">' +
      '<rect x="' + left + '" y="' + top + '" width="' + (width - left - right) + '" height="' + (height - top - bottom) + '" class="ctas-plot-bg"/>' +
      '<line x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + (height - bottom) + '" class="ctas-axis"/>' +
      '<line x1="' + left + '" y1="' + (height - bottom) + '" x2="' + (width - right) + '" y2="' + (height - bottom) + '" class="ctas-axis"/>' +
      '<text x="12" y="' + (height / 2) + '" transform="rotate(-90 12 ' + (height / 2) + ')" class="ctas-axis-label">Magnitude (brighter upward)</text>' +
      '<text x="' + left + '" y="' + (height - 15) + '" class="ctas-axis-label">' + esc(date0) + '</text><text x="' + (width - right) + '" y="' + (height - 15) + '" text-anchor="end" class="ctas-axis-label">' + esc(date1) + "</text>" +
      '<text x="' + (left - 8) + '" y="' + (top + 5) + '" text-anchor="end" class="ctas-axis-label">' + esc(num(minM, 1)) + '</text><text x="' + (left - 8) + '" y="' + (height - bottom) + '" text-anchor="end" class="ctas-axis-label">' + esc(num(maxM, 1)) + "</text>" +
      points + "</svg><p>" + esc(plotted.length.toLocaleString()) + " of " + esc(numeric.length.toLocaleString()) +
      " plottable rows shown. Hover a point for the retained source values; downward arrows mark limits.</p></div>";
  }

  function renderPhotometry(candidate) {
    var rows = (candidate.follow_up || {}).observations || [];
    if (!rows.length) return "";
    var bands = {}, selected = state.photBand[candidate.name] || "*";
    rows.forEach(function (row) { if (row.band) bands[row.band] = true; });
    var filtered = selected === "*" ? rows : rows.filter(function (row) { return text(row.band) === selected; });
    var tableRows = filtered.slice(0, 40);
    return '<details class="ctas-evidence-panel" data-phot-panel><summary>Photometry <small>' + rows.length.toLocaleString() +
      ' retained rows</small></summary><div class="ctas-evidence-panel__body"><div class="ctas-evidence-tools"><label>Band <select data-phot-band>' +
      '<option value="*">All bands</option>' + Object.keys(bands).sort().map(function (band) {
        return '<option value="' + esc(band) + '"' + (selected === band ? " selected" : "") + ">" + esc(band) + "</option>";
      }).join("") + '</select></label><button type="button" data-download-evidence="observations" data-format="csv">Download CSV</button>' +
      '<button type="button" data-download-evidence="observations" data-format="json">Download JSON</button></div>' +
      photometrySvg(filtered) + renderReferences(rows.slice(0, 20)) +
      '<div class="ctas-evidence-table-wrap"><table class="ctas-evidence-table"><caption>First ' + tableRows.length + " of " + filtered.length +
      ' matching rows; downloads contain all retained rows.</caption><thead><tr><th>Observed</th><th>Band</th><th>Magnitude / limit</th><th>System</th><th>Facility</th><th>Source</th></tr></thead><tbody>' +
      tableRows.map(function (row) {
        var measure = finiteNumber(row.magnitude) ? num(row.magnitude, 3) + (finiteNumber(row.magnitude_error) ? " ± " + num(row.magnitude_error, 3) : "") :
          finiteNumber(row.limiting_magnitude) ? "&gt; " + esc(num(row.limiting_magnitude, 3)) : "not reported";
        return "<tr><td>" + esc(absolute(row.observed_at)) + "</td><td>" + esc(row.band || "—") + "</td><td>" + measure +
          "</td><td>" + esc(row.magnitude_system || "—") + "</td><td>" + esc(row.telescope || row.instrument || "—") +
          "</td><td>" + renderReferences([row]) + "</td></tr>";
      }).join("") + "</tbody></table></div></div></details>";
  }

  function renderSpectra(candidate) {
    var rows = (candidate.follow_up || {}).spectra || [];
    if (!rows.length) return "";
    return '<details class="ctas-evidence-panel"><summary>Spectra <small>' + rows.length +
      " public metadata record" + (rows.length === 1 ? "" : "s") +
      '</small></summary><div class="ctas-evidence-panel__body"><p>CTAS plots a spectrum only when rights-cleared numerical arrays are exported. The current rows may be metadata-only; record and download links remain distinct.</p>' +
      '<div class="ctas-evidence-tools"><button type="button" data-download-evidence="spectra" data-format="json">Download metadata JSON</button></div><ul class="ctas-record-list">' +
      rows.slice(0, 40).map(function (row) {
        return "<li><strong>" + esc(row.file_name || row.provider_spectrum_id || "Spectrum") + "</strong><span>" +
          esc([row.observed_at ? absolute(row.observed_at) : "time unavailable", row.telescope, row.instrument,
            row.wavelength_unit, row.calibration_state].filter(Boolean).join(" · ")) + "</span>" +
          (row.checksum_sha256 ? "<small>SHA-256 " + esc(shortHash(row.checksum_sha256)) + "…</small>" : "") +
          renderReferences([row], [["source_url", null], ["public_download_url", "Download source artifact"]]) + "</li>";
      }).join("") + "</ul></div></details>";
  }

  function renderMessenger(candidate) {
    var rows = (candidate.follow_up || {}).messenger_signals || [];
    if (!rows.length) return "";
    return '<details class="ctas-evidence-panel"><summary>Messenger notices <small>' + rows.length +
      '</small></summary><div class="ctas-evidence-panel__body"><div class="ctas-evidence-tools"><button type="button" data-download-evidence="messenger_signals" data-format="csv">Download CSV</button><button type="button" data-download-evidence="messenger_signals" data-format="json">Download JSON</button></div>' +
      '<div class="ctas-evidence-table-wrap"><table class="ctas-evidence-table"><thead><tr><th>Scientific time</th><th>Messenger / role</th><th>Significance</th><th>False-alarm rate</th><th>Localization</th><th>Source</th></tr></thead><tbody>' +
      rows.slice(0, 40).map(function (row) {
        return "<tr><td>" + esc(absolute(row.observed_at || row.event_time)) + "</td><td>" +
          esc([row.messenger, row.alert_type || row.role].filter(Boolean).join(" · ")) + "</td><td>" +
          esc(row.significance === undefined ? "—" : text(row.significance)) + "</td><td>" +
          esc(row.false_alarm_rate === undefined ? (row.far === undefined ? "—" : text(row.far)) : text(row.false_alarm_rate)) + "</td><td>" +
          esc([row.localization_area_sq_deg ? num(row.localization_area_sq_deg, 2) + " deg²" : "", row.coordinate_error_deg ? num(row.coordinate_error_deg, 2) + "° radius" : ""].filter(Boolean).join(" · ") || "—") +
          "</td><td>" + renderReferences([row]) + "</td></tr>";
      }).join("") + "</tbody></table></div>" + (rows.length > 40 ? "<p>First 40 rows shown; downloads contain all retained rows.</p>" : "") + "</div></details>";
  }

  function renderClassifications(candidate) {
    var follow = candidate.follow_up || {};
    var classifications = (follow.classifications || []).concat(follow.classification_history || []);
    var reports = follow.publications || [];
    if (!classifications.length && !reports.length) return "";
    return '<details class="ctas-evidence-panel"><summary>Reported classifications and public reports <small>' +
      (classifications.length + reports.length) + '</small></summary><div class="ctas-evidence-panel__body"><div class="ctas-detail__grid">' +
      (classifications.length ? '<section class="ctas-detail__section"><h4>Classification assertions</h4><ul>' + classifications.slice(0, 30).map(function (row) {
        return "<li><strong>" + esc(row.classification || "Unclassified") + "</strong><span>" +
          esc([row.asserted_at ? absolute(row.asserted_at) : "time unavailable", row.provider, row.method,
            row.probability === undefined ? "" : num(100 * row.probability, 1) + "% reported probability",
            row.retracted ? "retracted" : row.superseded ? "superseded" : ""].filter(Boolean).join(" · ")) +
          "</span>" + renderReferences([row]) + "</li>";
      }).join("") + "</ul></section>" : "") +
      (reports.length ? '<section class="ctas-detail__section"><h4>Public reports</h4><ul>' + reports.slice(0, 30).map(function (row) {
        return "<li><strong>" + esc(row.title || row.publication_type || "Public report") + "</strong><span>" +
          esc([row.published_at ? absolute(row.published_at) : "", row.authors_text, row.provider].filter(Boolean).join(" · ")) +
          "</span>" + (row.abstract ? "<p>" + esc(row.abstract) + "</p>" : "") + renderReferences([row]) + "</li>";
      }).join("") + "</ul></section>" : "") + "</div></div></details>";
  }

  function renderEnvironment(candidate) {
    var follow = candidate.follow_up || {};
    var hosts = follow.host_context || [], counterparts = follow.catalog_counterparts || [], products = follow.archive_products || [];
    if (!hosts.length && !counterparts.length && !products.length) return "";
    return '<details class="ctas-evidence-panel"><summary>Environment and released archive context <small>' +
      (hosts.length + counterparts.length + products.length) + '</small></summary><div class="ctas-evidence-panel__body"><p>Positional catalog candidates are context, not host associations, unless a retained host record explicitly says otherwise.</p><div class="ctas-detail__grid">' +
      (hosts.length ? '<section class="ctas-detail__section"><h4>Reported host context</h4><ul>' + hosts.slice(0, 20).map(function (row) {
        return "<li><strong>" + esc(row.canonical_name || row.queried_name || "Host record") + "</strong><span>" +
          esc([row.redshift === undefined ? "" : "z " + num(row.redshift, 5), row.physical_type, row.morphology,
            row.separation_arcsec === undefined ? "" : num(row.separation_arcsec, 2) + " arcsec"].filter(Boolean).join(" · ")) +
          "</span>" + renderReferences([row]) + "</li>";
      }).join("") + "</ul></section>" : "") +
      (counterparts.length ? '<section class="ctas-detail__section"><h4>Positional catalog candidates</h4><ul>' + counterparts.slice(0, 30).map(function (row) {
        var motion = row.motion && typeof row.motion === "object" ? JSON.stringify(row.motion) : row.motion;
        var photometry = row.photometry && typeof row.photometry === "object" ? JSON.stringify(row.photometry) : row.photometry;
        return "<li><strong>" + esc(row.catalog_record_id || row.catalog_description || "Catalog row") + "</strong><span>" +
          esc([row.catalog, row.separation_arcsec === undefined ? "" : num(row.separation_arcsec, 2) + " arcsec",
            row.object_type || row.type, motion ? "motion " + motion : "", photometry ? "photometry " + photometry : ""].filter(Boolean).join(" · ")) +
          "</span>" + renderReferences([row]) + "</li>";
      }).join("") + "</ul>" + (counterparts.length > 30 ? "<p>First 30 shown; candidate JSON contains all rows.</p>" : "") + "</section>" : "") +
      (products.length ? '<section class="ctas-detail__section"><h4>Released archive products</h4><ul>' + products.slice(0, 20).map(function (row) {
        return "<li><strong>" + esc(row.product_filename || row.provider_product_id || "Archive product") + "</strong><span>" +
          esc([row.mission, row.instrument, row.observed_at ? absolute(row.observed_at) : ""].filter(Boolean).join(" · ")) +
          "</span>" + renderReferences([row]) + "</li>";
      }).join("") + "</ul></section>" : "") + "</div></div></details>";
  }

  function renderDetails(candidate) {
    var counts = candidate.follow_up_counts || {};
    var evidence = Object.keys(counts).filter(function (key) { return Number(counts[key]) > 0; }).map(function (key) {
      return Number(counts[key]).toLocaleString() + " " + humanKey(key).toLowerCase();
    });
    var labelKind = humanKey(candidate.reported_label_kind || "provider-reported");
    var summary = candidateSummary(candidate);
    var magnitude = candidate.discovery_magnitude === undefined || candidate.discovery_magnitude === null
      ? "Not retained as a scientific magnitude" : num(candidate.discovery_magnitude, 3) + " mag (source reported)";
    var quality = Array.isArray(candidate.data_quality_flags) && candidate.data_quality_flags.length
      ? '<div class="ctas-quality-note"><strong>Source-value quality note</strong><p>' +
        esc(candidate.data_quality_flags.join(" · ")) +
        (candidate.reported_discovery_magnitude !== undefined ? " · Raw provider value retained: " + esc(candidate.reported_discovery_magnitude) : "") +
        ". The flagged value is excluded from the plotted magnitude and brightness-derived score term.</p></div>" : "";
    return '<div class="ctas-workspace__head"><div><p class="eyebrow">Complete public candidate record</p><h3>' + esc(candidate.name) +
      '</h3><p>' + esc(summary.intro) +
      '</p></div><div class="ctas-detail__score"><span>CTAS follow-up score</span><strong>' + esc(num(candidate.ctas_score, 1)) +
      '</strong><small>ordering aid · not probability</small></div></div>' +
      '<div class="ctas-workspace__actions"><button type="button" data-copy-link>Copy permalink</button><button type="button" data-download-candidate>Download candidate JSON</button><button type="button" data-close-candidate>Close record</button></div>' + summary.details +
      '<dl class="ctas-detail__facts">' + fact("Event type", humanKey(candidate.event_type || "Not recorded")) +
      fact("Primary messenger", humanKey(candidate.primary_messenger || "Not recorded")) +
      fact("ICRS coordinates", sexagesimal(candidate.ra_deg, candidate.dec_deg) || "Unavailable") +
      fact("Discovery", [candidate.discovery_time ? absolute(candidate.discovery_time) : "time unavailable", candidate.discovery_survey || "survey unavailable"].join(" · ")) +
      fact("Source-reported magnitude", magnitude) + fact("Reported class / alert label", candidate.classification || "Unclassified") +
      fact("Label kind", labelKind) + fact("Reported classification probability", candidate.classification_probability === undefined ? "Not reported" : num(100 * candidate.classification_probability, 1) + "% (calibration not assumed)") +
      fact("Current record status", humanKey(candidate.status || "unknown")) + fact("Retained follow-up", evidence.join(" · ") || "Event record only") +
      fact("Redshift", num(candidate.redshift, 5)) + fact("Host", candidate.host_name) + "</dl>" + quality +
      '<section class="ctas-original-sources"><h4>Original sources retained for this candidate</h4>' + renderReferences(candidate.links || []) +
      ((candidate.links || []).some(function (row) { return row.source_key === "tns"; })
        ? "<p>TNS hourly intake does not itself expose every current object-page flag. Open the exact TNS record above for the provider’s current object page and status.</p>" : "") + "</section>" +
      renderScoreFactors(candidate) + renderCompleteness(candidate) +
      renderPhotometry(candidate) + renderSpectra(candidate) + renderMessenger(candidate) +
      renderClassifications(candidate) + renderEnvironment(candidate) + renderTimeline(candidate) + renderSourceCoverage(candidate);
  }

  function statusCell(label, value, sub) {
    return '<div class="ctas-status__cell"><p class="ctas-status__label">' + esc(label) +
      '</p><p class="ctas-status__value">' + value + "</p>" + (sub ? '<p class="ctas-status__sub">' + sub + "</p>" : "") + "</div>";
  }
  function renderStatus() {
    if (!el.status) return;
    var status = state.status || {}, snapshot = state.snapshot || {};
    var generated = status.last_successful_update || snapshot.generated_at;
    var degraded = status.pipeline_status === "degraded";
    var cached = status.pipeline_status === "cached";
    var assurance = status.static_catalog_assurance || {};
    el.status.classList.toggle("is-degraded", degraded || cached);
    el.status.innerHTML = statusCell("Pipeline", cached ? "Cached snapshot" : degraded ? "Operational with source limits" : "Operational",
      cached ? "A live refresh failed; the last successfully loaded public snapshot remains usable" : degraded ? esc(status.degraded_source_count || 0) + " represented source" + (Number(status.degraded_source_count) === 1 ? "" : "s") + " currently degraded; retained catalog remains available" : "Public pipeline healthy") +
      statusCell("Last successful public snapshot", esc(relative(generated) || "unavailable"), esc(absolute(generated))) +
      statusCell("Public candidates", Number(status.candidate_count || snapshot.candidate_count || state.candidates.length).toLocaleString(),
        "Positional catalog entries; not all are confirmed discoveries") +
      statusCell("Static release assurance", esc(humanKey(assurance.status || "pending")), assurance.content_release_id ? "Release " + esc(shortHash(assurance.content_release_id)) + "…" : "Checksum report available below") +
      statusCell("Publication cadence", "2-minute change check", "Runs while the publishing host is awake and online; unchanged snapshots receive a periodic freshness heartbeat");
  }

  function barRows(values, labels) {
    values = values || {};
    var rows = Object.keys(values).map(function (key) { return {key: key, count: Number(values[key] || 0)}; })
      .sort(function (a, b) { return b.count - a.count; });
    var max = Math.max.apply(null, rows.map(function (row) { return row.count; }).concat([1]));
    return rows.map(function (row) {
      return '<div class="ctas-bar"><span>' + esc(labels && labels[row.key] || humanKey(row.key)) + '</span><i style="--bar:' +
        (100 * row.count / max).toFixed(1) + '%"></i><strong>' + row.count.toLocaleString() + "</strong></div>";
    }).join("");
  }
  function renderOverview() {
    var stats = (state.snapshot || {}).statistics || {};
    var items = [
      ["Public candidates", stats.public_candidates], ["With follow-up", stats.candidates_with_follow_up],
      ["Observations", stats.observations], ["Spectra", stats.spectra],
      ["Messenger notices", stats.messenger_signals], ["Reported classifications", stats.classifications],
      ["Classification revisions", stats.classification_history], ["Public reports", stats.publications]
    ];
    el.metrics.innerHTML = items.map(function (item) {
      return '<div class="ctas-metric"><strong>' + Number(item[1] || 0).toLocaleString() + "</strong><span>" + esc(item[0]) + "</span></div>";
    }).join("");
    if (el.overviewSummary) el.overviewSummary.textContent = Number(stats.public_candidates || 0).toLocaleString() +
      " candidates · " + Number(stats.observations || 0).toLocaleString() + " observations · " + Number(stats.spectra || 0).toLocaleString() + " spectra";
    if (el.eventStats) el.eventStats.innerHTML = barRows(stats.event_types);
    if (el.messengerStats) el.messengerStats.innerHTML = barRows(stats.messengers);
    if (el.priorityStats) el.priorityStats.innerHTML = barRows(stats.priority_bands, {
      urgent_75_100: "Urgent 75–100", high_50_74: "High 50–74", routine_25_49: "Routine 25–49", low_0_24: "Low 0–24"
    });
    var stream = (state.snapshot || {}).recent_stream || [];
    el.stream.innerHTML = stream.slice(0, 3).map(function (candidate, index) {
      var counts = candidate.follow_up_counts || {};
      var evidence = [counts.observations ? counts.observations + " obs" : "", counts.spectra ? counts.spectra + " spectra" : "",
        counts.messenger_signals ? counts.messenger_signals + " notices" : "", counts.classifications ? counts.classifications + " classifications" : ""].filter(Boolean).join(" · ") || "event record only";
      return '<li><span class="ctas-stream__number">0' + (index + 1) + '</span><div><button type="button" data-open-candidate="' + esc(candidate.name) + '"><strong>' + esc(candidate.name) + "</strong></button><p>" +
        esc(candidate.classification || "Unclassified") + " · " + esc(candidate.primary_messenger || "messenger unavailable") +
        "</p><small>" + esc(absolute(candidate.updated_at || candidate.discovery_time)) + " · " + esc(evidence) +
        '</small></div><strong class="ctas-stream__score">' + esc(num(candidate.ctas_score, 1)) + "<span>CTAS score</span></strong></li>";
    }).join("");
    renderRepresentedSources();
  }

  function renderRepresentedSources() {
    var statusRows = (state.status || {}).sources || [];
    var represented = statusRows.filter(function (row) {
      return Object.keys(row.record_counts || {}).some(function (key) { return Number(row.record_counts[key] || 0) > 0; });
    });
    el.sources.innerHTML = represented.map(function (row) {
      var counts = Object.keys(row.record_counts || {}).filter(function (key) { return Number(row.record_counts[key]) > 0; })
        .map(function (key) { return Number(row.record_counts[key]).toLocaleString() + " " + humanKey(key).toLowerCase(); }).join(" · ");
      return '<li><div><strong>' + esc(row.label || row.source) + '</strong><p class="ctas-sources__counts">' + esc(counts) +
        "</p><small>" + esc([humanKey(row.state || "represented"), row.facility, row.protocol].filter(Boolean).join(" · ")) +
        "</small></div>" + renderReferences([row], [["documentation_url", "Open provider documentation"]]) + "</li>";
    }).join("") || "<li>No represented source summary is available.</li>";
    el.providerStats.innerHTML = ((state.snapshot || {}).provider_statistics || []).map(function (row) {
      var counts = Object.keys(row).filter(function (key) { return key !== "provider"; }).map(function (key) {
        return Number(row[key]).toLocaleString() + " " + humanKey(key).toLowerCase();
      }).join(" · ");
      return "<div><strong>" + esc(row.provider) + "</strong><span>" + esc(counts) + "</span></div>";
    }).join("");
    el.surveys.innerHTML = ((state.snapshot || {}).surveys || []).map(function (row) {
      return "<span>" + esc(row.survey) + " <strong>" + Number(row.candidate_count || 0).toLocaleString() + "</strong></span>";
    }).join("");
  }

  function renderSourceUniverse() {
    var universe = state.sourceUniverse;
    if (!universe || !Array.isArray(universe.sources)) return;
    var groups = {}, states = {};
    universe.sources.forEach(function (row) {
      var family = row.primary_family || row.source_family || "other";
      (groups[family] = groups[family] || []).push(row);
      states[row.operational_state || "unknown"] = (states[row.operational_state || "unknown"] || 0) + 1;
    });
    el.sourceUniverseSummary.innerHTML = '<span><strong>' + universe.source_count + "</strong> source contracts</span>" +
      '<span><strong>' + universe.family_count + "</strong> source families</span>" + Object.keys(states).sort().map(function (key) {
        return "<span><strong>" + states[key] + "</strong> " + esc(humanKey(key).toLowerCase()) + "</span>";
      }).join("");
    el.sourceUniverseGroups.innerHTML = Object.keys(groups).sort().map(function (family) {
      return "<details><summary><strong>" + esc(humanKey(family)) + "</strong><small>" + groups[family].length +
        " sources</small></summary><ul>" + groups[family].map(function (row) {
          return "<li><div><strong>" + esc(row.name) + '</strong><span class="pill">' + esc(humanKey(row.operational_state)) +
            "</span></div><p>" + esc(row.data_types ? row.data_types.join(", ") : "Data products not specified") +
            "</p><small>" + esc([humanKey(row.implementation_state), humanKey(row.representation_state), row.organization_or_facility].filter(Boolean).join(" · ")) +
            "</small>" + renderReferences([row], [["documentation_url", "Open provider documentation"]]) + "</li>";
        }).join("") + "</ul></details>";
    }).join("");
  }

  function renderReleaseHistory() {
    var history = state.releaseHistory || {}, entries = Array.isArray(history.entries) ? history.entries : [];
    if (!entries.length) { el.releaseHistory.innerHTML = "<p>No release-history entries are available.</p>"; return; }
    var visibleEntries = window.CTASCatalogModel
      ? window.CTASCatalogModel.releaseHistorySelection(entries, 6, 8)
      : entries.slice(0, 6);
    el.releaseHistory.innerHTML = '<p class="ctas-claim-boundary">' + esc(history.claim_boundary || "Catalog changes are not scientific validation.") +
      '</p><ol class="ctas-release-list">' + visibleEntries.map(function (entry) {
        var delta = (entry.added_count ? "+" + entry.added_count + " added" : "0 added") + " · " + entry.removed_count + " removed · " + entry.changed_count + " changed";
        return "<li><div><strong>" + esc(absolute(entry.published_at)) + "</strong><span>" + esc(delta) + "</span></div><p>" +
          esc(entry.summary) + "</p>" + (entry.evidence ? "<small>" + esc(entry.evidence) + "</small>" : "") +
          '<code title="Catalog checksum">' + esc(shortHash(entry.catalog_content_checksum_sha256)) + "…</code></li>";
      }).join("") + "</ol>";
    var batch = entries.find(function (entry) { return Number(entry.added_count) === 81 && Number(entry.previous_candidate_count) === 2661; });
    if (batch && el.releaseAlert) {
      el.releaseAlert.hidden = false;
      el.releaseAlert.innerHTML = '<strong>Catalog change explained:</strong> the 2,661 → 2,742 jump was one accepted public TNS hourly batch—80 WFST records and 1 ZTF record, with no simulations, removals, or changes to existing candidates. It was a provider batch/backfill, not 81 discoveries occurring within 53 minutes. <a href="#catalog-changes">Review the checksum-bound change record</a>.';
    }
  }

  function visible() {
    var query = state.q.trim().toLowerCase();
    function sortValue(candidate, key) {
      if (key === "record_completeness") return Number((candidate.record_completeness || {}).fraction || 0);
      return candidate[key];
    }
    return state.candidates.filter(function (candidate) {
      if (state.cls && text(candidate.classification) !== state.cls) return false;
      if (state.msg && text(candidate.primary_messenger) !== state.msg) return false;
      if (state.stat && text(candidate.status) !== state.stat) return false;
      if (window.CTASCatalogModel && !window.CTASCatalogModel.matchesPreset(candidate, state.preset, Date.now())) return false;
      if (!query) return true;
      var aliases = (candidate.designations || []).map(function (row) { return row.designation; }).join(" ");
      return (text(candidate.name) + " " + aliases + " " + text(candidate.classification) + " " + text(candidate.event_type) +
        " " + text(candidate.primary_messenger) + " " + text(candidate.discovery_survey)).toLowerCase().indexOf(query) !== -1;
    }).sort(function (a, b) {
      var av = sortValue(a, state.sortKey), bv = sortValue(b, state.sortKey);
      if (av === undefined || av === null || av === "") return 1;
      if (bv === undefined || bv === null || bv === "") return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * state.sortDir;
      return String(av).localeCompare(String(bv)) * state.sortDir;
    });
  }

  var COLUMNS = [
    {key: "name", label: "Candidate"}, {key: "classification", label: "Reported class / alert label"},
    {key: "ctas_score", label: "CTAS score"}, {key: "record_completeness", label: "Public record"},
    {key: "ra_deg", label: "ICRS position"}, {key: "discovery_time", label: "Reported discovery"},
    {key: "discovery_magnitude", label: "Reported mag"}, {key: "discovery_survey", label: "Survey"},
    {key: "links", label: "Original source", nosort: true}
  ];
  function renderTable() {
    var rows = visible(), shown = rows.slice(0, state.shown);
    el.count.textContent = "Showing " + shown.length.toLocaleString() + " of " + rows.length.toLocaleString() +
      (rows.length === state.candidates.length ? " candidates" : " matching candidates (" + state.candidates.length.toLocaleString() + " total)");
    if (!state.candidates.length) {
      el.results.innerHTML = '<div class="ctas-empty"><h3>No current candidates</h3><p>The next automatic two-minute check will preserve or update this state.</p></div>'; return;
    }
    if (!rows.length) {
      el.results.innerHTML = '<div class="ctas-empty"><h3>Nothing matches those filters</h3><p>Use “Clear filters” to return to the complete catalog.</p><button type="button" data-clear-inline>Clear filters</button></div>'; return;
    }
    var head = COLUMNS.map(function (column) {
      var sorted = state.sortKey === column.key ? (state.sortDir === 1 ? "ascending" : "descending") : "none";
      return '<th scope="col"' + (column.nosort ? ">" : ' aria-sort="' + sorted + '">') +
        (column.nosort ? esc(column.label) : '<button type="button" data-sort="' + column.key + '">' + esc(column.label) + "</button>") + "</th>";
    }).join("");
    var body = shown.map(function (candidate) {
      var counts = candidate.follow_up_counts || {};
      var evidence = [counts.observations ? counts.observations + " obs" : "", counts.spectra ? counts.spectra + " spectra" : "",
        counts.messenger_signals ? counts.messenger_signals + " notices" : "", counts.publications ? counts.publications + " reports" : ""].filter(Boolean).join(" · ") || "event only";
      var label = candidate.classification || "Unclassified";
      return "<tr><td><button type=\"button\" class=\"ctas-candidate\" data-open-candidate=\"" + esc(candidate.name) + "\"><span>" +
        esc(candidate.name) + "</span><small>Open complete record</small></button></td><td><span class=\"pill\">" + esc(label) +
        "</span><small class=\"ctas-label-kind\">" + esc(humanKey(candidate.reported_label_kind || "provider-reported")) +
        "</small></td><td class=\"num\">" + esc(num(candidate.ctas_score, 1)) + "</td><td><strong>" +
        esc((candidate.record_completeness || {}).label || "Not assessed") + "</strong><small class=\"ctas-table-sub\">" + esc(evidence) +
        "</small></td><td class=\"num\">" + esc(sexagesimal(candidate.ra_deg, candidate.dec_deg)) + "</td><td>" +
        esc(candidate.discovery_time ? absolute(candidate.discovery_time) : "—") + "</td><td class=\"num\">" +
        esc(num(candidate.discovery_magnitude, 2) || "—") + "</td><td>" + esc(candidate.discovery_survey || "—") +
        "</td><td>" + renderReferences(candidate.links || []) + "</td></tr>";
    }).join("");
    el.results.innerHTML = '<div class="ctas-table-wrap"><table class="ctas-table"><caption>Public CTAS candidates. Positions are ICRS; source-reported discovery magnitudes may use heterogeneous bands and systems.</caption><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>" + (rows.length > state.shown
        ? '<p class="ctas-more"><button type="button" id="ctas-more">Show ' + Math.min(PAGE, rows.length - state.shown) + " more</button></p>" : "");
  }

  function skyRows() {
    return window.CTASCatalogModel ? window.CTASCatalogModel.skyCandidates(state.candidates, state.skyDays, Date.now()) : [];
  }
  function mollweide(ra, dec, width, height) {
    var longitude = (180 - Number(ra)) * Math.PI / 180, latitude = Number(dec) * Math.PI / 180, theta = latitude;
    for (var index = 0; index < 8; index += 1) {
      var denominator = 2 + 2 * Math.cos(2 * theta);
      if (Math.abs(denominator) < 1e-7) break;
      theta -= (2 * theta + Math.sin(2 * theta) - Math.PI * Math.sin(latitude)) / denominator;
    }
    var margin = 18, sx = (width - margin * 2) / (4 * Math.SQRT2), sy = (height - margin * 2) / (2 * Math.SQRT2);
    return {x: width / 2 + (2 * Math.SQRT2 / Math.PI) * longitude * Math.cos(theta) * sx,
      y: height / 2 - Math.SQRT2 * Math.sin(theta) * sy};
  }
  function magnitudeColor(value) {
    var magnitude = Number(value);
    if (!finiteNumber(value)) return "#a9b3c7";
    var t = Math.max(0, Math.min(1, (magnitude - 13) / 10));
    var stops = [[255, 211, 105], [88, 210, 226], [132, 94, 247]];
    var a = t < 0.5 ? stops[0] : stops[1], b = t < 0.5 ? stops[1] : stops[2], u = t < 0.5 ? t * 2 : (t - 0.5) * 2;
    return "rgb(" + a.map(function (value_, i) { return Math.round(value_ + (b[i] - value_) * u); }).join(",") + ")";
  }
  function drawCurve(context, samples, project) {
    context.beginPath();
    samples.forEach(function (sample, index) {
      var point = project(sample); if (!index) context.moveTo(point.x, point.y); else context.lineTo(point.x, point.y);
    });
    context.stroke();
  }
  function drawSky() {
    if (!el.sky || !el.skyStage || el.skyStage.offsetParent === null) return;
    var width = Math.max(320, Math.floor(el.skyStage.getBoundingClientRect().width));
    var height = Math.max(260, Math.min(520, Math.round(width * 0.5)));
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    el.sky.width = width * ratio; el.sky.height = height * ratio; el.sky.style.height = height + "px";
    var context = el.sky.getContext("2d"); context.scale(ratio, ratio); context.clearRect(0, 0, width, height);
    var margin = 18;
    context.fillStyle = "#07101d"; context.strokeStyle = "rgba(184,200,223,.34)"; context.lineWidth = 1;
    context.beginPath(); context.ellipse(width / 2, height / 2, (width - margin * 2) / 2, (height - margin * 2) / 2, 0, 0, Math.PI * 2); context.fill(); context.stroke();
    context.save(); context.beginPath(); context.ellipse(width / 2, height / 2, (width - margin * 2) / 2, (height - margin * 2) / 2, 0, 0, Math.PI * 2); context.clip();
    context.strokeStyle = "rgba(184,200,223,.16)";
    [-60, -30, 0, 30, 60].forEach(function (dec) {
      var samples = []; for (var ra = 0; ra <= 360; ra += 4) samples.push({ra: ra, dec: dec});
      drawCurve(context, samples, function (sample) { return mollweide(sample.ra, sample.dec, width, height); });
    });
    for (var meridian = 0; meridian < 360; meridian += 30) {
      var meridianSamples = []; for (var d = -89; d <= 89; d += 3) meridianSamples.push({ra: meridian, dec: d});
      drawCurve(context, meridianSamples, function (sample) { return mollweide(sample.ra, sample.dec, width, height); });
    }
    context.restore();
    var rows = skyRows();
    state.skyPoints = rows.map(function (candidate) {
      var point = mollweide(candidate.ra_deg, candidate.dec_deg, width, height); point.candidate = candidate; return point;
    });
    state.skyPoints.forEach(function (point) {
      var selected = state.skySelected && state.skySelected.name === point.candidate.name;
      context.beginPath(); context.arc(point.x, point.y, selected ? 6.5 : 4.2, 0, Math.PI * 2);
      context.fillStyle = magnitudeColor(point.candidate.discovery_magnitude); context.fill();
      context.strokeStyle = selected ? "#fff" : "rgba(255,255,255,.56)"; context.lineWidth = selected ? 2.2 : 0.7; context.stroke();
    });
    el.skyCount.textContent = rows.length.toLocaleString() + " candidates reported in the last " + (state.skyDays === 7 ? "week" : "month") + ".";
    el.sky.setAttribute("aria-label", "Interactive all-sky map of " + rows.length + " CTAS candidates; use the synchronized accessible list or arrow keys and Enter.");
    if (el.skyAccessible) {
      el.skyAccessible.innerHTML = '<option value="">Choose a plotted candidate…</option>' + rows.map(function (candidate) {
        return '<option value="' + esc(candidate.name) + '">' + esc(candidate.name + " — " + (candidate.classification || "Unclassified") + " — score " + num(candidate.ctas_score, 1)) + "</option>";
      }).join("");
    }
  }
  function nearestSkyPoint(event) {
    var rect = el.sky.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
    var best = null, distanceBest = 100;
    state.skyPoints.forEach(function (point) {
      var distance = Math.pow(point.x - x, 2) + Math.pow(point.y - y, 2);
      if (distance < distanceBest) { distanceBest = distance; best = point; }
    });
    return best && distanceBest <= 100 ? {point: best, x: x, y: y} : null;
  }
  function selectSky(candidate, open) {
    state.skySelected = candidate;
    state.skyKeyboardIndex = state.skyPoints.map(function (point) { return point.candidate.name; }).indexOf(candidate.name);
    drawSky();
    if (open) openCandidate(candidate, true);
  }
  function bindSky() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-sky-days]"), function (button) {
      button.addEventListener("click", function () {
        state.skyDays = Number(button.getAttribute("data-sky-days")); state.skySelected = null; state.skyKeyboardIndex = -1;
        Array.prototype.forEach.call(document.querySelectorAll("[data-sky-days]"), function (item) {
          var active = item === button; item.classList.toggle("is-active", active); item.setAttribute("aria-pressed", active ? "true" : "false");
        });
        drawSky();
      });
    });
    if (!el.sky) return;
    el.sky.addEventListener("pointermove", function (event) {
      var hit = nearestSkyPoint(event);
      if (!hit) { el.skyTip.hidden = true; el.sky.style.cursor = "default"; return; }
      var candidate = hit.point.candidate; el.sky.style.cursor = "pointer"; el.skyTip.hidden = false;
      el.skyTip.style.left = Math.min(hit.x + 14, el.sky.clientWidth - 220) + "px"; el.skyTip.style.top = Math.max(8, hit.y - 64) + "px";
      el.skyTip.innerHTML = "<strong>" + esc(candidate.name) + "</strong><span>" + esc(candidate.classification || "Unclassified") +
        " · reported mag " + esc(num(candidate.discovery_magnitude, 2) || "unknown") + "</span><span>" + esc(sexagesimal(candidate.ra_deg, candidate.dec_deg)) + "</span>";
    });
    el.sky.addEventListener("pointerleave", function () { el.skyTip.hidden = true; });
    el.sky.addEventListener("click", function (event) { var hit = nearestSkyPoint(event); if (hit) selectSky(hit.point.candidate, true); });
    el.sky.addEventListener("keydown", function (event) {
      var keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Enter", " "];
      if (!state.skyPoints.length || keys.indexOf(event.key) === -1) return;
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") {
        if (state.skyKeyboardIndex < 0) state.skyKeyboardIndex = 0;
        selectSky(state.skyPoints[state.skyKeyboardIndex].candidate, true); return;
      }
      if (event.key === "Home") state.skyKeyboardIndex = 0;
      else if (event.key === "End") state.skyKeyboardIndex = state.skyPoints.length - 1;
      else {
        var direction = event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
        state.skyKeyboardIndex = (state.skyKeyboardIndex + direction + state.skyPoints.length) % state.skyPoints.length;
      }
      selectSky(state.skyPoints[state.skyKeyboardIndex].candidate, false);
      el.skyCount.textContent = "Selected " + state.skySelected.name + ". Press Enter to open the complete public record.";
    });
    if (el.skyAccessible) el.skyAccessible.addEventListener("change", function () {
      var candidate = state.candidates.find(function (row) { return row.name === el.skyAccessible.value; });
      if (candidate) selectSky(candidate, true);
    });
    var timer;
    window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(drawSky, 120); });
    var skyDetails = document.getElementById("celestial-sphere");
    if (skyDetails) skyDetails.addEventListener("toggle", function () { if (skyDetails.open) setTimeout(drawSky, 0); });
  }

  function getJSON(name, attempts) {
    attempts = attempts === undefined ? 3 : attempts;
    return fetch(DATA_DIR + name, {cache: "no-cache"}).then(function (response) {
      if (!response.ok) throw new Error(name + " returned HTTP " + response.status);
      return response.json();
    }).catch(function (error) {
      if (attempts <= 1) throw error;
      return new Promise(function (resolve) { setTimeout(resolve, (4 - attempts) * 350); })
        .then(function () { return getJSON(name, attempts - 1); });
    });
  }
  function loadChunk(path) {
    if (!state.chunks[path]) state.chunks[path] = getJSON(path).catch(function (error) { delete state.chunks[path]; throw error; });
    return state.chunks[path];
  }
  function setCandidateHash(name) {
    if (candidateFromHash() !== name) history.pushState(null, "", "#candidate=" + encodeURIComponent(name));
  }
  function candidateFromHash() {
    var match = window.location.hash.match(/^#candidate=(.+)$/);
    if (!match) return null;
    try { return decodeURIComponent(match[1]); } catch (_) { return null; }
  }
  function revealFragment(hash) {
    var fragment = String(hash || window.location.hash || "").replace(/^#/, "");
    if (!fragment || fragment.indexOf("candidate=") === 0) return;
    try { fragment = decodeURIComponent(fragment); } catch (_) { return; }
    var target = document.getElementById(fragment);
    if (!target) return;
    if (target.tagName === "DETAILS") target.open = true;
    var parent = target.parentElement && target.parentElement.closest("details");
    while (parent) {
      parent.open = true;
      parent = parent.parentElement && parent.parentElement.closest("details");
    }
  }
  function openCandidate(summary, scroll) {
    if (!summary || !summary.detail_chunk) return;
    state.activeSummary = summary; state.activeDetail = null; setCandidateHash(summary.name);
    el.workspace.hidden = false; el.workspace.setAttribute("tabindex", "-1");
    el.workspace.innerHTML = '<div class="ctas-loading"><strong>Loading ' + esc(summary.name) + "…</strong><span>Fetching its checksum-bound public evidence shard.</span></div>";
    if (scroll) el.workspace.scrollIntoView({behavior: "smooth", block: "start"});
    loadChunk(summary.detail_chunk).then(function (document_) {
      var detail = (document_.candidates || []).find(function (candidate) { return candidate.name === summary.name; });
      if (!detail) throw new Error("Candidate was not found in its published detail shard.");
      state.activeDetail = detail; el.workspace.innerHTML = renderDetails(detail); el.workspace.focus({preventScroll: true});
    }).catch(function (error) {
      el.workspace.innerHTML = '<div class="ctas-empty ctas-empty--error"><h3>Candidate details could not be loaded</h3><p>' +
        esc(error.message) + '</p><button type="button" data-retry-candidate>Retry this record</button></div>';
    });
  }
  function closeCandidate() {
    state.activeSummary = null; state.activeDetail = null; el.workspace.hidden = true; el.workspace.innerHTML = "";
    history.pushState(null, "", window.location.pathname + window.location.search + "#ranked-candidates");
  }
  function openHashCandidate(scroll) {
    var name = candidateFromHash(); if (!name) return;
    var summary = state.candidates.find(function (candidate) { return candidate.name.toLowerCase() === name.toLowerCase(); });
    if (summary) openCandidate(summary, scroll);
  }

  function populateFilters() {
    function fill(select, key, blank) {
      if (!select) return; var values = {};
      state.candidates.forEach(function (candidate) { if (candidate[key]) values[candidate[key]] = true; });
      select.innerHTML = '<option value="">' + blank + "</option>" + Object.keys(values).sort().map(function (value) {
        return '<option value="' + esc(value) + '">' + esc(value) + "</option>";
      }).join("");
    }
    fill(el.cls, "classification", "All labels"); fill(el.msg, "primary_messenger", "All messengers"); fill(el.stat, "status", "All statuses");
  }
  function clearFilters() {
    state.q = state.cls = state.msg = state.stat = ""; state.preset = "all"; state.sortKey = "ctas_score"; state.sortDir = -1; state.shown = PAGE;
    if (el.q) el.q.value = ""; if (el.cls) el.cls.value = ""; if (el.msg) el.msg.value = ""; if (el.stat) el.stat.value = "";
    Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
      var active = button.getAttribute("data-preset") === "all"; button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    renderTable();
  }
  function bindInterface() {
    if (el.q) el.q.addEventListener("input", function () { state.q = el.q.value; state.shown = PAGE; renderTable(); });
    if (el.cls) el.cls.addEventListener("change", function () { state.cls = el.cls.value; state.shown = PAGE; renderTable(); });
    if (el.msg) el.msg.addEventListener("change", function () { state.msg = el.msg.value; state.shown = PAGE; renderTable(); });
    if (el.stat) el.stat.addEventListener("change", function () { state.stat = el.stat.value; state.shown = PAGE; renderTable(); });
    if (el.clear) el.clear.addEventListener("click", clearFilters);
    Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
      button.addEventListener("click", function () {
        state.preset = button.getAttribute("data-preset") || "all"; state.shown = PAGE;
        if (["all", "priority", "needs-follow-up"].indexOf(state.preset) !== -1) { state.sortKey = "ctas_score"; state.sortDir = -1; }
        if (state.preset === "newest") state.sortKey = "discovery_time";
        if (state.preset === "updated") state.sortKey = "updated_at";
        if (state.preset === "classified") state.sortKey = "latest_classification_at";
        if (state.preset === "retracted") state.sortKey = "status";
        if (state.preset === "spectra") state.sortKey = "latest_spectrum_at";
        if (state.preset === "messenger") state.sortKey = "latest_messenger_at";
        if (state.preset === "bright") { state.sortKey = "discovery_magnitude"; state.sortDir = 1; }
        Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (item) {
          var active = item === button; item.classList.toggle("is-active", active); item.setAttribute("aria-pressed", active ? "true" : "false");
        });
        renderTable();
      });
    });
    document.addEventListener("click", function (event) {
      var fragmentLink = event.target.closest('a[href^="#"]');
      if (fragmentLink) revealFragment(fragmentLink.getAttribute("href"));
      var open = event.target.closest("[data-open-candidate]");
      if (open) {
        var summary = state.candidates.find(function (candidate) { return candidate.name === open.getAttribute("data-open-candidate"); });
        if (summary) openCandidate(summary, true); return;
      }
      if (event.target.closest("[data-close-candidate]")) { closeCandidate(); return; }
      if (event.target.closest("[data-retry-candidate]")) {
        if (state.activeSummary) { delete state.chunks[state.activeSummary.detail_chunk]; openCandidate(state.activeSummary, false); } return;
      }
      if (event.target.closest("[data-copy-link]") && state.activeDetail) {
        var url = window.location.origin + window.location.pathname + "#candidate=" + encodeURIComponent(state.activeDetail.name);
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url);
        event.target.closest("[data-copy-link]").textContent = "Permalink copied"; return;
      }
      if (event.target.closest("[data-download-candidate]") && state.activeDetail) {
        downloadBlob(state.activeDetail.name + "-ctas.json", JSON.stringify(state.activeDetail, null, 2) + "\n", "application/json"); return;
      }
      var evidenceButton = event.target.closest("[data-download-evidence]");
      if (evidenceButton && state.activeDetail) {
        var kind = evidenceButton.getAttribute("data-download-evidence"), rows = (state.activeDetail.follow_up || {})[kind] || [];
        var format = evidenceButton.getAttribute("data-format");
        downloadBlob(state.activeDetail.name + "-" + kind + "." + format,
          format === "csv" ? rowsToCsv(rows) : JSON.stringify(rows, null, 2) + "\n",
          format === "csv" ? "text/csv" : "application/json"); return;
      }
      var sort = event.target.closest("[data-sort]");
      if (sort) {
        var key = sort.getAttribute("data-sort");
        if (state.sortKey === key) state.sortDir *= -1; else { state.sortKey = key; state.sortDir = key === "name" ? 1 : -1; }
        renderTable(); return;
      }
      if (event.target.closest("#ctas-more")) { state.shown += PAGE; renderTable(); return; }
      if (event.target.closest("[data-clear-inline]")) clearFilters();
    });
    document.addEventListener("change", function (event) {
      if (!event.target.matches("[data-phot-band]") || !state.activeDetail) return;
      state.photBand[state.activeDetail.name] = event.target.value;
      var panel = el.workspace.querySelector("[data-phot-panel]"); if (panel) panel.outerHTML = renderPhotometry(state.activeDetail);
    });
    window.addEventListener("hashchange", function () {
      if (candidateFromHash()) openHashCandidate(false); else revealFragment();
    });
    window.addEventListener("popstate", function () {
      if (candidateFromHash()) openHashCandidate(false); else revealFragment();
    });
  }

  function applySnapshot(index, status, universe, releaseHistory, cached) {
    state.snapshot = index; state.candidates = Array.isArray(index.candidates) ? index.candidates : [];
    state.status = status || {pipeline_status: "unknown", last_successful_update: index.generated_at, candidate_count: state.candidates.length};
    state.sourceUniverse = universe; state.releaseHistory = releaseHistory; state.cachedSnapshot = Boolean(cached);
    if (cached) state.status = Object.assign({}, state.status, {pipeline_status: "cached"});
    if (el.toolbar) el.toolbar.hidden = !state.candidates.length;
    renderStatus(); renderOverview(); renderSourceUniverse(); renderReleaseHistory(); populateFilters(); renderTable(); drawSky(); openHashCandidate(false); revealFragment();
  }
  function showLoadError(error) {
    var cached = null;
    try { cached = JSON.parse(sessionStorage.getItem(CACHE_KEY)); } catch (_) { cached = null; }
    if (cached && cached.index) {
      applySnapshot(cached.index, cached.status, cached.universe, cached.history, true);
      el.results.insertAdjacentHTML("beforebegin", '<div class="ctas-cache-warning"><strong>Showing the last successfully loaded snapshot.</strong> The current compact catalog could not be refreshed. <button type="button" id="ctas-retry-load">Retry now</button></div>');
      document.getElementById("ctas-retry-load").addEventListener("click", function () { window.location.reload(); });
      return;
    }
    el.results.innerHTML = '<div class="ctas-empty ctas-empty--error"><h3>CTAS data could not be loaded</h3><p>' + esc(error.message || "Unknown loading error") +
      '.</p><button type="button" onclick="window.location.reload()">Retry now</button></div>';
  }
  function boot() {
    el.results.innerHTML = '<p class="ctas-loading">Loading the compact public catalog…</p>';
    Promise.all([
      getJSON("catalog-index.json"), getJSON("status.json").catch(function () { return null; }),
      getJSON("source-universe.json").catch(function () { return null; }),
      getJSON("release-history.json").catch(function () { return null; })
    ]).then(function (result) {
      try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({index: result[0], status: result[1], universe: result[2], history: result[3]})); }
      catch (_) { /* cache is best effort */ }
      applySnapshot(result[0], result[1], result[2], result[3], false);
    }).catch(showLoadError);
  }
  function pollStatus() {
    getJSON("status.json", 2).then(function (status) {
      var oldChecksum = (state.status || {}).catalog_content_checksum_sha256;
      state.status = status; renderStatus();
      if (oldChecksum && status.catalog_content_checksum_sha256 && oldChecksum !== status.catalog_content_checksum_sha256) {
        getJSON("catalog-index.json", 2).then(function (index) {
          state.snapshot = index; state.candidates = index.candidates || []; state.chunks = {};
          renderOverview(); populateFilters(); renderTable(); drawSky();
        });
      }
    }).catch(function () { /* the loaded catalog remains usable */ });
  }

  bindInterface(); bindSky(); boot(); window.setInterval(pollStatus, 120000);
}());
