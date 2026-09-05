/* CTAS public catalog: compact index first, complete evidence on demand. */
(function () {
  "use strict";

  var DATA_DIR = "ctas/data/";
  var PAGE = 100;
  var CACHE_KEY = "ctas-public-bootstrap-v4";
  var PUBLIC_LINK_HOSTS = {
    "api.fink-portal.org": 1, "api.ztf.fink-portal.org": 1, "fink-portal.org": 1,
    "apps.aavso.org": 1, "archive.eso.org": 1, "archive.gemini.edu": 1,
    "archive.stsci.edu": 1, "asas-sn.osu.edu": 1, "blackgem.org": 1,
    "cgbm.calet.jp": 1, "chime-experiment.ca": 1, "doc.lsst.fink-broker.org": 1,
    "docs.aavso.org": 1, "ep.bao.ac.cn": 1, "fallingstar-data.com": 1,
    "gcn.gsfc.nasa.gov": 1, "gcn.nasa.gov": 1, "github.com": 1,
    "goto-observatory.org": 1, "heasarc.gsfc.nasa.gov": 1,
    "irsa.ipac.caltech.edu": 1, "lasair.readthedocs.io": 1, "lasair-ztf.lsst.ac.uk": 1,
    "mast.stsci.edu": 1,
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
    candidates: [], skyCandidates: [], resolvedCandidates: {}, releaseEpoch: 0, routeRequest: 0,
    snapshot: null, status: null, sourceUniverse: null, releaseHistory: null,
    catalogManifest: null, aliasIndex: null, aliasPromise: null,
    chunks: {}, activeSummary: null, activeDetail: null, cachedSnapshot: false,
    sortKey: "ctas_score", sortDir: -1, preset: "all", q: "", cls: "", msg: "",
    stat: "", survey: "", from: "", to: "", magMin: null, magMax: null,
    scoreMin: null, scoreMax: null, spectrum: "", conflict: "", richness: "",
    coneRa: null, coneDec: null, coneRadius: null,
    shown: PAGE, skyDays: 7, skyPoints: [], skySelected: null,
    hoveredEventId: null, focusedEventId: null, linkedHighlightId: null,
    skyKeyboardIndex: -1, photBand: {}, activeOpener: null,
    autoRefreshPaused: false, exportBusy: false, refreshError: null, polling: false
  };

  function normalizeWorkspaceOrder() {
    var host = document.querySelector(".ctas-interface > .wrap");
    if (!host) return;
    ["celestial-sphere", "recent-stream", "ranked-candidates", "ctas-learn", "about-ctas", "ctas-reference", "methods-and-use"].forEach(function (id) {
      var section = document.getElementById(id); if (section) host.appendChild(section);
    });
  }
  normalizeWorkspaceOrder();

  var el = {
    status: document.getElementById("ctas-status"),
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
    downloadStatus: document.getElementById("ctas-download-status"),
    downloadParts: document.getElementById("ctas-download-parts"),
    toolbar: document.getElementById("ctas-toolbar"),
    results: document.getElementById("ctas-results"),
    loadComplete: document.getElementById("ctas-load-complete"),
    completeStatus: document.getElementById("ctas-complete-status"),
    workspace: document.getElementById("candidate-workspace"),
    count: document.getElementById("ctas-count"),
    clear: document.getElementById("ctas-clear"),
    q: document.getElementById("ctas-q"),
    cls: document.getElementById("ctas-class"),
    msg: document.getElementById("ctas-messenger"),
    stat: document.getElementById("ctas-statusfilter"),
    survey: document.getElementById("ctas-survey"),
    from: document.getElementById("ctas-from"),
    to: document.getElementById("ctas-to"),
    scoreMin: document.getElementById("ctas-score-min"),
    scoreMax: document.getElementById("ctas-score-max"),
    magMax: document.getElementById("ctas-mag-max"),
    spectrum: document.getElementById("ctas-spectrum-filter"),
    conflict: document.getElementById("ctas-conflict-filter"),
    richness: document.getElementById("ctas-richness-filter"),
    coneRa: document.getElementById("ctas-cone-ra"),
    coneDec: document.getElementById("ctas-cone-dec"),
    coneRadius: document.getElementById("ctas-cone-radius"),
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
  function reducedMotion() {
    return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }
  function qualityText(value) {
    if (!Array.isArray(value)) return text(value);
    return value.map(function (item) {
      if (!item || typeof item !== "object") return text(item);
      return Object.keys(item).sort().map(function (key) { return humanKey(key) + ": " + text(item[key]); }).join("; ");
    }).filter(Boolean).join(" · ");
  }
  function parsedValue(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "object") return value;
    try { return JSON.parse(value); } catch (_) { return fallback; }
  }
  function structuredText(value, emptyLabel) {
    if (value === null || value === undefined || value === "") return emptyLabel || "Not retained";
    var parsed = parsedValue(value, value);
    return typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2);
  }
  function retainedRecordDetails(label, row) {
    return '<details class="ctas-record-details"><summary>' + esc(label || "Complete retained record") +
      '</summary><pre>' + esc(structuredText(row, "No retained fields")) + '</pre></details>';
  }
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
    } else if (host === "www.wis-tns.org" && path.indexOf("/object") === 0) {
      return {unavailable: true, label: "Original TNS link unavailable (not an exact public object path)"};
    } else if (host === "lasair-ztf.lsst.ac.uk" && /^\/object\/ZTF\d{2}[a-z]+\/?$/i.test(path)) {
      label = "Open Lasair object record"; kind = "object";
    } else if (host === "lasair-ztf.lsst.ac.uk") {
      return {unavailable: true, label: "Original Lasair link unavailable (not an exact public object path)"};
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
    var factors = candidate.score_factors || {}, model = candidate.score_model || {};
    var terms = Array.isArray(model.terms) ? model.terms : Object.keys(labels).filter(function (key) {
      return key !== "status" && factors[key] !== undefined && factors[key] !== null;
    }).map(function (key) {
      return {code: key, label: labels[key], points: (key === "coverage_reduction" ? -1 : 1) * Number(factors[key] || 0), basis: "persisted score factor"};
    });
    var visualRows = [{code: "baseline", label: "Baseline", points: Number(model.baseline === undefined ? 35 : model.baseline), basis: "CTAS score model"}].concat(terms);
    if (Number(model.multimessenger_bonus || 0)) visualRows.push({code: "multimessenger_bonus", label: "Messenger diversity", points: Number(model.multimessenger_bonus), basis: "retained messenger channels"});
    if (Number(model.persisted_factor_rounding_residual || 0)) visualRows.push({code: "persisted_factor_rounding_residual", label: "Persisted-factor rounding", points: Number(model.persisted_factor_rounding_residual), basis: "explicit ≤0.01 residual from independently rounded persisted terms"});
    var bars = visualRows.map(function (row) {
      var negative = Number(row.points) < 0, size = Math.min(50, Math.abs(Number(row.points) || 0) / 100 * 100).toFixed(2) + "%";
      return '<div class="ctas-score-waterfall__row' + (negative ? " is-negative" : "") + '"><span>' + esc(row.label) + '<small class="ctas-table-sub">' + esc(row.basis || "") + '</small></span><span class="ctas-score-waterfall__track" aria-hidden="true"><i style="--size:' + size + '"></i></span><strong>' + (negative ? "−" : "+") + Math.abs(Number(row.points || 0)).toFixed(2) + '</strong></div>';
    }).join("");
    var arithmetic = '<div class="ctas-score-waterfall__row"><span>Core before clipping</span><span></span><strong>' + esc(num(model.core_preclip, 2) || "—") + '</strong></div><div class="ctas-score-waterfall__row"><span>Core after 0–100 clipping</span><span></span><strong>' + esc(num(model.core_postclip, 2) || "—") + '</strong></div><div class="ctas-score-waterfall__row"><span>Final before clipping</span><span></span><strong>' + esc(num(model.final_preclip, 2) || "—") + '</strong></div>' +
      (model.status_override ? '<div class="ctas-score-waterfall__row is-negative"><span>Terminal status override: ' + esc(humanKey(model.status_override)) + '</span><span></span><strong>0.00</strong></div>' : "") +
      '<div class="ctas-score-waterfall__row"><span><strong>Published CTAS score</strong></span><span></span><strong>' + esc(num(candidate.ctas_score, 2)) + '</strong></div>';
    var tableRows = visualRows.map(function (row) { return '<tr><th scope="row">' + esc(row.label) + '</th><td>' + esc(row.code) + '</td><td>' + (Number(row.points) < 0 ? "−" : "+") + Math.abs(Number(row.points || 0)).toFixed(2) + '</td><td>' + esc(row.basis || "") + '</td></tr>'; }).join("");
    var sandbox = '<details class="ctas-score-sandbox"><summary>Try an educational what-if scenario</summary><p>These controls demonstrate the arithmetic only. They never alter ranking, the URL event score, the retained dossier, or any export.</p><div class="ctas-score-sandbox__controls">' + terms.map(function (row) {
      return '<label>' + esc(row.label) + '<input type="range" min="-20" max="25" step=".5" value="' + esc(Number(row.points || 0)) + '" data-score-sandbox="' + esc(row.code) + '"></label>';
    }).join("") + '</div><p>Scenario result: <output data-score-sandbox-output>' + esc(num(candidate.ctas_score, 2)) + '</output></p><button type="button" data-reset-score-sandbox>Reset scenario</button></details>';
    return '<details class="ctas-score-factors" data-dossier-view="score"><summary>Why this candidate has this CTAS score <small>' + (model.reconciled ? "arithmetic reconciled" : "reconciliation unavailable") + '</small></summary><p>' +
      esc(candidate.score_explanation || "The displayed terms reproduce the public follow-up ordering score.") +
      '</p><p class="ctas-claim-boundary">Operational follow-up ordering aid only—not probability, confidence, or scientific importance.</p><div class="ctas-score-waterfall" aria-label="CTAS score waterfall">' + bars + arithmetic + '</div><details><summary>Exact score arithmetic table</summary><div class="ctas-evidence-table-wrap"><table class="ctas-evidence-table"><caption>Signed persisted terms in evaluation order; any ≤0.01 factor-rounding residual, clipping, and terminal override are explicit.</caption><thead><tr><th>Term</th><th>Code</th><th>Points</th><th>Basis</th></tr></thead><tbody>' + tableRows + '</tbody></table></div></details>' + sandbox + '</details>';
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

  function renderReceiptDetails(receipt, extension) {
    var nullableExtension = {
      receiptId: receipt.receiptId,
      sourceContractId: receipt.sourceContractId,
      targetIdentity: null,
      providerRelease: null,
      normalizedRequest: null,
      responseStatus: null,
      pagination: null,
      caps: null,
      immutableArtifactReference: null,
      latencyMs: null,
      retryCount: null,
      errorCategory: null,
      metadataCompleteness: "LEGACY_NULLABLE",
      executionState: null
    };
    var completeExtension = Object.assign(nullableExtension, extension || {});
    return '<details class="ctas-receipt-detail"><summary>Complete receipt details</summary>' +
      '<p>Core receipt and compatibility extension are joined by the stable receipt ID. Null means the value was not retained; it is not inferred.</p>' +
      '<div class="ctas-code-grid"><div><h4>Core persisted receipt</h4><pre>' +
      esc(JSON.stringify(receipt, null, 2)) + '</pre></div><div><h4>Receipt provenance extension</h4><pre>' +
      esc(JSON.stringify(completeExtension, null, 2)) + '</pre></div></div></details>';
  }

  function renderSourceCoverage(candidate) {
    var accounting = candidate.source_accounting || {}, rows = Array.isArray(candidate.source_matrix) ? candidate.source_matrix : [];
    var universeRows = ((state.sourceUniverse || {}).sources || []), sourceById = {};
    universeRows.forEach(function (row) { sourceById[row.source_key] = row; });
    var receiptRows = ((candidate.astro_evidence || {}).persistedQueryReceipts || []);
    var compatibility = candidate.compatibility_provenance || {};
    var receiptExtensions = compatibility.receiptExtensions || compatibility.receiptProvenance ||
      ((candidate.astro_evidence || {}).receiptExtensions || []), extensionByReceiptId = {};
    receiptExtensions.forEach(function (row) { if (row && row.receiptId) extensionByReceiptId[row.receiptId] = row; });
    var counts = [
      ["Declared", accounting.declaredSources, "contracts in this source-universe version"],
      ["Applicable", accounting.applicableSources, "contracts whose event rule applies"],
      ["Executed", accounting.executedQueryReceipts, "persisted query-attempt receipts"],
      ["Data-bearing", accounting.dataBearingSources, "providers with retained public evidence"]
    ];
    var metricHtml = '<div class="ctas-source-denominators">' + counts.map(function (item) {
      return '<div><strong>' + Number(item[1] || 0).toLocaleString() + '</strong><span>' + esc(item[0]) +
        '</span><small>' + esc(item[2]) + '</small></div>';
    }).join("") + "</div>";
    if (!rows.length) return '<details class="ctas-source-coverage" data-dossier-view="sources"><summary>Source accounting and receipts</summary>' +
      metricHtml + "<p>No applicable-source matrix is retained. This means not assessed—not searched with no match.</p></details>";
    var table = '<div class="ctas-evidence-table-wrap" role="region" aria-label="Applicable source coverage matrix" tabindex="0"><table class="ctas-evidence-table ctas-source-matrix">' +
      '<caption>Every applicable source has an explicit current terminal state. Query health and retained evidence are separate columns.</caption>' +
      '<thead><tr><th scope="col">Source</th><th scope="col">Current query health</th><th scope="col">Persisted attempts</th>' +
      '<th scope="col">Retained evidence</th><th scope="col">Evidence age / state</th><th scope="col">Contract</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var source = sourceById[row.sourceContractId] || {name: row.sourceContractId};
        var types = Object.keys(row.retainedRecordTypes || {}).map(function (key) {
          return Number(row.retainedRecordTypes[key]).toLocaleString() + " " + humanKey(key).toLowerCase();
        }).join(" · ");
        var age = row.retainedEvidenceLatestAt ? relative(row.retainedEvidenceLatestAt) + " (" + absolute(row.retainedEvidenceLatestAt) + ")" : "No retained evidence clock";
        return '<tr><th scope="row"><strong>' + esc(source.name || row.sourceContractId) + '</strong><small>' + esc(row.sourceContractId) +
          '</small></th><td><span class="pill">' + esc(humanKey(row.currentQueryOutcome || "NOT_QUERIED")) + '</span><small>' +
          esc(row.currentQueryCheckedAt ? absolute(row.currentQueryCheckedAt) : "No event-specific query clock") + '</small></td><td class="num">' +
          Number(row.executedReceiptCount || 0).toLocaleString() + '</td><td><strong>' + Number(row.retainedRecordCount || 0).toLocaleString() +
          '</strong><small>' + esc(types || "No retained rows") + '</small></td><td><span class="ctas-evidence-state">' +
          esc(humanKey(row.retainedEvidenceState || "NO_RETAINED_EVIDENCE")) + '</span><small>' + esc(age) + '</small></td><td>' +
          renderReferences([source], [["documentation_url", "Open provider documentation"]]) + '<details class="ctas-inline-rule"><summary>Why applicable</summary><p>' +
          esc(row.applicabilityRule || "Referenced by the declared event rule.") + '</p></details></td></tr>';
      }).join("") + "</tbody></table></div>";
    var history = receiptRows.length ? '<details class="ctas-receipt-history"><summary>Append-only persisted receipt history <small>' + receiptRows.length +
      ' receipts</small></summary><div class="ctas-evidence-table-wrap" role="region" aria-label="Persisted source-query receipt history" tabindex="0"><table class="ctas-evidence-table"><caption>Legacy receipts retain null fields where the original provider release, latency, pagination, or response checksum was not stored.</caption>' +
      '<thead><tr><th scope="col">Completed</th><th scope="col">Source</th><th scope="col">Query</th><th scope="col">Outcome</th><th scope="col">Rows seen / retained / rejected</th><th scope="col">Response checksum</th><th scope="col">Full receipt</th></tr></thead><tbody>' +
      receiptRows.map(function (row) { return '<tr><td>' + esc(absolute(row.completedAt)) + '</td><td>' + esc(row.sourceContractId) +
        '</td><td>' + esc(humanKey(row.queryKind)) + '</td><td><span class="pill">' + esc(humanKey(row.outcome)) + '</span></td><td>' +
        esc([row.recordsSeen, row.recordsRetained, row.recordsRejected].map(function (value) { return value === null || value === undefined ? "unknown" : Number(value).toLocaleString(); }).join(" / ")) +
        '</td><td><code>' + esc(row.responseChecksumSha256 ? shortHash(row.responseChecksumSha256) + "…" : "not retained") + '</code></td><td>' +
        renderReceiptDetails(row, extensionByReceiptId[row.receiptId]) + '</td></tr>'; }).join("") +
      '</tbody></table></div></details>' : '<p>No persisted event-specific query receipt exists; applicable sources still retain explicit not-queried or link-only states.</p>';
    return '<details class="ctas-source-coverage" data-dossier-view="sources"><summary>Source accounting and receipts <small>' + rows.length +
      " applicable source contracts</small></summary>" + metricHtml +
      '<p>Provider failure, blockage, and unperformed searches are not “no match.” A failed current check does not erase an older rights-cleared measurement.</p>' + table + history + "</details>";
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

  function renderScienceBrief(candidate) {
    var brief = candidate.science_brief;
    if (!brief || typeof brief !== "object") return candidateSummary(candidate).details;
    function listRows(rows, renderer, empty) {
      return rows && rows.length ? "<ul>" + rows.slice(0, 4).map(renderer).join("") + "</ul>" : "<p>" + esc(empty) + "</p>";
    }
    var known = listRows(brief.confidently_known, function (row) { return "<li><strong>" + esc(row.label) + ":</strong> " + esc(row.value) + "</li>"; }, "No summary fact is promoted beyond the retained source record.");
    var uncertain = listRows(brief.uncertain_or_conflicting, function (row) { return "<li><strong>" + esc(humanKey(row.label)) + ":</strong> " + esc(humanKey(row.state)) + "</li>"; }, "No explicit conflict state is retained.");
    var missing = listRows(brief.missing_information, function (row) { return "<li>" + esc(row.label) + " <small>(" + esc(humanKey(row.state)) + ")</small></li>"; }, "No applicable component is marked missing or not assessed.");
    var changed = brief.most_recent_change ? "<p><strong>" + esc(humanKey(brief.most_recent_change.evidence_type)) + ":</strong> " + esc(brief.most_recent_change.title || "Retained evidence update") + "</p><p>" + esc([brief.most_recent_change.provider, brief.most_recent_change.public_available_at ? absolute(brief.most_recent_change.public_available_at) : "availability clock unavailable"].filter(Boolean).join(" · ")) + "</p>" : "<p>No provider change is retained.</p>";
    var visibility = "<p><strong>INSUFFICIENT_DATA:</strong> valid coordinates are not retained.</p>";
    if (window.CTASObservability && finiteNumber(candidate.ra_deg) && finiteNumber(candidate.dec_deg)) {
      var planning = window.CTASObservability.evaluate(candidate, {date: new Date().toISOString().slice(0, 10), latitude_deg: 41.0983,
        longitude_deg: -105.976, min_altitude_deg: 30, max_airmass: 3, max_sun_altitude_deg: -12, min_moon_separation_deg: 20});
      visibility = planning.intervals && planning.intervals.length
        ? "<p><strong>" + planning.intervals.length + " geometric window" + (planning.intervals.length === 1 ? "" : "s") + "</strong> pass the default WIR constraints today (UTC).</p><p>Open the observability planner to inspect or change the assumptions.</p>"
        : "<p>No interval passes the default WIR geometric constraints today (UTC).</p><p>This is not a weather or telescope-availability claim.</p>";
    }
    return '<details class="ctas-science-brief" data-dossier-view="brief"><summary><span id="ctas-science-brief-title">At a glance</span><small>' +
      Number((brief.confidently_known || []).length).toLocaleString() + " known · " + Number((brief.uncertain_or_conflicting || []).length).toLocaleString() +
      " uncertain · " + Number((brief.missing_information || []).length).toLocaleString() +
      ' missing</small></summary><div class="ctas-science-brief__body"><div class="ctas-science-brief__grid"><section><h5>What happened</h5><p>' + esc((brief.what_happened || {}).text || "Public CTAS event record.") + '</p></section><section><h5>Known</h5>' + known + '</section><section><h5>Uncertain</h5>' + uncertain + '</section><section><h5>Missing</h5>' + missing + '</section><section><h5>Latest evidence</h5>' + changed + '</section><section><h5>Visible from WIR today?</h5>' + visibility + '</section></div><p class="ctas-claim-boundary">' + esc(brief.claim_boundary) + '</p></div></details>';
  }

  function messengerProperties(row) {
    var parsed = parsedValue(row && row.properties, {});
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  }
  function messengerRevision(row) {
    var properties = messengerProperties(row), explicit = properties.revision;
    if (explicit === undefined || explicit === null || explicit === "") explicit = row && row.revision;
    if (explicit !== undefined && explicit !== null && explicit !== "") return text(explicit).replace(/^r/i, "");
    var match = text(row && row.provider_signal_id).match(/:r(\d+)$/i);
    return match ? match[1] : "";
  }
  function messengerBaseId(row) {
    return text(row && row.provider_signal_id).replace(/:r\d+$/i, "");
  }
  function messengerRole(row) {
    var role = text(row && (row.role || row.alert_type)).toLowerCase();
    if (/retract|withdraw|cancel/.test(role)) return "Retraction";
    var revision = messengerRevision(row);
    return revision !== "" && Number(revision) > 0 ? "Revision" : humanKey(row && (row.alert_type || row.role) || "Notice");
  }
  function messengerLineage(row, rows) {
    var properties = messengerProperties(row), base = messengerBaseId(row), revision = messengerRevision(row);
    var family = (rows || []).filter(function (candidate) { return base && messengerBaseId(candidate) === base; }).sort(function (a, b) {
      return Number(messengerRevision(a) || 0) - Number(messengerRevision(b) || 0);
    });
    var index = family.indexOf(row), previous = index > 0 ? family[index - 1] : null, next = index >= 0 && index < family.length - 1 ? family[index + 1] : null;
    return {
      revision: revision,
      supersedes: properties.supersedes || properties.supersedes_notice || row.supersedes || (previous && previous.provider_signal_id) || null,
      supersededBy: properties.superseded_by || properties.supersededBy || row.superseded_by || (next && next.provider_signal_id) || null,
      role: messengerRole(row),
      basis: (properties.supersedes || properties.supersedes_notice || properties.superseded_by || properties.supersededBy || row.supersedes || row.superseded_by)
        ? "provider-retained lineage" : family.length > 1 ? "provider identifier revision sequence" : "no linked revision retained"
    };
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
      return [row.subtype, row.method, finiteNumber(row.probability) ? num(100 * row.probability, 1) + "% reported probability" : ""].filter(Boolean).join(" · ");
    });
    add(follow.spectra, "Spectrum", "observed_at", function (row) { return row.file_name || row.provider_spectrum_id || "Spectrum metadata"; },
      function (row) { return [row.telescope, row.instrument, row.calibration_state].filter(Boolean).join(" · "); });
    add(follow.messenger_signals, function (row) { return messengerRole(row); }, "observed_at", function (row) {
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
    return '<details class="ctas-timeline" data-dossier-view="timeline"><summary>Concise scientific timeline <small>' + entries.length +
      " entries</small></summary><ol>" + entries.map(function (entry) {
        return '<li><div class="ctas-timeline__clocks"><span>' + esc(entry.time ? absolute(entry.time) : "Time not recorded") +
          '</span></div><div><span class="pill">' + esc(entry.kind) + "</span><strong>" + esc(entry.title) +
          "</strong><small>" + esc(entry.provider || "Provider not recorded") + "</small>" +
          (entry.summary ? "<p>" + esc(entry.summary) + "</p>" : "") + (entry.row ? renderReferences([entry.row], undefined, false) : "") + "</div></li>";
      }).join("") + "</ol></details>";
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
    var bands = {}, selected = state.photBand[candidate.event_id] || "*";
    rows.forEach(function (row) { if (row.band) bands[row.band] = true; });
    var filtered = selected === "*" ? rows : rows.filter(function (row) { return text(row.band) === selected; });
    return '<details class="ctas-evidence-panel" data-phot-panel data-dossier-view="photometry"><summary>Photometry <small>' + rows.length.toLocaleString() +
      ' retained rows</small></summary><div class="ctas-evidence-panel__body"><div class="ctas-evidence-tools"><label>Band <select data-phot-band>' +
      '<option value="*">All bands</option>' + Object.keys(bands).sort().map(function (band) {
        return '<option value="' + esc(band) + '"' + (selected === band ? " selected" : "") + ">" + esc(band) + "</option>";
      }).join("") + '</select></label><button type="button" data-download-evidence="observations" data-format="csv">Download CSV</button>' +
      '<button type="button" data-download-evidence="observations" data-format="json">Download JSON</button></div>' +
      photometrySvg(filtered) + renderReferences(rows) +
      '<div class="ctas-evidence-table-wrap ctas-evidence-table-wrap--tall" role="region" aria-label="Complete source-native photometry table" tabindex="0"><table class="ctas-evidence-table"><caption>All ' + filtered.length.toLocaleString() +
      " matching rows are inspectable here" + (selected === "*" ? "." : "; the candidate retains " + rows.length.toLocaleString() + " rows across all bands.") +
      '</caption><thead><tr><th scope="col">Observed</th><th scope="col">Band</th><th scope="col">Detection / limit</th><th scope="col">Source-native value</th><th scope="col">System · method</th><th scope="col">Facility</th><th scope="col">Assertion / source</th></tr></thead><tbody>' +
      filtered.map(function (row) {
        var measure = finiteNumber(row.magnitude) ? num(row.magnitude, 3) + (finiteNumber(row.magnitude_error) ? " ± " + num(row.magnitude_error, 3) : "") :
          finiteNumber(row.flux) ? num(row.flux, 6) + (finiteNumber(row.flux_error) ? " ± " + num(row.flux_error, 6) : "") + " " + esc(row.flux_unit || "") :
          finiteNumber(row.limiting_magnitude) ? "magnitude &gt; " + esc(num(row.limiting_magnitude, 3)) :
          finiteNumber(row.limiting_flux) ? "flux ≤ " + esc(num(row.limiting_flux, 6)) + " " + esc(row.flux_unit || "") : "not reported";
        var semantics = row.detection ? "Detection" : finiteNumber(row.limiting_flux) ? "Nondetection · flux upper limit" : finiteNumber(row.limiting_magnitude) ? "Nondetection · magnitude lower bound" : "Nondetection reported";
        if (row.superseded) semantics += " · superseded by revision " + text(row.superseded_by_revision || "not identified");
        return "<tr><td>" + esc(absolute(row.observed_at)) + "</td><td>" + esc(row.band || row.original_band || "—") + "</td><td><strong>" + esc(semantics) +
          "</strong></td><td>" + measure + "</td><td>" + esc([row.magnitude_system, row.photometry_method || row.pipeline,
            row.difference_photometry ? "difference photometry" : "direct/unspecified"].filter(Boolean).join(" · ") || "—") + "</td><td>" +
          esc(row.telescope || row.observatory || row.instrument || "—") + "</td><td><code>" + esc(shortHash(row.assertion_id || row.provider_observation_id || "not retained")) +
          (row.assertion_id || row.provider_observation_id ? "…" : "") + "</code>" + renderReferences([row]) + "</td></tr>";
      }).join("") + "</tbody></table></div></div></details>";
  }

  function spectrumPreviewPoints(row) {
    var raw = parsedValue(row && row.preview_points, []);
    if (!Array.isArray(raw)) return [];
    return raw.map(function (point, index) {
      var wavelength, flux;
      if (Array.isArray(point)) { wavelength = point[0]; flux = point[1]; }
      else if (point && typeof point === "object") {
        wavelength = point.wavelength;
        if (wavelength === undefined) wavelength = point.wavelength_value;
        if (wavelength === undefined) wavelength = point.wavelength_angstrom;
        if (wavelength === undefined) wavelength = point.lambda;
        flux = point.flux;
        if (flux === undefined) flux = point.flux_value;
        if (flux === undefined) flux = point.flux_density;
      }
      return {index: index + 1, wavelength: Number(wavelength), flux: Number(flux)};
    }).filter(function (point) { return isFinite(point.wavelength) && isFinite(point.flux); });
  }
  function spectrumNumber(value) {
    var number = Number(value), absoluteValue = Math.abs(number);
    if (!isFinite(number)) return "";
    return absoluteValue && (absoluteValue < 0.001 || absoluteValue >= 100000) ? number.toExponential(6) : Number(number.toPrecision(8)).toString();
  }
  function spectrumSvg(row, points, index) {
    if (!points.length) return '<p class="ctas-link-empty">No rights-cleared numerical preview points are retained for this spectrum.</p>';
    var cap = 900, plotted = points.length <= cap ? points : Array.from({length: cap}, function (_, pointIndex) {
      return points[Math.floor(pointIndex * (points.length - 1) / (cap - 1))];
    });
    var minX = points[0].wavelength, maxX = points[0].wavelength, minY = points[0].flux, maxY = points[0].flux;
    points.forEach(function (point) {
      minX = Math.min(minX, point.wavelength); maxX = Math.max(maxX, point.wavelength);
      minY = Math.min(minY, point.flux); maxY = Math.max(maxY, point.flux);
    });
    if (minX === maxX) { minX -= 0.5; maxX += 0.5; }
    if (minY === maxY) { minY -= Math.abs(minY || 1) * 0.05; maxY += Math.abs(maxY || 1) * 0.05; }
    var yPadding = (maxY - minY) * 0.04; minY -= yPadding; maxY += yPadding;
    var width = 820, height = 310, left = 76, right = 22, top = 24, bottom = 52;
    function x(point) { return left + (point.wavelength - minX) / (maxX - minX) * (width - left - right); }
    function y(point) { return top + (maxY - point.flux) / (maxY - minY) * (height - top - bottom); }
    var path = plotted.map(function (point, pointIndex) {
      return (pointIndex ? "L" : "M") + num(x(point), 2) + " " + num(y(point), 2);
    }).join(" ");
    var titleId = "ctas-spectrum-title-" + index, descriptionId = "ctas-spectrum-description-" + index;
    var wavelengthUnit = row.wavelength_unit || "source-native wavelength units", fluxUnit = row.flux_unit || "source-native flux units";
    return '<figure class="ctas-spectrum-preview"><svg viewBox="0 0 ' + width + " " + height + '" role="img" aria-labelledby="' +
      titleId + " " + descriptionId + '"><title id="' + titleId + '">Spectrum preview for ' +
      esc(row.file_name || row.provider_spectrum_id || "retained spectrum") + '</title><desc id="' + descriptionId + '">' +
      esc(points.length.toLocaleString() + " retained wavelength and flux pairs, spanning " + spectrumNumber(minX) + " to " + spectrumNumber(maxX) + " " + wavelengthUnit + ". The complete numerical table follows the plot.") +
      '</desc><rect x="' + left + '" y="' + top + '" width="' + (width - left - right) + '" height="' + (height - top - bottom) + '" class="ctas-plot-bg"/>' +
      '<line x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + (height - bottom) + '" class="ctas-axis"/>' +
      '<line x1="' + left + '" y1="' + (height - bottom) + '" x2="' + (width - right) + '" y2="' + (height - bottom) + '" class="ctas-axis"/>' +
      '<path d="' + path + '" class="ctas-spectrum-line"/><text x="12" y="' + (height / 2) + '" transform="rotate(-90 12 ' + (height / 2) + ')" class="ctas-axis-label">Flux (' + esc(fluxUnit) + ')</text>' +
      '<text x="' + (width / 2) + '" y="' + (height - 12) + '" text-anchor="middle" class="ctas-axis-label">Wavelength (' + esc(wavelengthUnit) + ')</text>' +
      '<text x="' + left + '" y="' + (height - 31) + '" class="ctas-axis-label">' + esc(spectrumNumber(minX)) + '</text><text x="' + (width - right) + '" y="' + (height - 31) + '" text-anchor="end" class="ctas-axis-label">' + esc(spectrumNumber(maxX)) + '</text>' +
      '<text x="' + (left - 8) + '" y="' + (top + 4) + '" text-anchor="end" class="ctas-axis-label">' + esc(spectrumNumber(maxY)) + '</text><text x="' + (left - 8) + '" y="' + (height - bottom) + '" text-anchor="end" class="ctas-axis-label">' + esc(spectrumNumber(minY)) + '</text></svg>' +
      '<figcaption>' + esc(plotted.length.toLocaleString()) + " of " + esc(points.length.toLocaleString()) +
      ' retained points drawn for browser performance; every point is available in the table and downloads below.</figcaption></figure>';
  }
  function renderSpectrumPreview(row, index) {
    var points = spectrumPreviewPoints(row);
    if (!points.length) return '<p class="ctas-link-empty">Metadata-only record: no rights-cleared numerical preview points are retained.</p>';
    return spectrumSvg(row, points, index) + '<div class="ctas-evidence-tools"><button type="button" data-download-spectrum="' + index +
      '" data-format="csv">Download preview CSV</button><button type="button" data-download-spectrum="' + index +
      '" data-format="json">Download preview JSON</button></div><details class="ctas-spectrum-points"><summary>Inspect all ' +
      points.length.toLocaleString() + ' numerical preview points</summary><div class="ctas-evidence-table-wrap ctas-evidence-table-wrap--tall" role="region" aria-label="Complete retained spectrum preview point table" tabindex="0"><table class="ctas-evidence-table ctas-spectrum-table"><caption>All rights-cleared numerical points retained in this public preview.</caption>' +
      '<thead><tr><th scope="col">Point</th><th scope="col">Wavelength (' + esc(row.wavelength_unit || "source-native units") +
      ')</th><th scope="col">Flux (' + esc(row.flux_unit || "source-native units") + ')</th></tr></thead><tbody>' + points.map(function (point) {
        return '<tr><th scope="row">' + point.index.toLocaleString() + '</th><td>' + esc(spectrumNumber(point.wavelength)) +
          '</td><td>' + esc(spectrumNumber(point.flux)) + '</td></tr>';
      }).join("") + '</tbody></table></div></details>';
  }
  function renderSpectra(candidate) {
    var rows = (candidate.follow_up || {}).spectra || [];
    if (!rows.length) return "";
    return '<details class="ctas-evidence-panel" data-dossier-view="spectra"><summary>Spectra <small>' + rows.length +
      " public record" + (rows.length === 1 ? "" : "s") +
      '</small></summary><div class="ctas-evidence-panel__body"><p>Numerical previews appear only when rights-cleared wavelength and flux pairs are retained. Metadata-only records remain explicit, and provider record links are distinct from downloadable artifacts.</p>' +
      '<div class="ctas-evidence-tools"><button type="button" data-download-evidence="spectra" data-format="json">Download all spectra JSON</button></div><ul class="ctas-record-list ctas-spectrum-list">' +
      rows.map(function (row, index) {
        return '<li><details class="ctas-spectrum-record"><summary><span><strong>' + esc(row.file_name || row.provider_spectrum_id || "Spectrum") + '</strong><small>' +
          esc([row.observed_at ? absolute(row.observed_at) : "time unavailable", row.telescope, row.instrument,
            row.wavelength_unit, row.calibration_state].filter(Boolean).join(" · ")) + '</small></span></summary><div class="ctas-spectrum-record__body">' +
          (row.file_checksum || row.checksum_sha256 ? "<p><strong>File SHA-256:</strong> <code>" + esc(row.file_checksum || row.checksum_sha256) + "</code></p>" : "") +
          renderReferences([row], [["source_url", null], ["public_download_url", "Download source artifact"]]) +
          renderSpectrumPreview(row, index) + retainedRecordDetails("Inspect complete retained spectrum record", row) + '</div></details></li>';
      }).join("") + "</ul></div></details>";
  }

  function renderMessenger(candidate) {
    var rows = (candidate.follow_up || {}).messenger_signals || [];
    if (!rows.length) return "";
    return '<details class="ctas-evidence-panel" data-dossier-view="messenger"><summary>Messenger notices <small>' + rows.length +
      '</small></summary><div class="ctas-evidence-panel__body"><div class="ctas-evidence-tools"><button type="button" data-download-evidence="messenger_signals" data-format="csv">Download CSV</button><button type="button" data-download-evidence="messenger_signals" data-format="json">Download JSON</button></div>' +
      '<div class="ctas-evidence-table-wrap ctas-evidence-table-wrap--tall" role="region" aria-label="Complete messenger notice and revision table" tabindex="0"><table class="ctas-evidence-table ctas-messenger-table"><caption>All retained notices are shown. Retractions remain visible, and revision lineage is explicit rather than silently replacing earlier messages.</caption><thead><tr><th scope="col">Scientific time</th><th scope="col">Notice / revision</th><th scope="col">State and lineage</th><th scope="col">Messenger / detection</th><th scope="col">Significance / false-alarm rate</th><th scope="col">Localization / distance</th><th scope="col">Source and complete record</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var lineage = messengerLineage(row, rows), roleClass = lineage.role === "Retraction" ? " is-retracted" : "";
        var lineageText = [lineage.supersedes ? "Supersedes " + lineage.supersedes : "",
          lineage.supersededBy ? "Superseded by " + lineage.supersededBy : "Current/latest retained revision",
          lineage.basis].filter(Boolean).join(" · ");
        return "<tr><td>" + esc(absolute(row.observed_at || row.event_time)) + "</td><th scope=\"row\"><strong>" +
          esc(row.provider_signal_id || row.assertion_id || "Provider notice") + "</strong><small>" +
          esc(lineage.revision === "" ? "Revision not encoded" : "Revision r" + lineage.revision) + "</small></th><td><span class=\"pill" + roleClass + "\">" +
          esc(lineage.role) + "</span><small>" + esc(lineageText) + "</small></td><td>" +
          esc([row.messenger, row.alert_type || row.role, row.detection === undefined || row.detection === null ? "" : row.detection ? "detection" : "nondetection"].filter(Boolean).join(" · ") || "—") +
          (row.detector_network ? "<small>Detector network: " + esc(structuredText(row.detector_network)) + "</small>" : "") + "</td><td>" +
          esc(row.significance_sigma === undefined || row.significance_sigma === null ? "Significance not retained" : num(row.significance_sigma, 3) + " σ") + "<small>" +
          esc(row.false_alarm_rate_hz === undefined || row.false_alarm_rate_hz === null ? "False-alarm rate not retained" : text(row.false_alarm_rate_hz) + " Hz") + "</small></td><td>" +
          esc([row.sky_area_90_sq_deg === undefined || row.sky_area_90_sq_deg === null ? "" : num(row.sky_area_90_sq_deg, 2) + " deg² (90%)",
            row.sky_area_50_sq_deg === undefined || row.sky_area_50_sq_deg === null ? "" : num(row.sky_area_50_sq_deg, 2) + " deg² (50%)",
            row.distance_mpc === undefined || row.distance_mpc === null ? "" : num(row.distance_mpc, 2) + (row.distance_std_mpc === undefined || row.distance_std_mpc === null ? "" : " ± " + num(row.distance_std_mpc, 2)) + " Mpc"].filter(Boolean).join(" · ") || "—") +
          "</td><td>" + renderReferences([row]) + retainedRecordDetails("Inspect complete retained notice", row) + "</td></tr>";
      }).join("") + "</tbody></table></div></div></details>";
  }

  function renderClassifications(candidate) {
    var follow = candidate.follow_up || {};
    var classifications = (follow.classifications || []).concat(follow.classification_history || []);
    var reports = follow.publications || [], revisions = follow.publication_revisions || [], publicationGroups = {};
    function publicationKey(row) {
      return text(row.publication_assertion_id || row.assertion_id || [row.provider, row.provider_publication_id].filter(Boolean).join(":"));
    }
    reports.forEach(function (row) {
      var key = publicationKey(row); publicationGroups[key] = publicationGroups[key] || {report: null, revisions: []}; publicationGroups[key].report = row;
    });
    revisions.forEach(function (row) {
      var key = publicationKey(row); publicationGroups[key] = publicationGroups[key] || {report: null, revisions: []}; publicationGroups[key].revisions.push(row);
    });
    Object.keys(publicationGroups).forEach(function (key) {
      publicationGroups[key].revisions.sort(function (a, b) {
        var sequence = Number(a.source_revision_sequence || 0) - Number(b.source_revision_sequence || 0);
        return sequence || text(a.retrieved_at).localeCompare(text(b.retrieved_at));
      });
    });
    if (!classifications.length && !reports.length && !revisions.length) return "";
    return '<details class="ctas-evidence-panel" data-dossier-view="classifications"><summary>Reported classifications and public reports <small>' +
      classifications.length + " classification rows · " + reports.length + " reports · " + revisions.length + ' revision rows</small></summary><div class="ctas-evidence-panel__body"><div class="ctas-detail__grid">' +
      (classifications.length ? '<section class="ctas-detail__section"><h4>Classification assertions</h4><ul>' + classifications.map(function (row) {
        return "<li><strong>" + esc(row.classification || "Unclassified") + "</strong><span>" +
          esc([row.asserted_at ? absolute(row.asserted_at) : "time unavailable", row.provider, row.method,
            finiteNumber(row.probability) ? num(100 * row.probability, 1) + "% reported probability" : "",
            row.retracted ? "retracted" : row.superseded ? "superseded" : ""].filter(Boolean).join(" · ")) +
          "</span><small>Assertion " + esc(row.assertion_id || "identifier not retained") + "</small>" + renderReferences([row]) +
          retainedRecordDetails("Inspect complete classification assertion", row) + "</li>";
      }).join("") + "</ul></section>" : "") +
      (Object.keys(publicationGroups).length ? '<section class="ctas-detail__section ctas-publication-section"><h4>Public reports and revision history</h4><ul>' +
        Object.keys(publicationGroups).sort().map(function (key) {
          var group = publicationGroups[key], row = group.report || group.revisions[group.revisions.length - 1] || {};
          return '<li><details class="ctas-publication-record"><summary><span><strong>' + esc(row.title || row.publication_type || "Public report") + '</strong><small>' +
            esc([row.published_at ? absolute(row.published_at) : "publication time unavailable", row.authors_text, row.provider].filter(Boolean).join(" · ")) +
            '</small></span></summary><div class="ctas-publication-record__body">' + (row.abstract ? "<p>" + esc(row.abstract) + "</p>" : "") +
            renderReferences([row]) + retainedRecordDetails("Inspect current retained report record", row) +
            (group.revisions.length ? '<div class="ctas-evidence-table-wrap ctas-evidence-table-wrap--tall" role="region" aria-label="Complete publication revision history" tabindex="0"><table class="ctas-evidence-table ctas-publication-revisions"><caption>All ' +
              group.revisions.length.toLocaleString() + ' retained revisions for this publication. A superseded revision remains part of the public history.</caption><thead><tr><th scope="col">Revision</th><th scope="col">Retrieved</th><th scope="col">State</th><th scope="col">Title / authors</th><th scope="col">Content checksums</th><th scope="col">Source and complete record</th></tr></thead><tbody>' +
              group.revisions.map(function (revision) {
                return '<tr><th scope="row">' + esc(revision.source_revision_sequence === undefined || revision.source_revision_sequence === null ? "Sequence not retained" : "Revision " + revision.source_revision_sequence) +
                  '</th><td>' + esc(absolute(revision.retrieved_at)) + '</td><td><span class="pill' + (revision.superseded ? " is-superseded" : "") + '">' +
                  esc(revision.superseded ? "Superseded" : "Current/latest retained") + '</span></td><td><strong>' + esc(revision.title || "Title not retained") +
                  '</strong><small>' + esc(revision.authors_text || "Authors not retained") + '</small></td><td><small>Content <code>' +
                  esc(revision.content_checksum ? shortHash(revision.content_checksum) + "…" : "not retained") + '</code></small><small>Source <code>' +
                  esc(revision.source_content_checksum ? shortHash(revision.source_content_checksum) + "…" : "not retained") + '</code></small></td><td>' +
                  renderReferences([revision]) + retainedRecordDetails("Inspect complete publication revision", revision) + '</td></tr>';
              }).join("") + '</tbody></table></div>' : '<p>No separate publication-revision rows are retained.</p>') + '</div></details></li>';
        }).join("") + "</ul></section>" : "") + "</div></div></details>";
  }

  function renderEnvironment(candidate) {
    var follow = candidate.follow_up || {};
    var hosts = follow.host_context || [], counterparts = follow.catalog_counterparts || [], products = follow.archive_products || [];
    if (!hosts.length && !counterparts.length && !products.length) return "";
    return '<details class="ctas-evidence-panel" data-dossier-view="environment"><summary>Environment and released archive context <small>' +
      (hosts.length + counterparts.length + products.length) + '</small></summary><div class="ctas-evidence-panel__body"><p>Positional catalog candidates are context, not host associations, unless a retained host record explicitly says otherwise.</p><div class="ctas-detail__grid">' +
      (hosts.length ? '<section class="ctas-detail__section"><h4>Reported host context</h4><ul>' + hosts.map(function (row) {
        return '<li><details class="ctas-environment-record"><summary><span><strong>' + esc(row.canonical_name || row.queried_name || "Host record") + '</strong><small>' +
          esc([row.redshift === undefined || row.redshift === null ? "" : "z " + num(row.redshift, 5), row.physical_type, row.morphology,
            row.transient_offset_arcsec === undefined || row.transient_offset_arcsec === null ? "" : num(row.transient_offset_arcsec, 2) + " arcsec reported offset"].filter(Boolean).join(" · ") || "Host values retained") +
          '</small></span></summary><div class="ctas-environment-record__body"><dl class="ctas-detail__facts">' +
          fact("Queried / canonical name", [row.queried_name, row.canonical_name].filter(Boolean).join(" → ") || "Not retained") +
          fact("Host ICRS position", finiteNumber(row.ra_deg) && finiteNumber(row.dec_deg) ? num(row.ra_deg, 7) + "°, " + num(row.dec_deg, 7) + "°" : "Not retained") +
          fact("Reported transient offset", finiteNumber(row.transient_offset_arcsec) ? num(row.transient_offset_arcsec, 3) + " arcsec" : "Not retained") +
          fact("Redshift", finiteNumber(row.redshift) ? num(row.redshift, 7) + (finiteNumber(row.redshift_error) ? " ± " + num(row.redshift_error, 7) : "") : "Not retained") +
          fact("Redshift reference", row.redshift_reference || "Not retained") +
          fact("Heliocentric velocity", finiteNumber(row.heliocentric_velocity_km_s) ? num(row.heliocentric_velocity_km_s, 2) + " km s⁻¹" : "Not retained") +
          fact("Hubble-flow distance", finiteNumber(row.hubble_flow_distance_mpc) ? num(row.hubble_flow_distance_mpc, 3) + " Mpc" : "Not retained") +
          fact("Mean distance", finiteNumber(row.mean_distance_mpc) ? num(row.mean_distance_mpc, 3) + (finiteNumber(row.mean_distance_error_mpc) ? " ± " + num(row.mean_distance_error_mpc, 3) : "") + " Mpc" : "Not retained") +
          fact("Physical / morphology / activity", [row.physical_type, row.morphology, row.activity_type].filter(Boolean).join(" · ") || "Not retained") +
          fact("Angular / physical major axis", [finiteNumber(row.major_axis_arcsec) ? num(row.major_axis_arcsec, 3) + " arcsec" : "", finiteNumber(row.physical_major_axis_kpc) ? num(row.physical_major_axis_kpc, 3) + " kpc" : ""].filter(Boolean).join(" · ") || "Not retained") +
          fact("Galactic V extinction", finiteNumber(row.galactic_extinction_v_mag) ? num(row.galactic_extinction_v_mag, 4) + " mag" : "Not retained") +
          fact("Queried", row.queried_at ? absolute(row.queried_at) : "Not retained") + fact("Response checksum", row.response_checksum || "Not retained") +
          '</dl>' + (row.cross_identifications ? '<details class="ctas-record-details"><summary>Cross-identifications</summary><pre>' + esc(structuredText(row.cross_identifications)) + '</pre></details>' : "") +
          (row.overview_note ? '<p><strong>Provider overview:</strong> ' + esc(row.overview_note) + '</p>' : "") +
          (row.attribution ? '<p><strong>Required attribution:</strong> ' + esc(row.attribution) + '</p>' : "") + renderReferences([row]) +
          retainedRecordDetails("Inspect complete retained host assertion", row) + '</div></details></li>';
      }).join("") + "</ul></section>" : "") +
      (counterparts.length ? '<section class="ctas-detail__section"><h4>Positional catalog candidates</h4><ul>' + counterparts.map(function (row) {
        return '<li><details class="ctas-environment-record"><summary><span><strong>' + esc(row.catalog_record_id || row.catalog_description || "Catalog row") + '</strong><small>' +
          esc([row.catalog, finiteNumber(row.separation_arcsec) ? num(row.separation_arcsec, 3) + " arcsec separation" : "separation not retained", row.counterpart_type].filter(Boolean).join(" · ")) +
          '</small></span></summary><div class="ctas-environment-record__body"><dl class="ctas-detail__facts">' +
          fact("Catalog", [row.catalog, row.catalog_description].filter(Boolean).join(" · ") || "Not retained") +
          fact("Catalog record ID", row.catalog_record_id || "Not retained") +
          fact("Catalog ICRS position", finiteNumber(row.ra_deg) && finiteNumber(row.dec_deg) ? num(row.ra_deg, 7) + "°, " + num(row.dec_deg, 7) + "°" : "Not retained") +
          fact("Separation", finiteNumber(row.separation_arcsec) ? num(row.separation_arcsec, 4) + " arcsec" : "Not retained") +
          fact("Position uncertainty", finiteNumber(row.position_error_arcsec) ? num(row.position_error_arcsec, 4) + " arcsec" : "Not retained") +
          fact("Counterpart type", row.counterpart_type || "Not retained") + fact("Queried", row.queried_at ? absolute(row.queried_at) : "Not retained") +
          fact("Response checksum", row.response_checksum || "Not retained") + '</dl>' +
          (row.description ? '<p><strong>Provider description:</strong> ' + esc(row.description) + '</p>' : "") +
          '<div class="ctas-code-grid"><div><h4>Photometry</h4><pre>' + esc(structuredText(row.photometry, "Not retained")) +
          '</pre></div><div><h4>Motion</h4><pre>' + esc(structuredText(row.motion, "Not retained")) +
          '</pre></div><div><h4>Quality flags</h4><pre>' + esc(structuredText(row.quality_flags, "Not retained")) +
          '</pre></div><div><h4>Rights and source-row boundary</h4><pre>' + esc([row.rights_basis, row.source_row_exclusion].filter(Boolean).join("\n\n") || "Not retained") + '</pre></div></div>' +
          (row.attribution ? '<p><strong>Required attribution:</strong> ' + esc(row.attribution) + '</p>' : "") +
          renderReferences([row], [["source_url", null], ["catalog_documentation_url", "Open catalog documentation"]]) +
          retainedRecordDetails("Inspect complete retained catalog assertion", row) + '</div></details></li>';
      }).join("") + "</ul></section>" : "") +
      (products.length ? '<section class="ctas-detail__section"><h4>Released archive products</h4><ul>' + products.map(function (row) {
        return '<li><details class="ctas-environment-record"><summary><span><strong>' + esc(row.product_filename || row.provider_product_id || "Archive product") + '</strong><small>' +
          esc([row.mission, row.instrument, row.data_product_type || row.product_type, finiteNumber(row.angular_distance_arcsec) ? num(row.angular_distance_arcsec, 3) + " arcsec" : ""].filter(Boolean).join(" · ")) +
          '</small></span></summary><div class="ctas-environment-record__body">' + (row.description ? "<p>" + esc(row.description) + "</p>" : "") +
          renderReferences([row], [["source_url", null], ["public_download_url", "Download source artifact"], ["product_documentation_url", "Open product documentation"]]) +
          retainedRecordDetails("Inspect complete retained archive product", row) + '</div></details></li>';
      }).join("") + "</ul></section>" : "") + "</div></div></details>";
  }

  function renderIdentity(candidate) {
    var identity = candidate.identity_resolution || {}, aliases = candidate.designations || [];
    var warning = identity.state && identity.state !== "RESOLVED"
      ? '<p class="ctas-identity-warning"><strong>' + esc(humanKey(identity.state)) + ' identity:</strong> CTAS will not guess between colliding aliases. Use provider plus alias or the stable UUID.</p>' : "";
    var rows = aliases.map(function (row) {
      var sourceLink = (candidate.links || []).find(function (link) {
        return link.source_key === row.source_key && link.designation === row.designation;
      });
      return '<tr><th scope="row">' + esc(row.designation) + '</th><td>' + esc(row.source || row.source_key) + '</td><td>' +
        esc(row.is_preferred ? "Preferred by source" : "Source alias") + (row.ambiguous ? ' · <strong>Ambiguous binding</strong>' : "") +
        '</td><td>' + esc(row.asserted_at ? absolute(row.asserted_at) : "Assertion time not retained") + '</td><td>' +
        (sourceLink ? renderReferences([sourceLink]) : '<span class="ctas-link-unavailable">No verified object link retained</span>') + '</td></tr>';
    }).join("");
    return '<details class="ctas-evidence-panel ctas-identity" data-dossier-view="identity"><summary>Identity and aliases <small>' +
      aliases.length + ' source-native aliases</small></summary><div class="ctas-evidence-panel__body">' + warning +
      '<dl class="ctas-detail__facts">' + fact("Stable event UUID", candidate.event_id) + fact("Identity state", humanKey(identity.state || "UNREVIEWED")) +
      fact("Lookup policy", identity.policy || "Provider-scoped exact alias; unscoped ambiguity is explicit") + '</dl>' +
      (rows ? '<div class="ctas-evidence-table-wrap" role="region" aria-label="Provider-scoped event aliases" tabindex="0"><table class="ctas-evidence-table"><caption>Source-native aliases remain attached to the stable event UUID through display-name changes.</caption><thead><tr><th scope="col">Alias</th><th scope="col">Provider</th><th scope="col">Binding</th><th scope="col">Asserted</th><th scope="col">Original source</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '<p>No source-native alias is retained.</p>') +
      '</div></details>';
  }

  function renderEvidenceLedger(candidate) {
    var descriptor = candidate.astro_evidence || {}, conflicts = descriptor.conflictSets || [];
    var selections = ((candidate.compatibility_provenance || {}).selectionProvenance || []);
    var conflictHtml = conflicts.length ? '<ul class="ctas-conflict-list">' + conflicts.map(function (row) {
      return '<li><div><span class="pill">' + esc((row.relations || []).map(humanKey).join(" · ")) + '</span><strong>' +
        esc(humanKey(row.propertyCode)) + '</strong></div><p>' + esc(row.explanation) + '</p><small>' +
        Number((row.measurementIds || []).length).toLocaleString() + ' source assertions · ' + esc(row.assessmentState) +
        (row.significanceSigma === null || row.significanceSigma === undefined ? "" : " · " + num(row.significanceSigma, 2) + "σ") + '</small></li>';
    }).join("") + '</ul>' : '<p class="ctas-no-conflict"><strong>No automated compatible-assertion conflict is retained.</strong> This is not proof that the source values are scientifically equivalent.</p>';
    var selectionHtml = selections.length ? '<ul class="ctas-selection-list">' + selections.map(function (row) {
      return '<li><strong>' + esc(humanKey(row.propertyCode)) + '</strong><p>' + esc(row.rationale) + '</p><small>Selected ' +
        esc((row.selectedAssertionIds || []).join(", ")) + (row.rejectedAssertionIds && row.rejectedAssertionIds.length ? ' · Rejected ' + esc(row.rejectedAssertionIds.join(", ")) : " · No rejected assertion") +
        ' · ' + esc(row.actor) + ' · ' + esc(absolute(row.selectedAt)) + '</small></li>';
    }).join("") + '</ul>' : '<p>No display value is represented as an assertion-backed selection. Existing summary fields remain clearly labeled as source-reported or legacy summaries.</p>';
    return '<details class="ctas-evidence-panel" data-dossier-view="assertions"><summary>Assertion ledger, conflicts, and selections <small>' +
      Number(descriptor.measurementCount || 0).toLocaleString() + ' projected measurements</small></summary><div class="ctas-evidence-panel__body">' +
      '<p>Classification labels and probabilities, detections and limits, and host redshifts remain separate source assertions. The panels below expose their source-native values; the deterministic exports contain the complete normalized ledger and stable assertion IDs.</p>' +
      '<div class="ctas-detail__grid"><section class="ctas-detail__section"><h4>Grouped conflicts</h4>' + conflictHtml +
      '</section><section class="ctas-detail__section"><h4>Recorded display selections</h4>' + selectionHtml + '</section></div></div></details>';
  }

  function renderAnalysis(candidate) {
    var runs = (candidate.astro_evidence || {}).analysisRuns || [];
    if (!runs.length) return '<details class="ctas-evidence-panel" data-dossier-view="analysis"><summary>Read-only CTAS analysis <small>INSUFFICIENT_DATA</small></summary><div class="ctas-evidence-panel__body"><p><strong>INSUFFICIENT_DATA:</strong> no rights-cleared light-curve inference run is retained for this event, so CTAS makes no analysis claim.</p></div></details>';
    return '<details class="ctas-evidence-panel ctas-analysis" data-dossier-view="analysis"><summary>Read-only CTAS analysis <small>' +
      runs.length.toLocaleString() + ' retained run' + (runs.length === 1 ? "" : "s") + '</small></summary><div class="ctas-evidence-panel__body"><p class="ctas-claim-boundary">This panel is read-only. Opening it does not rerun a model, send an alert, or request follow-up.</p><div class="ctas-analysis-runs">' +
      runs.map(function (row) {
        var warnings = row.warnings || [], inputs = row.inputRecordIds || [];
        var statusCopy = row.status === "INSUFFICIENT_DATA"
          ? "Prerequisites were not met, so CTAS made no light-curve parameter claim."
          : row.status === "COMPLETE" ? "The existing result completed; warnings and quality limitations remain part of the record." : "The existing analysis did not produce an adopted scientific result.";
        return '<details class="ctas-analysis-run"><summary><span><strong>' +
          esc(humanKey(row.analysisType || "Analysis run")) + '</strong><small>' + esc(humanKey(row.status || "unknown")) +
          ' · ' + esc(row.createdAt ? absolute(row.createdAt) : "completion time unavailable") + '</small></span></summary><div class="ctas-analysis-run__body"><div class="ctas-analysis__status"><span class="pill">' +
          esc(humanKey(row.status)) + '</span><p>' + esc(statusCopy) + '</p></div><dl class="ctas-detail__facts">' +
          fact("Analysis", humanKey(row.analysisType)) + fact("Method", [row.methodName, row.methodVersion ? "version " + row.methodVersion : ""].filter(Boolean).join(" · ") || "Not retained") +
          fact("Input assertion IDs", inputs.length ? inputs.length.toLocaleString() + " checksum-bound inputs" : "None; prerequisite gate failed") +
          fact("Input checksum", row.inputChecksumSha256 || "No input checksum retained") + fact("Result checksum", row.resultChecksumSha256 || "No result checksum retained") +
          fact("Review state", humanKey(row.reviewState || "not recorded")) + fact("Completed", row.createdAt ? absolute(row.createdAt) : "Not retained") + '</dl>' +
          (warnings.length ? '<div class="ctas-analysis__warnings"><h4>Warnings</h4><ul>' + warnings.map(function (warning) { return '<li>' + esc(warning) + '</li>'; }).join("") + '</ul></div>' : "") +
          '<details class="ctas-record-details"><summary>Parameters, software, inputs, result, and complete run</summary><div class="ctas-code-grid"><div><h4>Parameters</h4><pre>' + esc(JSON.stringify(row.parameters || {}, null, 2)) +
          '</pre></div><div><h4>Software versions</h4><pre>' + esc(JSON.stringify(row.softwareVersions || {}, null, 2)) +
          '</pre></div><div><h4>Input assertion IDs</h4><pre>' + esc(inputs.join("\n") || "None") +
          '</pre></div><div><h4>Result</h4><pre>' + esc(JSON.stringify(row.result || {}, null, 2)) +
          '</pre></div><div><h4>Complete retained run</h4><pre>' + esc(JSON.stringify(row, null, 2)) + '</pre></div></div></details></div></details>';
      }).join("") + '</div></div></details>';
  }

  function renderExports(candidate) {
    return '<details class="ctas-evidence-panel ctas-exports" data-dossier-view="exports"><summary>Reproducible dossier exports <small>JSON · ECSV · VOTable · manifest</small></summary>' +
      '<div class="ctas-evidence-panel__body"><p>Exports are generated deterministically from this exact snapshot, stable event UUID, source-native rows, versioned source contracts, persisted receipts, conflicts, selections, and the existing read-only analysis product.</p>' +
      '<div class="ctas-evidence-tools"><button type="button" data-export-format="json">AstroEvidence JSON</button>' +
      '<button type="button" data-export-format="ecsv">ECSV evidence table</button><button type="button" data-export-format="vot">IVOA VOTable</button>' +
      '<button type="button" data-export-format="manifest">Checksum manifest</button></div>' +
      '<p class="ctas-export-status" data-export-status role="status" aria-live="polite">Ready. Nothing is sent to a server; files are assembled in this browser.</p></div></details>';
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
        esc(qualityText(candidate.data_quality_flags)) +
        (candidate.reported_discovery_magnitude !== undefined ? " · Raw provider value retained: " + esc(candidate.reported_discovery_magnitude) : "") +
        ". The flagged value is excluded from the plotted magnitude and brightness-derived score term.</p></div>" : "";
    return '<div id="dossier" class="ctas-dossier"><div class="ctas-workspace__head"><div><p class="eyebrow">Candidate dossier</p><h3 id="ctas-dossier-title" tabindex="-1" data-dossier-focus>' + esc(candidate.name) +
      '</h3><p>' + esc(summary.intro) +
      '</p></div><div class="ctas-detail__score"><span>CTAS follow-up score</span><strong>' + esc(num(candidate.ctas_score, 1)) +
      '</strong><small>ordering aid · not probability</small></div></div>' +
      '<div class="ctas-workspace__actions"><button type="button" data-close-candidate>Close dossier</button><details class="ctas-more-actions"><summary>More actions</summary><div><button type="button" data-copy-link>Copy link</button><button type="button" data-download-candidate>Download JSON</button><button type="button" data-compare-event="' + esc(candidate.event_id) + '" aria-pressed="false">Compare</button><button type="button" data-watch-event="' + esc(candidate.event_id) + '" aria-pressed="false">Watch locally</button></div></details></div>' + renderScienceBrief(candidate) +
      '<dl class="ctas-detail__facts ctas-detail__facts--essential">' + fact("Event / messenger", humanKey(candidate.event_type || "Not recorded") + " · " + humanKey(candidate.primary_messenger || "Not recorded")) +
      fact("Reported class / alert label", candidate.classification || "Unclassified") +
      fact("Current record status", humanKey(candidate.status || "unknown")) +
      fact("ICRS coordinates", sexagesimal(candidate.ra_deg, candidate.dec_deg) || "Unavailable") +
      fact("Discovery", [candidate.discovery_time ? absolute(candidate.discovery_time) : "time unavailable", candidate.discovery_survey || "survey unavailable"].join(" · ")) +
      fact("Source-reported magnitude", magnitude) + fact("Available evidence", evidence.join(" · ") || "Event record only") + "</dl>" +
      '<details class="ctas-secondary-facts"><summary>Identifiers and additional facts</summary><dl class="ctas-detail__facts">' +
      fact("Stable event UUID", candidate.event_id) + fact("Label kind", labelKind) +
      fact("Reported classification probability", candidate.classification_probability === undefined ? "Not reported" : num(100 * candidate.classification_probability, 1) + "% (calibration not assumed)") +
      fact("Redshift", num(candidate.redshift, 5)) + fact("Host", candidate.host_name) + "</dl></details>" + quality +
      '<section class="ctas-original-sources"><h4>Original sources</h4>' + renderReferences(candidate.links || []) +
      ((candidate.links || []).some(function (row) { return row.source_key === "tns"; })
        ? "<p>TNS hourly intake does not itself expose every current object-page flag. Open the exact TNS record above for the provider’s current object page and status.</p>" : "") + "</section>" +
      (window.CTASWorkbench && window.CTASWorkbench.skyContextPanel ? window.CTASWorkbench.skyContextPanel(candidate) : "") +
      renderIdentity(candidate) + renderScoreFactors(candidate) + renderCompleteness(candidate) + renderSourceCoverage(candidate) +
      renderEvidenceLedger(candidate) +
      renderPhotometry(candidate) + renderSpectra(candidate) + renderMessenger(candidate) +
      renderClassifications(candidate) + renderEnvironment(candidate) + renderTimeline(candidate) + renderAnalysis(candidate) +
      (window.CTASWorkbench && window.CTASWorkbench.candidatePanels ? window.CTASWorkbench.candidatePanels(candidate) : "") +
      renderExports(candidate) + "</div>";
  }

  function statusCell(label, value) {
    return '<span class="ctas-status__cell"><span class="ctas-status__label">' + esc(label) +
      '</span><strong class="ctas-status__value">' + value + "</strong></span>";
  }
  function renderStatus() {
    if (!el.status) return;
    var previousDetails = el.status.querySelector("details");
    var detailsOpen = Boolean(previousDetails && previousDetails.open);
    var focusedStatusControl = el.status.contains(document.activeElement) && document.activeElement.matches("summary, [data-toggle-refresh]")
      ? (document.activeElement.matches("summary") ? "summary" : "[data-toggle-refresh]") : null;
    var status = state.status || {}, snapshot = state.snapshot || {};
    var generated = status.last_successful_update || snapshot.catalog_as_of || snapshot.generated_at;
    var degraded = status.pipeline_status === "degraded";
    var cached = state.cachedSnapshot;
    var localPreview = window.location.protocol === "file:";
    var validUntilMs = status.valid_until ? new Date(status.valid_until).getTime() : NaN;
    var stale = Number.isFinite(validUntilMs) && Date.now() > validUntilMs;
    var skyHeading = document.querySelector(".ctas-console-identity h1 span");
    var liveDot = document.querySelector(".ctas-live-dot");
    if (skyHeading) skyHeading.textContent = localPreview || stale || cached || !state.snapshot ? "Sky catalog" : "Live sky";
    if (liveDot) liveDot.style.opacity = localPreview || stale || cached || !state.snapshot ? "0.35" : "1";
    var assurance = status.static_snapshot_verification || status.static_catalog_assurance || {};
    var snapshotVerified = assurance.status === "verified-static-snapshot" || assurance.status === "certified-static-catalog";
    var failedGateIds = Array.isArray(assurance.failed_gate_ids) ? assurance.failed_gate_ids : [];
    var publicationBindingGates = ["deployed-code-binding", "local-origin-code-alignment"];
    var publicationBindingPending = failedGateIds.length > 0 && failedGateIds.every(function (gateId) {
      return publicationBindingGates.indexOf(gateId) !== -1;
    });
    var checkCount = Number(assurance.check_count);
    var passedCheckCount = Number(assurance.passed_check_count);
    var integrityValue = snapshotVerified ? "Verified" : publicationBindingPending && Number.isFinite(checkCount) && Number.isFinite(passedCheckCount)
      ? passedCheckCount + " of " + checkCount + " checks passed"
      : humanKey(assurance.status || "pending");
    var integrityDetail = publicationBindingPending
      ? "Only local commit and origin publication bindings are pending; this successor has not been published"
      : assurance.content_release_id
        ? "Checksums and public-file consistency · Snapshot " + esc(shortHash(assurance.content_release_id)) + "…"
        : "Checksum report available below";
    el.status.classList.toggle("is-degraded", !localPreview && (degraded || cached || stale));
    var pipelineValue = localPreview ? "Local preview" : cached ? "Cached snapshot" : stale ? "Snapshot out of date" : degraded ? "Source limits" : "Operational";
    var pipelineDetail = localPreview ? "This file is a bundled development snapshot, not the live publishing endpoint. Its age does not describe the public CTAS publisher."
      : cached ? "A live refresh failed; the last successfully loaded public snapshot remains usable."
      : stale ? "This snapshot has passed its freshness window. That alone does not establish whether the publisher is stopped, still exporting, or unable to publish."
      : degraded ? "Catalog updates are active; individual source availability is reported in Catalog details."
      : "Catalog updates are active.";
    el.status.innerHTML = '<div class="ctas-status__line">' +
      statusCell("Pipeline", esc(pipelineValue)) +
      statusCell(localPreview ? "Bundled snapshot" : "Updated", esc(relative(generated) || "unavailable")) +
      statusCell("Public candidates", Number(status.candidate_count || snapshot.candidate_count || state.candidates.length).toLocaleString()) +
      statusCell("Snapshot integrity", esc(integrityValue)) +
      statusCell("Browser check", localPreview ? "Public site only" : state.autoRefreshPaused ? "Paused" : "Every 2 minutes") +
      '</div>' + (state.refreshError ? '<p role="status" class="ctas-cache-warning">Refresh not applied: ' + esc(state.refreshError) + ' The last coherent snapshot remains visible; the next browser check will retry.</p>' : '') +
      '<details class="ctas-status__details"><summary>Status details</summary><div><p>' + esc(pipelineDetail) +
      '</p><p>The browser checks every two minutes. Export, verification, and GitHub publication take longer; this is not a two-minute publication guarantee.' +
      '</p><p><strong>Last successful snapshot:</strong> ' + esc(absolute(generated)) + '</p><p><strong>Integrity:</strong> ' + integrityDetail +
      (localPreview ? '</p><p><a href="https://jackmcguireastro.github.io/ctas.html">Open the current public CTAS catalog</a>' : "") +
      '</p><button type="button" class="ctas-refresh-toggle" data-toggle-refresh aria-pressed="' + (state.autoRefreshPaused ? "true" : "false") + '">' +
      (state.autoRefreshPaused ? "Resume 2-minute checks" : "Pause 2-minute checks") + "</button></div></details>";
    el.status.querySelector("details").open = detailsOpen;
    if (focusedStatusControl) el.status.querySelector(focusedStatusControl).focus({preventScroll: true});
    Array.prototype.forEach.call(document.querySelectorAll("[data-score-valid-until]"), function (label) {
      label.textContent = Date.now() > Date.parse(label.getAttribute("data-score-valid-until")) ? "snapshot score · expired" : "ordering aid";
    });
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
    renderStream();
    renderRepresentedSources();
  }

  function renderStream() {
    if (!el.stream) return;
    var cutoff = Date.now() - 24 * 60 * 60 * 1000;
    var stream = filteredRows().filter(function (candidate) {
      var discovery = Date.parse(candidate.discovery_time || "");
      return Number.isFinite(discovery) && discovery >= cutoff;
    }).sort(function (a, b) { return Date.parse(b.discovery_time) - Date.parse(a.discovery_time); });
    var title = document.getElementById("ctas-stream-title");
    if (title) title.textContent = "Latest 3 of " + stream.length.toLocaleString() + " reported in the last 24 hours";
    el.stream.innerHTML = stream.slice(0, 3).map(function (candidate, index) {
      var counts = candidate.follow_up_counts || {};
      var evidence = [counts.observations ? counts.observations + " obs" : "", counts.spectra ? counts.spectra + " spectra" : "",
        counts.messenger_signals ? counts.messenger_signals + " notices" : "", counts.classifications ? counts.classifications + " classifications" : ""].filter(Boolean).join(" · ") || "event record only";
      return '<li data-candidate-id="' + esc(candidate.event_id) + '"><span class="ctas-stream__number">0' + (index + 1) + '</span><div><button type="button" data-open-event="' + esc(candidate.event_id) + '"><strong>' + esc(candidate.name) + "</strong></button><p>" +
        esc(candidate.classification || "Unclassified") + " · " + esc(candidate.primary_messenger || "messenger unavailable") +
        "</p><small>" + esc(absolute(candidate.updated_at || candidate.discovery_time)) + " · " + esc(evidence) +
        '</small><div class="ctas-card-actions"><button type="button" data-compare-event="' + esc(candidate.event_id) + '" aria-pressed="false">Compare</button><button type="button" data-watch-event="' + esc(candidate.event_id) + '" aria-pressed="false">Watch locally</button></div></div><strong class="ctas-stream__score">' + esc(num(candidate.ctas_score, 1)) + "<span>CTAS score</span></strong></li>";
    }).join("") || '<li><div><strong>No matching candidates have a reported discovery time in the last 24 hours.</strong><p>Clear linked filters or use the leaderboard and complete catalog below.</p></div></li>';
  }

  function safeCatalogDownloadPath(value) {
    var path = text(value);
    return /^ctas\/data\/(?:live-summary\.json|catalog-index\.json|source-matrix-patterns\.json|catalog-pages\/(?:manifest|\d{4})\.json|candidate-chunks\/(?:manifest|[0-9a-f]{2,4})\.json)$/.test(path) ? path : null;
  }
  function renderCatalogDownloads() {
    if (!el.downloadStatus || !el.downloadParts) return;
    var manifest = state.catalogManifest;
    if (!manifest || !Array.isArray(manifest.chunks)) {
      el.downloadStatus.textContent = state.catalogManifestPromise
        ? "Loading the complete-catalog download manifest…"
        : "The complete-catalog download manifest loads on request; catalog browsing does not need it.";
      el.downloadParts.innerHTML = "";
      return;
    }
    var validChunks = manifest.chunks.filter(function (row) {
      return row && safeCatalogDownloadPath(row.path) && finiteNumber(row.bytes) && finiteNumber(row.candidate_count) && /^[0-9a-f]{64}$/.test(text(row.sha256));
    });
    var totalBytes = validChunks.reduce(function (sum, row) { return sum + Number(row.bytes); }, 0);
    el.downloadStatus.textContent = Number(manifest.candidate_count || 0).toLocaleString() +
      " complete records in " + validChunks.length + " verified parts · " + (totalBytes / 1048576).toFixed(1) + " MiB total";
    el.downloadParts.innerHTML = '<ol aria-label="Complete catalog download parts">' + validChunks.map(function (row, index) {
      var path = safeCatalogDownloadPath(row.path);
      return '<li><div><strong>Part ' + String(index + 1).padStart(2, "0") + '</strong><span>' +
        Number(row.candidate_count).toLocaleString() + " candidates · " + (Number(row.bytes) / 1048576).toFixed(1) +
        ' MiB</span><code aria-label="SHA-256 checksum">' + esc(row.sha256) + '</code></div><a href="' + esc(path) +
        '" download>Download part <span class="sr-only">' + (index + 1) + " of " + validChunks.length + "</span></a></li>";
    }).join("") + "</ol>";
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
    var representation = {};
    universe.sources.forEach(function (row) { representation[row.representation_state || "none"] = (representation[row.representation_state || "none"] || 0) + 1; });
    el.sourceUniverseSummary.innerHTML = '<span><strong>' + universe.source_count + "</strong> maintained contracts—not every astronomical source</span>" +
      '<span><strong>' + Number(representation.direct || 0) + "</strong> directly represented</span>" +
      '<span><strong>' + Number(representation["through-provider"] || 0) + "</strong> represented through providers</span>" +
      '<span><strong>' + Number(representation["dispositions-only"] || 0) + "</strong> checked without retained records</span>" +
      '<span><strong>' + Number(representation.none || 0) + "</strong> not represented in this snapshot</span>" +
      '<span><strong>' + universe.family_count + "</strong> source families</span>" + Object.keys(states).sort().map(function (key) {
        return "<span><strong>" + states[key] + "</strong> " + esc(humanKey(key).toLowerCase()) + "</span>";
      }).join("");
    el.sourceUniverseGroups.innerHTML = Object.keys(groups).sort().map(function (family) {
      return "<details><summary><strong>" + esc(humanKey(family)) + "</strong><small>" + groups[family].length +
        " sources</small></summary><ul>" + groups[family].map(function (row) {
          return "<li><div><strong>" + esc(row.name) + '</strong><span class="pill">' + esc(humanKey(row.operational_state)) +
            "</span></div><p>" + esc(row.data_types ? row.data_types.join(", ") : "Data products not specified") +
            "</p><small>" + esc([humanKey(row.implementation_state), humanKey(row.representation_state), row.organization_or_facility].filter(Boolean).join(" · ")) +
            "</small>" + renderReferences([row], [["documentation_url", "Open provider documentation"]]) +
            '<details class="ctas-source-constraints"><summary>Access, pagination, rights, and limits</summary><dl>' +
            fact("Access / authentication", row.authentication_requirement || "Not documented") +
            fact("Pagination / completeness", row.pagination_policy || "Provider documentation does not state a separate pagination rule") +
            fact("Rate or cadence", row.rate_or_cadence_limit || "Provider documentation does not state a numeric limit") +
            fact("Rights / redistribution", row.redistribution_constraint || row.rights_or_public_access_basis || "Unresolved") +
            fact("Known limitations", row.known_limitations) + fact("Documentation checked", row.last_verified) +
            "</dl></details></li>";
        }).join("") + "</ul></details>";
    }).join("");
  }

  function renderReleaseHistory() {
    var history = state.releaseHistory || {}, entries = Array.isArray(history.entries) ? history.entries : [];
    if (!entries.length) { el.releaseHistory.innerHTML = "<p>No release-history entries are available.</p>"; return; }
    el.releaseHistory.innerHTML = '<p class="ctas-claim-boundary">' + esc(history.claim_boundary || "Catalog changes are not scientific validation.") +
      '</p><div class="ctas-release-scroll" role="region" aria-label="Complete public catalog release history" tabindex="0"><ol class="ctas-release-list">' + entries.map(function (entry) {
        var delta = (entry.added_count ? "+" + entry.added_count + " added" : "0 added") + " · " + entry.removed_count + " removed · " + entry.changed_count + " changed";
        return "<li><div><strong>" + esc(absolute(entry.published_at)) + "</strong><span>" + esc(delta) + "</span></div><p>" +
          esc(entry.summary) + "</p>" + (entry.evidence ? "<small>" + esc(entry.evidence) + "</small>" : "") +
          '<code title="Catalog checksum">' + esc(shortHash(entry.catalog_content_checksum_sha256)) + "…</code></li>";
      }).join("") + "</ol></div>";
  }

  function currentFilters() {
    return {
      q: state.q, class: state.cls, msg: state.msg, status: state.stat, survey: state.survey,
      from: state.from, to: state.to, magMin: state.magMin, magMax: state.magMax,
      scoreMin: state.scoreMin, scoreMax: state.scoreMax, spectrum: state.spectrum,
      conflict: state.conflict, richness: state.richness, preset: state.preset,
      window: String(state.skyDays), coneRa: state.coneRa, coneDec: state.coneDec,
      coneRadius: state.coneRadius
    };
  }
  function filteredRows() {
    return window.CTASCatalogModel
      ? window.CTASCatalogModel.filteredCandidates(state.candidates, currentFilters(), Date.now())
      : state.candidates.slice();
  }
  function syncFilterRoute() {
    if (!window.CTASCatalogModel) return;
    var url = new URL(window.location.href), params = window.CTASCatalogModel.serializeFilters(currentFilters());
    ["q", "class", "msg", "status", "survey", "from", "to", "magMin", "magMax", "scoreMin", "scoreMax",
      "spectrum", "conflict", "richness", "preset", "window", "coneRa", "coneDec", "coneRadius"].forEach(function (key) { url.searchParams.delete(key); });
    params.forEach(function (value, key) { url.searchParams.set(key, value); });
    history.replaceState(null, "", url.pathname + (url.searchParams.toString() ? "?" + url.searchParams.toString() : "") + url.hash);
  }
  function notifyFilterChange() {
    renderStream(); drawSky(); syncFilterRoute();
    window.dispatchEvent(new CustomEvent("ctas:filters-changed", {detail: {filters: currentFilters(), candidates: filteredRows()}}));
  }
  function visible() {
    function sortValue(candidate, key) {
      if (key === "record_completeness") return Number((candidate.record_completeness || {}).fraction || 0);
      return candidate[key];
    }
    return filteredRows().sort(function (a, b) {
      var av = sortValue(a, state.sortKey), bv = sortValue(b, state.sortKey);
      if (av === undefined || av === null || av === "") return 1;
      if (bv === undefined || bv === null || bv === "") return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * state.sortDir;
      return String(av).localeCompare(String(bv)) * state.sortDir;
    });
  }

  function triageReasons(candidate) {
    var counts = candidate.follow_up_counts || {}, reasons = [];
    var discovered = Date.parse(candidate.discovery_time || "");
    if (Number.isFinite(discovered) && discovered >= Date.now() - 86400000) reasons.push("New <24h");
    if (!candidate.classification || candidate.classification === "Unclassified") reasons.push("Unclassified");
    if (!Number(counts.spectra || 0) && (candidate.score_applicable_terms || []).indexOf("spectroscopy_gap_points") !== -1) reasons.push("No spectrum retained");
    if ((candidate.score_detected_messengers || []).length >= 2) reasons.push("Multiple detected messengers");
    if (Number(candidate.conflict_count || 0) > 0) reasons.push("Conflicting evidence");
    return reasons.slice(0, 3);
  }
  function renderTriageReasons(candidate) {
    var reasons = triageReasons(candidate);
    return reasons.length ? reasons.map(function (reason) { return '<span class="ctas-reason">' + esc(reason) + "</span>"; }).join("") : '<span class="ctas-reason ctas-reason--quiet">Score-ranked</span>';
  }

  var COLUMNS = [
    {key: "name", label: "Candidate"}, {key: "ctas_score", label: "Score"},
    {key: "triage", label: "Why now", nosort: true}, {key: "classification", label: "Reported class"},
    {key: "discovery_time", label: "Age / magnitude"}, {key: "record_completeness", label: "Evidence"},
    {key: "links", label: "Original source", nosort: true}
  ];
  function renderTable() {
    var rows = visible(), shown = rows.slice(0, state.shown);
    var defaultLeaderboard = state.preset === "all" && !state.q && !state.cls && !state.msg && !state.stat && !state.survey &&
      !state.from && !state.to && state.scoreMin === null && state.scoreMax === null && state.magMax === null &&
      !state.spectrum && !state.conflict && !state.richness && state.coneRa === null && state.coneDec === null && state.coneRadius === null;
    if (defaultLeaderboard && state.shown <= PAGE && state.sortKey === "ctas_score" && state.sortDir === -1 && (state.snapshot || {}).leaderboard) {
      var byId = {}; state.candidates.forEach(function (candidate) { byId[candidate.event_id] = candidate; });
      shown = state.snapshot.leaderboard.event_ids.map(function (id) { return byId[id]; }).filter(Boolean);
    }
    var retainedTotal = Number((state.snapshot || {}).candidate_count || state.candidates.length).toLocaleString();
    el.count.textContent = defaultLeaderboard && state.shown <= PAGE
      ? "Top " + shown.length.toLocaleString() + " follow-up candidates · " + retainedTotal + " retained records"
      : "Showing " + shown.length.toLocaleString() + " of " + rows.length.toLocaleString() +
        " matches in " + (state.completeCatalogLoaded ? "the complete catalog" : "the loaded summary") + " · " + retainedTotal + " retained total";
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
      return '<tr data-candidate-id="' + esc(candidate.event_id) + '"><td><button type="button" class="ctas-candidate" data-open-event="' + esc(candidate.event_id) + '"><span>' +
        esc(candidate.name) + '</span><small>' + esc(candidate.discovery_survey || "Survey unavailable") + " · " + esc(sexagesimal(candidate.ra_deg, candidate.dec_deg) || "position unavailable") +
        '</small></button><div class="ctas-card-actions"><button type="button" data-compare-event="' + esc(candidate.event_id) + '" aria-pressed="false">Compare</button><button type="button" data-watch-event="' + esc(candidate.event_id) + '" aria-pressed="false">Watch locally</button></div></td><td class="num ctas-score-cell">' + esc(num(candidate.ctas_score, 1)) +
        '<small data-score-valid-until="' + esc(candidate.score_valid_until || "") + '" title="' + esc(candidate.score_as_of ? "Score computed " + absolute(candidate.score_as_of) : "Score clock not included in this release") + '">' +
        (candidate.score_valid_until && Date.now() > Date.parse(candidate.score_valid_until) ? "snapshot score · expired" : "ordering aid") + '</small></td><td><div class="ctas-reasons">' + renderTriageReasons(candidate) + '</div></td><td><span class="pill">' + esc(label) +
        '</span><small class="ctas-label-kind">' + esc(humanKey(candidate.reported_label_kind || "provider-reported")) +
        '</small></td><td><strong>' + esc(candidate.discovery_time ? relative(candidate.discovery_time) : "Time unavailable") +
        '</strong><small class="ctas-table-sub">' + esc(num(candidate.discovery_magnitude, 2) ? num(candidate.discovery_magnitude, 2) + " mag · source reported" : "Magnitude unavailable") +
        '</small></td><td><strong>' + esc((candidate.record_completeness || {}).label || "Not assessed") + '</strong><small class="ctas-table-sub">' + esc(evidence) +
        '</small></td><td>' + renderReferences(candidate.links || []) + "</td></tr>";
    }).join("");
    el.results.innerHTML = '<div class="ctas-table-wrap" role="region" aria-label="Candidate table" tabindex="0"><table class="ctas-table"><caption>Public CTAS candidates. Positions are ICRS; source-reported discovery magnitudes may use heterogeneous bands and systems.</caption><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>" + (rows.length > state.shown
        ? '<p class="ctas-more"><button type="button" id="ctas-more">' + (defaultLeaderboard && state.shown <= PAGE
          ? "Show more loaded candidates (" + rows.length.toLocaleString() + ")"
          : "Show the next " + Math.min(PAGE, rows.length - state.shown).toLocaleString()) + "</button></p>" : "");
    if (window.CTASWorkbench && window.CTASWorkbench.refreshActions) window.CTASWorkbench.refreshActions();
    repaintCandidateLinks(false);
  }

  function skyNeedsCompleteCatalog() {
    return state.skyDays > 90 || Boolean(state.q || state.msg || state.stat || state.survey || state.spectrum || state.conflict || state.richness ||
      ["all", "priority", "today", "newest", "bright", "unclassified"].indexOf(state.preset) === -1);
  }
  function skyRows() {
    if (!window.CTASCatalogModel) return [];
    var rows = state.completeCatalogLoaded || !state.snapshot.sky || skyNeedsCompleteCatalog()
      ? filteredRows() : window.CTASCatalogModel.filteredCandidates(state.skyCandidates, currentFilters(), Date.now());
    return window.CTASCatalogModel.skyCandidates(rows, state.skyDays, Date.now());
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
  function emphasizedEventId() {
    return state.focusedEventId || state.hoveredEventId ||
      (state.activeSummary && state.activeSummary.event_id) ||
      (state.skySelected && state.skySelected.event_id) || null;
  }
  function repaintCandidateLinks(redrawSky) {
    var eventId = emphasizedEventId();
    var unchanged = eventId === state.linkedHighlightId;
    state.linkedHighlightId = eventId;
    Array.prototype.forEach.call(document.querySelectorAll("[data-candidate-id]"), function (element) {
      element.classList.toggle("is-candidate-emphasized", Boolean(eventId && element.getAttribute("data-candidate-id") === eventId));
      element.classList.toggle("is-active-candidate", Boolean(state.activeSummary && element.getAttribute("data-candidate-id") === state.activeSummary.event_id));
    });
    if (!unchanged || redrawSky === true) { if (redrawSky !== false) drawSky(); }
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
    var width = Math.max(1, Math.floor(el.skyStage.getBoundingClientRect().width));
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
      var selected = state.skySelected && state.skySelected.event_id === point.candidate.event_id;
      var emphasized = state.linkedHighlightId === point.candidate.event_id;
      if (emphasized) {
        context.beginPath(); context.arc(point.x, point.y, selected ? 10 : 8, 0, Math.PI * 2);
        context.strokeStyle = "rgba(255,255,255,.9)"; context.lineWidth = 1.4; context.stroke();
      }
      context.beginPath(); context.arc(point.x, point.y, selected ? 6.5 : emphasized ? 5.4 : 4.2, 0, Math.PI * 2);
      context.fillStyle = magnitudeColor(point.candidate.discovery_magnitude); context.fill();
      context.strokeStyle = selected ? "#fff" : "rgba(255,255,255,.56)"; context.lineWidth = selected ? 2.2 : 0.7; context.stroke();
    });
    var windowLabel = state.skyDays === 1 ? "24 hours" : state.skyDays === 7 ? "week" : state.skyDays === 30 ? "month" : state.skyDays === 90 ? "90 days" : "retained catalog window";
    var skyMessage = rows.length.toLocaleString() + " candidates with coordinates reported in the " + windowLabel + ".";
    if (!state.completeCatalogLoaded && skyNeedsCompleteCatalog()) skyMessage += " Summary-only until the complete catalog is loaded for these filters.";
    if (el.skyCount.textContent !== skyMessage) el.skyCount.textContent = skyMessage;
    el.sky.setAttribute("aria-label", "Interactive all-sky map of " + rows.length + " CTAS candidates; use the synchronized accessible list or arrow keys and Enter.");
    if (el.skyAccessible) {
      var options = '<option value="">Choose a plotted candidate…</option>' + rows.map(function (candidate) {
        return '<option value="' + esc(candidate.event_id) + '">' + esc(candidate.name + " — " + (candidate.classification || "Unclassified") + " — " + sexagesimal(candidate.ra_deg, candidate.dec_deg) + " — magnitude " + (num(candidate.discovery_magnitude, 2) || "unknown")) + "</option>";
      }).join("");
      if (el.skyAccessible.dataset.options !== options) { el.skyAccessible.innerHTML = options; el.skyAccessible.dataset.options = options; }
      if (state.skySelected && rows.some(function (candidate) { return candidate.event_id === state.skySelected.event_id; })) el.skyAccessible.value = state.skySelected.event_id;
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
  function selectSky(candidate, open, opener) {
    state.skySelected = candidate;
    state.skyKeyboardIndex = state.skyPoints.map(function (point) { return point.candidate.event_id; }).indexOf(candidate.event_id);
    repaintCandidateLinks();
    if (open) openResolvedCandidate(candidate.event_id, true, opener, false);
  }
  function bindSky() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-sky-days]"), function (button) {
      button.addEventListener("click", function () {
        state.skyDays = Number(button.getAttribute("data-sky-days")); state.skySelected = null; state.skyKeyboardIndex = -1;
        Array.prototype.forEach.call(document.querySelectorAll("[data-sky-days]"), function (item) {
          var active = item === button; item.classList.toggle("is-active", active); item.setAttribute("aria-pressed", active ? "true" : "false");
        });
        drawSky(); syncFilterRoute();
        if (state.skyDays > 90 && !state.completeCatalogLoaded) loadCompleteCatalog().catch(function () {});
      });
    });
    if (!el.sky) return;
    el.sky.addEventListener("pointermove", function (event) {
      var hit = nearestSkyPoint(event);
      if (!hit) { el.skyTip.hidden = true; el.sky.style.cursor = "default"; state.hoveredEventId = null; repaintCandidateLinks(); return; }
      var candidate = hit.point.candidate; el.sky.style.cursor = "pointer"; el.skyTip.hidden = false;
      if (state.hoveredEventId !== candidate.event_id) { state.hoveredEventId = candidate.event_id; repaintCandidateLinks(); }
      el.skyTip.style.left = Math.min(hit.x + 14, el.sky.clientWidth - 220) + "px"; el.skyTip.style.top = Math.max(8, hit.y - 64) + "px";
      el.skyTip.innerHTML = "<strong>" + esc(candidate.name) + "</strong><span>" + esc(candidate.classification || "Unclassified") +
        " · reported mag " + esc(num(candidate.discovery_magnitude, 2) || "unknown") + "</span><span>" + esc(sexagesimal(candidate.ra_deg, candidate.dec_deg)) + "</span><span>Click to open the dossier and comparison controls</span>";
    });
    el.sky.addEventListener("pointerleave", function () { el.skyTip.hidden = true; state.hoveredEventId = null; repaintCandidateLinks(); });
    el.sky.addEventListener("click", function (event) { var hit = nearestSkyPoint(event); if (hit) selectSky(hit.point.candidate, true, el.sky); });
    el.sky.addEventListener("keydown", function (event) {
      var keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Enter", " "];
      if (!state.skyPoints.length || keys.indexOf(event.key) === -1) return;
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") {
        if (state.skyKeyboardIndex < 0) state.skyKeyboardIndex = 0;
        selectSky(state.skyPoints[state.skyKeyboardIndex].candidate, true, el.sky); return;
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
      var candidate = state.skyPoints.map(function (point) { return point.candidate; }).find(function (row) { return row.event_id === el.skyAccessible.value; });
      if (candidate) selectSky(candidate, true, el.skyAccessible);
    });
    var timer;
    window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(drawSky, 120); });
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
  function sha256Hex(bytes) {
    if (!window.crypto || !window.crypto.subtle) return Promise.reject(new Error("This browser cannot verify SHA-256 detail-shard integrity."));
    return window.crypto.subtle.digest("SHA-256", bytes).then(function (digest) {
      return Array.prototype.map.call(new Uint8Array(digest), function (value) { return value.toString(16).padStart(2, "0"); }).join("");
    });
  }
  function assertCurrentRelease(epoch) {
    if (epoch !== state.releaseEpoch) throw new Error("The catalog release changed while this request was loading. Please retry.");
  }
  function ensureCatalogManifest() {
    if (state.catalogManifest) return Promise.resolve(state.catalogManifest);
    if (!state.catalogManifestPromise) {
      var epoch = state.releaseEpoch;
      state.catalogManifestPromise = getJSON("candidate-chunks/manifest.json").then(function (manifest) {
        assertCurrentRelease(epoch);
        if (!manifest || manifest.catalog_content_checksum_sha256 !== (state.snapshot || {}).catalog_content_checksum_sha256) {
          throw new Error("The detail manifest belongs to a different catalog release. Refresh after publication finishes.");
        }
        state.catalogManifest = manifest;
        renderCatalogDownloads();
        return manifest;
      }).catch(function (error) {
        if (epoch === state.releaseEpoch) state.catalogManifestPromise = null;
        throw error;
      });
    }
    return state.catalogManifestPromise;
  }
  function chunkMetadata(path) {
    var rows = (state.catalogManifest || {}).chunks || [];
    return rows.find(function (row) { return text(row.path).replace(/^ctas\/data\//, "") === path; });
  }
  function trackApplicationHeaderHeight() {
    // The CTAS bar sticks below the global site header rather than underneath
    // it. The header's height changes with viewport width, so measure it
    // rather than freezing a number that is wrong on the next breakpoint.
    var header = document.querySelector(".site-header");
    if (!header) return;
    function apply() {
      var height = Math.round(header.getBoundingClientRect().height);
      if (height > 0) {
        document.documentElement.style.setProperty("--ctas-app-header-height", height + "px");
      }
    }
    apply();
    if (window.ResizeObserver) new ResizeObserver(apply).observe(header);
    window.addEventListener("resize", apply, {passive: true});
  }
  function loadCompleteCatalog() {
    // The first screen holds the Top 100 per channel and the last 24 hours.
    // Everything else arrives only here, in bounded checksum-bound pages, so a
    // reader who never asks never downloads the complete catalog.
    if (state.completeCatalogPromise) return state.completeCatalogPromise;
    var release = (state.snapshot || {}).catalog_content_checksum_sha256;
    var epoch = state.releaseEpoch;
    state.completeCatalogPromise = getJSON("catalog-pages/manifest.json").then(function (manifest) {
      assertCurrentRelease(epoch);
      if (!manifest || manifest.catalog_content_checksum_sha256 !== release) {
        throw new Error("The catalog pages belong to a different release. Refresh after publication finishes.");
      }
      var pages = Array.isArray(manifest.pages) ? manifest.pages : [];
      if (!pages.length) throw new Error("The catalog page manifest lists no pages.");
      var columns = manifest.candidate_columns, loaded = 0;
      function report() {
        assertCurrentRelease(epoch);
        if (el.completeStatus) {
          el.completeStatus.textContent = "Loading complete catalog… page " + loaded + " of " + pages.length + ".";
        }
      }
      report();
      return pages.reduce(function (chain, row) {
        return chain.then(function (collected) {
          var path = "catalog-pages/" + String(row.page).padStart(4, "0") + ".json";
          return fetch(DATA_DIR + path, {cache: "no-cache"}).then(function (response) {
            if (!response.ok) throw new Error(path + " returned HTTP " + response.status);
            return response.arrayBuffer();
          }).then(function (bytes) {
            if (bytes.byteLength !== Number(row.bytes)) throw new Error("Catalog page " + row.page + " byte length does not match its manifest.");
            return sha256Hex(bytes).then(function (checksum) {
              if (checksum !== row.sha256) throw new Error("Catalog page " + row.page + " SHA-256 does not match its manifest.");
              var document_ = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
              loaded += 1; report();
              return collected.concat(window.CTASCatalogModel.inflateBootstrap({
                candidate_columns: columns, candidate_rows: document_.candidate_rows
              }));
            });
          });
        });
      }, Promise.resolve([])).then(function (all) {
        assertCurrentRelease(epoch);
        if (all.length !== Number(manifest.candidate_count) || new Set(all.map(function (candidate) { return candidate.event_id; })).size !== all.length) {
          throw new Error("The complete catalog count or event identities do not match its manifest.");
        }
        var byId = {};
        state.candidates.forEach(function (candidate) { byId[candidate.event_id] = candidate; });
        all.forEach(function (candidate) { byId[candidate.event_id] = candidate; });
        state.candidates = Object.keys(byId).map(function (key) { return byId[key]; });
        state.completeCatalogLoaded = true;
        if (el.completeStatus) {
          el.completeStatus.textContent = "Complete catalog loaded: " + state.candidates.length.toLocaleString() + " retained records.";
        }
        if (el.loadComplete) { el.loadComplete.disabled = true; el.loadComplete.textContent = "Complete catalog loaded"; }
        populateFilters(); restoreFiltersFromRoute(); renderOverview(); renderTable(); drawSky();
        window.dispatchEvent(new CustomEvent("ctas:catalog-loaded", {detail: {candidates: state.candidates}}));
        return state.candidates;
      });
    }).catch(function (error) {
      if (epoch === state.releaseEpoch) {
        state.completeCatalogPromise = null;
        if (el.loadComplete) el.loadComplete.disabled = false;
        if (el.completeStatus) el.completeStatus.textContent = "The complete catalog could not be loaded: " + (error.message || "unknown error") + " The records already shown remain usable.";
      }
      throw error;
    });
    return state.completeCatalogPromise;
  }
  function loadSourceMatrixPatterns() {
    if (!state.sourceMatrixPatternsPromise) {
      var epoch = state.releaseEpoch;
      state.sourceMatrixPatternsPromise = getJSON("source-matrix-patterns.json").then(function (document_) {
        assertCurrentRelease(epoch);
        if (document_ && document_.catalog_content_checksum_sha256 !== (state.snapshot || {}).catalog_content_checksum_sha256) {
          throw new Error("Source-matrix patterns belong to a different catalog release.");
        }
        state.sourceMatrixPatterns = (document_ || {}).patterns || {};
        return state.sourceMatrixPatterns;
      }).catch(function (error) {
        if (epoch === state.releaseEpoch) state.sourceMatrixPatternsPromise = null;
        throw error;
      });
    }
    return state.sourceMatrixPatternsPromise;
  }
  function loadChunk(path) {
    if (!state.chunks[path]) {
      var epoch = state.releaseEpoch;
      state.chunks[path] = (function () {
        return ensureCatalogManifest().then(function () {
        assertCurrentRelease(epoch);
        var metadata = chunkMetadata(path);
        if (!metadata || !/^[0-9a-f]{64}$/.test(text(metadata.sha256))) return Promise.reject(new Error("The release manifest does not bind this detail shard."));
        return fetch(DATA_DIR + path, {cache: "no-cache"}).then(function (response) {
          if (!response.ok) throw new Error(path + " returned HTTP " + response.status);
          return response.arrayBuffer();
        }).then(function (bytes) {
          assertCurrentRelease(epoch);
          if (bytes.byteLength !== Number(metadata.bytes)) throw new Error("Detail-shard byte length does not match the release manifest.");
          return sha256Hex(bytes).then(function (checksum) {
            if (checksum !== metadata.sha256) throw new Error("Detail-shard SHA-256 does not match the release manifest; refresh after publication finishes.");
            var document_ = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
            if (Number(document_.candidate_count) !== Number(metadata.candidate_count)) throw new Error("Detail-shard candidate count does not match the release manifest.");
            return loadSourceMatrixPatterns().then(function (patterns) {
              assertCurrentRelease(epoch);
              (document_.candidates || []).forEach(function (candidate) {
                candidate.source_matrix = window.CTASCatalogModel.expandSourceMatrix(candidate.source_matrix, patterns);
              });
              return document_;
            });
          });
        });
        });
      }()).catch(function (error) { if (epoch === state.releaseEpoch) delete state.chunks[path]; throw error; });
    }
    return state.chunks[path];
  }
  function applyAliasIndex(document_) {
    if (!document_ || document_.catalog_content_checksum_sha256 !== (state.snapshot || {}).catalog_content_checksum_sha256) {
      throw new Error("Alias index belongs to a different catalog release.");
    }
    var columns = document_.columns || [], rows = document_.rows || [], byId = {};
    if (!Array.isArray(columns) || !Array.isArray(rows) || columns.indexOf("event_id") === -1) throw new Error("Alias index table is invalid.");
    rows.forEach(function (values) {
      if (!Array.isArray(values) || values.length !== columns.length) throw new Error("Alias index row width is invalid.");
      var row = {}; columns.forEach(function (column, index) { row[column] = values[index]; });
      (byId[row.event_id] = byId[row.event_id] || []).push({source_key: row.source_key, source: row.source_key,
        designation: row.designation, ambiguous: Boolean(row.ambiguous)});
    });
    state.candidates.forEach(function (candidate) {
      candidate.designations = byId[candidate.event_id] || [];
      candidate.search_aliases = candidate.designations.map(function (row) { return row.designation; });
    });
    state.aliasIndex = document_;
  }
  function ensureAliasIndex() {
    if (state.aliasIndex) return Promise.resolve(state.aliasIndex);
    var epoch = state.releaseEpoch;
    if (!state.aliasPromise) state.aliasPromise = getJSON("alias-index.json", 2).then(function (document_) {
      assertCurrentRelease(epoch);
      applyAliasIndex(document_); return document_;
    }).catch(function (error) { if (epoch === state.releaseEpoch) state.aliasPromise = null; throw error; });
    return state.aliasPromise;
  }
  function legacyCandidateFromHash() {
    var match = window.location.hash.match(/^#candidate=(.+)$/);
    if (!match) return null;
    try { return decodeURIComponent(match[1]); } catch (_) { return null; }
  }
  function candidateRoute(summary, view, band) {
    var url = new URL(window.location.href);
    if (url.searchParams.get("event") !== summary.event_id) url.searchParams.delete("at");
    ["alias", "source", "candidate"].forEach(function (key) { url.searchParams.delete(key); });
    url.searchParams.set("event", summary.event_id);
    if (view) url.searchParams.set("view", view); else url.searchParams.delete("view");
    if (band && band !== "*") url.searchParams.set("band", band); else url.searchParams.delete("band");
    url.hash = "dossier";
    return url.pathname + (url.searchParams.toString() ? "?" + url.searchParams.toString() : "") + url.hash;
  }
  function setCandidateRoute(summary, replace) {
    var view = new URL(window.location.href).searchParams.get("view") || "";
    var band = state.photBand[summary.event_id] || new URL(window.location.href).searchParams.get("band") || "*";
    var target = candidateRoute(summary, view, band);
    if (window.location.pathname + window.location.search + window.location.hash === target) return;
    history[replace ? "replaceState" : "pushState"](null, "", target);
  }
  function routeMatches() {
    var url = new URL(window.location.href), eventId = url.searchParams.get("event");
    if (eventId) return {kind: "event UUID", value: eventId, matches: state.candidates.filter(function (candidate) { return candidate.event_id === eventId; })};
    var alias = url.searchParams.get("alias"), source = url.searchParams.get("source");
    var legacy = legacyCandidateFromHash();
    if (!alias && legacy) alias = legacy;
    if (!alias) return {kind: null, value: null, matches: []};
    var normalized = alias.toLowerCase();
    var matches = state.candidates.filter(function (candidate) {
      if (!source && text(candidate.name).toLowerCase() === normalized) return true;
      return (candidate.designations || []).some(function (row) {
        return text(row.designation).toLowerCase() === normalized && (!source || text(row.source_key).toLowerCase() === source.toLowerCase());
      });
    });
    return {kind: source ? "provider-scoped alias" : "unscoped alias", value: alias, source: source, matches: matches};
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
  function focusDossierTarget() {
    var target = el.workspace.querySelector("[data-dossier-focus]");
    if (target && typeof target.focus === "function") target.focus({preventScroll: true});
  }
  function rememberCandidateOpener(opener, summary) {
    var eventId = opener && opener.getAttribute && opener.getAttribute("data-open-event");
    state.activeOpener = {
      element: opener || null,
      elementId: opener && opener.id || null,
      eventId: eventId || summary && summary.event_id || null
    };
  }
  function restoreCandidateOpener(opener, summary) {
    opener = opener || {};
    var target = opener.element && document.contains(opener.element) ? opener.element : null;
    if (!target && opener.elementId) target = document.getElementById(opener.elementId);
    if (!target && (opener.eventId || summary && summary.event_id)) {
      target = document.querySelector('[data-open-event="' + CSS.escape(opener.eventId || summary.event_id) + '"]');
    }
    if (!target) target = el.q || document.getElementById("ranked-title");
    if (target && typeof target.focus === "function") {
      if (!target.matches("button, a, input, select, textarea, [tabindex]")) target.setAttribute("tabindex", "-1");
      target.focus({preventScroll: true});
    }
  }
  function renderAliasAmbiguity(route) {
    state.activeSummary = null; state.activeDetail = null; el.workspace.hidden = false;
    el.workspace.innerHTML = '<div class="ctas-ambiguity"><p class="eyebrow">Explicit identity ambiguity</p><h3 tabindex="-1" data-dossier-focus>' + esc(route.value || "Candidate identifier") +
      '</h3><p>' + esc(route.matches.length ? "This unscoped identifier matches more than one stable CTAS event. Choose a UUID; CTAS will not guess." : "No public candidate matches this exact identifier in the loaded snapshot.") +
      '</p>' + (route.matches.length ? '<ul>' + route.matches.map(function (candidate) {
        var aliases = (candidate.designations || []).map(function (row) { return row.source_key + ":" + row.designation; }).join(" · ");
        return '<li><button type="button" data-open-event="' + esc(candidate.event_id) + '"><strong>' + esc(candidate.name) +
          '</strong><span>' + esc(candidate.event_id) + '</span><small>' + esc(aliases || "No source alias") + '</small></button></li>';
      }).join("") + '</ul>' : "") + '<button type="button" data-close-candidate>Close</button></div>';
    focusDossierTarget();
  }
  function restoreDossierView(candidate) {
    var url = new URL(window.location.href), view = url.searchParams.get("view"), band = url.searchParams.get("band");
    if (band) state.photBand[candidate.event_id] = band;
    if (view) {
      var panel = el.workspace.querySelector('[data-dossier-view="' + CSS.escape(view) + '"]');
      if (panel && panel.tagName === "DETAILS") panel.open = true;
    }
  }
  function openCandidate(summary, scroll, opener, replaceRoute, quietRefresh) {
    if (!summary || !summary.detail_chunk) return;
    var request = ++state.routeRequest;
    var focusReplacement = !quietRefresh || el.workspace.contains(document.activeElement);
    if (opener) rememberCandidateOpener(opener, summary);
    else if (!state.activeOpener) rememberCandidateOpener(null, summary);
    state.activeSummary = summary; state.activeDetail = null; repaintCandidateLinks(); setCandidateRoute(summary, Boolean(replaceRoute));
    var requestedBand = new URL(window.location.href).searchParams.get("band");
    if (requestedBand) state.photBand[summary.event_id] = requestedBand;
    var requestedEventId = summary.event_id, epoch = state.releaseEpoch;
    el.workspace.hidden = false;
    el.workspace.innerHTML = '<div class="ctas-loading" role="status" aria-live="polite" tabindex="-1" data-dossier-focus><strong>Loading ' + esc(summary.name) + '…</strong><span>Fetching its checksum-bound public evidence shard.</span><button type="button" data-close-candidate>Close</button></div>';
    if (scroll && !(window.matchMedia && window.matchMedia("(min-width: 961px)").matches)) el.workspace.scrollIntoView({behavior: reducedMotion() ? "auto" : "smooth", block: "start"});
    if (focusReplacement) focusDossierTarget();
    loadChunk(summary.detail_chunk).then(function (document_) {
      if (request !== state.routeRequest || epoch !== state.releaseEpoch || !state.activeSummary || state.activeSummary.event_id !== requestedEventId || el.workspace.hidden) return;
      var detail = (document_.candidates || []).find(function (candidate) { return candidate.event_id === summary.event_id; });
      if (!detail) throw new Error("Candidate was not found in its published detail shard.");
      state.activeDetail = detail; el.workspace.innerHTML = renderDetails(detail); restoreDossierView(detail);
      window.dispatchEvent(new CustomEvent("ctas:candidate-opened", {detail: {candidate: detail, summary: summary}}));
      if (focusReplacement) focusDossierTarget();
    }).catch(function (error) {
      if (request !== state.routeRequest || epoch !== state.releaseEpoch || !state.activeSummary || state.activeSummary.event_id !== requestedEventId || el.workspace.hidden) return;
      el.workspace.innerHTML = '<div class="ctas-empty ctas-empty--error"><h3 tabindex="-1" data-dossier-focus>Candidate details could not be loaded</h3><p>' +
        esc(error.message) + '</p><button type="button" data-retry-candidate>Retry this record</button><button type="button" data-close-candidate>Close</button></div>';
      if (focusReplacement) focusDossierTarget();
    });
  }
  function closeCandidate() {
    state.routeRequest += 1;
    var opener = state.activeOpener, summary = state.activeSummary;
    state.activeSummary = null; state.activeDetail = null; el.workspace.hidden = true; el.workspace.innerHTML = ""; repaintCandidateLinks();
    window.dispatchEvent(new CustomEvent("ctas:candidate-closed"));
    var url = new URL(window.location.href);
    ["event", "view", "band", "alias", "source", "candidate", "at"].forEach(function (key) { url.searchParams.delete(key); });
    url.hash = "ranked-candidates";
    history.pushState(null, "", url.pathname + (url.searchParams.toString() ? "?" + url.searchParams.toString() : "") + url.hash);
    state.activeOpener = null;
    restoreCandidateOpener(opener, summary);
  }
  function clearCandidateForHistoryNavigation() {
    state.routeRequest += 1;
    state.activeSummary = null;
    state.activeDetail = null;
    state.activeOpener = null;
    state.exportBusy = false;
    el.workspace.hidden = true;
    el.workspace.innerHTML = "";
    repaintCandidateLinks();
  }
  function openRouteCandidate(scroll, quietRefresh) {
    var route = routeMatches();
    if (!route.kind) return false;
    if (route.kind === "event UUID") { openResolvedCandidate(route.value, scroll, null, true, quietRefresh); return true; }
    openByName(route.value, route.source, scroll, true, quietRefresh); return true;
  }
  function reconcileHistoryRoute() {
    restoreFiltersFromRoute();
    renderTable(); renderStream(); drawSky();
    var route = routeMatches();
    if (!route.kind) {
      clearCandidateForHistoryNavigation();
      revealFragment();
      return;
    }
    if (legacyCandidateFromHash() || window.location.hash === "#dossier") {
      openRouteCandidate(false);
      return;
    }
    revealFragment();
  }

  function populateFilters() {
    function fill(select, key, blank) {
      if (!select) return; var values = {};
      state.candidates.forEach(function (candidate) { if (candidate[key]) values[candidate[key]] = true; });
      select.innerHTML = '<option value="">' + blank + "</option>" + Object.keys(values).sort().map(function (value) {
        return '<option value="' + esc(value) + '">' + esc(value) + "</option>";
      }).join("");
    }
    fill(el.cls, "classification", "All labels"); fill(el.msg, "primary_messenger", "All messengers");
    fill(el.stat, "status", "All statuses"); fill(el.survey, "discovery_survey", "All surveys");
  }
  function restoreFiltersFromRoute() {
    if (!window.CTASCatalogModel) return;
    var filters = window.CTASCatalogModel.parseFilters(new URL(window.location.href).searchParams);
    state.q = filters.q; state.cls = filters.class[0] || ""; state.msg = filters.msg[0] || "";
    state.stat = filters.status[0] || ""; state.survey = filters.survey[0] || "";
    state.from = filters.from; state.to = filters.to; state.magMin = filters.magMin; state.magMax = filters.magMax;
    state.scoreMin = filters.scoreMin; state.scoreMax = filters.scoreMax; state.spectrum = filters.spectrum;
    state.conflict = filters.conflict; state.richness = filters.richness; state.preset = filters.preset || "all";
    state.coneRa = filters.coneRa; state.coneDec = filters.coneDec; state.coneRadius = filters.coneRadius;
    if (filters.window && isFinite(Number(filters.window))) state.skyDays = Number(filters.window);
    var values = [[el.q, state.q], [el.cls, state.cls], [el.msg, state.msg], [el.stat, state.stat], [el.survey, state.survey],
      [el.from, state.from], [el.to, state.to], [el.scoreMin, state.scoreMin], [el.scoreMax, state.scoreMax], [el.magMax, state.magMax],
      [el.spectrum, state.spectrum], [el.conflict, state.conflict], [el.richness, state.richness], [el.coneRa, state.coneRa],
      [el.coneDec, state.coneDec], [el.coneRadius, state.coneRadius]];
    values.forEach(function (pair) { if (pair[0]) pair[0].value = pair[1] === null || pair[1] === undefined ? "" : pair[1]; });
    Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
      var active = button.getAttribute("data-preset") === state.preset; button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-sky-days]"), function (button) {
      var active = Number(button.getAttribute("data-sky-days")) === state.skyDays; button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }
  function clearFilters() {
    state.q = state.cls = state.msg = state.stat = state.survey = state.from = state.to = state.spectrum = state.conflict = state.richness = "";
    state.magMin = state.magMax = state.scoreMin = state.scoreMax = state.coneRa = state.coneDec = state.coneRadius = null;
    state.preset = "all"; state.sortKey = "ctas_score"; state.sortDir = -1; state.shown = PAGE;
    [el.q, el.cls, el.msg, el.stat, el.survey, el.from, el.to, el.scoreMin, el.scoreMax, el.magMax, el.spectrum, el.conflict, el.richness, el.coneRa, el.coneDec, el.coneRadius].forEach(function (field) { if (field) field.value = ""; });
    Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
      var active = button.getAttribute("data-preset") === "all"; button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    renderTable(); notifyFilterChange();
  }
  function updateDossierRoute(view, band) {
    if (!state.activeSummary) return;
    var url = new URL(window.location.href);
    url.searchParams.set("event", state.activeSummary.event_id);
    if (view) url.searchParams.set("view", view);
    if (band && band !== "*") url.searchParams.set("band", band); else if (band !== undefined) url.searchParams.delete("band");
    url.hash = "dossier";
    history.replaceState(null, "", url.pathname + "?" + url.searchParams.toString() + url.hash);
  }
  function downloadAstroEvidence(format, button) {
    if (!state.activeDetail || state.exportBusy) return;
    var status = el.workspace.querySelector("[data-export-status]");
    if (!window.CTASAstroEvidence) {
      if (status) status.textContent = "The AstroEvidence exporter did not load. Refresh the page and try again.";
      return;
    }
    state.exportBusy = true;
    Array.prototype.forEach.call(el.workspace.querySelectorAll("[data-export-format]"), function (item) { item.disabled = true; });
    if (status) status.textContent = "Building the checksum-bound " + format.toUpperCase() + " export…";
    window.CTASAstroEvidence.buildExportBundle(state.activeDetail, state.sourceUniverse).then(function (bundle) {
      var names = Object.keys(bundle.files), chosen;
      if (format === "manifest") chosen = bundle.manifestName;
      else chosen = names.find(function (name) { return name.endsWith("." + format); });
      if (!chosen) throw new Error("The requested export was not generated.");
      var file = bundle.files[chosen]; downloadBlob(chosen, file.content, file.contentType);
      if (status) status.textContent = "Downloaded " + chosen + ". Its checksum is recorded in the companion manifest.";
      if (button) button.textContent = "Downloaded " + format.toUpperCase();
    }).catch(function (error) {
      if (status) status.textContent = "Export failed: " + error.message;
    }).finally(function () {
      state.exportBusy = false;
      Array.prototype.forEach.call(el.workspace.querySelectorAll("[data-export-format]"), function (item) { item.disabled = false; });
    });
  }
  function rerenderForFilters() {
    state.shown = PAGE; renderTable(); notifyFilterChange();
    if (skyNeedsCompleteCatalog() && !state.completeCatalogLoaded) loadCompleteCatalog().catch(function () {});
    if (window.CTASWorkbench && window.CTASWorkbench.refreshActions) window.CTASWorkbench.refreshActions();
  }
  function inputNumber(field) { return field && field.value !== "" && isFinite(Number(field.value)) ? Number(field.value) : null; }
  function updateScoreSandbox() {
    if (!state.activeDetail || !window.CTASCatalogModel || !state.activeDetail.score_model) return;
    var overrides = {};
    Array.prototype.forEach.call(el.workspace.querySelectorAll("[data-score-sandbox]"), function (input) { overrides[input.getAttribute("data-score-sandbox")] = Number(input.value); });
    var scenario = window.CTASCatalogModel.scoreScenario(state.activeDetail.score_model, overrides);
    var output = el.workspace.querySelector("[data-score-sandbox-output]");
    if (output) output.value = num(scenario.final_score, 2);
  }
  function bindInterface() {
    if (el.q) el.q.addEventListener("input", function () {
      state.q = el.q.value; rerenderForFilters();
      if (state.q && !state.aliasIndex) ensureAliasIndex().then(rerenderForFilters).catch(function () { /* name and UUID search remain usable */ });
    });
    if (el.cls) el.cls.addEventListener("change", function () { state.cls = el.cls.value; rerenderForFilters(); });
    if (el.msg) el.msg.addEventListener("change", function () { state.msg = el.msg.value; rerenderForFilters(); });
    if (el.stat) el.stat.addEventListener("change", function () { state.stat = el.stat.value; rerenderForFilters(); });
    if (el.survey) el.survey.addEventListener("change", function () { state.survey = el.survey.value; rerenderForFilters(); });
    if (el.from) el.from.addEventListener("change", function () { state.from = el.from.value; rerenderForFilters(); });
    if (el.to) el.to.addEventListener("change", function () { state.to = el.to.value; rerenderForFilters(); });
    if (el.scoreMin) el.scoreMin.addEventListener("input", function () { state.scoreMin = inputNumber(el.scoreMin); rerenderForFilters(); });
    if (el.scoreMax) el.scoreMax.addEventListener("input", function () { state.scoreMax = inputNumber(el.scoreMax); rerenderForFilters(); });
    if (el.magMax) el.magMax.addEventListener("input", function () { state.magMax = inputNumber(el.magMax); rerenderForFilters(); });
    if (el.spectrum) el.spectrum.addEventListener("change", function () { state.spectrum = el.spectrum.value; rerenderForFilters(); });
    if (el.conflict) el.conflict.addEventListener("change", function () { state.conflict = el.conflict.value; rerenderForFilters(); });
    if (el.richness) el.richness.addEventListener("change", function () { state.richness = el.richness.value; rerenderForFilters(); });
    if (el.coneRa) el.coneRa.addEventListener("input", function () { state.coneRa = inputNumber(el.coneRa); rerenderForFilters(); });
    if (el.coneDec) el.coneDec.addEventListener("input", function () { state.coneDec = inputNumber(el.coneDec); rerenderForFilters(); });
    if (el.coneRadius) el.coneRadius.addEventListener("input", function () { state.coneRadius = inputNumber(el.coneRadius); rerenderForFilters(); });
    trackApplicationHeaderHeight();
    if (el.clear) el.clear.addEventListener("click", clearFilters);
    var downloadPanel = document.getElementById("catalog-downloads");
    if (downloadPanel) {
      downloadPanel.addEventListener("toggle", function () {
        if (downloadPanel.open && !state.catalogManifest) {
          renderCatalogDownloads();
          ensureCatalogManifest().catch(function (error) {
            if (el.downloadStatus) el.downloadStatus.textContent = "The download manifest could not be loaded: " + (error.message || "unknown error");
          });
        }
      });
    }
    if (el.loadComplete) {
      el.loadComplete.addEventListener("click", function () {
        el.loadComplete.disabled = true;
        loadCompleteCatalog().catch(function () {});
      });
    }
    document.addEventListener("pointerover", function (event) {
      var row = event.target.closest && event.target.closest("[data-candidate-id]");
      if (!row || (event.relatedTarget && row.contains(event.relatedTarget))) return;
      state.hoveredEventId = row.getAttribute("data-candidate-id"); repaintCandidateLinks();
    });
    document.addEventListener("pointerout", function (event) {
      var row = event.target.closest && event.target.closest("[data-candidate-id]");
      if (!row || (event.relatedTarget && row.contains(event.relatedTarget))) return;
      state.hoveredEventId = null; repaintCandidateLinks();
    });
    document.addEventListener("focusin", function (event) {
      var row = event.target.closest && event.target.closest("[data-candidate-id]");
      if (!row) return; state.focusedEventId = row.getAttribute("data-candidate-id"); repaintCandidateLinks();
    });
    document.addEventListener("focusout", function (event) {
      var row = event.target.closest && event.target.closest("[data-candidate-id]");
      if (!row || (event.relatedTarget && row.contains(event.relatedTarget))) return;
      state.focusedEventId = null; repaintCandidateLinks();
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
      button.addEventListener("click", function () {
        state.preset = button.getAttribute("data-preset") || "all"; state.shown = PAGE;
        if (["all", "priority", "needs-follow-up"].indexOf(state.preset) !== -1) { state.sortKey = "ctas_score"; state.sortDir = -1; }
        if (state.preset === "today") { state.sortKey = "discovery_time"; state.sortDir = -1; }
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
        renderTable(); notifyFilterChange();
      });
    });
    document.addEventListener("click", function (event) {
      var fragmentLink = event.target.closest('a[href^="#"]');
      if (fragmentLink) revealFragment(fragmentLink.getAttribute("href"));
      var open = event.target.closest("[data-open-event]");
      if (open) {
        openResolvedCandidate(open.getAttribute("data-open-event"), true, open, false); return;
      }
      if (event.target.closest("[data-close-candidate]")) { closeCandidate(); return; }
      if (event.target.closest("[data-retry-candidate]")) {
        if (state.activeSummary) { delete state.chunks[state.activeSummary.detail_chunk]; openCandidate(state.activeSummary, false, null, true); } return;
      }
      if (event.target.closest("[data-copy-link]") && state.activeDetail) {
        var url = new URL(candidateRoute(state.activeDetail,
          new URL(window.location.href).searchParams.get("view") || "",
          state.photBand[state.activeDetail.event_id] || "*"), window.location.origin).href;
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url);
        event.target.closest("[data-copy-link]").textContent = "Permalink copied"; return;
      }
      if (event.target.closest("[data-download-candidate]") && state.activeDetail) {
        downloadBlob(state.activeDetail.name + "-ctas.json", JSON.stringify(state.activeDetail, null, 2) + "\n", "application/json"); return;
      }
      var spectrumButton = event.target.closest("[data-download-spectrum]");
      if (spectrumButton && state.activeDetail) {
        var spectrumIndex = Number(spectrumButton.getAttribute("data-download-spectrum"));
        var spectrum = ((state.activeDetail.follow_up || {}).spectra || [])[spectrumIndex];
        var spectrumPoints = spectrumPreviewPoints(spectrum), spectrumFormat = spectrumButton.getAttribute("data-format") || "json";
        if (!spectrum || !spectrumPoints.length) return;
        var spectrumName = text(spectrum.file_name || spectrum.provider_spectrum_id || "spectrum-preview").replace(/[^A-Za-z0-9._-]+/g, "-");
        downloadBlob(state.activeDetail.name + "-" + spectrumName + "-preview." + spectrumFormat,
          spectrumFormat === "csv" ? rowsToCsv(spectrumPoints) : JSON.stringify(spectrumPoints, null, 2) + "\n",
          spectrumFormat === "csv" ? "text/csv;charset=utf-8" : "application/json"); return;
      }
      var evidenceButton = event.target.closest("[data-download-evidence]");
      if (evidenceButton && state.activeDetail) {
        var kind = evidenceButton.getAttribute("data-download-evidence"), rows = (state.activeDetail.follow_up || {})[kind] || [];
        var format = evidenceButton.getAttribute("data-format");
        downloadBlob(state.activeDetail.name + "-" + kind + "." + format,
          format === "csv" ? rowsToCsv(rows) : JSON.stringify(rows, null, 2) + "\n",
          format === "csv" ? "text/csv" : "application/json"); return;
      }
      var exportButton = event.target.closest("[data-export-format]");
      if (exportButton) { downloadAstroEvidence(exportButton.getAttribute("data-export-format"), exportButton); return; }
      if (event.target.closest("[data-toggle-refresh]")) {
        state.autoRefreshPaused = !state.autoRefreshPaused; renderStatus(); return;
      }
      var sort = event.target.closest("[data-sort]");
      if (sort) {
        var key = sort.getAttribute("data-sort");
        if (state.sortKey === key) state.sortDir *= -1; else { state.sortKey = key; state.sortDir = key === "name" ? 1 : -1; }
        renderTable(); return;
      }
      if (event.target.closest("#ctas-more")) { state.shown += PAGE; renderTable(); return; }
      if (event.target.closest("[data-clear-inline]")) clearFilters();
      if (event.target.closest("[data-reset-score-sandbox]") && state.activeDetail) {
        var termByCode = {}; ((state.activeDetail.score_model || {}).terms || []).forEach(function (term) { termByCode[term.code] = term.points; });
        Array.prototype.forEach.call(el.workspace.querySelectorAll("[data-score-sandbox]"), function (input) { input.value = termByCode[input.getAttribute("data-score-sandbox")] || 0; });
        updateScoreSandbox(); return;
      }
      var dossierSummary = event.target.closest("summary");
      var dossierPanel = dossierSummary && dossierSummary.parentElement && dossierSummary.parentElement.matches("[data-dossier-view]") ? dossierSummary.parentElement : null;
      if (dossierPanel) window.setTimeout(function () {
        if (dossierPanel.open) updateDossierRoute(dossierPanel.getAttribute("data-dossier-view"));
      }, 0);
    });
    document.addEventListener("change", function (event) {
      if (!event.target.matches("[data-phot-band]") || !state.activeDetail) return;
      state.photBand[state.activeDetail.event_id] = event.target.value;
      updateDossierRoute("photometry", event.target.value);
      var panel = el.workspace.querySelector("[data-phot-panel]");
      if (panel) {
        panel.outerHTML = renderPhotometry(state.activeDetail);
        var rebuiltPanel = el.workspace.querySelector("[data-phot-panel]"), rebuiltFilter = rebuiltPanel && rebuiltPanel.querySelector("[data-phot-band]");
        if (rebuiltPanel) rebuiltPanel.open = true;
        if (rebuiltFilter) rebuiltFilter.focus({preventScroll: true});
      }
    });
    document.addEventListener("input", function (event) { if (event.target.matches("[data-score-sandbox]")) updateScoreSandbox(); });
    window.addEventListener("hashchange", reconcileHistoryRoute);
    window.addEventListener("popstate", reconcileHistoryRoute);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !el.workspace.hidden) closeCandidate();
    });
  }

  function applySnapshot(index, status, universe, releaseHistory, catalogManifest, cached, quietRefresh) {
    // Decode the successor completely before replacing any visible release state.
    var candidates = window.CTASCatalogModel.inflateBootstrap(index);
    var sky = window.CTASCatalogModel.inflateSky(index);
    state.releaseEpoch += 1; state.routeRequest += 1;
    state.chunks = {}; state.aliasIndex = null; state.aliasPromise = null;
    state.resolvedCandidates = {}; state.sourceMatrixPatterns = null; state.sourceMatrixPatternsPromise = null;
    state.catalogManifestPromise = null; state.completeCatalogLoaded = false; state.completeCatalogPromise = null;
    state.activeSummary = null; state.activeDetail = null; state.skySelected = null;
    state.refreshError = null;
    if (el.loadComplete) { el.loadComplete.disabled = false; el.loadComplete.textContent = "Browse complete catalog"; }
    if (el.completeStatus) el.completeStatus.textContent = "Showing the compact summary. Browse the complete catalog to load all " + Number(index.candidate_count).toLocaleString() + " retained records in this release.";
    state.snapshot = index;
    state.candidates = candidates; state.skyCandidates = sky;
    state.status = status || {pipeline_status: "unknown", last_successful_update: index.catalog_as_of || index.generated_at, candidate_count: state.candidates.length};
    state.sourceUniverse = universe; state.releaseHistory = releaseHistory;
    state.catalogManifest = catalogManifest; state.cachedSnapshot = Boolean(cached);
    if (cached) state.status = Object.assign({}, state.status, {pipeline_status: "cached"});
    if (el.toolbar) el.toolbar.hidden = !state.candidates.length;
    populateFilters(); restoreFiltersFromRoute();
    renderStatus(); renderOverview(); renderCatalogDownloads(); renderSourceUniverse(); renderReleaseHistory(); renderTable(); drawSky();
    window.dispatchEvent(new CustomEvent("ctas:snapshot", {detail: {snapshot: state.snapshot, status: state.status,
      candidates: state.candidates, sourceUniverse: state.sourceUniverse, releaseHistory: state.releaseHistory,
      catalogManifest: state.catalogManifest, cached: state.cachedSnapshot}}));
    openRouteCandidate(false, quietRefresh);
    revealFragment();
  }
  function showLoadError(error) {
    var cached = null;
    try { cached = JSON.parse(sessionStorage.getItem(CACHE_KEY)); } catch (_) { cached = null; }
    if (cached && cached.index) {
      applySnapshot(cached.index, cached.status, cached.universe, cached.history, cached.manifest, true);
      el.results.insertAdjacentHTML("beforebegin", '<div class="ctas-cache-warning"><strong>Showing the last successfully loaded snapshot.</strong> The current compact catalog could not be refreshed. <button type="button" id="ctas-retry-load">Retry now</button></div>');
      document.getElementById("ctas-retry-load").addEventListener("click", function () { window.location.reload(); });
      return;
    }
    el.results.innerHTML = '<div class="ctas-empty ctas-empty--error"><h3>CTAS data could not be loaded</h3><p>' + esc(error.message || "Unknown loading error") +
      '.</p><button type="button" onclick="window.location.reload()">Retry now</button></div>';
  }
  function assertReleaseConsistency(index, status, universe, manifest) {
    var checksum = index && index.catalog_content_checksum_sha256, problems = [];
    if (!checksum) problems.push("browser bootstrap has no catalog checksum");
    if (status && status.catalog_content_checksum_sha256 !== checksum) problems.push("status belongs to a different catalog checksum");
    if (manifest && manifest.catalog_content_checksum_sha256 !== checksum) problems.push("detail manifest belongs to a different catalog checksum");
    // The detail manifest is fetched lazily, so its absence here is normal.
    if (Number(index && index.candidate_count) !== Number(status && status.candidate_count)) problems.push("status and bootstrap candidate counts differ");
    if (manifest && Number(index && index.candidate_count) !== Number(manifest.candidate_count)) problems.push("manifest and bootstrap candidate counts differ");
    var expectedUniverse = ((index || {}).source_universe || {}).contract_set_checksum_sha256;
    if (expectedUniverse && universe && universe.contract_set_checksum_sha256 !== expectedUniverse) problems.push("source universe contract set differs from the bootstrap binding");
    if (problems.length) throw new Error("CTAS detected a staggered or mixed static release: " + problems.join("; ") + ". Refresh after publication finishes.");
  }
  function fetchReleaseBundle(cacheBust) {
    var suffix = cacheBust ? "?coherence=" + encodeURIComponent(cacheBust) : "";
    return Promise.all([
      getJSON("live-summary.json" + suffix, 1).catch(function () {
        // Transitional: a release published before the summary-first layout
        // still carries the old columnar bootstrap. Never leave the page blank
        // because one artifact name changed between releases.
        return getJSON("catalog-bootstrap.json" + suffix);
      }), getJSON("status.json" + suffix).catch(function () { return null; }),
      getJSON("source-universe.json" + suffix).catch(function () { return null; }),
      getJSON("release-history.json" + suffix).catch(function () { return null; }),
      Promise.resolve(null)
    ]).then(function (result) { assertReleaseConsistency(result[0], result[1], result[2], result[4]); return result; });
  }
  function boot() {
    el.results.innerHTML = '<p class="ctas-loading">Loading the compact public catalog…</p>';
    fetchReleaseBundle().catch(function (error) {
      if (!/mixed static release|staggered/.test(error.message)) throw error;
      return new Promise(function (resolve) { setTimeout(resolve, 700); }).then(function () { return fetchReleaseBundle(Date.now()); });
    }).then(function (result) {
      try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({index: result[0], status: result[1], universe: result[2], history: result[3], manifest: result[4]})); }
      catch (_) { /* cache is best effort */ }
      applySnapshot(result[0], result[1], result[2], result[3], result[4], false);
    }).catch(showLoadError);
  }
  function pollStatus() {
    renderStatus();
    if (state.autoRefreshPaused || document.hidden || state.polling) return Promise.resolve();
    state.polling = true;
    return getJSON("status.json", 2).then(function (status) {
      var previousStatus = state.status || {};
      var oldCatalogChecksum = (state.snapshot || {}).catalog_content_checksum_sha256;
      var oldPublicationChecksum = previousStatus.publication_state_checksum_sha256;
      var catalogChanged = Boolean(status.catalog_content_checksum_sha256) && oldCatalogChecksum !== status.catalog_content_checksum_sha256;
      var publicationStateChanged = Boolean(status.publication_state_checksum_sha256) && oldPublicationChecksum !== status.publication_state_checksum_sha256;
      if (catalogChanged || publicationStateChanged) {
        return fetchReleaseBundle(Date.now()).then(function (result) {
          var index = result[0];
          applySnapshot(index, result[1], result[2], result[3], result[4], false, true);
          try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({index: index, status: state.status, universe: result[2], history: result[3], manifest: result[4]})); }
          catch (_) { /* cache is best effort */ }
        });
      }
      assertReleaseConsistency(state.snapshot, status, state.sourceUniverse, null);
      state.status = status; state.refreshError = null; state.cachedSnapshot = false; renderStatus();
    }).catch(function (error) {
      state.refreshError = error.message || "The current release could not be loaded.";
      renderStatus();
    }).finally(function () { state.polling = false; });
  }

  function candidateSummaryById(eventId) {
    eventId = text(eventId).toLowerCase();
    return state.candidates.find(function (candidate) { return candidate.event_id === eventId; }) || state.resolvedCandidates[eventId] || null;
  }
  function loadCandidateDetail(eventId) {
    eventId = text(eventId).toLowerCase();
    var summary = candidateSummaryById(eventId), epoch = state.releaseEpoch;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(text(eventId))) {
      return Promise.reject(new Error("A valid stable CTAS event UUID is required."));
    }
    var pathPromise = summary && summary.detail_chunk ? Promise.resolve(summary.detail_chunk) : ensureCatalogManifest().then(function (manifest) {
      // Match candidate_bucket(): SHA-256(UUID), first 32 bits modulo shard count.
      // The derived path is still required to exist in the checksum-bound manifest.
      var count = Number(manifest.chunk_count);
      if (count !== 4096 && count !== 256) throw new Error("This release uses an unsupported detail-shard layout.");
      return sha256Hex(new TextEncoder().encode(eventId.toLowerCase())).then(function (hash) {
        return "candidate-chunks/" + (parseInt(hash.slice(0, 8), 16) % count).toString(16).padStart(count === 4096 ? 3 : 2, "0") + ".json";
      });
    });
    return pathPromise.then(function (path) {
      assertCurrentRelease(epoch);
      return loadChunk(path).then(function (document_) {
      assertCurrentRelease(epoch);
      var detail = (document_.candidates || []).find(function (candidate) { return candidate.event_id === eventId; });
      if (!detail) throw new Error("Candidate is absent from its checksum-bound detail shard.");
      state.resolvedCandidates[eventId] = Object.assign({}, detail, {detail_chunk: path});
      return detail;
      });
    });
  }
  function showResolutionError(error) {
    el.workspace.hidden = false;
    el.workspace.innerHTML = '<div class="ctas-empty ctas-empty--error"><h3 tabindex="-1" data-dossier-focus>Candidate could not be resolved</h3><p>' + esc(error.message) + '</p><button type="button" data-close-candidate>Close</button></div>';
    focusDossierTarget();
  }
  function openResolvedCandidate(eventId, scroll, opener, replaceRoute, quietRefresh) {
    eventId = text(eventId).toLowerCase();
    var request = ++state.routeRequest, epoch = state.releaseEpoch;
    var summary = candidateSummaryById(eventId);
    if (summary) { openCandidate(summary, scroll, opener, replaceRoute, quietRefresh); return Promise.resolve(summary); }
    el.workspace.hidden = false;
    el.workspace.innerHTML = '<div role="status" class="ctas-loading">Resolving the archived record…<button type="button" data-close-candidate>Close</button></div>';
    return loadCandidateDetail(eventId).then(function () {
      if (request !== state.routeRequest || epoch !== state.releaseEpoch) return null;
      var resolved = candidateSummaryById(eventId);
      openCandidate(resolved, scroll, opener, replaceRoute, quietRefresh); return resolved;
    }).catch(function (error) {
      if (request === state.routeRequest && epoch === state.releaseEpoch) showResolutionError(error);
      return null;
    });
  }
  function openByName(name, source, scroll, replaceRoute, quietRefresh) {
    var normalized = text(name).trim().toLowerCase();
    if (!normalized) return Promise.reject(new Error("A candidate name or alias is required."));
    var request = ++state.routeRequest, epoch = state.releaseEpoch;
    function finish(matches) {
      if (request !== state.routeRequest || epoch !== state.releaseEpoch) return null;
      if (matches.length === 1) { openCandidate(matches[0], scroll !== false, null, Boolean(replaceRoute), quietRefresh); return matches[0]; }
      renderAliasAmbiguity({kind: "unscoped alias", value: name, matches: matches});
      return null;
    }
    return ensureAliasIndex().then(function (document_) {
      var columns = document_.columns, ids = new Set();
      if (!source) state.candidates.concat(state.skyCandidates).forEach(function (candidate) {
        if (text(candidate.name).toLowerCase() === normalized) ids.add(candidate.event_id);
      });
      document_.rows.forEach(function (row) {
        if (text(row[columns.indexOf("designation")]).toLowerCase() === normalized &&
          (!source || text(row[columns.indexOf("source_key")]).toLowerCase() === source.toLowerCase())) ids.add(row[columns.indexOf("event_id")]);
      });
      return Promise.all(Array.from(ids).map(function (id) {
        var summary = candidateSummaryById(id);
        return summary ? Promise.resolve(summary) : loadCandidateDetail(id).then(function () { return candidateSummaryById(id); });
      })).then(finish);
    }).catch(function (error) {
      if (request === state.routeRequest && epoch === state.releaseEpoch) showResolutionError(error);
      return null;
    });
  }

  window.CTASApp = {
    getStatus: function () { return state.status || {}; },
    getSnapshot: function () { return state.snapshot || {}; },
    getCandidates: function () { return state.candidates.slice(); },
    getVisibleCandidates: function () { return visible(); },
    getFilters: currentFilters,
    loadCandidateDetail: loadCandidateDetail,
    openById: function (eventId) {
      return openResolvedCandidate(eventId, true, null, false);
    },
    openByName: openByName
  };

  bindInterface(); bindSky(); boot(); window.setInterval(pollStatus, 120000);
  window.setInterval(renderStatus, 60000);
}());
