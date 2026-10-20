/* CTAS status page (Framework v2 Track 1, part C): ctas/status.html.

   Reads the release's own files, one request per file and only files the release names
   (status.json first; then the paths it declares), and renders: the release and the
   freshness watchdog's view of it; every source with its four denominators (declared,
   applicable, executed, data-bearing) from source-accounting.json beside the provider
   health states of status.json and the states of source-universe.json; the plain-words
   changelog; the feeds; and the links to openapi.yaml, the schemas, provenance.json, the
   bulk downloads, the DataLink manifest and the research tables (the 700 KB verification
   report is linked, not fetched; status.json carries the failed gate ids). Every table
   has a text summary before it. Nothing is stored about the reader; the one optional live request
   (an open freshness-watchdog issue on GitHub) happens only when a button is pressed and
   is labelled as live. */
(function () {
  "use strict";

  var DATA = "data/";
  var REPOSITORY = "https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io";
  var WATCHDOG_WORKFLOW = REPOSITORY + "/blob/main/.github/workflows/freshness-watchdog.yml";
  var WATCHDOG_ISSUES = REPOSITORY + "/issues?q=is%3Aissue+is%3Aopen+label%3Afreshness-watchdog";
  var WATCHDOG_API = "https://api.github.com/repos/JackMcGuireAstro/JackMcGuireAstro.github.io/issues?state=open&labels=freshness-watchdog&per_page=5";
  // The watchdog's limits as its workflow configures them (freshness-watchdog.yml,
  // 2026-10-19). They live in that file, not in any release, so this page states them
  // and links the file rather than pretending to read them live.
  var WATCHDOG_LIMITS = [
    ["CTAS release age", "4 h", "an alert when the newest release is older than this"],
    ["Cross-check", "2 h fresh / 3 h stale", "WorldsIndex published within 2 h while CTAS has not for 3 h means the CTAS publisher itself is stuck"],
    ["Restored state age", "3 h", "a fresh run that restored a state copy older than this means the runs publish but their saves fail"],
    ["Run results", "3 in a row, or more than 50% of a day", "failed cloud runs are an alert even while the data is fresh"],
    ["State key", "10 days before expiry", "a rejected or soon-expiring CTAS_STATE_TOKEN is an alert before every run fails"]
  ];
  var SCHEMA_NAMES = ["common", "status", "live-summary", "catalog-index", "catalog-page", "catalog-page-manifest", "candidate-chunk", "candidate-json-part",
    "candidate-chunk-manifest", "alias-index", "source-universe", "source-matrix-patterns", "research-manifest", "release-history", "link-health",
    "certification", "bulk-downloads", "datalink-manifest", "provenance", "delivery", "observatories", "cloud-run", "changelog", "source-accounting", "feeds-index"];

  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character];
    });
  }
  function human(value) { return text(value).replace(/[_-]+/g, " ").replace(/^\w/, function (letter) { return letter.toUpperCase(); }); }
  function number(value) { return Number(value || 0).toLocaleString(); }
  function absolute(value) {
    var parsed = new Date(value); if (!value || isNaN(parsed.getTime())) return "unavailable";
    return parsed.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC").replace("Z", " UTC");
  }
  function relative(value) {
    var parsed = new Date(value); if (!value || isNaN(parsed.getTime())) return "unavailable";
    var minutes = Math.round((Date.now() - parsed.getTime()) / 60000);
    if (Math.abs(minutes) < 1) return "just now";
    if (Math.abs(minutes) < 90) return minutes + " min " + (minutes < 0 ? "from now" : "ago");
    var hours = Math.round(minutes / 60);
    if (Math.abs(hours) < 48) return hours + " h " + (hours < 0 ? "from now" : "ago");
    return Math.round(hours / 24) + " d " + (hours < 0 ? "from now" : "ago");
  }
  function getJSON(path) {
    return fetch(path, {cache: "no-cache"}).then(function (response) {
      if (!response.ok) throw new Error(path + " returned HTTP " + response.status);
      return response.json();
    });
  }
  function dataPath(declared) { return declared && declared.indexOf("ctas/data/") === 0 ? DATA + declared.slice("ctas/data/".length) : null; }
  function cell(label, value) { return '<span class="ctas-status__cell"><span class="ctas-status__label">' + esc(label) + '</span><strong class="ctas-status__value">' + value + '</strong></span>'; }
  function table(caption, columns, rows, summaryId) {
    return '<div class="ctas-evidence-table-wrap" role="region" aria-labelledby="' + esc(summaryId) + '" tabindex="0"><table class="ctas-evidence-table"><caption>' + esc(caption) + '</caption><thead><tr>' +
      columns.map(function (column) { return '<th scope="col">' + esc(column) + '</th>'; }).join("") + '</tr></thead><tbody>' +
      rows.map(function (row) { return "<tr>" + row.map(function (value, index) { return (index ? "<td>" : '<th scope="row">') + value + (index ? "</td>" : "</th>"); }).join("") + "</tr>"; }).join("") + '</tbody></table></div>';
  }

  // ------------------------------------------------------------ release and watchdog
  function renderRelease(status) {
    var host = document.getElementById("ctas-status-release"); if (!host) return;
    var verification = status.static_snapshot_verification || {};
    var stale = status.valid_until && Date.now() > new Date(status.valid_until).getTime();
    host.classList.toggle("is-degraded", Boolean(stale || status.pipeline_status === "degraded"));
    host.innerHTML = '<div class="ctas-status__line">' +
      cell("Pipeline", esc(stale ? "Snapshot out of date" : status.pipeline_status === "degraded" ? "Source limits" : "Operational")) +
      cell("Snapshot built", esc(relative(status.last_successful_update))) +
      cell("Latest record", esc(relative(status.latest_record_update))) +
      cell("Public candidates", number(status.candidate_count)) +
      cell("Snapshot integrity", esc(verification.status === "verified-static-snapshot" ? "Verified" : human(verification.status || "pending")) + (verification.check_count ? " <small>" + number(verification.passed_check_count) + " of " + number(verification.check_count) + " checks</small>" : "")) +
      cell("Release id", '<code>' + esc(text(verification.content_release_id || status.catalog_content_checksum_sha256).slice(0, 12)) + '…</code>') + '</div>';
  }

  function renderFreshness(status, marker) {
    var host = document.getElementById("ctas-status-freshness"); if (!host) return;
    var stale = status.valid_until && Date.now() > new Date(status.valid_until).getTime();
    var failed = Array.isArray((status.static_snapshot_verification || {}).failed_gate_ids) ? status.static_snapshot_verification.failed_gate_ids : [];
    var summary = "This release was built " + absolute(status.last_successful_update) + " and is valid until " + absolute(status.valid_until) + " (" + (stale ? "that window has passed" : "within its window") + "); " +
      "the newest retained record changed " + absolute(status.latest_record_update) + ". " + (status.degraded_source_count ? number(status.degraded_source_count) + " source" + (status.degraded_source_count === 1 ? " is" : "s are") + " in a degraded state." : "No source is in a degraded state.") +
      (failed.length ? " " + failed.length + " verification gate" + (failed.length === 1 ? "" : "s") + " did not pass: " + failed.join(", ") + "." : " Every verification gate passed.");
    var rows = [
      ["Snapshot built", esc(absolute(status.last_successful_update)), esc(relative(status.last_successful_update))],
      ["Export checked", esc(absolute(status.export_checked_at)), esc(relative(status.export_checked_at))],
      ["Valid until (freshness window)", esc(absolute(status.valid_until)), esc(stale ? "passed " + relative(status.valid_until) : "ends " + relative(status.valid_until))],
      ["Latest retained record", esc(absolute(status.latest_record_update)), esc(relative(status.latest_record_update))],
      ["Cadence", esc(status.cadence || "unavailable"), ""]
    ];
    var markerRows = marker ? [
      ["Built by run", marker.run_url ? '<a href="' + esc(marker.run_url) + '" target="_blank" rel="noopener">' + esc(marker.workflow) + " #" + esc(marker.run_id || "?") + '<span class="sr-only"> (opens in a new tab)</span></a>' : esc(marker.workflow), esc(relative(marker.generated_at))],
      ["Working state restored", esc(marker.state_asset || "unavailable"), esc(marker.state_restored_at ? relative(marker.state_restored_at) : "")],
      ["That state was saved", esc(absolute(marker.state_saved_at)), esc(relative(marker.state_saved_at))],
      ["State key", esc(marker.token_status || "not probed") + (marker.token_expires_at ? ", expires " + esc(absolute(marker.token_expires_at)) : ""), esc(marker.token_checked_at ? "checked " + relative(marker.token_checked_at) : "")]
    ] : [];
    var gateRows = failed.map(function (gateId) { return [esc(gateId), '<a href="data/certification.json">evidence in the verification report</a>']; });
    host.innerHTML = '<p id="freshness-summary">' + esc(summary) + '</p>' +
      table("Release clocks", ["Clock", "UTC", "Relative to now"], rows, "freshness-summary") +
      '<h3 id="watchdog-title">What the watchdog watches</h3>' +
      '<p id="watchdog-summary">An hourly GitHub Actions check (<a href="' + WATCHDOG_WORKFLOW + '" target="_blank" rel="noopener">freshness-watchdog.yml<span class="sr-only"> (opens in a new tab)</span></a>) reads this release\'s status and the recent run results and keeps one issue labelled <code>freshness-watchdog</code> open while something is wrong, closing it on recovery. Its limits, as that file sets them, are below; the issue list is the live answer.</p>' +
      table("Watchdog limits (as configured in the workflow file)", ["Signal", "Limit", "Meaning"], WATCHDOG_LIMITS.map(function (row) { return row.map(esc); }), "watchdog-summary") +
      (marker ? '<p id="marker-summary">The GitHub Actions run that built this release, the working-state copy it restored and the state key, from the run marker the release carries.</p>' + table("Cloud run marker", ["Fact", "Value", "When"], markerRows, "marker-summary")
        : '<p>This release carries no cloud-run marker, so the run, the restored state and the state key cannot be shown here.</p>') +
      '<div class="ctas-evidence-tools"><a href="' + WATCHDOG_ISSUES + '" target="_blank" rel="noopener">Open watchdog issues on GitHub<span class="sr-only"> (opens in a new tab)</span></a><button type="button" data-watchdog-check>Check GitHub now (live)</button></div>' +
      '<p data-watchdog-live role="status" aria-live="polite">Pressing the button asks the public GitHub API for open issues with that label; that is a live request from your browser, outside the certified release, and nothing is stored.</p>' +
      (gateRows.length ? '<p id="gates-summary">' + gateRows.length + ' verification gate' + (gateRows.length === 1 ? "" : "s") + ' did not pass in this release; each is named with the evidence the exporter recorded.</p>' + table("Verification gates that did not pass", ["Gate", "Evidence"], gateRows, "gates-summary") : "");
  }

  function checkWatchdog(button) {
    var status = document.querySelector("[data-watchdog-live]"); if (!status) return;
    button.disabled = true; status.textContent = "Asking the public GitHub API…";
    fetch(WATCHDOG_API, {headers: {Accept: "application/vnd.github+json"}}).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    }).then(function (issues) {
      if (!Array.isArray(issues) || !issues.length) { status.textContent = "Live: no open freshness-watchdog issue right now (" + absolute(new Date().toISOString()) + ")."; return; }
      status.innerHTML = "Live: " + issues.length + " open watchdog issue" + (issues.length === 1 ? "" : "s") + ": " + issues.map(function (issue) {
        return '<a href="' + esc(issue.html_url) + '" target="_blank" rel="noopener">' + esc(issue.title) + '<span class="sr-only"> (opens in a new tab)</span></a> (updated ' + esc(relative(issue.updated_at)) + ')';
      }).join("; ") + ".";
    }).catch(function (error) { status.textContent = "The live check did not complete (" + error.message + "); the issue list link above still works."; })
      .then(function () { button.disabled = false; });
  }

  // ------------------------------------------------------------ sources
  function renderSources(status, universe, accounting) {
    var host = document.getElementById("ctas-status-sources"); if (!host) return;
    var universeByKey = {}, healthByKey = {};
    ((universe || {}).sources || []).forEach(function (row) { universeByKey[row.source_key] = row; });
    (status.sources || []).forEach(function (row) { healthByKey[row.source] = row; });
    var rows = (accounting && accounting.sources) || [];
    var totals = (accounting && accounting.totals) || {};
    var applicable = rows.filter(function (row) { return row.applicable_events > 0; }).length;
    var executed = rows.filter(function (row) { return row.executed_query_receipts > 0; }).length;
    var bearing = rows.filter(function (row) { return row.data_returned_events > 0 || row.events_with_retained_records > 0; }).length;
    var summary = number(totals.declared_sources) + " source contracts are declared in this release's universe; " + number(applicable) + " apply to at least one of the " + number(accounting ? accounting.candidate_count : status.candidate_count) +
      " public records, " + number(executed) + " hold at least one executed query receipt, and " + number(bearing) + " returned or retained data for at least one record. Summed over records: " +
      number(totals.applicable_source_evaluations) + " applicable evaluations, " + number(totals.executed_query_receipts) + " executed receipts, " + number(totals.data_returned_source_evaluations) + " current DATA_RETURNED outcomes and " +
      number(totals.data_bearing_source_evaluations) + " records with retained rows; the status file's aggregate counts are these sums.";
    var healthCounts = {};
    (status.sources || []).forEach(function (row) { healthCounts[row.state] = (healthCounts[row.state] || 0) + 1; });
    var healthSummary = (status.sources || []).length + " live connectors report their own health separately from the retained evidence: " +
      Object.keys(healthCounts).sort().map(function (state) { return healthCounts[state] + " " + human(state).toLowerCase(); }).join(", ") + ". A failed connector cannot erase an earlier public measurement.";
    var accountingRows = rows.map(function (row) {
      var contract = universeByKey[row.source_key] || {}, health = healthByKey[row.source_key];
      return [
        esc(row.name) + (row.declared ? "" : ' <small>(referenced by retained rows; not in this universe version)</small>'),
        row.declared ? "yes" : "no",
        number(row.applicable_events),
        number(row.executed_query_receipts) + (row.events_with_executed_receipts ? " <small>in " + number(row.events_with_executed_receipts) + " records</small>" : ""),
        number(row.data_returned_events) + " <small>+ " + number(row.events_with_retained_records) + " with retained rows</small>",
        esc(human(contract.operational_state || row.operational_state || "unavailable")) + " <small>" + esc(human(contract.representation_state || row.representation_state || "")) + "</small>",
        health ? esc(human(health.state)) + (health.last_message_at ? " <small>last message " + esc(relative(health.last_message_at)) + "</small>" : "") : "<small>no live connector</small>"
      ];
    });
    var outcomes = Object.keys(totals.outcome_counts || {}).sort().map(function (outcome) { return [esc(outcome), number(totals.outcome_counts[outcome])]; });
    host.innerHTML = '<p id="sources-summary">' + esc(summary) + '</p><p class="ctas-claim-boundary">' + esc((accounting || {}).per_source_versus_aggregate || "") + '</p>' +
      table("Four denominators per source (counts are public records unless stated)", ["Source", "Declared", "Applicable records", "Executed receipts", "Data-bearing (DATA_RETURNED)", "Universe state", "Live connector"], accountingRows, "sources-summary") +
      '<h3 id="outcomes-title">Current query outcomes across all records</h3><p id="outcomes-summary">One current outcome per applicable source per record, summed: ' +
      esc(outcomes.map(function (row) { return row[1] + " " + row[0].replace(/<[^>]+>/g, ""); }).join(", ")) + '. NOT_QUERIED, NOT_CONFIGURED and LINK_ONLY_NOT_QUERIED describe CTAS, not the provider.</p>' +
      table("Outcome totals", ["Outcome", "Evaluations"], outcomes, "outcomes-summary") +
      '<h3 id="health-title">Live connector health</h3><p id="health-summary">' + esc(healthSummary) + '</p>' +
      table("Provider connectors as the publisher saw them at export", ["Connector", "State", "Enabled", "Last message", "Lag", "Detail"], (status.sources || []).map(function (row) {
        return [esc(row.label || row.source), esc(human(row.state)), row.enabled ? "yes" : "no", esc(row.last_message_at ? absolute(row.last_message_at) : "none"), row.lag_seconds === null || row.lag_seconds === undefined ? "" : esc(Math.round(row.lag_seconds) + " s"), esc(row.detail || "")];
      }), "health-summary") +
      '<p class="ctas-changelog__meta">Files: <a href="' + esc(dataPath(status.source_accounting_path) || "data/source-accounting.json") + '">source-accounting.json</a>, <a href="data/source-universe.json">source-universe.json</a>, <a href="data/research/source-accounting.ecsv">per-record accounting (ECSV)</a>.</p>';
  }

  // ------------------------------------------------------------ feeds
  function renderFeeds(status, index) {
    var host = document.getElementById("ctas-status-feeds"); if (!host) return;
    if (!index) { host.innerHTML = "<p>This release declares no feeds index.</p>"; return; }
    var feeds = index.feeds || [];
    var byKind = {};
    feeds.forEach(function (row) { (byKind[row.kind] = byKind[row.kind] || []).push(row); });
    var regions = byKind.region || [], populated = regions.filter(function (row) { return row.member_count > 0; }).length;
    var healpix = (index.regions || {}).healpix || {};
    var summary = feeds.length + " feeds, each as Atom and JSON Feed: all public records, " + (byKind["event-type"] || []).length + " event types, " + (byKind.channel || []).length + " messenger families and " + regions.length +
      " sky regions (" + (healpix.count || 192) + " HEALPix order-" + (healpix.order || 2) + " " + (healpix.scheme || "NESTED") + " pixels in ICRS, about 215 square degrees each, plus the galactic plane within " + ((index.regions || {}).galactic_plane_half_width_deg || 10) + " degrees and the northern and southern hemispheres). " +
      populated + " regions currently hold records. Each feed lists its newest " + (index.item_limit || 100) + " records by public change clock; unchanged feeds are byte-identical between releases.";
    function feedRow(row) {
      return [esc(row.title.replace(/^CTAS — /, "")), number(row.member_count), number(row.item_count),
        '<a href="' + esc(dataPath(row.atom.path)) + '">Atom</a> · <a href="' + esc(dataPath(row.json_feed.path)) + '">JSON Feed</a>'];
    }
    var named = (byKind.all || []).concat(byKind["event-type"] || [], byKind.channel || []);
    host.innerHTML = '<p id="feeds-summary">' + esc(summary) + '</p>' +
      table("Feeds by category", ["Feed", "Records in set", "Items", "Formats"], named.map(feedRow), "feeds-summary") +
      '<details class="ctas-changelog__list"><summary>Sky-region feeds <small>' + regions.length + '</small></summary><p id="regions-summary">One row per region; a region with no records still has a valid, empty feed. HEALPix pixel numbers follow the NESTED scheme at nside ' + esc(healpix.nside || 4) + ' in ICRS; the regions a record belongs to are listed in its feed item under _ctas.regions.</p>' +
      table("Sky-region feeds", ["Region", "Records in set", "Items", "Formats"], regions.map(feedRow), "regions-summary") + '</details>' +
      '<p class="ctas-changelog__meta">Index: <a href="' + esc(dataPath(status.feeds_index_path) || "data/feeds/index.json") + '">feeds/index.json</a> · legacy strong-targets feed: <a href="data/feed.xml">feed.xml</a>. Feeds are files a reader fetches; CTAS keeps no subscriber list.</p>';
  }

  // ------------------------------------------------------------ machine descriptions
  function renderMachine(status, bulk) {
    var host = document.getElementById("ctas-status-machine"); if (!host) return;
    var assets = (bulk && bulk.assets) || [];
    var assetRows = assets.map(function (asset) {
      return ['<a href="' + esc(asset.download_url || asset.url || "#") + '">' + esc(asset.name || asset.asset || "asset") + '</a>', number(asset.rows || asset.row_count), (Number(asset.bytes || 0) / 1048576).toFixed(1) + " MiB", '<code>' + esc(text(asset.sha256).slice(0, 16)) + '…</code>'];
    });
    var links = [
      ["OpenAPI 3.1 description of every static endpoint", "openapi.yaml"],
      ["W3C PROV-JSON provenance of this build", dataPath(status.provenance_path) || "data/provenance.json"],
      ["Verification report (every gate, every bound file)", "data/certification.json"],
      ["DataLink 1.1 manifest (one links document per dossier bucket)", dataPath(status.datalink_manifest_path) || "data/links/manifest.json"],
      ["Research tables (CSV, ECSV, VOTable, TOM targets)", "data/research/manifest.json"],
      ["Bulk VOParquet downloads index", dataPath(status.bulk_downloads_path) || "data/bulk-downloads.json"],
      ["Release history", "data/release-history.json"],
      ["Source universe", "data/source-universe.json"],
      ["Repository metadata", "../CITATION.cff"]
    ];
    host.innerHTML = '<p id="machine-summary">' + links.length + ' entry points for programs, then the ' + SCHEMA_NAMES.length + ' JSON Schemas (2020-12) that scripts/validate_ctas_schemas.py checks every published JSON file against' + (assets.length ? ', and the ' + assets.length + ' VOParquet bulk assets of the rolling GitHub Release' : '') + '.</p>' +
      '<ul class="ctas-status-links">' + links.map(function (row) { return '<li><a href="' + esc(row[1]) + '">' + esc(row[0]) + '</a> <small>' + esc(row[1]) + '</small></li>'; }).join("") + '</ul>' +
      '<details class="ctas-changelog__list"><summary>JSON Schemas <small>' + SCHEMA_NAMES.length + '</small></summary><ul class="ctas-status-links">' + SCHEMA_NAMES.map(function (name) { return '<li><a href="schemas/' + esc(name) + '.schema.json">' + esc(name) + '.schema.json</a></li>'; }).join("") + '</ul></details>' +
      (assetRows.length ? table("VOParquet bulk assets (verify the SHA-256 after download; an asset can lag the Pages release by one cycle)", ["Asset", "Rows", "Size", "SHA-256"], assetRows, "machine-summary") : "");
  }

  function renderChangelog(status, changelog) {
    var host = document.getElementById("ctas-status-changelog"); if (!host) return;
    if (!changelog) { host.innerHTML = "<p>This release declares no changelog.</p>"; return; }
    host.innerHTML = (window.CTASChangelog ? window.CTASChangelog.render(changelog, {base: "../"}) : "<p>The changelog renderer did not load.</p>") +
      '<p class="ctas-changelog__meta">File: <a href="' + esc(dataPath(status.changelog_path) || "data/changelog.json") + '">changelog.json</a>.</p>';
  }

  function failed(id, error) {
    var host = document.getElementById(id); if (host) host.innerHTML = '<p role="status">Could not load this section: ' + esc(error.message) + '.</p>';
  }

  function boot() {
    getJSON(DATA + "status.json").then(function (status) {
      renderRelease(status);
      var optional = function (declared) { var path = dataPath(declared); return path ? getJSON(path).catch(function (error) { return {__error: error}; }) : Promise.resolve(null); };
      return Promise.all([
        getJSON(DATA + "source-universe.json").catch(function (error) { return {__error: error}; }),
        optional(status.source_accounting_path),
        optional(status.changelog_path),
        optional(status.feeds_index_path),
        optional(status.bulk_downloads_path),
        optional(status.cloud_run_marker_path)
      ]).then(function (loaded) {
        var universe = loaded[0], accounting = loaded[1], changelog = loaded[2], feeds = loaded[3], bulk = loaded[4], marker = loaded[5];
        function clean(value) { return value && value.__error ? null : value; }
        renderFreshness(status, clean(marker));
        if (accounting && accounting.__error) failed("ctas-status-sources", accounting.__error); else renderSources(status, clean(universe), accounting);
        if (changelog && changelog.__error) failed("ctas-status-changelog", changelog.__error); else renderChangelog(status, changelog);
        if (feeds && feeds.__error) failed("ctas-status-feeds", feeds.__error); else renderFeeds(status, feeds);
        renderMachine(status, clean(bulk));
      });
    }).catch(function (error) {
      var host = document.getElementById("ctas-status-release");
      if (host) host.innerHTML = '<p role="status">The release status could not be loaded (' + esc(error.message) + '). <a href="data/status.json">Open status.json directly</a>.</p>';
      ["ctas-status-freshness", "ctas-status-sources", "ctas-status-changelog", "ctas-status-feeds", "ctas-status-machine"].forEach(function (id) { failed(id, error); });
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-watchdog-check]");
    if (button) checkWatchdog(button);
  });
  document.documentElement.classList.remove("no-js");
  boot();
  window.CTASStatusPage = {WATCHDOG_LIMITS: WATCHDOG_LIMITS, SCHEMA_NAMES: SCHEMA_NAMES, dataPath: dataPath};
}());
