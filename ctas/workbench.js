/* CTAS scientist workspace: browser-local modes, comparison, replay, planning and reuse. */
(function () {
  "use strict";

  var MODE_COPY = {
    learn: "Learn shows guided examples and plain-language interpretation.",
    explore: "Explore shows the latest stream, linked sky, and ranked catalog.",
    research: "Research focuses on the ranked catalog, filters, comparison, exports, and source coverage."
  };
  var MAX_COMPARE = 5;
  var WATCH_KEY = "ctas-browser-watchlist-v1";
  var MODE_KEY = "ctas-browser-mode-v1";
  var state = {
    mode: "explore", candidates: [], snapshot: null, sourceUniverse: null,
    compareIds: [], watchIds: [], detailById: {}, replayTimers: {}, observatories: null,
    comparisonOpen: false
  };

  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character];
    });
  }
  function finite(value) { return value !== null && value !== undefined && value !== "" && isFinite(Number(value)); }
  function human(value) { return text(value).replace(/[_-]+/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); }); }
  function absolute(value) {
    var parsed = new Date(value); if (isNaN(parsed.getTime())) return "Time unavailable";
    return parsed.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC").replace("Z", " UTC");
  }
  function candidateById(id) { return state.candidates.find(function (candidate) { return candidate.event_id === id; }); }
  function currentReleaseId() {
    var assurance = (window.CTASApp && window.CTASApp.getStatus && window.CTASApp.getStatus().static_snapshot_verification) || {};
    return assurance.content_release_id || (state.snapshot || {}).catalog_content_checksum_sha256 || "unavailable";
  }
  function saveLocal(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* best effort */ } }
  function loadLocal(key, fallback) { try { var value = JSON.parse(localStorage.getItem(key)); return value === null ? fallback : value; } catch (_) { return fallback; } }
  function updateUrl(changes, replace) {
    var url = new URL(window.location.href);
    Object.keys(changes).forEach(function (key) {
      var value = changes[key];
      if (value === null || value === undefined || value === "") url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    });
    history[replace === false ? "pushState" : "replaceState"](null, "", url.pathname + (url.searchParams.toString() ? "?" + url.searchParams.toString() : "") + url.hash);
  }

  function setMode(mode, options) {
    options = options || {};
    if (!MODE_COPY[mode]) mode = "explore";
    state.mode = mode; document.body.setAttribute("data-ctas-mode", mode);
    Array.prototype.forEach.call(document.querySelectorAll("[data-ctas-mode]"), function (button) {
      button.setAttribute("aria-pressed", button.getAttribute("data-ctas-mode") === mode ? "true" : "false");
    });
    var description = document.getElementById("ctas-mode-description");
    if (description) description.textContent = MODE_COPY[mode];
    var learn = document.getElementById("ctas-learn"), research = document.getElementById("ctas-research-tools");
    var stream = document.getElementById("recent-stream"), sky = document.getElementById("celestial-sphere");
    var ranked = document.getElementById("ranked-candidates");
    if (learn) { learn.hidden = mode !== "learn"; learn.open = mode === "learn"; }
    if (research) { research.hidden = mode !== "research"; research.open = false; }
    if (stream) stream.hidden = mode !== "explore";
    if (sky) { sky.hidden = mode !== "explore"; sky.open = false; }
    if (ranked) ranked.hidden = mode === "learn";
    saveLocal(MODE_KEY, mode);
    if (!options.fromHistory) updateUrl({mode: mode}, true);
    if (options.focus) {
      var target = mode === "learn" ? learn : mode === "research" ? ranked : document.getElementById("recent-stream");
      if (target) target.scrollIntoView({behavior: window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"});
    }
  }

  function restoreMode() {
    var url = new URL(window.location.href), requested = url.searchParams.get("mode");
    var fragment = String(url.hash || "").replace(/^#/, "");
    var fragmentMode = fragment === "ctas-learn" ? "learn" : ["recent-stream", "celestial-sphere"].indexOf(fragment) !== -1 ? "explore" : null;
    setMode(fragmentMode || (MODE_COPY[requested] ? requested : "explore"), {fromHistory: true});
  }

  function restoreCompareIds() {
    var raw = new URL(window.location.href).searchParams.get("compare") || "";
    var seen = {};
    state.compareIds = raw.split(",").filter(function (id) {
      if (!candidateById(id) || seen[id] || Object.keys(seen).length >= MAX_COMPARE) return false;
      seen[id] = true; return true;
    });
    renderComparisonTray(); updateActionStates();
  }

  function comparisonStatus(message) {
    var tray = document.getElementById("ctas-comparison-tray");
    if (tray) tray.setAttribute("data-status-message", message || "");
  }

  function toggleCompare(id) {
    if (!candidateById(id)) return;
    var position = state.compareIds.indexOf(id);
    if (position !== -1) state.compareIds.splice(position, 1);
    else if (state.compareIds.length >= MAX_COMPARE) {
      comparisonStatus("Comparison is limited to five candidates. Remove one before adding another.");
      var tray = document.getElementById("ctas-comparison-tray"); if (tray) tray.focus();
      return;
    } else state.compareIds.push(id);
    updateUrl({compare: state.compareIds.length ? state.compareIds.join(",") : null}, true);
    renderComparisonTray(); updateActionStates();
    if (state.comparisonOpen) renderComparison();
  }

  function renderComparisonTray() {
    var tray = document.getElementById("ctas-comparison-tray"); if (!tray) return;
    tray.hidden = !state.compareIds.length; tray.tabIndex = -1;
    if (!state.compareIds.length) { tray.innerHTML = ""; return; }
    var names = state.compareIds.map(function (id) {
      var candidate = candidateById(id);
      return '<span><strong>' + esc(candidate ? candidate.name : id) + '</strong><button type="button" data-remove-compare="' + esc(id) + '" aria-label="Remove ' + esc(candidate ? candidate.name : id) + ' from comparison">×</button></span>';
    }).join("");
    var message = tray.getAttribute("data-status-message") || state.compareIds.length + " of " + MAX_COMPARE + " candidates selected";
    tray.innerHTML = '<div><small>' + esc(message) + '</small><div class="ctas-comparison-tray__items">' + names + '</div></div><div class="ctas-evidence-tools"><button type="button" data-open-comparison>Compare selected</button><button type="button" data-clear-comparison>Clear</button></div>';
    tray.removeAttribute("data-status-message");
  }

  function comparisonFact(label, value, stateLabel) {
    return '<div><dt>' + esc(label) + '</dt><dd>' + esc(value === null || value === undefined || value === "" ? stateLabel || "Not retained" : value) + '</dd></div>';
  }

  function observabilitySummary(candidate) {
    if (!window.CTASObservability || !finite(candidate.ra_deg) || !finite(candidate.dec_deg)) return "INSUFFICIENT_DATA — coordinates not retained";
    var result = window.CTASObservability.evaluate(candidate, {date: new Date().toISOString().slice(0, 10), latitude_deg: 41.0983,
      longitude_deg: -105.976, min_altitude_deg: 30, max_airmass: 3, max_sun_altitude_deg: -12, min_moon_separation_deg: 20});
    if (result.status !== "COMPLETE") return result.status;
    return result.intervals.length ? result.intervals.length + " planning window" + (result.intervals.length === 1 ? "" : "s") + " from WIR today (UTC)" : "No interval passes the selected WIR planning constraints today";
  }

  function compareCard(candidate, detail) {
    var counts = candidate.follow_up_counts || {}, accounting = candidate.source_accounting || {};
    var conflicts = detail ? ((detail.astro_evidence || {}).conflictSets || []).length : Number(candidate.conflict_count || 0);
    var recent = detail && detail.science_brief && detail.science_brief.most_recent_change;
    return '<article class="ctas-compare-card" data-compare-card="' + esc(candidate.event_id) + '"><header><span class="pill">' + esc(candidate.classification || "Unclassified") + '</span><h4>' + esc(candidate.name) + '</h4><small>' + esc(candidate.event_id) + '</small></header><dl>' +
      comparisonFact("CTAS score", Number(candidate.ctas_score || 0).toFixed(1) + " · ordering aid, not probability") +
      comparisonFact("Reported discovery", [candidate.discovery_time ? absolute(candidate.discovery_time) : "time unavailable", candidate.discovery_survey || "survey unavailable", finite(candidate.discovery_magnitude) ? Number(candidate.discovery_magnitude).toFixed(2) + " mag" : "magnitude unavailable"].join(" · ")) +
      comparisonFact("ICRS position", window.CTASCatalogModel ? window.CTASCatalogModel.sexagesimal(candidate.ra_deg, candidate.dec_deg) : "Not retained") +
      comparisonFact("Public record", (candidate.record_completeness || {}).label || "Not assessed") +
      comparisonFact("Evidence", [counts.observations + " observations", counts.spectra + " spectra", counts.classifications + " classifications", counts.messenger_signals + " notices"].join(" · ")) +
      comparisonFact("Source accounting", [accounting.applicableSources || 0, "applicable ·", accounting.executedQueryReceipts || 0, "executed ·", accounting.dataBearingSources || 0, "data-bearing"].join(" ")) +
      comparisonFact("Explicit conflicts", conflicts) + comparisonFact("Recent retained change", recent ? [human(recent.evidence_type), recent.provider, recent.public_available_at ? absolute(recent.public_available_at) : "availability clock unavailable"].filter(Boolean).join(" · ") : detail ? "No provider change retained" : "Loading complete record…") +
      comparisonFact("Observability", observabilitySummary(candidate)) +
      '</dl><footer class="ctas-card-actions"><button type="button" data-open-event="' + esc(candidate.event_id) + '">Open dossier</button><button type="button" data-remove-compare="' + esc(candidate.event_id) + '">Remove</button></footer></article>';
  }

  function renderComparison() {
    var workspace = document.getElementById("ctas-comparison-workspace"); if (!workspace) return;
    if (state.compareIds.length < 2) {
      workspace.hidden = false; workspace.innerHTML = '<div class="ctas-empty"><h3 id="ctas-comparison-title">Choose at least two candidates</h3><p>The tray accepts two to five unique stable UUIDs.</p></div>'; return;
    }
    state.comparisonOpen = true; workspace.hidden = false;
    var candidates = state.compareIds.map(candidateById).filter(Boolean);
    workspace.innerHTML = '<div class="ctas-comparison-workspace__head"><div><p class="eyebrow">Side-by-side evidence</p><h3 id="ctas-comparison-title" tabindex="-1">Candidate comparison</h3><p>Values remain source-native; CTAS does not average incompatible systems, epochs, limits, or methods.</p></div><div class="ctas-evidence-tools"><button type="button" data-export-comparison>Export comparison JSON</button><button type="button" data-close-comparison>Close</button></div></div><div class="ctas-compare-grid" style="--compare-count:' + candidates.length + '">' + candidates.map(function (candidate) { return compareCard(candidate, state.detailById[candidate.event_id]); }).join("") + '</div><p id="ctas-comparison-status" role="status" aria-live="polite">Loading complete evidence only for the selected candidates…</p>';
    var title = document.getElementById("ctas-comparison-title"); if (title) title.focus({preventScroll: true});
    if (!window.CTASApp || !window.CTASApp.loadCandidateDetail) return;
    Promise.all(candidates.map(function (candidate) {
      if (state.detailById[candidate.event_id]) return Promise.resolve(state.detailById[candidate.event_id]);
      return window.CTASApp.loadCandidateDetail(candidate.event_id).then(function (detail) { state.detailById[candidate.event_id] = detail; return detail; });
    })).then(function () {
      if (!state.comparisonOpen) return;
      var grid = workspace.querySelector(".ctas-compare-grid");
      if (grid) grid.innerHTML = candidates.map(function (candidate) { return compareCard(candidate, state.detailById[candidate.event_id]); }).join("");
      var status = document.getElementById("ctas-comparison-status"); if (status) status.textContent = "Complete selected records loaded from checksum-bound detail shards.";
      updateActionStates();
    }).catch(function (error) {
      var status = document.getElementById("ctas-comparison-status"); if (status) status.textContent = "Comparison detail could not be loaded: " + error.message;
    });
  }

  function toggleWatch(id) {
    if (!candidateById(id)) return;
    var index = state.watchIds.indexOf(id);
    if (index === -1) state.watchIds.push(id); else state.watchIds.splice(index, 1);
    saveLocal(WATCH_KEY, state.watchIds); renderWatchlist(); updateActionStates();
  }

  function renderWatchlist() {
    var host = document.getElementById("ctas-watchlist"); if (!host) return;
    var rows = state.watchIds.map(candidateById).filter(Boolean);
    host.innerHTML = rows.length ? '<ul>' + rows.map(function (candidate) {
      return '<li><button type="button" data-open-event="' + esc(candidate.event_id) + '">' + esc(candidate.name) + '</button><button type="button" data-watch-event="' + esc(candidate.event_id) + '" aria-label="Remove ' + esc(candidate.name) + ' from local watchlist">×</button></li>';
    }).join("") + '</ul>' : '<p>No candidates saved in this browser.</p>';
  }

  function updateActionStates() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-compare-event]"), function (button) {
      var active = state.compareIds.indexOf(button.getAttribute("data-compare-event")) !== -1;
      button.setAttribute("aria-pressed", active ? "true" : "false"); button.textContent = active ? "Compared" : "Compare";
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-watch-event]"), function (button) {
      var active = state.watchIds.indexOf(button.getAttribute("data-watch-event")) !== -1;
      button.setAttribute("aria-pressed", active ? "true" : "false");
      if (button.textContent !== "×") button.textContent = active ? "Watching locally" : "Watch locally";
    });
  }

  function download(filename, content, type) {
    var blob = new Blob([content], {type: type || "application/octet-stream"}), url = URL.createObjectURL(blob);
    var anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function csvCell(value) { var string = text(value); if (/^[=+\-@]/.test(string) && !isFinite(Number(string))) string = "'" + string; return '"' + string.replace(/"/g, '""') + '"'; }
  function cohortRows() { return window.CTASApp && window.CTASApp.getVisibleCandidates ? window.CTASApp.getVisibleCandidates() : state.candidates; }
  function xml(value) { return esc(value).replace(/&#39;/g, "&apos;"); }
  function cohortVotable(rows) {
    return '<?xml version="1.0" encoding="UTF-8"?>\n<VOTABLE xmlns="http://www.ivoa.net/xml/VOTable/v1.3" version="1.4"><RESOURCE type="results"><INFO name="QUERY_STATUS" value="OK"/><TABLE name="CTAS filtered cohort"><FIELD name="event_id" datatype="char" arraysize="*"/><FIELD name="name" datatype="char" arraysize="*"/><FIELD name="ra" datatype="double" unit="deg" ucd="pos.eq.ra;meta.main"/><FIELD name="dec" datatype="double" unit="deg" ucd="pos.eq.dec;meta.main"/><FIELD name="ctas_score" datatype="double"/><FIELD name="classification" datatype="char" arraysize="*"/><DATA><TABLEDATA>' + rows.map(function (row) {
      return '<TR><TD>' + xml(row.event_id) + '</TD><TD>' + xml(row.name) + '</TD><TD>' + (finite(row.ra_deg) ? Number(row.ra_deg) : "") + '</TD><TD>' + (finite(row.dec_deg) ? Number(row.dec_deg) : "") + '</TD><TD>' + Number(row.ctas_score || 0) + '</TD><TD>' + xml(row.classification || "Unclassified") + '</TD></TR>';
    }).join("") + '</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>\n';
  }
  function exportCohort(format) {
    var rows = cohortRows(), filters = window.CTASApp && window.CTASApp.getFilters ? window.CTASApp.getFilters() : {};
    var status = document.getElementById("ctas-cohort-status");
    if (format === "json") {
      var document_ = {schema: "ctas.browser-cohort@1.0.0", content_release_id: currentReleaseId(),
        catalog_content_checksum_sha256: (state.snapshot || {}).catalog_content_checksum_sha256 || null,
        source_universe_checksum_sha256: ((state.snapshot || {}).source_universe || {}).contract_set_checksum_sha256 || null,
        filters: filters, candidate_count: rows.length, event_ids: rows.map(function (row) { return row.event_id; }), candidates: rows};
      download("ctas-cohort.json", JSON.stringify(document_, null, 2) + "\n", "application/json");
    } else if (format === "csv") {
      var columns = ["event_id", "name", "ra_deg", "dec_deg", "discovery_time", "discovery_survey", "discovery_magnitude", "classification", "ctas_score", "status", "detail_chunk"];
      download("ctas-cohort.csv", [columns.map(csvCell).join(",")].concat(rows.map(function (row) { return columns.map(function (column) { return csvCell(row[column]); }).join(","); })).join("\n") + "\n", "text/csv;charset=utf-8");
    } else if (format === "tom") {
      var valid = rows.filter(function (row) { return finite(row.ra_deg) && finite(row.dec_deg); });
      var tomColumns = ["name", "type", "ra", "dec", "epoch", "ctas_event_id", "ctas_score", "classification"];
      download("ctas-tom-targets.csv", [tomColumns.map(csvCell).join(",")].concat(valid.map(function (row) {
        return [row.name, "SIDEREAL", row.ra_deg, row.dec_deg, "2000.0", row.event_id, row.ctas_score, row.classification || "Unclassified"].map(csvCell).join(",");
      })).join("\n") + "\n", "text/csv;charset=utf-8");
    } else download("ctas-cohort.vot", cohortVotable(rows), "application/x-votable+xml");
    if (status) status.textContent = "Downloaded " + rows.length.toLocaleString() + " candidates from the current linked filter as " + format.toUpperCase() + ".";
  }

  function exportComparison() {
    var rows = state.compareIds.map(function (id) { return state.detailById[id] || candidateById(id); }).filter(Boolean);
    var document_ = {schema: "ctas.browser-comparison@1.0.0", content_release_id: currentReleaseId(),
      catalog_content_checksum_sha256: (state.snapshot || {}).catalog_content_checksum_sha256 || null,
      ordered_event_ids: state.compareIds.slice(), candidates: rows};
    download("ctas-comparison.json", JSON.stringify(document_, null, 2) + "\n", "application/json");
  }

  function renderSourceExplorer() {
    var host = document.getElementById("ctas-source-explorer"), universe = state.sourceUniverse;
    if (!host || !universe || !Array.isArray(universe.sources)) return;
    host.innerHTML = '<div class="ctas-source-explorer__controls"><label>Search sources <input type="search" data-source-search placeholder="Provider, survey, product…"></label><label>State <select data-source-state><option value="">All current states</option>' + Array.from(new Set(universe.sources.map(function (row) { return row.operational_state; }))).sort().map(function (value) { return '<option value="' + esc(value) + '">' + esc(human(value)) + '</option>'; }).join("") + '</select></label><label>Family <select data-source-family><option value="">All families</option>' + Array.from(new Set(universe.sources.map(function (row) { return row.primary_family || row.source_family; }))).sort().map(function (value) { return '<option value="' + esc(value) + '">' + esc(human(value)) + '</option>'; }).join("") + '</select></label></div><div class="ctas-source-explorer__summary"></div><ul class="ctas-source-explorer__list"></ul>';
    filterSourceExplorer();
  }
  function filterSourceExplorer() {
    var host = document.getElementById("ctas-source-explorer"), universe = state.sourceUniverse;
    if (!host || !universe) return;
    var q = text((host.querySelector("[data-source-search]") || {}).value).toLowerCase();
    var status = text((host.querySelector("[data-source-state]") || {}).value), family = text((host.querySelector("[data-source-family]") || {}).value);
    var rows = universe.sources.filter(function (row) {
      return (!status || row.operational_state === status) && (!family || (row.primary_family || row.source_family) === family) &&
        (!q || [row.name, row.source_key, row.primary_family, (row.data_types || []).join(" ")].join(" ").toLowerCase().indexOf(q) !== -1);
    });
    var represented = rows.filter(function (row) { return row.representation_state && row.representation_state !== "none"; }).length;
    host.querySelector(".ctas-source-explorer__summary").innerHTML = '<span><strong>' + rows.length + '</strong> matching maintained contracts</span><span><strong>' + represented + '</strong> represented in this snapshot</span><span>“Maintained” is not globally exhaustive</span>';
    host.querySelector(".ctas-source-explorer__list").innerHTML = rows.map(function (row) {
      var url = /^https:\/\//.test(row.documentation_url || "") ? '<a href="' + esc(row.documentation_url) + '" target="_blank" rel="noopener">Documentation</a>' : '<span>Documentation unavailable</span>';
      return '<li><strong>' + esc(row.name) + '</strong><span class="pill">' + esc(human(row.operational_state)) + '</span><small>' + esc([human(row.implementation_state), human(row.representation_state), (row.data_types || []).join(", ")].filter(Boolean).join(" · ")) + '</small>' + url + '</li>';
    }).join("") || '<li>No maintained source contract matches those filters.</li>';
  }

  function candidatePanels(candidate) {
    var hasCoordinates = finite(candidate.ra_deg) && finite(candidate.dec_deg);
    return '<div class="ctas-astronomy-tools">' +
      '<details class="ctas-evidence-panel ctas-aladin" data-dossier-view="sky-context"><summary>Astronomy-native sky context <small>' + (hasCoordinates ? "Aladin Lite on demand" : "INSUFFICIENT_DATA") + '</small></summary><div class="ctas-evidence-panel__body">' +
      (hasCoordinates ? '<p>Load the CDS Aladin Lite atlas only when requested. The CTAS ICRS position remains the marker authority; imagery and catalog layers are fetched from their named providers.</p><button type="button" data-load-aladin="' + esc(candidate.event_id) + '">Load interactive sky atlas</button><p data-aladin-status role="status" aria-live="polite"></p><div class="ctas-aladin__stage" data-aladin-stage hidden></div>' : '<p><strong>INSUFFICIENT_DATA:</strong> no valid coordinate pair is retained, so CTAS does not open a sky-image context.</p>') + '</div></details>' +
      '<details class="ctas-evidence-panel ctas-observability" data-dossier-view="observability"><summary>Browser-local observability planner <small>' + (hasCoordinates ? "geometric estimate" : "INSUFFICIENT_DATA") + '</small></summary><div class="ctas-evidence-panel__body">' +
      (hasCoordinates ? '<p>Adjust public site coordinates and geometric constraints. This does not include weather, telescope state, instrument limits, or scheduling authority.</p><div class="ctas-observability__controls"><label>Site<select data-observatory-site><option value="wir" data-lat="41.0983" data-lon="-105.976">Wyoming Infrared Observatory</option><option value="gemini-n" data-lat="19.8233333" data-lon="-155.4683333">Gemini North</option><option value="gemini-s" data-lat="-30.2416667" data-lon="-70.7466667">Gemini South</option><option value="rubin" data-lat="-30.2446333" data-lon="-70.7494167">Rubin Observatory</option><option value="custom">Custom coordinates</option></select></label><label>Date (UTC)<input type="date" data-observatory-date value="' + new Date().toISOString().slice(0, 10) + '"></label><label>Latitude (deg)<input type="number" data-observatory-lat min="-90" max="90" step="0.0001" value="41.0983"></label><label>Longitude (deg, east +)<input type="number" data-observatory-lon min="-180" max="180" step="0.0001" value="-105.976"></label><label>Minimum altitude (deg)<input type="number" data-observatory-alt min="0" max="90" value="30"></label><label>Maximum airmass<input type="number" data-observatory-airmass min="1" max="10" step="0.1" value="3"></label><label>Sun altitude maximum<select data-observatory-sun><option value="-6">Civil twilight (−6°)</option><option value="-12" selected>Nautical twilight (−12°)</option><option value="-18">Astronomical twilight (−18°)</option></select></label><label>Minimum Moon separation (deg)<input type="number" data-observatory-moon min="0" max="180" value="20"></label><button type="button" data-run-observability="' + esc(candidate.event_id) + '">Calculate</button></div><div data-observability-result role="status" aria-live="polite"></div>' : '<p><strong>INSUFFICIENT_DATA:</strong> observability requires a retained ICRS position.</p>') + '</div></details>' +
      '<details class="ctas-evidence-panel ctas-time-machine" data-dossier-view="history"><summary>Evidence time machine <small>what CTAS had received by a chosen time</small></summary><div class="ctas-evidence-panel__body"><p>This replay gates rows on provider-publication or CTAS-receipt time. It does not claim what every astronomer globally knew, and it never treats observation time alone as availability.</p><div data-time-machine="' + esc(candidate.event_id) + '"></div></div></details></div>';
  }

  function renderObservability(candidate, host) {
    if (!window.CTASObservability) { host.textContent = "The browser observability model did not load."; return; }
    var panel = host.closest(".ctas-observability");
    var result = window.CTASObservability.evaluate(candidate, {
      date: panel.querySelector("[data-observatory-date]").value,
      latitude_deg: panel.querySelector("[data-observatory-lat]").value,
      longitude_deg: panel.querySelector("[data-observatory-lon]").value,
      min_altitude_deg: panel.querySelector("[data-observatory-alt]").value,
      max_airmass: panel.querySelector("[data-observatory-airmass]").value,
      max_sun_altitude_deg: panel.querySelector("[data-observatory-sun]").value,
      min_moon_separation_deg: panel.querySelector("[data-observatory-moon]").value
    });
    if (result.status !== "COMPLETE") { host.innerHTML = '<p><strong>' + esc(result.status) + ':</strong> ' + esc(result.reason) + '</p>'; return; }
    var width = 840, height = 270, left = 48, right = 16, top = 18, bottom = 38;
    function x(index) { return left + index / (result.rows.length - 1) * (width - left - right); }
    function y(value) { return top + (90 - Number(value)) / 180 * (height - top - bottom); }
    var targetPath = result.rows.map(function (row, index) { return (index ? "L" : "M") + x(index).toFixed(1) + " " + y(row.altitude_deg).toFixed(1); }).join(" ");
    var sunPath = result.rows.map(function (row, index) { return (index ? "L" : "M") + x(index).toFixed(1) + " " + y(row.sun_altitude_deg).toFixed(1); }).join(" ");
    var intervals = result.intervals.length ? result.intervals.map(function (row) { return '<span>' + esc(absolute(row.start_utc)) + ' → ' + esc(absolute(row.end_utc)) + ' · ' + Math.round(row.duration_minutes) + ' min</span>'; }).join("") : '<span>No interval passes every selected constraint.</span>';
    host.innerHTML = '<div class="ctas-observability__plot"><svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Target and Sun altitude across the selected UTC date"><rect x="' + left + '" y="' + top + '" width="' + (width-left-right) + '" height="' + (height-top-bottom) + '" class="ctas-plot-bg"/><line x1="' + left + '" y1="' + y(0) + '" x2="' + (width-right) + '" y2="' + y(0) + '" class="ctas-axis"/><path d="' + targetPath + '" fill="none" stroke="#8ad5df" stroke-width="2"/><path d="' + sunPath + '" fill="none" stroke="#d4a74f" stroke-width="1.5"/><text x="' + left + '" y="' + (height-12) + '" class="ctas-axis-label">00 UTC</text><text x="' + (width-right) + '" y="' + (height-12) + '" text-anchor="end" class="ctas-axis-label">24 UTC</text><text x="' + (left+6) + '" y="' + (top+15) + '" class="ctas-axis-label">target cyan · Sun gold</text></svg></div><div class="ctas-observability__intervals">' + intervals + '</div><details><summary>Complete sampled planning table</summary><div class="ctas-evidence-table-wrap ctas-evidence-table-wrap--tall" tabindex="0"><table class="ctas-evidence-table"><caption>Browser-local geometry sampled every 10 minutes. Approximate solar/lunar ephemerides are not precision scheduling products.</caption><thead><tr><th>UTC</th><th>Altitude</th><th>Airmass</th><th>Sun altitude</th><th>Moon separation</th><th>Passes</th></tr></thead><tbody>' + result.rows.map(function (row) { return '<tr><td>' + esc(absolute(row.utc)) + '</td><td>' + row.altitude_deg.toFixed(2) + '°</td><td>' + (row.airmass === null ? 'below horizon' : row.airmass.toFixed(3)) + '</td><td>' + row.sun_altitude_deg.toFixed(2) + '°</td><td>' + row.moon_separation_deg.toFixed(2) + '°</td><td>' + (row.observable ? 'yes' : 'no') + '</td></tr>'; }).join("") + '</tbody></table></div></details><p class="ctas-claim-boundary">' + esc(result.claim_boundary) + '</p>';
  }

  function initializeReplay(candidate) {
    var host = document.querySelector('[data-time-machine="' + CSS.escape(candidate.event_id) + '"]'); if (!host) return;
    var timeline = (candidate.evidence_timeline || []).slice();
    var dated = timeline.filter(function (row) { return row.public_available_at; }).sort(function (a, b) { return new Date(a.public_available_at) - new Date(b.public_available_at); });
    if (!dated.length) { host.innerHTML = '<p><strong>INSUFFICIENT_DATA:</strong> no defensible public-availability clocks are retained for replay. Undated raw rows remain visible elsewhere in the dossier.</p>'; return; }
    var requested = new URL(window.location.href).searchParams.get("at"), requestedTime = requested ? Date.parse(requested) : NaN;
    var selected = dated.length - 1;
    if (Number.isFinite(requestedTime)) {
      selected = -1;
      dated.forEach(function (row, index) {
        if (Date.parse(row.public_available_at) <= requestedTime) selected = index;
      });
    }
    host.innerHTML = '<div class="ctas-time-machine__controls"><label>Available by <input type="range" min="-1" max="' + (dated.length - 1) + '" value="' + selected + '" data-replay-index="' + esc(candidate.event_id) + '"></label><button type="button" data-replay-play="' + esc(candidate.event_id) + '">Play</button><button type="button" data-replay-live="' + esc(candidate.event_id) + '">Return to live</button></div><p data-replay-label role="status" aria-live="polite"></p><div data-replay-result></div>';
    renderReplay(candidate, selected);
  }

  function renderReplay(candidate, selected) {
    var host = document.querySelector('[data-time-machine="' + CSS.escape(candidate.event_id) + '"]'); if (!host) return;
    var dated = (candidate.evidence_timeline || []).filter(function (row) { return row.public_available_at; }).sort(function (a, b) { return new Date(a.public_available_at) - new Date(b.public_available_at); });
    selected = Math.max(-1, Math.min(dated.length - 1, Number(selected)));
    var requested = new URL(window.location.href).searchParams.get("at"), requestedTime = requested ? Date.parse(requested) : NaN;
    var firstTime = Date.parse(dated[0].public_available_at);
    var cutoff = selected >= 0 ? dated[selected].public_available_at
      : new Date(Number.isFinite(requestedTime) && requestedTime < firstTime ? requestedTime : firstTime - 1).toISOString();
    var replay = window.CTASCatalogModel.evidenceAt(candidate.evidence_timeline || [], cutoff);
    host.querySelector("[data-replay-index]").value = selected;
    host.querySelector("[data-replay-label]").textContent = replay.visible.length + " dated entries had reached CTAS by " + absolute(cutoff) + ". Historical CTAS score is unavailable because no append-only per-release score ledger is retained.";
    host.querySelector("[data-replay-result]").innerHTML = (replay.visible.length ? '<ol class="ctas-time-machine__entries">' + replay.visible.slice().sort(function (a,b) { return new Date(b.public_available_at) - new Date(a.public_available_at); }).map(function (row) {
      return '<li><strong>' + esc(human(row.evidence_type)) + ' · ' + esc(row.title || "Untitled evidence") + '</strong><time>Available ' + esc(absolute(row.public_available_at)) + ' via ' + esc(row.availability_basis) + '</time><small>Scientific: ' + esc(row.scientific_time ? absolute(row.scientific_time) : "not recorded") + ' · Provider publication: ' + esc(row.provider_publication_time ? absolute(row.provider_publication_time) : "not recorded") + ' · CTAS receipt: ' + esc(row.ctas_receipt_time ? absolute(row.ctas_receipt_time) : "not recorded") + '</small></li>';
    }).join("") + '</ol>' : '<p>No dated evidence had reached CTAS by this cutoff.</p>') + (replay.undated.length ? '<details><summary>' + replay.undated.length + ' undated entries excluded from historical replay</summary><p>These rows remain in the complete record but have no defensible provider-publication or CTAS-receipt clock.</p></details>' : "");
    updateUrl({at: cutoff}, true);
  }

  function loadAladin(candidate, button) {
    var panel = button.closest(".ctas-aladin"), stage = panel.querySelector("[data-aladin-stage]"), status = panel.querySelector("[data-aladin-status]");
    button.disabled = true; status.textContent = "Loading the CDS Aladin Lite runtime and public sky imagery…"; stage.hidden = false;
    function start() {
      if (!window.A || !window.A.init) { status.textContent = "Aladin Lite did not become available. The CTAS coordinate facts remain usable."; button.disabled = false; return; }
      window.A.init.then(function () {
        window.A.aladin(stage, {target: Number(candidate.ra_deg).toFixed(7) + " " + Number(candidate.dec_deg).toFixed(7), fov: .2,
          cooFrame: "ICRSd", survey: "P/DSS2/color", showReticle: true, showCooGrid: true});
        status.textContent = "Interactive CDS Aladin Lite context centered on the retained CTAS ICRS position. External layers retain their provider attribution.";
        button.hidden = true;
      }).catch(function (error) { status.textContent = "Aladin Lite could not initialize: " + error.message; button.disabled = false; });
    }
    if (window.A && window.A.init) { start(); return; }
    var script = document.createElement("script"); script.src = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js";
    script.charset = "utf-8"; script.onload = start; script.onerror = function () { status.textContent = "The external Aladin Lite runtime could not be loaded. Coordinates and source links remain available."; button.disabled = false; };
    document.head.appendChild(script);
  }

  function xmlRpcValue(value) {
    if (typeof value === "string") return "<value><string>" + xml(value) + "</string></value>";
    if (Array.isArray(value)) return "<value><array><data>" + value.map(xmlRpcValue).join("") + "</data></array></value>";
    if (value && typeof value === "object") return "<value><struct>" + Object.keys(value).map(function (key) { return "<member><name>" + xml(key) + "</name>" + xmlRpcValue(value[key]) + "</member>"; }).join("") + "</struct></value>";
    return xmlRpcValue(text(value));
  }
  function sampCall(method, params) {
    var body = '<?xml version="1.0"?><methodCall><methodName>' + method + '</methodName><params>' + params.map(function (value) { return "<param>" + xmlRpcValue(value) + "</param>"; }).join("") + '</params></methodCall>';
    return fetch("http://localhost:21012/", {method: "POST", headers: {"Content-Type": "text/xml"}, body: body}).then(function (response) { if (!response.ok) throw new Error("hub returned HTTP " + response.status); return response.text(); });
  }
  function sendSamp() {
    var status = document.getElementById("ctas-samp-status"); if (status) status.textContent = "Contacting a SAMP Web Profile hub on this computer because you explicitly requested it…";
    sampCall("samp.webhub.register", [{"samp.name": "CTAS public catalog"}]).then(function (raw) {
      var document_ = new DOMParser().parseFromString(raw, "application/xml"), key = null;
      Array.prototype.forEach.call(document_.querySelectorAll("member"), function (member) {
        if (member.querySelector("name") && member.querySelector("name").textContent === "samp.private-key") key = member.querySelector("value string") && member.querySelector("value string").textContent;
      });
      if (!key) throw new Error("the SAMP hub did not return a private registration key");
      var tableUrl = new URL("ctas/data/research/events.vot", window.location.href).href;
      return sampCall("samp.hub.notifyAll", [key, {"samp.mtype": "table.load.votable", "samp.params": {url: tableUrl, name: "CTAS public events"}}]);
    }).then(function () { if (status) status.textContent = "Sent the checksum-bound all-events VOTable to the running SAMP hub."; }).catch(function (error) {
      if (status) status.innerHTML = "SAMP send was unavailable (" + esc(error.message) + '). This is common in Safari or when TOPCAT has no running hub. <a href="ctas/data/research/events.vot" download>Download the VOTable instead</a>.';
    });
  }

  function afterCandidateRender(candidate) {
    state.detailById[candidate.event_id] = candidate; initializeReplay(candidate); updateActionStates();
  }

  function bind() {
    document.addEventListener("click", function (event) {
      var mode = event.target.closest("[data-ctas-mode]"); if (mode) { setMode(mode.getAttribute("data-ctas-mode"), {focus: true}); return; }
      var switcher = event.target.closest("[data-switch-mode]"); if (switcher) { setMode(switcher.getAttribute("data-switch-mode"), {focus: true}); return; }
      var named = event.target.closest("[data-open-name]"); if (named && window.CTASApp) { setMode("explore", {}); window.CTASApp.openByName(named.getAttribute("data-open-name")); return; }
      var compare = event.target.closest("[data-compare-event]"); if (compare) { toggleCompare(compare.getAttribute("data-compare-event")); return; }
      var remove = event.target.closest("[data-remove-compare]"); if (remove) { toggleCompare(remove.getAttribute("data-remove-compare")); return; }
      var watch = event.target.closest("[data-watch-event]"); if (watch) { toggleWatch(watch.getAttribute("data-watch-event")); return; }
      if (event.target.closest("[data-open-comparison]")) { renderComparison(); return; }
      if (event.target.closest("[data-clear-comparison]")) { state.compareIds = []; state.comparisonOpen = false; updateUrl({compare: null}, true); renderComparisonTray(); updateActionStates(); var comparison = document.getElementById("ctas-comparison-workspace"); if (comparison) comparison.hidden = true; return; }
      if (event.target.closest("[data-close-comparison]")) { state.comparisonOpen = false; document.getElementById("ctas-comparison-workspace").hidden = true; return; }
      if (event.target.closest("[data-export-comparison]")) { exportComparison(); return; }
      var exportButton = event.target.closest("[data-export-cohort]"); if (exportButton) { exportCohort(exportButton.getAttribute("data-export-cohort")); return; }
      if (event.target.closest("[data-copy-filtered-view]")) {
        var status = document.getElementById("ctas-cohort-status");
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(window.location.href).then(function () { if (status) status.textContent = "Copied the current shareable linked-filter URL."; });
        else if (status) status.textContent = "Clipboard access is unavailable; copy the browser address."; return;
      }
      if (event.target.closest("[data-send-samp]")) { sendSamp(); return; }
      var aladin = event.target.closest("[data-load-aladin]"); if (aladin) { var candidate = state.detailById[aladin.getAttribute("data-load-aladin")] || candidateById(aladin.getAttribute("data-load-aladin")); if (candidate) loadAladin(candidate, aladin); return; }
      var run = event.target.closest("[data-run-observability]"); if (run) { var target = state.detailById[run.getAttribute("data-run-observability")] || candidateById(run.getAttribute("data-run-observability")); var resultHost = run.closest(".ctas-observability").querySelector("[data-observability-result]"); if (target) renderObservability(target, resultHost); return; }
      var live = event.target.closest("[data-replay-live]"); if (live) { var liveCandidate = state.detailById[live.getAttribute("data-replay-live")]; if (liveCandidate) { var dated = (liveCandidate.evidence_timeline || []).filter(function (row) { return row.public_available_at; }); renderReplay(liveCandidate, dated.length - 1); updateUrl({at: null}, true); } return; }
      var play = event.target.closest("[data-replay-play]"); if (play) {
        var id = play.getAttribute("data-replay-play"), replayCandidate = state.detailById[id], slider = document.querySelector('[data-replay-index="' + CSS.escape(id) + '"]');
        if (!replayCandidate || !slider) return;
        if (state.replayTimers[id]) { clearInterval(state.replayTimers[id]); delete state.replayTimers[id]; play.textContent = "Play"; return; }
        if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) { play.textContent = "Reduced motion: use the slider"; return; }
        slider.value = slider.min; renderReplay(replayCandidate, slider.min); play.textContent = "Pause";
        state.replayTimers[id] = setInterval(function () { var next = Number(slider.value) + 1; if (next > Number(slider.max)) { clearInterval(state.replayTimers[id]); delete state.replayTimers[id]; play.textContent = "Play"; return; } renderReplay(replayCandidate, next); }, 800); return;
      }
    });
    document.addEventListener("input", function (event) {
      if (event.target.matches("[data-source-search]")) filterSourceExplorer();
      if (event.target.matches("[data-replay-index]")) { var candidate = state.detailById[event.target.getAttribute("data-replay-index")]; if (candidate) renderReplay(candidate, event.target.value); }
    });
    document.addEventListener("change", function (event) {
      if (event.target.matches("[data-source-state], [data-source-family]")) filterSourceExplorer();
      if (event.target.matches("[data-observatory-site]")) {
        var option = event.target.options[event.target.selectedIndex], panel = event.target.closest(".ctas-observability");
        if (option.dataset.lat) panel.querySelector("[data-observatory-lat]").value = option.dataset.lat;
        if (option.dataset.lon) panel.querySelector("[data-observatory-lon]").value = option.dataset.lon;
      }
    });
    window.addEventListener("ctas:snapshot", function (event) {
      state.snapshot = event.detail.snapshot; state.candidates = event.detail.candidates || []; state.sourceUniverse = event.detail.sourceUniverse || null;
      state.watchIds = loadLocal(WATCH_KEY, []).filter(function (id) { return candidateById(id); });
      restoreCompareIds(); renderWatchlist(); renderSourceExplorer(); updateActionStates();
    });
    window.addEventListener("ctas:candidate-opened", function (event) { afterCandidateRender(event.detail.candidate); });
    window.addEventListener("ctas:filters-changed", function () {
      updateActionStates();
      if (state.comparisonOpen) renderComparison();
    });
    window.addEventListener("popstate", function () { restoreMode(); restoreCompareIds(); });
  }

  restoreMode(); bind();
  window.CTASWorkbench = {
    afterCandidateRender: afterCandidateRender,
    candidatePanels: candidatePanels,
    setMode: setMode,
    refreshActions: updateActionStates
  };
}());
