/* CTAS source comparison in the dossier (Framework v2 Track 2b).
   Draws the candidate's comparison block (scripts/ctas_comparison.py): for position,
   classification, redshift, proposed host and exactly compatible photometry, one row per
   source assertion with its value, uncertainty or limit, epoch and frame, method, reference
   (with the ADS bibcode where the provider gives one), compatibility under the stated rule and
   state (active, superseded, retracted); every row is shown; filters only hide rows. The
   headline selection is shown beside the rows as "selection, not a measurement". The rows a
   reader ticks (or every visible row) export as ECSV or VOTable, with a manifest of SHA-256
   checksums and the citations. The panel and the brief's link are absent without the block.
   No dependencies, so the same file runs in the page and under node. */
(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASComparison = model;
  if (typeof document !== "undefined" && document.addEventListener) model.install(document);
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SCHEMA = "ctas.comparison@1";
  var STATES = {active: "Active", superseded: "Superseded", retracted: "Retracted"};
  var VERDICTS = {reference: "Reference row", consistent: "Consistent", inconsistent: "Inconsistent", "not-comparable": "Not compared"};
  var COLUMNS = [
    ["event_id", "string"], ["event_name", "string"], ["property", "string"], ["source_id", "string"], ["source_record_id", "string"],
    ["value", "string"], ["normalized_value", "string"], ["unit", "string"], ["err_plus", "float64"], ["err_minus", "float64"],
    ["limit_type", "string"], ["probability", "float64"], ["definition", "string"], ["epoch_utc", "string"], ["epoch_kind", "string"],
    ["frame", "string"], ["method", "string"], ["instrument", "string"], ["reference", "string"], ["reference_url", "string"],
    ["bibcode", "string"], ["state", "string"], ["receipt_kind", "string"], ["receipt_sha256", "string"], ["relation", "string"],
    ["verdict", "string"], ["sigma", "float64"], ["reason", "string"], ["comparison_policy", "string"]
  ];

  function esc(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function finite(value) { return typeof value === "number" && isFinite(value); }
  function human(value) { return String(value || "").replace(/[-_]+/g, " ").replace(/^\w/, function (c) { return c.toUpperCase(); }); }
  function block(candidate) {
    var value = candidate && candidate.comparison;
    return value && value.schema === SCHEMA && Array.isArray(value.properties) && value.properties.length ? value : null;
  }
  function anchor(candidate) { return "ctas-comparison-" + String(candidate.event_id || "").slice(0, 8); }
  function safeUrl(value) { return typeof value === "string" && /^https:\/\//.test(value) ? value : null; }

  function valueText(row) {
    if (Array.isArray(row.normalized) && row.normalized.length === 2 && finite(row.normalized[0])) {
      return row.normalized[0].toFixed(6) + "°, " + row.normalized[1].toFixed(6) + "°";
    }
    return row.value;
  }
  function uncertaintyText(row) {
    if (row.limitType === "UPPER_LIMIT") return "Upper limit (value is an upper bound)";
    if (row.limitType === "LOWER_LIMIT") return "Limit (object fainter than this)";
    if (finite(row.errPlus)) return "± " + row.errPlus + (row.unit === "deg" ? " arcsec" : row.unit && row.unit !== "1" ? " " + row.unit : "");
    return "Not reported";
  }
  function referenceHtml(ref) {
    ref = ref || {};
    var url = safeUrl(ref.url), codes = Array.isArray(ref.bibcodes) ? ref.bibcodes : [];
    var label = url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(ref.label || "Source") + '<span class="sr-only"> (opens in a new tab)</span></a>' : esc(ref.label || "Source");
    var bib = codes.map(function (code) {
      return '<a class="ctas-bibcode" href="https://ui.adsabs.harvard.edu/abs/' + encodeURIComponent(code) + '/abstract" target="_blank" rel="noopener">' + esc(code) + '<span class="sr-only"> (ADS, opens in a new tab)</span></a>';
    }).join(" ");
    return label + (bib ? "<br><small>" + bib + "</small>" : "");
  }
  function compatHtml(row) {
    var c = row.compat || {};
    var label = VERDICTS[c.verdict] || "Not assessed";
    var relation = c.relation ? '<span class="pill ctas-comparison__relation">' + esc(human(c.relation)) + "</span> " : "";
    var shape = c.verdict === "inconsistent" ? "✕ " : c.verdict === "consistent" ? "✓ " : c.verdict === "reference" ? "◆ " : "– ";
    return relation + '<strong><span aria-hidden="true">' + shape + "</span>" + esc(label) + "</strong>" +
      (finite(c.sigma) ? " <small>(" + esc(c.sigma.toFixed(2)) + " σ)</small>" : "") + '<br><small>' + esc(c.reason || "") + "</small>";
  }

  function selectionHtml(prop) {
    return (prop.selections || []).map(function (sel) {
      var display = sel.matchesDisplayedValue === true ? "matches the value CTAS displays" :
        sel.matchesDisplayedValue === false ? "differs from the value CTAS displays (the displayed value is unchanged)" : "no displayed value to compare";
      return '<p class="ctas-comparison__selection"><span class="pill">' + esc(sel.label) + "</span> <strong>" + esc(sel.displayValue) +
        "</strong> from row " + esc(sel.chosenRow + 1) + " — " + esc(display) + ".<br><small>Rule " + esc(sel.ruleId + "." + sel.ruleVersion) + ": " +
        esc(sel.rule) + " Review state: " + esc(sel.reviewState) + ".</small></p>";
    }).join("");
  }

  function propertyHtml(candidate, prop, index) {
    var set = prop.set || {};
    var title = prop.label + (prop.group ? " · " + (prop.group.band || "band") + " · " + (prop.group.epoch || "") : "");
    var head = '<div class="ctas-comparison__head"><h5 id="' + esc(anchor(candidate)) + "-p" + index + '">' + esc(title) + "</h5>" +
      (set.conflict ? '<span class="pill ctas-comparison__conflict">In conflict: ' + esc(human(set.relationLabel)) + "</span>" :
        set.relationLabel ? '<span class="pill">' + esc(human(set.relationLabel)) + "</span>" : '<span class="pill">Consistent</span>') + "</div>";
    var rows = prop.rows.map(function (row, rowIndex) {
      return '<tr data-comparison-row data-source="' + esc(row.sourceId) + '" data-state="' + esc(row.state) + '" data-row-index="' + rowIndex + '">' +
        '<td><input type="checkbox" data-comparison-pick value="' + esc(row.id) + '" aria-label="Select row ' + (rowIndex + 1) + " (" + esc(row.sourceId) + ') for export"></td>' +
        "<td>" + (rowIndex + 1) + "</td><td>" + esc(row.sourceId) + "<br><small>" + esc(row.sourceRecordId) + "</small></td>" +
        "<td>" + esc(valueText(row)) + (finite(row.probability) ? "<br><small>probability " + esc(row.probability) + " (calibration not assumed)</small>" : "") +
        (row.host ? "<br><small>host " + esc(row.host) + "</small>" : "") + "</td>" +
        "<td>" + esc(uncertaintyText(row)) + "</td>" +
        "<td>" + esc((row.epoch && row.epoch.time) || "Not reported") + "<br><small>" + esc(human(row.epoch && row.epoch.kind)) + (row.epoch && row.epoch.frame ? " · " + esc(row.epoch.frame) : "") + "</small></td>" +
        "<td>" + esc(row.method || "Not reported") + (row.instrument ? "<br><small>" + esc(row.instrument) + "</small>" : "") + "<br><small>" + esc(human(row.definitionCode.split("|")[0])) + "</small></td>" +
        "<td>" + referenceHtml(row.reference) + "</td><td>" + compatHtml(row) + "</td>" +
        "<td>" + esc(STATES[row.state] || row.state) + (row.seenInEnvelopes > 1 ? "<br><small>seen in " + esc(row.seenInEnvelopes) + " receipts</small>" : "") + "</td></tr>";
    }).join("");
    return '<section class="ctas-comparison__property" data-comparison-property="' + esc(prop.code) + '" data-conflict="' + (set.conflict ? "1" : "0") + '" aria-labelledby="' + esc(anchor(candidate)) + "-p" + index + '">' + head +
      "<p>" + esc(set.explanation || "") + '</p><p class="ctas-comparison__rule"><small>Rule: ' + esc(prop.rule) + "</small></p>" + selectionHtml(prop) +
      '<div class="ctas-evidence-table-wrap" role="region" aria-label="' + esc(title) + ' source rows" tabindex="0"><table class="ctas-evidence-table ctas-comparison__table"><caption>' +
      esc(prop.rows.length) + " source assertion" + (prop.rows.length === 1 ? "" : "s") + (prop.rowsOmitted ? " (" + esc(prop.rowsOmitted) + " more in the JSON download)" : "") +
      '; nothing averaged.</caption><thead><tr><th scope="col">Export</th><th scope="col">#</th><th scope="col">Source / record</th><th scope="col">Value</th><th scope="col">Uncertainty / limit</th><th scope="col">Epoch / frame</th><th scope="col">Method / instrument</th><th scope="col">Reference</th><th scope="col">Compatibility</th><th scope="col">State</th></tr></thead><tbody>' +
      rows + "</tbody></table></div></section>";
  }

  function options(values) {
    return values.map(function (value) { return '<option value="' + esc(value) + '">' + esc(human(value)) + "</option>"; }).join("");
  }

  function panel(candidate) {
    var b = block(candidate);
    if (!b) return "";
    var codes = [], sources = [];
    b.properties.forEach(function (prop) {
      if (codes.indexOf(prop.code) < 0) codes.push(prop.code);
      prop.rows.forEach(function (row) { if (sources.indexOf(row.sourceId) < 0) sources.push(row.sourceId); });
    });
    sources.sort();
    var s = b.summary || {}, id = anchor(candidate);
    if (typeof window !== "undefined") register(window, candidate);
    return '<details class="ctas-evidence-panel ctas-comparison" id="' + esc(id) + '" data-dossier-view="comparison" data-comparison-event="' + esc(candidate.event_id) + '"' + (s.propertiesInConflict ? " open" : "") +
      "><summary>Compare sources <small>" + esc(s.properties) + " propert" + (s.properties === 1 ? "y" : "ies") + " · " + esc(s.rows) + " rows · " +
      esc(s.propertiesInConflict) + " in conflict</small></summary><div class=\"ctas-evidence-panel__body\">" +
      "<p>Every retained source value for each property, side by side. Rows are compared under the rules stated with each table; nothing is averaged, voted or overwritten.</p>" +
      '<div class="ctas-comparison__filters" role="group" aria-label="Filter the comparison rows">' +
      '<label>Property <select data-comparison-filter="property"><option value="">All</option>' + options(codes) + "</select></label>" +
      '<label>Source <select data-comparison-filter="source"><option value="">All</option>' + options(sources) + "</select></label>" +
      '<label>State <select data-comparison-filter="state"><option value="">All</option>' + options(["active", "superseded", "retracted"]) + "</select></label>" +
      '<label><input type="checkbox" data-comparison-filter="conflict"> Only properties in conflict</label></div>' +
      b.properties.map(function (prop, index) { return propertyHtml(candidate, prop, index); }).join("") +
      '<div class="ctas-evidence-tools ctas-comparison__export"><p>Export the ticked rows (or, if none is ticked, every visible row):</p>' +
      '<button type="button" data-comparison-export="ecsv">ECSV</button><button type="button" data-comparison-export="vot">VOTable</button>' +
      '<button type="button" data-comparison-export="manifest">Manifest and citations (JSON)</button>' +
      '<p class="sr-only" role="status" aria-live="polite" data-comparison-status></p></div>' +
      '<p class="ctas-claim-boundary">' + esc(b.claimBoundary) + " Policy " + esc(b.policyVersion) + ".</p></div></details>";
  }

  function briefLink(candidate) {
    var b = block(candidate);
    if (!b) return "";
    var s = b.summary || {};
    var text = s.propertiesInConflict
      ? s.propertiesInConflict + " propert" + (s.propertiesInConflict === 1 ? "y" : "ies") + " in conflict (" + (s.conflictCodes || []).map(human).join(", ") + ")"
      : "Sources compared for " + s.properties + " propert" + (s.properties === 1 ? "y" : "ies") + "; no conflict under the stated rules";
    return '<p class="ctas-comparison-link"><a href="#' + esc(anchor(candidate)) + '" data-comparison-open>' + esc(text) + " — compare sources</a></p>";
  }

  // ------------------------------------------------------------------ exports
  function flatRows(candidate, ids) {
    var b = block(candidate), out = [];
    if (!b) return out;
    b.properties.forEach(function (prop) {
      prop.rows.forEach(function (row) {
        if (ids && ids.indexOf(row.id) < 0) return;
        var c = row.compat || {}, ref = row.reference || {}, receipt = row.receipt || {};
        out.push({
          event_id: candidate.event_id, event_name: candidate.name, property: prop.code, source_id: row.sourceId,
          source_record_id: row.sourceRecordId, value: row.value,
          normalized_value: Array.isArray(row.normalized) ? row.normalized.join(" ") : row.normalized === null || row.normalized === undefined ? "" : String(row.normalized),
          unit: row.unit || "", err_plus: finite(row.errPlus) ? row.errPlus : null, err_minus: finite(row.errMinus) ? row.errMinus : null,
          limit_type: row.limitType, probability: finite(row.probability) ? row.probability : null, definition: row.definitionCode,
          epoch_utc: (row.epoch && row.epoch.time) || "", epoch_kind: (row.epoch && row.epoch.kind) || "", frame: (row.epoch && row.epoch.frame) || "",
          method: row.method || "", instrument: row.instrument || "", reference: ref.label || "", reference_url: ref.url || "",
          bibcode: (ref.bibcodes || []).join(" "), state: row.state, receipt_kind: receipt.kind || "", receipt_sha256: receipt.checksumSha256 || "",
          relation: c.relation || "", verdict: c.verdict || "", sigma: finite(c.sigma) ? c.sigma : null, reason: c.reason || "",
          comparison_policy: b.policyVersion
        });
      });
    });
    return out;
  }
  function ecsvCell(value, type) {
    if (value === null || value === undefined || value === "") return type === "float64" ? "nan" : '""';
    if (type === "float64") return String(value);
    return '"' + String(value).replace(/"/g, '""').replace(/[\r\n]+/g, " ") + '"';
  }
  function toEcsv(candidate, rows) {
    var b = block(candidate) || {};
    var header = ["# %ECSV 1.0", "# ---", "# datatype:"].concat(COLUMNS.map(function (col) {
      return "# - {name: " + col[0] + ", datatype: " + col[1] + "}";
    })).concat([
      "# meta:",
      "#   title: " + JSON.stringify("CTAS source comparison rows for " + (candidate.name || candidate.event_id)),
      "#   event_id: " + JSON.stringify(String(candidate.event_id || "")),
      "#   comparison_schema: " + JSON.stringify(b.schema || SCHEMA),
      "#   comparison_policy: " + JSON.stringify(b.policyVersion || ""),
      "#   note: " + JSON.stringify("Source values exactly as published by CTAS; no averaging. Positions: normalized_value is 'ra dec' in degrees; err in arcsec."),
      "# schema: astropy-2.0"
    ]);
    var lines = [COLUMNS.map(function (col) { return col[0]; }).join(" ")].concat(rows.map(function (row) {
      return COLUMNS.map(function (col) { return ecsvCell(row[col[0]], col[1]); }).join(" ");
    }));
    return header.join("\n") + "\n" + lines.join("\n") + "\n";
  }
  function xml(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}[c];
    });
  }
  function toVotable(candidate, rows) {
    var b = block(candidate) || {};
    var fields = COLUMNS.map(function (col) {
      return col[1] === "float64" ? '<FIELD name="' + col[0] + '" datatype="double"/>' : '<FIELD name="' + col[0] + '" datatype="char" arraysize="*"/>';
    }).join("\n      ");
    var body = rows.map(function (row) {
      return "<TR>" + COLUMNS.map(function (col) {
        var value = row[col[0]];
        return "<TD>" + (value === null || value === undefined ? (col[1] === "float64" ? "NaN" : "") : xml(value)) + "</TD>";
      }).join("") + "</TR>";
    }).join("\n        ");
    return '<?xml version="1.0" encoding="UTF-8"?>\n<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">\n  <RESOURCE type="results">\n' +
      '    <INFO name="comparison_policy" value="' + xml(b.policyVersion || "") + '"/>\n    <INFO name="event_id" value="' + xml(candidate.event_id) + '"/>\n' +
      '    <TABLE name="ctas_source_comparison">\n      <DESCRIPTION>CTAS source comparison rows (Track 2b); source values exactly as published, no averaging.</DESCRIPTION>\n      ' +
      fields + "\n      <DATA><TABLEDATA>\n        " + body + "\n      </TABLEDATA></DATA>\n    </TABLE>\n  </RESOURCE>\n</VOTABLE>\n";
  }
  function citations(rows) {
    var seen = {}, out = [];
    rows.forEach(function (row) {
      String(row.bibcode || "").split(" ").filter(Boolean).forEach(function (code) {
        if (!seen[code]) { seen[code] = true; out.push({bibcode: code, url: "https://ui.adsabs.harvard.edu/abs/" + code + "/abstract", sources: [row.source_id]}); }
      });
      var key = row.source_id + "|" + row.reference + "|" + row.reference_url;
      if (!seen[key]) { seen[key] = true; out.push({source: row.source_id, reference: row.reference, url: row.reference_url || null}); }
    });
    out.push({software: "CTAS — Cowboy Transient Alert System", url: "https://jackmcguireastro.github.io/ctas.html"});
    return out;
  }
  function hex(buffer) {
    return Array.prototype.map.call(new Uint8Array(buffer), function (byte) { return ("0" + byte.toString(16)).slice(-2); }).join("");
  }
  function sha256(textValue) {
    var subtle = typeof crypto !== "undefined" && crypto.subtle;
    if (!subtle) return Promise.resolve(null);
    return subtle.digest("SHA-256", new TextEncoder().encode(textValue)).then(hex);
  }
  function fileBase(candidate) { return "ctas-comparison-" + String(candidate.name || candidate.event_id || "event").replace(/[^A-Za-z0-9._-]+/g, "_"); }
  function manifest(candidate, rows) {
    var ecsv = toEcsv(candidate, rows), vot = toVotable(candidate, rows), base = fileBase(candidate);
    return Promise.all([sha256(ecsv), sha256(vot)]).then(function (digests) {
      return {
        schema: "ctas.comparison-export@1", event_id: candidate.event_id, event_name: candidate.name, rows: rows.length,
        comparison_policy: (block(candidate) || {}).policyVersion,
        files: [{name: base + ".ecsv", sha256: digests[0], bytes: new TextEncoder().encode(ecsv).length},
                {name: base + ".vot", sha256: digests[1], bytes: new TextEncoder().encode(vot).length}],
        row_ids: rows.map(function (row) { return row.property + ":" + row.source_id + ":" + row.source_record_id; }),
        citations: citations(rows),
        note: "Checksums cover the ECSV and VOTable files exported from exactly these rows."
      };
    });
  }

  // ------------------------------------------------------------------ page behaviour
  function applyFilters(root) {
    var get = function (name) { var input = root.querySelector('[data-comparison-filter="' + name + '"]'); return input ? (input.type === "checkbox" ? input.checked : input.value) : ""; };
    var property = get("property"), source = get("source"), state = get("state"), conflict = get("conflict");
    Array.prototype.forEach.call(root.querySelectorAll("[data-comparison-property]"), function (section) {
      var visible = (!property || section.getAttribute("data-comparison-property") === property) && (!conflict || section.getAttribute("data-conflict") === "1");
      var shown = 0;
      Array.prototype.forEach.call(section.querySelectorAll("[data-comparison-row]"), function (row) {
        var ok = visible && (!source || row.getAttribute("data-source") === source) && (!state || row.getAttribute("data-state") === state);
        row.hidden = !ok;
        if (ok) shown += 1;
      });
      section.hidden = !visible || shown === 0;
    });
  }
  function pickedIds(root) {
    var ticked = Array.prototype.filter.call(root.querySelectorAll("[data-comparison-pick]"), function (box) { return box.checked; });
    var boxes = ticked.length ? ticked : Array.prototype.filter.call(root.querySelectorAll("[data-comparison-pick]"), function (box) {
      var row = box.closest("[data-comparison-row]"), section = box.closest("[data-comparison-property]");
      return row && !row.hidden && section && !section.hidden;
    });
    return boxes.map(function (box) { return box.value; });
  }
  function download(doc, name, type, textValue) {
    var blob = new Blob([textValue], {type: type}), url = URL.createObjectURL(blob), link = doc.createElement("a");
    link.href = url; link.download = name; doc.body.appendChild(link); link.click(); link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function install(doc) {
    if (doc.__ctasComparisonInstalled) return;
    doc.__ctasComparisonInstalled = true;
    doc.addEventListener("change", function (event) {
      var root = event.target && event.target.closest && event.target.closest(".ctas-comparison");
      if (root && event.target.hasAttribute("data-comparison-filter")) applyFilters(root);
    });
    doc.addEventListener("click", function (event) {
      var target = event.target && event.target.closest && event.target.closest("[data-comparison-open],[data-comparison-export]");
      if (!target) return;
      if (target.hasAttribute("data-comparison-open")) {
        var panelNode = doc.getElementById((target.getAttribute("href") || "").slice(1));
        if (panelNode) { event.preventDefault(); panelNode.open = true; panelNode.scrollIntoView({block: "start"}); var summary = panelNode.querySelector("summary"); if (summary) summary.focus(); }
        return;
      }
      var root = target.closest(".ctas-comparison");
      if (!root) return;
      var win = doc.defaultView || (typeof window !== "undefined" ? window : null);
      var candidate = win && (win.CTASComparisonCandidates || {})[root.getAttribute("data-comparison-event")];
      if (!candidate) return;
      var rows = flatRows(candidate, pickedIds(root)), format = target.getAttribute("data-comparison-export"), status = root.querySelector("[data-comparison-status]");
      if (!rows.length) { if (status) status.textContent = "No visible row to export."; return; }
      if (format === "ecsv") download(doc, fileBase(candidate) + ".ecsv", "text/plain", toEcsv(candidate, rows));
      else if (format === "vot") download(doc, fileBase(candidate) + ".vot", "application/x-votable+xml", toVotable(candidate, rows));
      else manifest(candidate, rows).then(function (doc_) { download(doc, fileBase(candidate) + "-manifest.json", "application/json", JSON.stringify(doc_, null, 2) + "\n"); });
      if (status) status.textContent = rows.length + " row" + (rows.length === 1 ? "" : "s") + " exported.";
    });
  }
  /* The dossier calls this after rendering so the export handler finds the candidate data. */
  function register(win, candidate) {
    if (!win || !block(candidate)) return;
    win.CTASComparisonCandidates = win.CTASComparisonCandidates || {};
    win.CTASComparisonCandidates[candidate.event_id] = candidate;
  }

  return {SCHEMA: SCHEMA, block: block, panel: panel, briefLink: briefLink, register: register, flatRows: flatRows,
          toEcsv: toEcsv, toVotable: toVotable, citations: citations, manifest: manifest, applyFilters: applyFilters,
          pickedIds: pickedIds, install: install, esc: esc};
}));
