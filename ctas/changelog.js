/* CTAS plain-words changelog (Framework v2 Track 1, part C).

   Renders ctas/data/changelog.json (scripts/ctas_changelog.py): what changed between
   consecutive public releases, keyed by stable event id. On ctas.html it is a panel
   inside "Publication history" that fetches the file only when opened, and only when
   status.json names it (changelog_path), so nothing is probed and nothing loads before
   the reader asks. ctas/status.html renders the same document in full. */
(function () {
  "use strict";

  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character];
    });
  }
  function absolute(value) {
    var parsed = new Date(value); if (isNaN(parsed.getTime())) return "time unavailable";
    return parsed.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC").replace("Z", " UTC");
  }
  function dossierHref(eventId, base) { return (base || "") + "ctas.html?event=" + encodeURIComponent(eventId) + "#dossier"; }

  function list(title, rows, render, count) {
    if (!rows || !rows.length) return "";
    var more = count > rows.length ? '<li>… and ' + (count - rows.length).toLocaleString() + ' more (the list is capped; the count is complete).</li>' : "";
    return '<details class="ctas-changelog__list"><summary>' + esc(title) + ' <small>' + count.toLocaleString() + '</small></summary><ul>' + rows.map(render).join("") + more + '</ul></details>';
  }

  // One entry as HTML. `base` prefixes the dossier links ("" on ctas.html, "../" on the
  // status page).
  function renderEntry(entry, base) {
    var link = function (row) { return '<a href="' + esc(dossierHref(row.event_id, base)) + '">' + esc(row.name) + '</a>'; };
    return '<li class="ctas-changelog__entry"><div class="ctas-changelog__head"><strong>' + esc(absolute(entry.published_at)) + '</strong><span>' +
      esc([entry.candidate_count.toLocaleString() + " records", entry.new_event_count ? "+" + entry.new_event_count + " new" : null, entry.removed_event_count ? "−" + entry.removed_event_count + " removed" : null,
        entry.classification_change_count ? entry.classification_change_count + " classification" + (entry.classification_change_count === 1 ? "" : "s") : null,
        entry.retraction_count ? entry.retraction_count + " retraction" + (entry.retraction_count === 1 ? "" : "s") : null,
        entry.rename_count ? entry.rename_count + " rename" + (entry.rename_count === 1 ? "" : "s") : null,
        entry.source_state_change_count ? entry.source_state_change_count + " source-state change" + (entry.source_state_change_count === 1 ? "" : "s") : null].filter(Boolean).join(" · ")) + '</span></div>' +
      '<ul class="ctas-changelog__words">' + (entry.summary || []).map(function (sentence) { return "<li>" + esc(sentence) + "</li>"; }).join("") + '</ul>' +
      list("New public records", entry.new_events, function (row) { return "<li>" + link(row) + " <small>" + esc([row.event_type, row.discovery_survey, row.classification && row.classification !== "Unclassified" ? row.classification : null].filter(Boolean).join(" · ")) + "</small></li>"; }, entry.new_event_count) +
      list("Classifications", entry.classification_changes, function (row) { return "<li>" + link(row) + ": " + (row.kind === "revised" ? esc(row.previous_classification) + " → " : "now ") + "<strong>" + esc(row.classification) + "</strong></li>"; }, entry.classification_change_count) +
      list("Retractions", entry.retractions, function (row) { return "<li>" + link(row) + " <small>" + esc(row.basis === "status" ? (row.previous_status || "?") + " → " + (row.status || "?") : "retraction notice retained") + "</small></li>"; }, entry.retraction_count) +
      list("Renames (same event id)", entry.renames, function (row) { return "<li>" + esc(row.previous_name) + " → " + link(row) + "</li>"; }, entry.rename_count) +
      list("Records that left the catalog", entry.removed_events, function (row) { return "<li>" + esc(row.name) + " <small>" + esc(row.event_id) + "</small></li>"; }, entry.removed_event_count) +
      list("Source-state changes", entry.source_state_changes, function (row) { return "<li><strong>" + esc(row.name) + "</strong> " + esc(row.direction) + ": " + esc(row.previous_state || "?") + " → " + esc(row.state || "?") + "</li>"; }, entry.source_state_change_count) +
      list("Coverage milestones", entry.coverage_milestones, function (row) { return "<li>" + esc(row.text) + "</li>"; }, (entry.coverage_milestones || []).length) +
      '<p class="ctas-changelog__meta">Release checksum ' + esc(text(entry.catalog_content_checksum_sha256).slice(0, 16)) + '… from ' + esc(text(entry.previous_catalog_content_checksum_sha256 || "no previous release").slice(0, 16)) + (entry.previous_catalog_content_checksum_sha256 ? "…" : "") + '</p></li>';
  }

  function render(document_, options) {
    options = options || {};
    var base = options.base || "", entries = Array.isArray(document_ && document_.entries) ? document_.entries : [];
    var limit = options.limit ? entries.slice(0, options.limit) : entries;
    if (!entries.length) return '<p class="ctas-claim-boundary">' + esc((document_ && document_.claim_boundary) || "") + '</p><p>No release-to-release changes are recorded yet: the changelog starts with the first release built after this feature, and an entry appears once a later release differs from it.</p>';
    return '<p class="ctas-claim-boundary">' + esc(document_.claim_boundary || "") + '</p>' +
      '<p class="ctas-changelog__statement">' + esc(document_.statement || "") + '</p>' +
      '<ol class="ctas-changelog__entries">' + limit.map(function (entry) { return renderEntry(entry, base); }).join("") + '</ol>' +
      (limit.length < entries.length ? '<p class="ctas-changelog__meta">' + (entries.length - limit.length) + ' older entries are in <a href="' + esc(base) + 'ctas/data/changelog.json">changelog.json</a>' + (options.statusHref ? ' and on the <a href="' + esc(options.statusHref) + '">status page</a>' : "") + '.</p>' : "");
  }

  // ctas.html: the panel inside "Publication history", fetched on first open.
  function mountPanel(status) {
    var history = document.getElementById("ctas-release-history");
    if (!history || !status || !status.changelog_path || document.querySelector("[data-changelog-panel]")) return;
    var panel = document.createElement("details");
    panel.className = "ctas-changelog"; panel.setAttribute("data-changelog-panel", status.changelog_path);
    panel.innerHTML = '<summary><span><strong>Plain-words changelog</strong><small>New events, classifications, retractions, renames and source-state changes between releases · loads when opened</small></span></summary>' +
      '<div class="ctas-changelog__body" data-changelog-body><p role="status" aria-live="polite">Open to load ' + esc(status.changelog_path) + '.</p></div>' +
      '<p class="ctas-changelog__meta">Also on the <a href="ctas/status.html">status page</a>, with the per-source accounting and the feeds.</p>';
    history.parentNode.insertBefore(panel, history.nextSibling);
    panel.addEventListener("toggle", function () {
      if (!panel.open || panel.getAttribute("data-changelog-loaded")) return;
      panel.setAttribute("data-changelog-loaded", "1");
      var body = panel.querySelector("[data-changelog-body]");
      body.innerHTML = '<p role="status" aria-live="polite">Loading the changelog…</p>';
      fetch(status.changelog_path, {cache: "no-cache"}).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      }).then(function (document_) { body.innerHTML = render(document_, {base: "", limit: 6, statusHref: "ctas/status.html"}); })
        .catch(function (error) { panel.removeAttribute("data-changelog-loaded"); body.innerHTML = '<p role="status">The changelog could not be loaded (' + esc(error.message) + '). <a href="' + esc(status.changelog_path) + '">Open the file directly</a>.</p>'; });
    });
  }

  window.addEventListener("ctas:snapshot", function (event) { mountPanel(event.detail && event.detail.status); });
  window.CTASChangelog = {render: render, renderEntry: renderEntry, mountPanel: mountPanel};
}());
