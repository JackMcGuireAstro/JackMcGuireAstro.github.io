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
    if (preset === "retracted") return Boolean(retracted && retracted.getTime() >= sevenDays);
    if (preset === "spectra") return Boolean(spectrum && spectrum.getTime() >= thirtyDays);
    if (preset === "no-spectra") return !Number(counts.spectra || 0);
    if (preset === "messenger") return Boolean(messenger && messenger.getTime() >= sevenDays);
    if (preset === "unclassified") return !candidate.classification || candidate.classification === "Unclassified";
    if (preset === "bright") return candidate.discovery_magnitude !== null && candidate.discovery_magnitude !== undefined && Number(candidate.discovery_magnitude) <= 18;
    if (preset === "multimessenger") return String(candidate.primary_messenger || "").toLowerCase() === "multimessenger" || (candidate.messenger_channels || []).length >= 2;
    if (preset === "rich") return completeness.label === "Rich public record";
    if (preset === "event-only") return Number(candidate.follow_up_total || 0) === 0;
    return true;
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

  return { matchesPreset: matchesPreset, skyCandidates: skyCandidates };
}));
