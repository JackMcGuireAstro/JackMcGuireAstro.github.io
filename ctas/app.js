/* ==========================================================================
   CTAS public interface.

   Reads two static JSON files produced by scripts/update_ctas.py during the
   scheduled GitHub Actions run and renders them as a searchable, sortable
   candidate table. There is no backend: everything here is browser-side over
   files that were already sanitized server-side.
   ========================================================================== */
(function () {
  "use strict";

  var DATA_DIR = "ctas/data/";

  // 2,000+ rows is too many DOM nodes to paint at once; render a window and
  // let the reader ask for more. Search and sort always span the full set.
  var PAGE = 150;
  var state = { candidates: [], status: null, sortKey: "ctas_score", sortDir: -1,
               q: "", cls: "", msg: "", stat: "", shown: PAGE };

  var el = {
    status:   document.getElementById("ctas-status"),
    sources:  document.getElementById("ctas-sources"),
    toolbar:  document.getElementById("ctas-toolbar"),
    results:  document.getElementById("ctas-results"),
    count:    document.getElementById("ctas-count"),
    q:        document.getElementById("ctas-q"),
    cls:      document.getElementById("ctas-class"),
    msg:      document.getElementById("ctas-messenger"),
    stat:     document.getElementById("ctas-statusfilter")
  };
  if (!el.results) return;

  // ---------------------------------------------------------------- helpers
  function text(s) { return s === null || s === undefined ? "" : String(s); }

  function esc(s) {
    return text(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function num(v, digits) {
    if (v === null || v === undefined || v === "") return "";
    var n = Number(v);
    return isFinite(n) ? n.toFixed(digits === undefined ? 2 : digits) : "";
  }

  function parseDate(s) {
    if (!s) return null;
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function absolute(s) {
    var d = parseDate(s);
    if (!d) return "unknown";
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC").replace("Z", " UTC");
  }

  function relative(s) {
    var d = parseDate(s);
    if (!d) return "";
    var mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins === 1) return "1 minute ago";
    if (mins < 60) return mins + " minutes ago";
    var hrs = Math.round(mins / 60);
    if (hrs === 1) return "1 hour ago";
    if (hrs < 48) return hrs + " hours ago";
    return Math.round(hrs / 24) + " days ago";
  }

  function sexagesimal(ra, dec) {
    if (ra === undefined || dec === undefined) return "";
    var rh = ra / 15, h = Math.floor(rh), m = Math.floor((rh - h) * 60),
        s = ((rh - h) * 60 - m) * 60;
    var sign = dec < 0 ? "-" : "+", ad = Math.abs(dec),
        dd = Math.floor(ad), dm = Math.floor((ad - dd) * 60),
        ds = ((ad - dd) * 60 - dm) * 60;
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(h) + ":" + p(m) + ":" + s.toFixed(1) +
           " " + sign + p(dd) + ":" + p(dm) + ":" + ds.toFixed(0);
  }

  function link(url, label) {
    return url
      ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(label) + '</a>'
      : '';
  }

  function fact(label, value) {
    if (value === null || value === undefined || value === "") return "";
    return '<div><dt>' + esc(label) + '</dt><dd>' + esc(value) + '</dd></div>';
  }

  function detailList(title, rows, render) {
    if (!Array.isArray(rows) || !rows.length) return "";
    return '<section class="ctas-detail__section"><h4>' + esc(title) + '</h4><ul>' +
      rows.map(render).join("") + '</ul></section>';
  }

  function renderDetails(c) {
    var follow = c.follow_up || {};
    var counts = [
      ["classification", follow.classifications],
      ["observation", follow.observations],
      ["messenger notice", follow.messenger_signals],
      ["public report", follow.publications]
    ].filter(function (item) { return Array.isArray(item[1]) && item[1].length; })
      .map(function (item) {
        return item[1].length + " " + item[0] + (item[1].length === 1 ? "" : "s");
      });
    var rationale = counts.length
      ? "CTAS has retained " + counts.join(", ") + " for review."
      : "CTAS has a public event record, but no additional public follow-up rows are retained yet.";

    var context = '<dl class="ctas-detail__facts">' +
      fact("Coordinates", sexagesimal(c.ra_deg, c.dec_deg)) +
      fact("Coordinate uncertainty", c.coordinate_error_arcsec === undefined ? "" : num(c.coordinate_error_arcsec, 2) + " arcsec") +
      fact("First detection", c.first_detection_time ? absolute(c.first_detection_time) : "") +
      fact("Host", c.host_name) +
      fact("Host redshift", num(c.host_redshift, 5)) +
      fact("Transient redshift", num(c.redshift, 5)) +
      fact("Distance", c.distance_mpc === undefined ? "" : num(c.distance_mpc, 2) + " Mpc") +
      fact("Last CTAS update", c.updated_at ? absolute(c.updated_at) : "") +
      '</dl>';

    var classifications = detailList("Public classifications", follow.classifications, function (row) {
      var probability = row.probability === undefined ? "" : " · " + num(100 * row.probability, 1) + "%";
      return '<li><strong>' + esc(row.classification || "Unclassified") + '</strong>' +
        (row.subtype ? ' · ' + esc(row.subtype) : '') + probability +
        '<span>' + esc(row.provider || "") + (row.method ? ' · ' + esc(row.method) : '') +
        (row.asserted_at ? ' · ' + esc(absolute(row.asserted_at)) : '') + '</span>' +
        link(row.citation_url, "Open classification source") + '</li>';
    });
    var observations = detailList("Photometry and observations", follow.observations, function (row) {
      var measurement = row.magnitude !== undefined
        ? num(row.magnitude, 3) + (row.magnitude_error !== undefined ? " ± " + num(row.magnitude_error, 3) : "") + " mag"
        : row.limiting_magnitude !== undefined ? "limit " + num(row.limiting_magnitude, 3) + " mag"
        : row.flux !== undefined ? num(row.flux, 4) + " " + text(row.flux_unit) : "recorded observation";
      return '<li><strong>' + esc(measurement) + '</strong>' +
        '<span>' + esc([row.band, row.instrument || row.telescope, row.provider, row.observed_at ? absolute(row.observed_at) : ""].filter(Boolean).join(" · ")) + '</span>' +
        (row.summary ? '<p>' + esc(row.summary) + '</p>' : '') +
        link(row.source_url, "Open observation source") + '</li>';
    });
    var signals = detailList("Messenger notices", follow.messenger_signals, function (row) {
      return '<li><strong>' + esc([row.messenger, row.alert_type || row.role].filter(Boolean).join(" · ")) + '</strong>' +
        '<span>' + esc([row.instrument, row.provider, row.observed_at ? absolute(row.observed_at) : ""].filter(Boolean).join(" · ")) + '</span>' +
        (row.summary || row.measurement ? '<p>' + esc(row.summary || row.measurement) + '</p>' : '') +
        link(row.source_url, "Open notice") + (row.source_url && row.skymap_url ? " · " : "") +
        link(row.skymap_url, "Open sky map") + '</li>';
    });
    var publications = detailList("Circulars and public follow-up reports", follow.publications, function (row) {
      return '<li><strong>' + esc(row.title || row.publication_type || "Public report") + '</strong>' +
        '<span>' + esc([row.authors_text, row.provider, row.published_at ? absolute(row.published_at) : ""].filter(Boolean).join(" · ")) + '</span>' +
        (row.abstract ? '<p>' + esc(row.abstract) + '</p>' : '') +
        link(row.canonical_url, "Read the full report") + '</li>';
    });
    var catalogueLinks = (c.links || []).filter(function (row) { return row.url; }).map(function (row) {
      return link(row.url, row.label === "TNS" ? "Open TNS record" : row.label);
    }).join(" · ");

    return '<div class="ctas-detail"><div class="ctas-detail__intro"><div><p class="eyebrow">Follow-up record</p>' +
      '<h3>' + esc(c.name) + '</h3><p>' + esc(rationale) + '</p></div>' +
      '<p class="ctas-detail__score"><span>CTAS review score</span><strong>' + esc(num(c.ctas_score, 1) || "—") + '</strong><small>Ordering aid, not a calibrated scientific probability.</small></p></div>' +
      context + (catalogueLinks ? '<p class="ctas-detail__catalogues">' + catalogueLinks + '</p>' : '') +
      '<div class="ctas-detail__grid">' + classifications + signals + observations + publications + '</div></div>';
  }

  // ----------------------------------------------------------------- status
  function renderStatus() {
    var st = state.status || {};
    var pipeline = st.pipeline_status || "unknown";
    var dotClass = pipeline === "ok" ? "dot--ok"
                 : pipeline === "degraded" ? "dot--degraded"
                 : pipeline === "idle" ? "" : "dot--error";
    var label = pipeline === "ok" ? "Operating normally"
              : pipeline === "degraded" ? "Degraded, showing last good data"
              : pipeline === "idle" ? "Idle, no candidates" : "Unknown";

    el.status.innerHTML =
      cell("Pipeline status",
           '<span class="dot ' + dotClass + '"></span>' + esc(label)) +
      cell("Last successful update",
           esc(absolute(st.last_successful_update)),
           esc(relative(st.last_successful_update))) +
      cell("Update cadence",
           esc(st.cadence || "approximately every 30 minutes"),
           "Scheduled through GitHub Actions; exact minute not guaranteed.") +
      cell("Public candidates",
           esc(String(st.candidate_count === undefined ? state.candidates.length : st.candidate_count)),
           st.runtime_seconds ? "Run took " + esc(String(st.runtime_seconds)) + "s" : "");

    if (Array.isArray(st.sources) && st.sources.length && el.sources) {
      el.sources.innerHTML = st.sources.map(function (s) {
        var d = s.state === "ok" ? "dot--ok"
              : (s.state === "disabled" ? "" : (s.state === "error" ? "dot--error" : "dot--degraded"));
        return '<li><span class="dot ' + d + '"></span>' +
               '<span class="ctas-sources__name">' + esc(s.label || s.source) + "</span>" +
               '<span class="pill">' + esc(s.state) + "</span>" +
               '<span class="ctas-sources__detail">' + esc(s.detail || "") + "</span></li>";
      }).join("");
    }
  }

  function cell(label, value, sub) {
    return '<div class="ctas-status__cell">' +
           '<p class="ctas-status__label">' + esc(label) + "</p>" +
           '<p class="ctas-status__value">' + value + "</p>" +
           (sub ? '<p class="ctas-status__sub">' + sub + "</p>" : "") +
           "</div>";
  }

  // ------------------------------------------------------------- filtering
  function visible() {
    var q = state.q.trim().toLowerCase();
    return state.candidates.filter(function (c) {
      if (state.cls && text(c.classification) !== state.cls) return false;
      if (state.msg && text(c.primary_messenger) !== state.msg) return false;
      if (state.stat && text(c.status) !== state.stat) return false;
      if (!q) return true;
      return (text(c.name) + " " + text(c.classification) + " " +
              text(c.event_type) + " " + text(c.primary_messenger)).toLowerCase().indexOf(q) !== -1;
    }).sort(function (a, b) {
      var k = state.sortKey, va = a[k], vb = b[k];
      if (va === undefined || va === null) return 1;
      if (vb === undefined || vb === null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * state.sortDir;
      return String(va).localeCompare(String(vb)) * state.sortDir;
    });
  }

  var COLUMNS = [
    { key: "name",            label: "Object" },
    { key: "classification",  label: "Classification" },
    { key: "ctas_score",      label: "CTAS score", num: true },
    { key: "ra_deg",          label: "Position (RA / Dec)" },
    { key: "discovery_time",  label: "Discovered" },
    { key: "discovery_magnitude", label: "Mag", num: true },
    { key: "redshift",        label: "z", num: true },
    { key: "discovery_survey", label: "Survey" },
    { key: "links",           label: "Catalogues", nosort: true }
  ];


  // ---------------------------------------------------------------- render
  function renderTable() {
    var rows = visible();
    var shownN = Math.min(state.shown, rows.length);
    el.count.textContent = rows.length === state.candidates.length
      ? "showing " + shownN + " of " + rows.length + " candidates"
      : "showing " + shownN + " of " + rows.length + " matching (" + state.candidates.length + " total)";

    if (!state.candidates.length) {
      el.results.innerHTML =
        '<div class="ctas-empty"><h3>No current candidates</h3>' +
        "<p>No CTAS candidates currently meet the public selection criteria. " +
        "The system will check again during the next scheduled update.</p></div>";
      return;
    }
    if (!rows.length) {
      el.results.innerHTML =
        '<div class="ctas-empty"><h3>Nothing matches those filters</h3>' +
        "<p>Try clearing the search box or resetting the filters.</p></div>";
      return;
    }

    var head = COLUMNS.map(function (col) {
      var sorted = state.sortKey === col.key
        ? (state.sortDir === 1 ? "ascending" : "descending") : "none";
      var inner = col.nosort ? esc(col.label)
        : '<button type="button" data-sort="' + col.key + '">' + esc(col.label) + "</button>";
      return '<th scope="col" aria-sort="' + sorted + '">' + inner + "</th>";
    }).join("");

    var window_ = rows.slice(0, state.shown);
    var body = window_.map(function (c, index) {
      var detailId = "ctas-detail-" + index;
      var links = (c.links || []).map(function (l) {
        return l.url
          ? '<a href="' + esc(l.url) + '" target="_blank" rel="noopener">' + esc(l.label) + "</a>"
          : '<span class="ctas-sources__detail">' + esc(l.designation) + "</span>";
      }).join("");
      return '<tr class="ctas-candidate-row">' +
        '<td class="name"><button type="button" class="ctas-candidate" data-detail="' + detailId + '" aria-expanded="false" aria-controls="' + detailId + '"><span>' + esc(c.name) + '</span><small>Show follow-up</small></button></td>' +
        "<td>" + (c.classification
                  ? '<span class="pill">' + esc(c.classification) + "</span>"
                  : '<span class="ctas-sources__detail">unclassified</span>') + "</td>" +
        '<td class="num">' + esc(num(c.ctas_score, 1)) + "</td>" +
        '<td class="num">' + esc(sexagesimal(c.ra_deg, c.dec_deg)) + "</td>" +
        "<td>" + esc(c.discovery_time ? absolute(c.discovery_time) : "") + "</td>" +
        '<td class="num">' + esc(num(c.discovery_magnitude, 2)) + "</td>" +
        '<td class="num">' + esc(num(c.redshift, 4)) + "</td>" +
        "<td>" + esc(text(c.discovery_survey)) + "</td>" +
        '<td class="links">' + links + "</td>" +
      '</tr><tr class="ctas-detail-row" id="' + detailId + '" hidden><td colspan="9">' + renderDetails(c) + '</td></tr>';
    }).join("");

    el.results.innerHTML =
      '<div class="ctas-table-wrap"><table class="ctas-table">' +
      "<caption>Public CTAS candidates, highest score first. Positions are J2000.</caption>" +
      "<thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      (rows.length > state.shown
        ? '<p style="margin-top:1rem;text-align:center;">' +
          '<button type="button" class="btn btn--small" id="ctas-more">Show ' +
          Math.min(PAGE, rows.length - state.shown) + ' more</button></p>'
        : "");

    var more = document.getElementById("ctas-more");
    if (more) more.addEventListener("click", function () { state.shown += PAGE; renderTable(); });

    Array.prototype.forEach.call(el.results.querySelectorAll("[data-sort]"), function (btn) {
      btn.addEventListener("click", function () {
        var k = btn.getAttribute("data-sort");
        if (state.sortKey === k) { state.sortDir = -state.sortDir; }
        else { state.sortKey = k; state.sortDir = (k === "name" ? 1 : -1); }
        renderTable();
      });
    });
    Array.prototype.forEach.call(el.results.querySelectorAll("[data-detail]"), function (btn) {
      btn.addEventListener("click", function () {
        var detail = document.getElementById(btn.getAttribute("data-detail"));
        var opening = detail.hidden;
        detail.hidden = !opening;
        btn.setAttribute("aria-expanded", opening ? "true" : "false");
        var label = btn.querySelector("small");
        if (label) label.textContent = opening ? "Hide follow-up" : "Show follow-up";
      });
    });
  }

  function populateFilters() {
    function fill(select, key, blank) {
      if (!select) return;
      var seen = {};
      state.candidates.forEach(function (c) { if (c[key]) seen[c[key]] = true; });
      var keys = Object.keys(seen).sort();
      select.innerHTML = '<option value="">' + blank + "</option>" +
        keys.map(function (k) { return '<option value="' + esc(k) + '">' + esc(k) + "</option>"; }).join("");
      select.disabled = keys.length === 0;
    }
    fill(el.cls, "classification", "All classifications");
    fill(el.msg, "primary_messenger", "All messengers");
    fill(el.stat, "status", "All statuses");
  }

  function showError(message) {
    el.results.innerHTML =
      '<div class="ctas-empty ctas-empty--error"><h3>CTAS data could not be loaded</h3>' +
      "<p>" + esc(message) + "</p></div>";
    el.count.textContent = "";
  }

  // ------------------------------------------------------------------ boot
  function getJSON(name) {
    return fetch(DATA_DIR + name, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(name + " returned HTTP " + r.status);
      return r.json();
    });
  }

  el.results.innerHTML = '<p class="ctas-loading">Loading current CTAS data…</p>';

  Promise.all([
    getJSON("candidates.json"),
    getJSON("status.json").catch(function () { return null; })
  ]).then(function (res) {
    var data = res[0] || {};
    state.candidates = Array.isArray(data.candidates) ? data.candidates : [];
    state.status = res[1] || {
      pipeline_status: data.degraded ? "degraded" : "ok",
      last_successful_update: data.generated_at,
      candidate_count: state.candidates.length,
      cadence: data.cadence
    };
    if (el.toolbar) el.toolbar.hidden = state.candidates.length === 0;
    renderStatus();
    populateFilters();
    renderTable();
  }).catch(function (err) {
    if (el.toolbar) el.toolbar.hidden = true;
    renderStatus();
    showError(err && err.message
      ? err.message + ". The scheduled update may not have run yet."
      : "Unknown error.");
  });

  if (el.q)   el.q.addEventListener("input",  function () { state.q = el.q.value; state.shown = PAGE; renderTable(); });
  if (el.cls) el.cls.addEventListener("change", function () { state.cls = el.cls.value; state.shown = PAGE; renderTable(); });
  if (el.msg) el.msg.addEventListener("change", function () { state.msg = el.msg.value; state.shown = PAGE; renderTable(); });
  if (el.stat) el.stat.addEventListener("change", function () { state.stat = el.stat.value; state.shown = PAGE; renderTable(); });
})();
