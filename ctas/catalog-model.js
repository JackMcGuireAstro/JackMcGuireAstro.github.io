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
    if (preset === "newest") return Boolean(discovered && discovered.getTime() >= thirtyDays);
    if (preset === "updated") return Boolean(updated && updated.getTime() >= sevenDays);
    if (preset === "classified") return Boolean(classified && classified.getTime() >= sevenDays);
    if (preset === "retracted") return String(candidate.status || "").toLowerCase() === "retracted" || Boolean(retracted);
    if (preset === "spectra") return Boolean(spectrum && spectrum.getTime() >= thirtyDays);
    if (preset === "no-spectra") return !Number(counts.spectra || 0);
    if (preset === "messenger") return Boolean(messenger && messenger.getTime() >= sevenDays);
    if (preset === "unclassified") return !candidate.classification || candidate.classification === "Unclassified";
    if (preset === "bright") return candidate.discovery_magnitude !== null && candidate.discovery_magnitude !== undefined && Number(candidate.discovery_magnitude) <= 18;
    if (preset === "multimessenger") return String(candidate.primary_messenger || "").toLowerCase() === "multimessenger" || (candidate.messenger_channels || []).length >= 2;
    if (preset === "rich") return completeness.label === "Rich public record";
    if (preset === "event-only") return Number(candidate.follow_up_total || 0) === 0;
    if (preset === "needs-follow-up") {
      return Number(candidate.follow_up_total || 0) === 0 ||
        !Number(counts.spectra || 0) ||
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

  return {
    matchesPreset: matchesPreset,
    releaseHistorySelection: releaseHistorySelection,
    skyCandidates: skyCandidates,
    sexagesimal: sexagesimal
  };
}));
