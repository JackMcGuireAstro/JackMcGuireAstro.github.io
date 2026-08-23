/* ==========================================================================
   CTAS public interface.

   Reads the sanitized candidate, status, and source-universe artifacts
   produced by the local Python publisher and renders them as a searchable,
   sortable research catalog. There is no public backend.
   ========================================================================== */
(function () {
  "use strict";

  var DATA_DIR = "ctas/data/";
  var PUBLIC_LINK_HOSTS = {
    "api.fink-portal.org": 1, "api.ztf.fink-portal.org": 1, "apps.aavso.org": 1,
    "archive.eso.org": 1, "archive.stsci.edu": 1, "asas-sn.osu.edu": 1,
    "blackgem.org": 1, "cgbm.calet.jp": 1, "chime-experiment.ca": 1,
    "doc.lsst.fink-broker.org": 1, "docs.aavso.org": 1, "ep.bao.ac.cn": 1,
    "fallingstar-data.com": 1, "gcn.gsfc.nasa.gov": 1, "gcn.nasa.gov": 1,
    "github.com": 1, "goto-observatory.org": 1, "heasarc.gsfc.nasa.gov": 1,
    "irsa.ipac.caltech.edu": 1, "lasair.readthedocs.io": 1, "mast.stsci.edu": 1,
    "maxi.riken.jp": 1, "ned.ipac.caltech.edu": 1, "observ.pereplet.ru": 1,
    "outerspace.stsci.edu": 1, "roc-2.icecube.wisc.edu": 1, "roc.icecube.wisc.edu": 1,
    "rubinobservatory.org": 1, "simbad.cds.unistra.fr": 1, "ui.adsabs.harvard.edu": 1,
    "vizier.cds.unistra.fr": 1, "wfst.bao.ac.cn": 1, "www.aavso.org": 1,
    "www.cosmos.esa.int": 1, "www.wis-tns.org": 1, "www.wiserep.org": 1,
    "yse.ucsc.edu": 1, "www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca": 1,
    "archive.gemini.edu": 1, "www.ivoa.net": 1, "tom-toolkit.readthedocs.io": 1,
    "ampelproject.github.io": 1, "antares.noirlab.edu": 1, "babamul.caltech.edu": 1,
    "pitt-broker.readthedocs.io": 1, "ztf.uw.edu": 1
  };

  // 2,000+ rows is too many DOM nodes to paint at once; render a window and
  // let the reader ask for more. Search and sort always span the full set.
  var PAGE = 150;
  var state = { candidates: [], status: null, snapshot: null, sourceUniverse: null,
               sortKey: "ctas_score", sortDir: -1, preset: "all",
               q: "", cls: "", msg: "", stat: "", shown: PAGE,
               skyDays: 7, skyPoints: [], skySelected: null, skyKeyboardIndex: -1 };

  var el = {
    status:   document.getElementById("ctas-status"),
    metrics:  document.getElementById("ctas-metrics"),
    messengerStats: document.getElementById("ctas-messenger-stats"),
    priorityStats: document.getElementById("ctas-priority-stats"),
    stream:   document.getElementById("ctas-stream"),
    sources:  document.getElementById("ctas-sources"),
    providerStats: document.getElementById("ctas-provider-stats"),
    surveys:  document.getElementById("ctas-surveys"),
    sourceUniverseSummary: document.getElementById("ctas-source-universe-summary"),
    sourceUniverseGroups: document.getElementById("ctas-source-universe-groups"),
    toolbar:  document.getElementById("ctas-toolbar"),
    results:  document.getElementById("ctas-results"),
    count:    document.getElementById("ctas-count"),
    q:        document.getElementById("ctas-q"),
    cls:      document.getElementById("ctas-class"),
    msg:      document.getElementById("ctas-messenger"),
    stat:     document.getElementById("ctas-statusfilter"),
    skyStage: document.getElementById("ctas-sky-stage"),
    sky:      document.getElementById("ctas-sky-canvas"),
    skyTip:   document.getElementById("ctas-sky-tooltip"),
    skyCount: document.getElementById("ctas-sky-count"),
    skyDetail: document.getElementById("ctas-sky-detail")
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

  function publicUrl(url) {
    if (!url) return null;
    try {
      var parsed = new URL(url, window.location.href);
      if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port) return null;
      return PUBLIC_LINK_HOSTS[parsed.hostname.toLowerCase()] ? parsed.href : null;
    } catch (_) {
      return null;
    }
  }

  function link(url, label) {
    var safe = publicUrl(url);
    return safe
      ? '<a href="' + esc(safe) + '" target="_blank" rel="noopener">' + esc(label) + '</a>'
      : '';
  }

  function catalogueUrl(row) {
    if (!row || !row.url) return null;
    try {
      var parsed = new URL(row.url, window.location.href);
      if (parsed.protocol !== "https:") return null;
      if (row.label === "TNS") {
        if (parsed.hostname !== "www.wis-tns.org" || parsed.port || parsed.search || parsed.hash) return null;
        if (!/^\/object\/\d{4}[a-z]+$/i.test(parsed.pathname)) return null;
      }
      return parsed.href;
    } catch (_) {
      return null;
    }
  }

  function catalogueLink(row) {
    var url = catalogueUrl(row);
    if (!url) return '<span class="ctas-sources__detail">' + esc(row && row.designation) + '</span>';
    return link(url, row.label === "TNS" ? "TNS" : row.label);
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

  function renderScoreFactors(c) {
    var labels = {
      recency_points: "Recency points",
      brightness_points: "Brightness points",
      classification_gap_points: "Unclassified points",
      classification_conflict_points: "Classification conflict",
      spectroscopy_gap_points: "No-spectrum points",
      coverage_reduction: "Observation-coverage reduction",
      observation_gap_points: "Observation-gap points",
      multimessenger_points: "Multimessenger points",
      status: "Status override"
    };
    var factors = c.score_factors || {};
    var rows = Object.keys(labels).filter(function (key) {
      return factors[key] !== undefined && factors[key] !== null && factors[key] !== "";
    }).map(function (key) {
      var value = factors[key];
      if (key !== "status" && Number.isFinite(Number(value))) value = Number(value).toFixed(2);
      return '<div><dt>' + esc(labels[key]) + '</dt><dd>' + esc(value) + '</dd></div>';
    }).join("");
    if (!rows) return "";
    return '<section class="ctas-score-factors"><h4>Why this score?</h4><dl>' + rows + '</dl></section>';
  }

  function renderCompleteness(c) {
    var complete = c.record_completeness || {};
    var components = Array.isArray(complete.components) ? complete.components : [];
    var items = components.map(function (row) {
      var stateLabel = row.state === "present" ? "present" : row.state === "not-applicable" ? "not applicable" : row.state === "not-assessed" ? "not assessed" : "missing";
      return '<li class="is-' + esc(row.state) + '"><span>' + esc(row.label) + '</span><strong>' + esc(stateLabel) + '</strong></li>';
    }).join("");
    if (!complete.label) return "";
    return '<details class="ctas-completeness"><summary><span><strong>' + esc(complete.label) + '</strong>' +
      '<small>' + esc(complete.present) + ' of ' + esc(complete.applicable) + ' applicable public-record components present</small></span></summary>' +
      '<p>Record completeness is separate from CTAS follow-up priority and does not measure scientific importance, classification confidence or discovery probability.</p>' +
      '<ul>' + items + '</ul></details>';
  }

  function renderSourceCoverage(c) {
    var rows = Array.isArray(c.source_coverage) ? c.source_coverage : [];
    if (!rows.length) {
      return '<details class="ctas-source-coverage"><summary>Source-by-source coverage</summary>' +
        '<p>No bounded target-specific source searches are recorded for this candidate. This means not searched, not no-match.</p></details>';
    }
    return '<details class="ctas-source-coverage"><summary>Source-by-source coverage <small>' + rows.length + ' recorded evaluations</small></summary>' +
      '<p>These are recorded query dispositions. A provider failure, unavailable source or unperformed search is never reported as no-match.</p><ul>' +
      rows.map(function (row) {
        var sought = Array.isArray(row.data_types_sought) ? row.data_types_sought.slice(0, 4).join(", ") : "";
        var retained = Number(row.retained_record_count || 0);
        var retainedTypes = row.retained_record_types || {};
        var retainedSummary = Object.keys(retainedTypes).map(function (key) {
          return Number(retainedTypes[key]).toLocaleString() + " " + key.replace(/_/g, " ");
        }).join(" · ");
        return '<li><div><strong>' + esc(row.source_name || row.source_id) + '</strong><span class="pill">' + esc((row.disposition || "unknown").replace(/-/g, " ")) + '</span></div>' +
          (sought ? '<p>Sought: ' + esc(sought) + '</p>' : '') +
          '<small>' + esc(row.checked_at ? absolute(row.checked_at) : "No explicit query clock; ingested public record") +
          (row.reason_code ? ' · ' + esc(row.reason_code.replace(/_/g, " ")) : '') + '</small>' +
          '<p><strong>' + esc(retained.toLocaleString()) + '</strong> retained public record' + (retained === 1 ? '' : 's') +
          (retainedSummary ? ' · ' + esc(retainedSummary) : '') + '</p>' +
          '<p class="ctas-source-coverage__links">' +
          (row.object_specific_result_url ? link(row.object_specific_result_url, "Open object-specific source") : '') +
          (row.query_evidence_url ? link(row.query_evidence_url, "Query endpoint or evidence") : '') +
          (row.documentation_url ? link(row.documentation_url, "Source documentation") : '') + '</p></li>';
      }).join("") + '</ul></details>';
  }

  function renderTimeline(c) {
    var follow = c.follow_up || {}, entries = [], order = 0;
    function add(rows, kind, scientificKey, title, summary, url, assertionKind) {
      (rows || []).forEach(function (row) {
        entries.push({kind: typeof kind === "function" ? kind(row) : kind,
          scientificTime: scientificKey ? row[scientificKey] : null,
          publicationTime: row.source_published_at || row.published_at || null,
          receiptTime: row.ctas_received_at || null,
          title: title(row), summary: summary(row), provider: row.provider,
          facility: row.facility || row.observatory || row.telescope || row.instrument,
          assertionKind: assertionKind || "provider assertion", url: url(row), order: order++});
      });
    }
    add((follow.classifications || []).concat(follow.classification_history || []), function (r) {
        return r.retracted ? "Classification retraction" : r.superseded ? "Classification revision" : "Classification";
      }, "asserted_at",
      function (r) { return r.classification || "Unclassified"; },
      function (r) { return [r.subtype, r.method, r.probability === undefined ? "" : num(100 * r.probability, 1) + "%"].filter(Boolean).join(" · "); },
      function (r) { return r.citation_url; });
    add(follow.observations, "Observation", "observed_at",
      function (r) { return r.magnitude !== undefined ? num(r.magnitude, 3) + " mag" : r.limiting_magnitude !== undefined ? "Limit " + num(r.limiting_magnitude, 3) + " mag" : "Recorded observation"; },
      function (r) { return [r.band, r.instrument || r.telescope, r.summary].filter(Boolean).join(" · "); },
      function (r) { return r.source_url; });
    add(follow.spectra, "Spectrum", "observed_at", function (r) { return r.file_name || r.provider_spectrum_id || "Public spectrum"; },
      function (r) { return [r.telescope, r.instrument, r.calibration_state].filter(Boolean).join(" · "); },
      function (r) { return r.public_download_url || r.source_url; });
    add(follow.messenger_signals, "Messenger notice", "observed_at", function (r) { return [r.messenger, r.alert_type || r.role].filter(Boolean).join(" · ") || "Messenger notice"; },
      function (r) { return r.summary || r.measurement || "Provider notice"; }, function (r) { return r.source_url; });
    add(follow.publications, "Public report", null, function (r) { return r.title || r.publication_type || "Public report"; },
      function (r) { return r.abstract || r.authors_text || "Provider publication"; }, function (r) { return r.canonical_url; });
    add(follow.host_context, "Host context", null, function (r) { return r.canonical_name || r.queried_name || "Public host context"; },
      function (r) { return [r.physical_type, r.morphology, r.overview_note].filter(Boolean).join(" · "); }, function (r) { return r.source_url; });
    add(follow.catalog_counterparts, "Positional catalog candidate", null, function (r) { return r.catalog_record_id || r.catalog_description || "Catalog candidate"; },
      function (r) { return [r.catalog, r.separation_arcsec === undefined ? "" : num(r.separation_arcsec, 2) + " arcsec separation", r.description].filter(Boolean).join(" · "); }, function (r) { return r.source_url; });
    add(follow.archive_products, "Released archive product", "observed_at", function (r) { return r.product_filename || r.provider_product_id || "Archive product"; },
      function (r) { return [r.mission, r.instrument, r.description].filter(Boolean).join(" · "); }, function (r) { return r.public_download_url || r.source_url; });
    if (c.discovery_time) entries.push({kind: "Discovery record", scientificTime: c.discovery_time, title: c.name,
      summary: [c.discovery_survey, c.discovery_magnitude === undefined ? "" : num(c.discovery_magnitude, 2) + " mag"].filter(Boolean).join(" · "),
      provider: c.discovery_survey, assertionKind: "provider assertion", url: null, order: order++});
    if (c.updated_at) entries.push({kind: "CTAS catalog update", receiptTime: c.updated_at,
      title: "Public catalog record updated", summary: "CTAS update clock; not a provider scientific or publication time.",
      provider: "CTAS", assertionKind: "CTAS-derived summary", url: null, order: order++});
    entries.sort(function (a, b) {
      var at = parseDate(a.scientificTime || a.publicationTime || a.receiptTime);
      var bt = parseDate(b.scientificTime || b.publicationTime || b.receiptTime);
      if (!at && !bt) return a.order - b.order;
      if (!at) return 1;
      if (!bt) return -1;
      return bt.getTime() - at.getTime() || a.order - b.order;
    });
    if (!entries.length) return '<details class="ctas-timeline"><summary>Scientific timeline</summary><p>No time-stamped public evidence is retained.</p></details>';
    return '<details class="ctas-timeline"><summary>Unified scientific timeline <small>' + entries.length + ' entries</small></summary>' +
      '<p>Source scientific time is shown when supplied. Provider publication and CTAS update clocks remain separate and missing times remain explicit.</p><ol>' +
      entries.map(function (row) {
        var clocks = [
          row.scientificTime ? '<span><b>Scientific:</b> ' + esc(absolute(row.scientificTime)) + '</span>' : '',
          row.publicationTime ? '<span><b>Published:</b> ' + esc(absolute(row.publicationTime)) + '</span>' : '',
          row.receiptTime ? '<span><b>CTAS receipt/update:</b> ' + esc(absolute(row.receiptTime)) + '</span>' : ''
        ].filter(Boolean).join("");
        return '<li><div class="ctas-timeline__clocks">' + (clocks || '<span>Time not recorded</span>') + '</div><div><span class="pill">' + esc(row.kind) + '</span><strong>' + esc(row.title) + '</strong>' +
          '<small>' + esc([row.provider || "Provider not recorded", row.facility, row.assertionKind].filter(Boolean).join(" · ")) + '</small>' +
          (row.summary ? '<p>' + esc(row.summary) + '</p>' : '') + (row.url ? link(row.url, "Open source") : '') + '</div></li>';
      }).join("") + '</ol></details>';
  }

  function renderDetails(c) {
    var follow = c.follow_up || {};
    var counts = [
      ["classification", follow.classifications],
      ["classification revision", follow.classification_history],
      ["observation", follow.observations],
      ["spectrum", follow.spectra],
      ["messenger notice", follow.messenger_signals],
      ["public report", follow.publications],
      ["host-context record", follow.host_context],
      ["positional catalog candidate", follow.catalog_counterparts],
      ["archive product", follow.archive_products]
    ].filter(function (item) { return Array.isArray(item[1]) && item[1].length; })
      .map(function (item) {
        return item[1].length + " " + item[0] + (item[1].length === 1 ? "" : "s");
      });
    var rationale = counts.length
      ? "CTAS has retained " + counts.join(", ") + " for review."
      : "CTAS has a public event record, but no additional public follow-up rows are retained yet.";

    var context = '<dl class="ctas-detail__facts">' +
      fact("Event type", c.event_type || "Not recorded") +
      fact("Primary messenger", c.primary_messenger || "Not recorded") +
      fact("Coordinates", sexagesimal(c.ra_deg, c.dec_deg) || "Coordinates unavailable") +
      fact("Coordinate uncertainty", c.coordinate_error_arcsec === undefined ? "" : num(c.coordinate_error_arcsec, 2) + " arcsec") +
      fact("Discovery", [c.discovery_time ? absolute(c.discovery_time) : "time unavailable", c.discovery_survey || "survey unavailable"].join(" · ")) +
      fact("Discovery magnitude", c.discovery_magnitude === undefined ? "Not recorded" : num(c.discovery_magnitude, 3) + " mag") +
      fact("Current classification", c.classification || "Unclassified") +
      fact("Classification confidence", c.classification_probability === undefined ? "Not reported" : num(100 * c.classification_probability, 1) + "%") +
      fact("First detection", c.first_detection_time ? absolute(c.first_detection_time) : "") +
      fact("Host", c.host_name) +
      fact("Host redshift", num(c.host_redshift, 5)) +
      fact("Transient redshift", num(c.redshift, 5)) +
      fact("Distance", c.distance_mpc === undefined ? "" : num(c.distance_mpc, 2) + " Mpc") +
      fact("Most recent meaningful change", c.most_recent_meaningful_change ? [c.most_recent_meaningful_change.evidence_type, c.most_recent_meaningful_change.provider, absolute(c.most_recent_meaningful_change.scientific_time || c.most_recent_meaningful_change.provider_publication_time || c.most_recent_meaningful_change.ctas_receipt_time)].filter(Boolean).join(" · ") : "Not recorded") +
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
    var spectra = detailList("Public spectra", follow.spectra, function (row) {
      var coverage = [row.telescope, row.instrument, row.calibration_state,
                      row.observed_at ? absolute(row.observed_at) : ""].filter(Boolean).join(" · ");
      var url = row.public_download_url || row.source_url;
      return '<li><strong>' + esc(row.file_name || row.provider_spectrum_id || "Spectrum") + '</strong>' +
        '<span>' + esc(coverage) + '</span>' +
        (row.resolution !== undefined ? '<p>Reported resolution: ' + esc(num(row.resolution, 1)) + '</p>' : '') +
        link(url, "Open spectrum source") + '</li>';
    });
    var publications = detailList("Circulars and public follow-up reports", follow.publications, function (row) {
      return '<li><strong>' + esc(row.title || row.publication_type || "Public report") + '</strong>' +
        '<span>' + esc([row.authors_text, row.provider, row.published_at ? absolute(row.published_at) : ""].filter(Boolean).join(" · ")) + '</span>' +
        (row.abstract ? '<p>' + esc(row.abstract) + '</p>' : '') +
        link(row.canonical_url, "Read the full report") + '</li>';
    });
    var classificationHistory = detailList("Classification revisions and retractions", follow.classification_history, function (row) {
      return '<li><strong>' + esc(row.retracted ? "Retracted: " : "Revised: ") + esc(row.classification || "Unclassified") + '</strong>' +
        '<span>' + esc([row.provider, row.asserted_at ? absolute(row.asserted_at) : ""].filter(Boolean).join(" · ")) + '</span>' +
        link(row.citation_url, "Open classification source") + '</li>';
    });
    var hostContext = detailList("Host and environmental context", follow.host_context, function (row) {
      return '<li><strong>' + esc(row.canonical_name || row.queried_name || "Host context") + '</strong>' +
        '<span>' + esc([row.provider, row.physical_type, row.morphology, row.transient_offset_arcsec === undefined ? "" : num(row.transient_offset_arcsec, 2) + " arcsec offset"].filter(Boolean).join(" · ")) + '</span>' +
        (row.overview_note ? '<p>' + esc(row.overview_note) + '</p>' : '') + link(row.source_url, "Open host source") + '</li>';
    });
    var counterpartContext = detailList("Positional catalog candidates", follow.catalog_counterparts, function (row) {
      return '<li><strong>' + esc(row.catalog_record_id || row.catalog_description || "Catalog candidate") + '</strong>' +
        '<span>' + esc([row.catalog, row.provider, row.separation_arcsec === undefined ? "" : num(row.separation_arcsec, 2) + " arcsec separation"].filter(Boolean).join(" · ")) + '</span>' +
        (row.description ? '<p>' + esc(row.description) + '</p>' : '') + link(row.source_url, "Open catalog source") + '</li>';
    });
    var archiveProducts = detailList("Released archive products", follow.archive_products, function (row) {
      return '<li><strong>' + esc(row.product_filename || row.provider_product_id || "Archive product") + '</strong>' +
        '<span>' + esc([row.provider, row.mission, row.instrument].filter(Boolean).join(" · ")) + '</span>' +
        (row.description ? '<p>' + esc(row.description) + '</p>' : '') + link(row.public_download_url || row.source_url, "Open archive product") + '</li>';
    });
    var catalogueLinks = (c.links || []).filter(function (row) { return catalogueUrl(row); }).map(function (row) {
      return link(catalogueUrl(row), row.label === "TNS" ? "Open TNS record" : row.label);
    }).join(" · ");
    var completeness = c.record_completeness || {}, components = completeness.components || [];
    var missing = components.filter(function (row) { return row.state === "missing" || row.state === "not-assessed"; }).map(function (row) { return row.label.toLowerCase(); });
    var summary = c.candidate_summary || {};
    var known = summary.known || (counts.length ? counts.join(", ") : "an event identity and CTAS priority record");
    var designations = (c.designations || []).map(function (row) { return esc(row.source || row.source_key) + ": " + esc(row.designation); }).join(" · ");

    return '<div class="ctas-detail"><div class="ctas-detail__intro"><div><p class="eyebrow">Follow-up record</p>' +
      '<h3>' + esc(c.name) + '</h3><p>' + esc(summary.why_in_ctas || rationale) + '</p>' +
      (designations ? '<p class="ctas-designations"><strong>Source designations:</strong> ' + designations + '</p>' : '') + '</div>' +
      '<p class="ctas-detail__score"><span>CTAS follow-up priority</span><strong>' + esc(num(c.ctas_score, 1) || "—") + '</strong><small>Operational ordering aid, not scientific importance or probability.</small></p></div>' +
      '<div class="ctas-known-missing"><p><strong>Known:</strong> ' + esc(known) + '.</p><p><strong>Still missing or not assessed:</strong> ' + esc(summary.missing || (missing.length ? missing.join(", ") : "no applicable public-record component in this model")) + '</p><p><strong>Claim boundary:</strong> ' + esc(summary.non_claim || "Inclusion does not establish discovery, classification, counterpart, or host identity.") + '</p></div>' +
      context + (catalogueLinks ? '<p class="ctas-detail__catalogues">' + catalogueLinks + '</p>' : '') +
      (c.score_explanation ? '<p class="ctas-score-explanation"><strong>Why this score:</strong> ' + esc(c.score_explanation) + '</p>' : '') +
      renderCompleteness(c) + renderScoreFactors(c) + renderSourceCoverage(c) + renderTimeline(c) +
      '<details class="ctas-evidence-lists"><summary>Evidence grouped by type</summary><div class="ctas-detail__grid">' + classifications + classificationHistory + signals + observations + spectra + publications + hostContext + counterpartContext + archiveProducts + '</div></details></div>';
  }

  function renderOverview() {
    var snapshot = state.snapshot || {};
    var stats = snapshot.statistics || {};
    if (el.metrics) {
      [
        ["Public candidates", stats.public_candidates],
        ["With follow-up", stats.candidates_with_follow_up],
        ["Observations", stats.observations],
        ["Spectra", stats.spectra],
        ["Messenger notices", stats.messenger_signals],
        ["Classifications", stats.classifications],
        ["Reports & circulars", stats.publications],
        ["Host context", stats.host_context],
        ["Catalog candidates", stats.catalog_counterparts],
        ["Archive products", stats.archive_products]
      ].forEach(function (item) {
        if (item[1] === undefined) return;
        el.metrics.insertAdjacentHTML("beforeend",
          '<div class="ctas-metric"><strong>' + esc(Number(item[1]).toLocaleString()) +
          '</strong><span>' + esc(item[0]) + '</span></div>');
      });
    }

    function bars(target, rows) {
      if (!target || !rows.length) return;
      var max = Math.max.apply(null, rows.map(function (row) { return row[1]; })) || 1;
      target.innerHTML = rows.map(function (row) {
        return '<div class="ctas-bar"><span>' + esc(row[0]) + '</span>' +
          '<i style="--bar:' + esc(String(100 * row[1] / max)) + '%"></i>' +
          '<strong>' + esc(Number(row[1]).toLocaleString()) + '</strong></div>';
      }).join("");
    }
    bars(el.messengerStats, Object.keys(stats.messengers || {}).map(function (key) {
      return [key, stats.messengers[key]];
    }).sort(function (a, b) { return b[1] - a[1]; }));
    var priorityLabels = {
      urgent_75_100: "Urgent · 75–100",
      high_50_74: "High · 50–74",
      routine_25_49: "Routine · 25–49",
      low_0_24: "Low · 0–24"
    };
    bars(el.priorityStats, Object.keys(priorityLabels).map(function (key) {
      return [priorityLabels[key], (stats.priority_bands || {})[key] || 0];
    }));

    if (el.stream) {
      el.stream.innerHTML = (snapshot.recent_stream || []).slice(0, 3).map(function (row, index) {
        var counts = row.follow_up_counts || {};
        var evidence = [
          counts.observations ? counts.observations + " obs" : "",
          counts.spectra ? counts.spectra + " spec" : "",
          counts.messenger_signals ? counts.messenger_signals + " messages" : "",
          counts.publications ? counts.publications + " reports" : ""
        ].filter(Boolean).join(" · ") || "event record only";
        return '<li><span class="ctas-stream__number">0' + (index + 1) + '</span><div>' +
          '<p><strong>' + esc(row.name) + '</strong><span class="pill">' +
          esc(row.classification || "Unclassified") + '</span></p>' +
          '<small>' + esc(relative(row.updated_at || row.discovery_time)) + ' · ' +
          esc(row.primary_messenger || "unknown") + ' · ' + esc(evidence) + '</small></div>' +
          '<strong class="ctas-stream__score">' + esc(num(row.ctas_score, 1) || "—") +
          '<span>priority</span></strong></li>';
      }).join("");
    }

    if (el.surveys) {
      el.surveys.innerHTML = (snapshot.surveys || []).map(function (row) {
        return '<span><strong>' + esc(row.survey) + '</strong> ' +
          esc(Number(row.candidate_count).toLocaleString()) + '</span>';
      }).join("");
    }
    if (el.providerStats) {
      el.providerStats.innerHTML = (snapshot.provider_statistics || []).map(function (row) {
        var keys = Object.keys(row).filter(function (key) { return key !== "provider"; });
        var total = keys.reduce(function (sum, key) { return sum + Number(row[key] || 0); }, 0);
        var parts = keys.filter(function (key) { return row[key]; })
          .map(function (key) { return Number(row[key]).toLocaleString() + " " + key.replace("_", " "); });
        return '<div><strong>' + esc(row.provider) + '</strong><span>' +
          esc(total.toLocaleString()) + ' total</span><small>' + esc(parts.join(" · ")) + '</small></div>';
      }).join("");
    }
  }

  // ----------------------------------------------------------------- status
  function renderStatus() {
    var st = state.status || {};
    var assurance = st.static_catalog_assurance || {};
    var assuranceExpiry = parseDate(assurance.valid_until || st.valid_until);
    var assuranceFresh = assuranceExpiry && assuranceExpiry.getTime() >= Date.now();
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
      cell("Catalog assurance",
           assurance.status === "certified-static-catalog" && assuranceFresh
             ? '<span class="dot dot--ok"></span>Static catalog checks passed'
             : '<span class="dot dot--degraded"></span>' + (assuranceFresh ? "Checks not passing" : "Checks expired or unavailable"),
           "Automated static-catalog assurance; not peer review, scientific truth, classification validation or discovery authority.") +
      cell("Last successful update",
           esc(absolute(st.last_successful_update)),
           esc(relative(st.last_successful_update))) +
      cell("Update cadence",
           esc(st.cadence || "about every 2 minutes"),
           "The public mirror checks for new CTAS data every 2 minutes.");

    if (Array.isArray(st.sources) && st.sources.length && el.sources) {
      var universeByKey = {};
      ((state.sourceUniverse || {}).sources || []).forEach(function (row) { universeByKey[row.source_key] = row; });
      el.sources.innerHTML = st.sources.filter(function (s) {
        var counts = s.record_counts || {};
        return Object.keys(counts).some(function (key) { return Number(counts[key] || 0) > 0; });
      }).map(function (s) {
        var contract = universeByKey[s.source] || {};
        var publicState = contract.operational_state || s.state || "unknown";
        var d = publicState === "active-returning-data" ? "dot--ok"
              : (publicState === "provider-failure" ? "dot--error" : "dot--degraded");
        var recordCounts = s.record_counts || {};
        var retained = Object.keys(recordCounts).map(function (key) {
          return esc(Number(recordCounts[key]).toLocaleString()) + " " + esc(key.replace("_", " "));
        }).join(" · ");
        return '<li><span class="dot ' + d + '"></span><div>' +
               '<span class="ctas-sources__name">' + esc(s.label || s.source) + "</span>" +
               '<span class="pill">' + esc(publicState.replace(/-/g, " ")) + "</span>" +
               '<p class="ctas-sources__detail">' + esc(s.public_scope || s.detail || "") + "</p>" +
               (retained ? '<p class="ctas-sources__counts">' + retained + '</p>' : '') +
               (s.documentation_url ? link(s.documentation_url, "Source documentation") : '') +
               "</div></li>";
      }).join("");
    }
  }

  function renderSourceUniverse() {
    var universe = state.sourceUniverse || {}, rows = Array.isArray(universe.sources) ? universe.sources : [];
    if (!el.sourceUniverseGroups) return;
    if (!rows.length) {
      if (el.sourceUniverseSummary) el.sourceUniverseSummary.textContent = "Source-universe artifact unavailable";
      el.sourceUniverseGroups.innerHTML = '<p class="ctas-empty ctas-empty--error">The maintained source-universe file could not be loaded. Candidate records remain available.</p>';
      return;
    }
    var stateCounts = {};
    rows.forEach(function (row) { stateCounts[row.operational_state] = (stateCounts[row.operational_state] || 0) + 1; });
    if (el.sourceUniverseSummary) {
      el.sourceUniverseSummary.innerHTML = '<strong>' + esc(rows.length) + ' maintained sources</strong>' +
        Object.keys(stateCounts).sort().map(function (key) {
          return '<span><b>' + esc(stateCounts[key]) + '</b> ' + esc(key.replace(/-/g, " ")) + '</span>';
        }).join("");
    }
    var groups = {};
    rows.forEach(function (row) { (groups[row.source_family] = groups[row.source_family] || []).push(row); });
    el.sourceUniverseGroups.innerHTML = Object.keys(groups).sort().map(function (family) {
      return '<details><summary><strong>' + esc(family.replace(/-/g, " ")) + '</strong><small>' + groups[family].length + ' sources</small></summary><ul>' +
        groups[family].map(function (row) {
          var counts = row.public_record_counts || {};
          var represented = Object.keys(counts).map(function (key) { return Number(counts[key]).toLocaleString() + ' ' + key.replace(/_/g, " "); }).join(" · ");
          return '<li><div><strong>' + esc(row.name) + '</strong><span class="pill">' + esc((row.operational_state || "unknown").replace(/-/g, " ")) + '</span></div>' +
            '<small>Connector: ' + esc((row.implementation_state || "unknown").replace(/-/g, " ")) +
            ' · Representation: ' + esc((row.representation_state || "unknown").replace(/-/g, " ")) +
            (Array.isArray(row.represented_through) && row.represented_through.length ? ' through ' + esc(row.represented_through.join(", ")) : '') + '</small>' +
            '<p>' + esc(row.rights_or_public_access_basis || "Public-access basis not recorded") + '</p>' +
            (represented ? '<small>Represented: ' + esc(represented) + '</small>' : '<small>No directly represented public records in this snapshot.</small>') +
            (row.known_limitations ? '<p class="ctas-source-universe__limit">Limitation: ' + esc(row.known_limitations) + '</p>' : '') +
            (row.documentation_url ? link(row.documentation_url, "Documentation") : '') + '</li>';
        }).join("") + '</ul></details>';
    }).join("");
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
      if (window.CTASCatalogModel && !window.CTASCatalogModel.matchesPreset(c, state.preset, Date.now())) return false;
      if (!q) return true;
      var aliases = (c.designations || []).map(function (row) { return row.designation; }).join(" ");
      return (text(c.name) + " " + aliases + " " + text(c.classification) + " " +
              text(c.event_type) + " " + text(c.primary_messenger) + " " + text(c.discovery_survey)).toLowerCase().indexOf(q) !== -1;
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
    { key: "ctas_score",      label: "Follow-up priority", num: true },
    { key: "follow_up_total", label: "Record completeness / evidence", num: true },
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
      return '<th scope="col"' + (col.nosort ? '' : ' aria-sort="' + sorted + '"') + '>' + inner + "</th>";
    }).join("");

    var window_ = rows.slice(0, state.shown);
    var body = window_.map(function (c, index) {
      var detailId = "ctas-detail-" + index;
      var links = (c.links || []).map(catalogueLink).join("");
      var fc = c.follow_up_counts || {};
      var evidence = [
        fc.observations ? fc.observations + " obs" : "",
        fc.spectra ? fc.spectra + " spec" : "",
        fc.messenger_signals ? fc.messenger_signals + " msg" : "",
        fc.publications ? fc.publications + " reports" : "",
        fc.host_context ? fc.host_context + " host" : "",
        fc.catalog_counterparts ? fc.catalog_counterparts + " catalog" : ""
      ].filter(Boolean).join(" · ") || "event only";
      return '<tr class="ctas-candidate-row">' +
        '<td class="name"><button type="button" class="ctas-candidate" data-detail="' + detailId + '" data-row-index="' + index + '" aria-expanded="false" aria-controls="' + detailId + '"><span>' + esc(c.name) + '</span><small>Show follow-up</small></button></td>' +
        "<td>" + (c.classification
                  ? '<span class="pill">' + esc(c.classification) + "</span>"
                  : '<span class="ctas-sources__detail">unclassified</span>') + "</td>" +
        '<td class="num">' + esc(num(c.ctas_score, 1)) + "</td>" +
        '<td class="ctas-evidence-count"><strong>' + esc((c.record_completeness || {}).label || "Not assessed") + '</strong><small>' + esc(evidence) + "</small></td>" +
        '<td class="num">' + esc(sexagesimal(c.ra_deg, c.dec_deg)) + "</td>" +
        "<td>" + esc(c.discovery_time ? absolute(c.discovery_time) : "") + "</td>" +
        '<td class="num">' + esc(num(c.discovery_magnitude, 2)) + "</td>" +
        '<td class="num">' + esc(num(c.redshift, 4)) + "</td>" +
        "<td>" + esc(text(c.discovery_survey)) + "</td>" +
        '<td class="links">' + links + "</td>" +
      '</tr><tr class="ctas-detail-row" id="' + detailId + '" hidden><td colspan="10"></td></tr>';
    }).join("");

    el.results.innerHTML =
      '<div class="ctas-table-wrap"><table class="ctas-table">' +
      "<caption>Public CTAS candidates, highest follow-up priority first. Positions are J2000.</caption>" +
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
        if (opening && !detail.getAttribute("data-rendered")) {
          var candidate = window_[Number(btn.getAttribute("data-row-index"))];
          detail.firstElementChild.innerHTML = renderDetails(candidate);
          detail.setAttribute("data-rendered", "true");
        }
        detail.hidden = !opening;
        btn.setAttribute("aria-expanded", opening ? "true" : "false");
        var label = btn.querySelector("small");
        if (label) label.textContent = opening ? "Hide follow-up" : "Show follow-up";
      });
    });
  }

  // --------------------------------------------------------- celestial sky
  function skyRows() {
    if (window.CTASCatalogModel) {
      return window.CTASCatalogModel.skyCandidates(state.candidates, state.skyDays, Date.now());
    }
    return [];
  }

  function mollweide(ra, dec, width, height) {
    var lon = (180 - Number(ra)) * Math.PI / 180;
    var lat = Number(dec) * Math.PI / 180;
    var theta = lat;
    for (var i = 0; i < 8; i += 1) {
      var denominator = 2 + 2 * Math.cos(2 * theta);
      if (Math.abs(denominator) < 1e-7) break;
      theta -= (2 * theta + Math.sin(2 * theta) - Math.PI * Math.sin(lat)) / denominator;
    }
    var margin = 18;
    var sx = (width - margin * 2) / (4 * Math.SQRT2);
    var sy = (height - margin * 2) / (2 * Math.SQRT2);
    return {
      x: width / 2 + (2 * Math.SQRT2 / Math.PI) * lon * Math.cos(theta) * sx,
      y: height / 2 - Math.SQRT2 * Math.sin(theta) * sy
    };
  }

  function magnitudeColor(value) {
    var mag = Number(value);
    if (!isFinite(mag)) return "#a9b3c7";
    var t = Math.max(0, Math.min(1, (mag - 13) / 10));
    var stops = [[255, 211, 105], [88, 210, 226], [132, 94, 247]];
    var a = t < 0.5 ? stops[0] : stops[1];
    var b = t < 0.5 ? stops[1] : stops[2];
    var u = t < 0.5 ? t * 2 : (t - 0.5) * 2;
    return "rgb(" + a.map(function (v, i) { return Math.round(v + (b[i] - v) * u); }).join(",") + ")";
  }

  function drawCurve(ctx, samples, project) {
    ctx.beginPath();
    samples.forEach(function (sample, index) {
      var p = project(sample);
      if (index === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  }

  function drawSky() {
    if (!el.sky || !el.skyStage) return;
    var cssWidth = Math.max(320, Math.floor(el.skyStage.getBoundingClientRect().width));
    var cssHeight = Math.max(260, Math.min(520, Math.round(cssWidth * 0.5)));
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    el.sky.width = cssWidth * ratio;
    el.sky.height = cssHeight * ratio;
    el.sky.style.height = cssHeight + "px";
    var ctx = el.sky.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    var margin = 18;
    ctx.fillStyle = "#07101d";
    ctx.strokeStyle = "rgba(184, 200, 223, 0.34)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.ellipse(cssWidth / 2, cssHeight / 2, (cssWidth - margin * 2) / 2,
      (cssHeight - margin * 2) / 2, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cssWidth / 2, cssHeight / 2, (cssWidth - margin * 2) / 2,
      (cssHeight - margin * 2) / 2, 0, 0, Math.PI * 2);
    ctx.clip();
    ctx.strokeStyle = "rgba(184, 200, 223, 0.16)";
    [-60, -30, 0, 30, 60].forEach(function (dec) {
      var samples = [];
      for (var ra = 0; ra <= 360; ra += 4) samples.push({ra: ra, dec: dec});
      drawCurve(ctx, samples, function (s) { return mollweide(s.ra, s.dec, cssWidth, cssHeight); });
    });
    for (var meridian = 0; meridian < 360; meridian += 30) {
      var meridianSamples = [];
      for (var d = -89; d <= 89; d += 3) meridianSamples.push({ra: meridian, dec: d});
      drawCurve(ctx, meridianSamples, function (s) { return mollweide(s.ra, s.dec, cssWidth, cssHeight); });
    }
    ctx.restore();

    var rows = skyRows();
    state.skyPoints = rows.map(function (c) {
      var point = mollweide(c.ra_deg, c.dec_deg, cssWidth, cssHeight);
      point.candidate = c;
      return point;
    });
    state.skyPoints.forEach(function (point) {
      var selected = state.skySelected === point.candidate;
      ctx.beginPath();
      ctx.arc(point.x, point.y, selected ? 6.5 : 4.2, 0, Math.PI * 2);
      ctx.fillStyle = magnitudeColor(point.candidate.discovery_magnitude);
      ctx.fill();
      ctx.strokeStyle = selected ? "#ffffff" : "rgba(255,255,255,0.56)";
      ctx.lineWidth = selected ? 2.2 : 0.7;
      ctx.stroke();
    });
    el.skyCount.textContent = rows.length + " candidate" + (rows.length === 1 ? "" : "s") +
      " discovered in the last " + (state.skyDays === 7 ? "week" : "month") + ".";
    el.sky.setAttribute("aria-label", "All-sky map of " + rows.length + " CTAS candidates discovered in the last " +
      (state.skyDays === 7 ? "seven days" : "thirty days") +
      (state.skySelected ? ". Selected " + state.skySelected.name + ". Press Enter to open its public record." : ". Use left and right arrow keys to review plotted candidates."));
  }

  function nearestSkyPoint(event) {
    var rect = el.sky.getBoundingClientRect();
    var x = event.clientX - rect.left, y = event.clientY - rect.top;
    var best = null, bestDistance = 100;
    state.skyPoints.forEach(function (point) {
      var distance = Math.pow(point.x - x, 2) + Math.pow(point.y - y, 2);
      if (distance < bestDistance) { bestDistance = distance; best = point; }
    });
    return best && bestDistance <= 100 ? {point: best, x: x, y: y} : null;
  }

  function showSkyCandidate(candidate) {
    state.skySelected = candidate;
    state.skyKeyboardIndex = state.skyPoints.map(function (point) { return point.candidate; }).indexOf(candidate);
    el.skyDetail.hidden = false;
    el.skyDetail.innerHTML = renderDetails(candidate);
    drawSky();
  }

  function bindSky() {
    if (!el.sky) return;
    Array.prototype.forEach.call(document.querySelectorAll("[data-sky-days]"), function (button) {
      button.addEventListener("click", function () {
        state.skyDays = Number(button.getAttribute("data-sky-days"));
        state.skySelected = null;
        state.skyKeyboardIndex = -1;
        el.skyDetail.hidden = true;
        Array.prototype.forEach.call(document.querySelectorAll("[data-sky-days]"), function (item) {
          var active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
        });
        drawSky();
      });
    });
    el.sky.addEventListener("pointermove", function (event) {
      var hit = nearestSkyPoint(event);
      if (!hit) { el.skyTip.hidden = true; el.sky.style.cursor = "default"; return; }
      var c = hit.point.candidate;
      el.sky.style.cursor = "pointer";
      el.skyTip.hidden = false;
      el.skyTip.style.left = Math.min(hit.x + 14, el.sky.clientWidth - 210) + "px";
      el.skyTip.style.top = Math.max(8, hit.y - 64) + "px";
      el.skyTip.innerHTML = "<strong>" + esc(c.name) + "</strong><span>" +
        esc(c.classification || "Unclassified") + " · mag " + esc(num(c.discovery_magnitude, 2) || "unknown") +
        "</span><span>" + esc(sexagesimal(c.ra_deg, c.dec_deg)) + "</span>";
    });
    el.sky.addEventListener("pointerleave", function () { el.skyTip.hidden = true; });
    el.sky.addEventListener("click", function (event) {
      var hit = nearestSkyPoint(event);
      if (hit) showSkyCandidate(hit.point.candidate);
    });
    el.sky.addEventListener("keydown", function (event) {
      if (!state.skyPoints.length) return;
      var handled = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Enter", " "].indexOf(event.key) !== -1;
      if (!handled) return;
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") {
        if (state.skyKeyboardIndex < 0) state.skyKeyboardIndex = 0;
        showSkyCandidate(state.skyPoints[state.skyKeyboardIndex].candidate);
        return;
      }
      if (event.key === "Home") state.skyKeyboardIndex = 0;
      else if (event.key === "End") state.skyKeyboardIndex = state.skyPoints.length - 1;
      else {
        var direction = (event.key === "ArrowLeft" || event.key === "ArrowUp") ? -1 : 1;
        state.skyKeyboardIndex = (state.skyKeyboardIndex + direction + state.skyPoints.length) % state.skyPoints.length;
      }
      state.skySelected = state.skyPoints[state.skyKeyboardIndex].candidate;
      drawSky();
      el.skyCount.textContent = "Selected " + state.skySelected.name + ", " +
        (state.skySelected.classification || "unclassified") + ", discovery magnitude " +
        (num(state.skySelected.discovery_magnitude, 2) || "unknown") + ". Press Enter to open the complete public record.";
    });
    var resizeTimer;
    window.addEventListener("resize", function () {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(drawSky, 120);
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
    getJSON("status.json").catch(function () { return null; }),
    getJSON("source-universe.json").catch(function () { return null; })
  ]).then(function (res) {
    var data = res[0] || {};
    state.snapshot = data;
    state.candidates = Array.isArray(data.candidates) ? data.candidates : [];
    state.status = res[1] || {
      pipeline_status: data.degraded ? "degraded" : "ok",
      last_successful_update: data.generated_at,
      candidate_count: state.candidates.length,
      cadence: data.cadence
    };
    state.sourceUniverse = res[2];
    if (el.toolbar) el.toolbar.hidden = state.candidates.length === 0;
    renderStatus();
    renderOverview();
    renderSourceUniverse();
    populateFilters();
    drawSky();
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
  Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (button) {
    button.addEventListener("click", function () {
      state.preset = button.getAttribute("data-preset") || "all";
      state.shown = PAGE;
      if (state.preset === "all" || state.preset === "priority") { state.sortKey = "ctas_score"; state.sortDir = -1; }
      if (state.preset === "newest") { state.sortKey = "discovery_time"; state.sortDir = -1; }
      if (state.preset === "updated") { state.sortKey = "updated_at"; state.sortDir = -1; }
      if (state.preset === "classified") { state.sortKey = "latest_classification_at"; state.sortDir = -1; }
      if (state.preset === "retracted") { state.sortKey = "latest_retraction_at"; state.sortDir = -1; }
      if (state.preset === "spectra") { state.sortKey = "latest_spectrum_at"; state.sortDir = -1; }
      if (state.preset === "messenger") { state.sortKey = "latest_messenger_at"; state.sortDir = -1; }
      if (state.preset === "bright") { state.sortKey = "discovery_magnitude"; state.sortDir = 1; }
      Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (item) {
        var active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });
      renderTable();
    });
  });
  bindSky();
})();
