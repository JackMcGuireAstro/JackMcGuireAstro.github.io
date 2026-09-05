(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASCatalogModel = model;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function date(value) {
    if (!value) return null;
    var parsed = new Date(value);
    return isNaN(parsed.getTime()) ? null : parsed;
  }

  function matchesPreset(candidate, preset, now) {
    var current = Number(now === undefined ? Date.now() : now);
    var sevenDays = current - 7 * 86400000;
    var oneDay = current - 86400000;
    var thirtyDays = current - 30 * 86400000;
    var counts = candidate.follow_up_counts || {};
    var completeness = candidate.record_completeness || {};
    var discovered = date(candidate.discovery_time);
    var updated = date(candidate.updated_at);
    var classified = date(candidate.latest_classification_at);
    var retracted = date(candidate.latest_retraction_at);
    var spectrum = date(candidate.latest_spectrum_at);
    var messenger = date(candidate.latest_messenger_at);

    if (!preset || preset === "all" || preset === "priority") return true;
    if (preset === "today") return Boolean(discovered && discovered.getTime() >= oneDay);
    if (preset === "newest") return Boolean(discovered && discovered.getTime() >= thirtyDays);
    if (preset === "updated") return Boolean(updated && updated.getTime() >= sevenDays);
    if (preset === "classified") return Boolean(classified && classified.getTime() >= sevenDays);
    if (preset === "retracted") return String(candidate.status || "").toLowerCase() === "retracted" || Boolean(retracted);
    if (preset === "spectra") return Boolean(spectrum && spectrum.getTime() >= thirtyDays);
    if (preset === "no-spectra") return (candidate.score_applicable_terms || []).indexOf("spectroscopy_gap_points") !== -1 && !Number(counts.spectra || 0);
    if (preset === "messenger") return Boolean(messenger && messenger.getTime() >= sevenDays);
    if (preset === "unclassified") return !candidate.classification || candidate.classification === "Unclassified";
    if (preset === "bright") return candidate.discovery_magnitude !== null && candidate.discovery_magnitude !== undefined && Number(candidate.discovery_magnitude) <= 18;
    if (preset === "multimessenger") return (candidate.score_detected_messengers || []).length >= 2;
    if (preset === "rich") return completeness.label === "Rich public record";
    if (preset === "event-only") return Number(candidate.follow_up_total || 0) === 0;
    if (preset === "needs-follow-up") {
      if (candidate.default_leaderboard_eligible === false) return false;
      return Number(candidate.follow_up_total || 0) === 0 ||
        ((candidate.score_applicable_terms || []).indexOf("spectroscopy_gap_points") !== -1 && !Number(counts.spectra || 0)) ||
        !candidate.classification || candidate.classification === "Unclassified";
    }
    return true;
  }

  function pad2(value) {
    return (Number(value) < 10 ? "0" : "") + Number(value);
  }

  function sexagesimal(raDeg, decDeg) {
    var ra = Number(raDeg), dec = Number(decDeg);
    if (!isFinite(ra) || !isFinite(dec) || ra < 0 || ra >= 360 || dec < -90 || dec > 90) return "";

    var raTenths = Math.round((ra / 15) * 36000);
    var raDay = 24 * 36000;
    raTenths = ((raTenths % raDay) + raDay) % raDay;
    var h = Math.floor(raTenths / 36000);
    var m = Math.floor((raTenths % 36000) / 600);
    var s = (raTenths % 600) / 10;

    var decSeconds = Math.round(Math.abs(dec) * 3600);
    var d = Math.floor(decSeconds / 3600);
    var dm = Math.floor((decSeconds % 3600) / 60);
    var ds = decSeconds % 60;
    var sign = dec < 0 || Object.is(dec, -0) ? "-" : "+";

    return pad2(h) + ":" + pad2(m) + ":" + (s < 10 ? "0" : "") + s.toFixed(1) +
      " " + sign + pad2(d) + ":" + pad2(dm) + ":" + pad2(ds);
  }

  function skyCandidates(candidates, days, now) {
    var current = Number(now === undefined ? Date.now() : now);
    var cutoff = current - Number(days) * 86400000;
    return candidates.filter(function (candidate) {
      var discovered = date(candidate.discovery_time);
      var ra = Number(candidate.ra_deg), dec = Number(candidate.dec_deg);
      return Boolean(discovered && discovered.getTime() >= cutoff && discovered.getTime() <= current &&
        candidate.ra_deg !== null && candidate.ra_deg !== undefined &&
        candidate.dec_deg !== null && candidate.dec_deg !== undefined &&
        isFinite(ra) && isFinite(dec) && ra >= 0 && ra < 360 && dec >= -90 && dec <= 90);
    });
  }

  var FILTER_KEYS = [
    "q", "class", "msg", "status", "survey", "from", "to", "magMin", "magMax",
    "scoreMin", "scoreMax", "spectrum", "conflict", "richness", "preset", "window",
    "coneRa", "coneDec", "coneRadius"
  ];

  function finite(value) {
    return value !== null && value !== undefined && value !== "" && isFinite(Number(value));
  }

  function list(value) {
    if (Array.isArray(value)) return value.map(String).filter(Boolean);
    return String(value || "").split(",").map(function (item) { return item.trim(); }).filter(Boolean);
  }

  function normalizeFilters(input) {
    input = input || {};
    var filters = {
      q: String(input.q || "").trim(),
      class: list(input.class || input.cls),
      msg: list(input.msg), status: list(input.status || input.stat), survey: list(input.survey),
      from: String(input.from || ""), to: String(input.to || ""),
      magMin: finite(input.magMin) ? Number(input.magMin) : null,
      magMax: finite(input.magMax) ? Number(input.magMax) : null,
      scoreMin: finite(input.scoreMin) ? Number(input.scoreMin) : null,
      scoreMax: finite(input.scoreMax) ? Number(input.scoreMax) : null,
      spectrum: ["yes", "no"].indexOf(input.spectrum) !== -1 ? input.spectrum : "",
      conflict: ["yes", "no"].indexOf(input.conflict) !== -1 ? input.conflict : "",
      richness: String(input.richness || ""),
      preset: String(input.preset || "all"),
      window: String(input.window || "all"),
      coneRa: finite(input.coneRa) ? Number(input.coneRa) : null,
      coneDec: finite(input.coneDec) ? Number(input.coneDec) : null,
      coneRadius: finite(input.coneRadius) ? Math.max(0, Number(input.coneRadius)) : null
    };
    if (!date(filters.from)) filters.from = "";
    if (!date(filters.to)) filters.to = "";
    return filters;
  }

  function greatCircleDistanceDeg(ra1, dec1, ra2, dec2) {
    if (![ra1, dec1, ra2, dec2].every(finite)) return null;
    var d2r = Math.PI / 180;
    var firstDec = Number(dec1) * d2r, secondDec = Number(dec2) * d2r;
    var deltaRa = (Number(ra1) - Number(ra2)) * d2r;
    var cosine = Math.sin(firstDec) * Math.sin(secondDec) +
      Math.cos(firstDec) * Math.cos(secondDec) * Math.cos(deltaRa);
    return Math.acos(Math.max(-1, Math.min(1, cosine))) / d2r;
  }

  function latestMeaningful(candidate) {
    var clocks = [candidate.updated_at, candidate.latest_classification_at, candidate.latest_spectrum_at,
      candidate.latest_messenger_at, candidate.latest_retraction_at, candidate.discovery_time]
      .map(date).filter(Boolean).map(function (value) { return value.getTime(); });
    return clocks.length ? Math.max.apply(null, clocks) : 0;
  }

  function matchesFilters(candidate, rawFilters, now) {
    var filters = normalizeFilters(rawFilters);
    function includes(values, value) { return !values.length || values.indexOf(String(value || "")) !== -1; }
    if (!includes(filters.class, candidate.classification)) return false;
    if (!includes(filters.msg, candidate.primary_messenger)) return false;
    if (!includes(filters.status, candidate.status)) return false;
    if (!includes(filters.survey, candidate.discovery_survey)) return false;
    if (!matchesPreset(candidate, filters.preset, now)) return false;
    if (filters.q) {
      var aliases = (candidate.search_aliases || candidate.designations || []).map(function (row) {
        return typeof row === "string" ? row : row.designation;
      }).join(" ");
      var haystack = [candidate.name, aliases, candidate.classification, candidate.event_type,
        candidate.primary_messenger, candidate.discovery_survey, candidate.event_id].join(" ").toLowerCase();
      if (haystack.indexOf(filters.q.toLowerCase()) === -1) return false;
    }
    var discovery = date(candidate.discovery_time);
    if (filters.from && (!discovery || discovery < date(filters.from))) return false;
    if (filters.to) {
      var end = date(filters.to); if (end) end = new Date(end.getTime() + 86400000 - 1);
      if (!discovery || discovery > end) return false;
    }
    if (filters.magMin !== null && (!finite(candidate.discovery_magnitude) || Number(candidate.discovery_magnitude) < filters.magMin)) return false;
    if (filters.magMax !== null && (!finite(candidate.discovery_magnitude) || Number(candidate.discovery_magnitude) > filters.magMax)) return false;
    if (filters.scoreMin !== null && (!finite(candidate.ctas_score) || Number(candidate.ctas_score) < filters.scoreMin)) return false;
    if (filters.scoreMax !== null && (!finite(candidate.ctas_score) || Number(candidate.ctas_score) > filters.scoreMax)) return false;
    var spectra = Number((candidate.follow_up_counts || {}).spectra || 0);
    if (filters.spectrum === "yes" && !spectra) return false;
    if (filters.spectrum === "no" && spectra) return false;
    var conflicts = Number(candidate.conflict_count || 0);
    if (filters.conflict === "yes" && !conflicts) return false;
    if (filters.conflict === "no" && conflicts) return false;
    if (filters.richness && String((candidate.record_completeness || {}).label || "") !== filters.richness) return false;
    if (filters.coneRadius !== null) {
      var separation = greatCircleDistanceDeg(candidate.ra_deg, candidate.dec_deg, filters.coneRa, filters.coneDec);
      if (separation === null || separation > filters.coneRadius) return false;
    }
    return true;
  }

  function filteredCandidates(candidates, filters, now) {
    return (candidates || []).filter(function (candidate) { return matchesFilters(candidate, filters, now); });
  }

  function parseFilters(search) {
    var params = search instanceof URLSearchParams ? search : new URLSearchParams(String(search || "").replace(/^\?/, ""));
    var raw = {};
    FILTER_KEYS.forEach(function (key) { if (params.has(key)) raw[key] = params.get(key); });
    return normalizeFilters(raw);
  }

  function serializeFilters(rawFilters) {
    var filters = normalizeFilters(rawFilters), params = new URLSearchParams();
    FILTER_KEYS.forEach(function (key) {
      var value = filters[key];
      if (Array.isArray(value)) value = value.join(",");
      if (value !== null && value !== "" && value !== "all") params.set(key, String(value));
    });
    return params;
  }

  function inflateSky(index) {
    var sky = index && index.sky;
    if (!sky) return [];
    if (!Array.isArray(sky.columns) || !Array.isArray(sky.rows) || new Set(sky.columns).size !== sky.columns.length) {
      throw new Error("The compact sky table is invalid.");
    }
    return sky.rows.map(function (row) {
      if (!Array.isArray(row) || row.length !== sky.columns.length) throw new Error("A compact sky row has the wrong width.");
      var candidate = {};
      sky.columns.forEach(function (key, i) { candidate[key] = row[i]; });
      return candidate;
    });
  }

  function inflateBootstrap(index) {
    if (Array.isArray(index && index.candidates)) return index.candidates.slice();
    var columns = index && index.candidate_columns, rows = index && index.candidate_rows;
    if (!Array.isArray(columns) || !Array.isArray(rows) || new Set(columns).size !== columns.length) {
      throw new Error("The CTAS browser bootstrap has no valid candidate table.");
    }
    return rows.map(function (row) {
      if (!Array.isArray(row) || row.length !== columns.length) throw new Error("A CTAS browser bootstrap row has the wrong width.");
      var flat = {}; columns.forEach(function (column, index_) { flat[column] = row[index_]; });
      var countKeys = ["classifications", "classification_history", "observations", "spectra", "messenger_signals",
        "publications", "publication_revisions", "host_context", "catalog_counterparts", "archive_products"];
      var counts = {}; countKeys.forEach(function (key) { counts[key] = Number(flat["n_" + key] || 0); delete flat["n_" + key]; });
      var record = {label: flat.record_label, present: flat.record_present, applicable: flat.record_applicable,
        not_assessed: flat.record_not_assessed, fraction: flat.record_fraction};
      ["label", "present", "applicable", "not_assessed", "fraction"].forEach(function (key) { delete flat["record_" + key]; });
      var link = flat.primary_source_url ? {source_key: flat.primary_source_key, url: flat.primary_source_url,
        designation: flat.primary_source_designation} : null;
      delete flat.primary_source_key; delete flat.primary_source_url; delete flat.primary_source_designation;
      var accounting = {declaredSources: flat.source_declared, applicableSources: flat.source_applicable,
        executedQueryReceipts: flat.source_executed, dataBearingSources: flat.source_data_bearing};
      delete flat.source_declared; delete flat.source_applicable; delete flat.source_executed; delete flat.source_data_bearing;
      flat.follow_up_counts = counts; flat.record_completeness = record; flat.links = link ? [link] : [];
      flat.identity_resolution = {state: flat.identity_state}; delete flat.identity_state;
      flat.conflict_count = Number(flat.conflict_count || 0); flat.source_accounting = accounting;
      return flat;
    });
  }

  function scoreScenario(model, overrides) {
    model = model || {}; overrides = overrides || {};
    var baseline = finite(overrides.baseline) ? Number(overrides.baseline) : Number(model.baseline || 35);
    var terms = (model.terms || []).map(function (term) {
      var points = finite(overrides[term.code]) ? Number(overrides[term.code]) : Number(term.points || 0);
      return Object.assign({}, term, {points: points});
    });
    var corePreclip = baseline + terms.reduce(function (sum, term) { return sum + term.points; }, 0);
    var corePostclip = Math.max(0, Math.min(100, corePreclip));
    var bonus = finite(overrides.multimessenger_bonus) ? Number(overrides.multimessenger_bonus) : Number(model.multimessenger_bonus || 0);
    var residual = finite(overrides.persisted_factor_rounding_residual)
      ? Number(overrides.persisted_factor_rounding_residual)
      : Number(model.persisted_factor_rounding_residual || 0);
    var finalPreclip = corePostclip + bonus + residual;
    var finalScore = model.status_override ? 0 : Math.max(0, Math.min(100, finalPreclip));
    return {baseline: baseline, terms: terms, core_preclip: corePreclip, core_postclip: corePostclip,
      multimessenger_bonus: bonus, persisted_factor_rounding_residual: residual,
      final_preclip: finalPreclip, final_score: finalScore,
      status_override: model.status_override || null};
  }

  function evidenceAt(timeline, cutoff) {
    var boundary = date(cutoff);
    var dated = [], undated = [];
    (timeline || []).forEach(function (entry) {
      var available = date(entry.public_available_at);
      if (!available) undated.push(entry);
      else if (!boundary || available <= boundary) dated.push(entry);
    });
    return {visible: dated, undated: undated};
  }

  function releaseHistorySelection(entries, recentLimit, totalLimit) {
    var rows = Array.isArray(entries) ? entries : [];
    var recent = Math.max(0, Number(recentLimit) || 0);
    var total = Math.max(recent, Number(totalLimit) || recent);
    var selected = rows.slice(0, recent), seen = {};
    selected.forEach(function (entry) {
      seen[String(entry.catalog_content_checksum_sha256 || entry.published_at || "")] = true;
    });
    rows.forEach(function (entry) {
      var notable = Boolean(entry.evidence) || Number(entry.added_count || 0) + Number(entry.removed_count || 0) >= 50;
      var key = String(entry.catalog_content_checksum_sha256 || entry.published_at || "");
      if (notable && !seen[key] && selected.length < total) {
        selected.push(entry); seen[key] = true;
      }
    });
    return selected;
  }

  function expandSourceMatrix(compact, patterns) {
    // A record's source matrix is published as its own informative rows plus a
    // reference to a shared "declared, never queried, nothing retained"
    // pattern. Rebuilding it here keeps every consumer working with the
    // complete ordered matrix while the release stores each repeated statement
    // once instead of once per record.
    if (Array.isArray(compact)) return compact.slice();
    if (!compact || typeof compact !== "object") return [];
    var quiet = ((patterns || {})[String(compact.no_evidence_pattern || "")] || []).slice();
    var total = Number(compact.row_count || 0);
    var placed = {}, rows = Array.isArray(compact.rows) ? compact.rows : [];
    rows.forEach(function (row) {
      var copy = {}, index = Number(row.row_index);
      Object.keys(row).forEach(function (key) { if (key !== "row_index") copy[key] = row[key]; });
      placed[index] = copy;
    });
    var out = [], next = 0;
    for (var position = 0; position < total; position += 1) {
      if (Object.prototype.hasOwnProperty.call(placed, position)) out.push(placed[position]);
      else if (next < quiet.length) out.push(quiet[next++]);
    }
    return out;
  }

  return {
    evidenceAt: evidenceAt,
    expandSourceMatrix: expandSourceMatrix,
    filteredCandidates: filteredCandidates,
    greatCircleDistanceDeg: greatCircleDistanceDeg,
    inflateBootstrap: inflateBootstrap,
    inflateSky: inflateSky,
    latestMeaningful: latestMeaningful,
    matchesFilters: matchesFilters,
    matchesPreset: matchesPreset,
    normalizeFilters: normalizeFilters,
    parseFilters: parseFilters,
    releaseHistorySelection: releaseHistorySelection,
    scoreScenario: scoreScenario,
    serializeFilters: serializeFilters,
    skyCandidates: skyCandidates,
    sexagesimal: sexagesimal
  };
}));
