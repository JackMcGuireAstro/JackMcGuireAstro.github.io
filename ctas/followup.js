/* CTAS suggested follow-up: the published clock-free draft plus tonight's windows at WIRO
   and Red Buttes and S/N-20 exposure estimates from the site's own CCD model.
   No dependencies: the observability model and the exposure model are passed in (or found on
   window), so the same file runs in the page, in the Tonight planner and under node. */
(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASFollowup = model;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SCHEMA = "ctas-suggested-follow-up.v1";
  var CLAIM = "Heuristic draft from public evidence; not an exposure-time calculation or observing request.";
  var EVIDENCE_TYPES = {
    "classification": "classification assertion",
    "follow-up-photometry": "time-series photometry or limiting magnitude",
    "spectrum": "rights-cleared spectrum or spectrum metadata",
    "host-context": "explicit host-association assertion",
    "catalog-context": "position-aware catalog query",
    "messenger": "source-linked messenger notice",
    "reports": "public report or publication"
  };
  var CADENCES = {
    rapid: "Rapid: the first available window, then every 15-30 minutes while the target is above the altitude limit.",
    nightly: "Nightly while the record stays inside the follow-up window.",
    weekly: "Weekly photometric monitoring.",
    none: "No follow-up is suggested for this record."
  };
  var UNCLASSIFIED = {"": 1, "unclassified": 1, "unknown": 1, "at": 1, "candidate": 1};
  var TARGET_ROLES = {"follow-up-target-candidate": 1};
  var COUNTERPART_ROLES = {"follow-up-target-candidate": 1, "localization-region-alert": 1};
  var FOLLOW_UP_WINDOW_DAYS = 180;

  // Jack's two telescopes. The same values live in ctas/data/observatories.json, which the
  // page prefers when it has loaded; these keep the card working offline and under node.
  var SITES = [
    {id: "wiro", observatory_id: "wyoming-infrared-observatory", name: "WIRO 2.3 m", latitude_deg: 41.0983, longitude_deg: -105.976, timezone: "America/Denver", telescope: "wiro"},
    {id: "rbo", observatory_id: "red-buttes-observatory", name: "Red Buttes 0.6 m", latitude_deg: 41.1764, longitude_deg: -105.5739, timezone: "America/Denver", telescope: "rbo"}
  ];
  // PLACEHOLDER spectroscopy thresholds, labelled as such wherever they are shown. Replace
  // with measured limits once the instrument profiles in tools/exposure/ccd.js are real.
  var SPECTROSCOPY = {
    wiro: {limit_mag: 19.0, placeholder: true, text: "classification spectrum practical to about 19.0 mag"},
    rbo: {limit_mag: null, placeholder: true, text: "imaging only; no spectrograph in the profile"}
  };
  var IMAGING_FILTERS = ["g", "r", "i", "V", "R"];
  var SNR_TARGET = 20;
  var MAX_EXPOSURE_S = 600;
  var MAX_EXPOSURES = 3;
  var MAX_TOTAL_S = MAX_EXPOSURE_S * MAX_EXPOSURES;
  var MIN_ALTITUDE_DEG = 30;
  var TWILIGHT_DEG = -12;

  // ------------------------------------------------------------ small helpers
  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character];
    });
  }
  function clean(value) { if (value === null || value === undefined) return null; var s = text(value).trim(); return s || null; }
  function finite(value) { return value !== null && value !== undefined && value !== "" && isFinite(Number(value)); }
  function truthy(value) { return value === true || value === 1; }

  // ISO text the way the exporter writes it (UTC, "Z", microseconds only when non-zero).
  var ISO = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::(\d{2}))?(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;
  function isoText(value, requireZone) {
    var raw = text(value).trim();
    if (!raw) return null;
    var match = raw.match(ISO);
    if (!match) {
      var parsed = Date.parse(raw);
      return isNaN(parsed) ? null : new Date(parsed).toISOString().replace(/\.000Z$/, "Z");
    }
    if (requireZone && !match[5]) return null;
    var zone = match[5] || "Z";
    if (zone !== "Z" && !/^[+-]00:?00$/.test(zone)) {
      var shifted = Date.parse(raw);
      return isNaN(shifted) ? null : new Date(shifted).toISOString().replace(/\.000Z$/, "Z");
    }
    var fraction = (match[4] || "").slice(1);
    fraction = (fraction + "000000").slice(0, 6);
    return match[1] + "T" + (match[2] || "00:00") + ":" + (match[3] || "00") + (/^0*$/.test(fraction) ? "" : "." + fraction) + "Z";
  }
  function dateOf(isoValue) { return isoValue ? isoValue.slice(0, 10) : null; }
  // Chronological order of two normalised ISO texts, down to the microsecond.
  function compareIso(a, b) {
    var difference = Date.parse(a) - Date.parse(b);
    if (difference) return difference;
    var fa = ((a.match(/\.(\d+)Z$/) || [])[1] || "000000"), fb = ((b.match(/\.(\d+)Z$/) || [])[1] || "000000");
    return fa < fb ? -1 : fa > fb ? 1 : 0;
  }
  // Round the way the exporter's decimal rounding of the exact binary value does.
  function round3(value) { return Number(Number(value).toFixed(3)); }

  // ------------------------------------------------------------ the published derivation
  // Mirrors suggested_follow_up_for in scripts/export_ctas_snapshot.py exactly; the static
  // test runs both on the same records. Accepts a complete dossier record or a compact
  // catalog row (n_spectra, photometry_outcomes); a compact row cannot see classification
  // conflicts or retained detections, so its draft is the one the exporter would publish
  // for a record without those rows.
  function activeClassificationLabels(candidate) {
    var labels = [];
    (((candidate || {}).follow_up || {}).classifications || []).forEach(function (row) {
      if (row.retracted || row.superseded) return;
      if (row.probability !== null && row.probability !== undefined && finite(row.probability) && Number(row.probability) <= 0) return;
      var label = text(row.classification).trim().toLowerCase();
      if (label && !UNCLASSIFIED[label]) labels.push(label);
    });
    return labels;
  }
  function classificationsIncompatible(labels) {
    var distinct = Array.from(new Set(labels)).sort();
    for (var i = 0; i < distinct.length; i++) for (var j = i + 1; j < distinct.length; j++) {
      if (!(distinct[i].indexOf(distinct[j]) === 0 || distinct[j].indexOf(distinct[i]) === 0)) return true;
    }
    return false;
  }
  function latestRetainedObservation(candidate) {
    var latest = null;
    (((candidate || {}).follow_up || {}).observations || []).forEach(function (row) {
      if (row.superseded) return;
      var iso = isoText(row.observed_at, true);
      if (iso && isFinite(Date.parse(iso)) && (latest === null || compareIso(iso, latest) > 0)) latest = iso;
    });
    return latest;
  }
  function photometryOutcomes(rows) {
    var active = (rows || []).filter(function (row) { return !row.superseded && !row.retracted; });
    return {detections: active.filter(function (row) { return truthy(row.detection); }).length};
  }
  function latestMagnitude(candidate) {
    var best = null, bestKey = null;
    (((candidate || {}).follow_up || {}).observations || []).forEach(function (row) {
      if (row.superseded || row.retracted || !truthy(row.detection)) return;
      var magnitude = Number(row.magnitude);
      if (!finite(row.magnitude) || !(magnitude > -5 && magnitude < 35)) return;
      var observed = isoText(row.observed_at);
      if (!observed) return;
      var key = [observed, text(row.provider_observation_id || row.assertion_id || "")];
      var order = bestKey === null ? 1 : compareIso(key[0], bestKey[0]) || (key[1] > bestKey[1] ? 1 : key[1] < bestKey[1] ? -1 : 0);
      if (order > 0) { best = row; bestKey = key; }
    });
    if (best) {
      return {value: round3(best.magnitude), band: clean(best.band), system: clean(best.magnitude_system),
        observed_at: bestKey[0], provider: clean(best.provider), basis: "latest-retained-detection"};
    }
    if (candidate.discovery_magnitude !== null && candidate.discovery_magnitude !== undefined) {
      return {value: round3(candidate.discovery_magnitude), band: null, system: null,
        observed_at: isoText(candidate.discovery_time), provider: clean(candidate.discovery_survey), basis: "discovery-magnitude"};
    }
    return {value: null, band: null, system: null, observed_at: null, provider: null, basis: null};
  }
  function humanRole(role) { return text(role).replace(/-/g, " "); }

  function derive(candidate) {
    candidate = candidate || {};
    var role = text(candidate.record_role), channel = text(candidate.ranking_channel);
    var status = text(candidate.status || "candidate").toLowerCase();
    var counts = candidate.follow_up_counts || {spectra: candidate.n_spectra, observations: candidate.n_observations};
    var heldOut = status === "retracted" || status === "bogus" || role === "retracted-event";
    var coordinates = candidate.ra_deg !== null && candidate.ra_deg !== undefined && candidate.dec_deg !== null && candidate.dec_deg !== undefined;
    var classified = !UNCLASSIFIED[text(candidate.classification).trim().toLowerCase()];
    var conflict = classificationsIncompatible(activeClassificationLabels(candidate));
    var hasSpectrum = Number(counts.spectra || 0) > 0;
    var observationCount = Number(counts.observations || 0);
    var outcomes = candidate.photometry_outcomes && Object.keys(candidate.photometry_outcomes).length ? candidate.photometry_outcomes : photometryOutcomes((candidate.follow_up || {}).observations);
    var detections = Number(outcomes.detections || 0);
    var latestObservation = latestRetainedObservation(candidate);
    var discovered = isoText(candidate.discovery_time, true);
    var newest = discovered;
    if (latestObservation && (!discovered || compareIso(latestObservation, discovered) > 0)) newest = latestObservation;
    var magnitude = latestMagnitude(candidate);
    var opticalTarget = Boolean(TARGET_ROLES[role]) && channel === "optical";
    var counterpartTarget = Boolean(COUNTERPART_ROLES[role]) && channel !== "optical";
    var isTarget = !heldOut && coordinates && (opticalTarget || counterpartTarget);

    var basis = [];
    if (heldOut) basis.push("Reported " + (status === "retracted" || status === "bogus" ? status : "retracted") + "; held out of follow-up.");
    else if (!COUNTERPART_ROLES[role]) basis.push("Record role " + (humanRole(role) || "unknown") + ": not a follow-up target.");
    else {
      if (discovered) basis.push("Discovered " + dateOf(discovered) + ".");
      if (counterpartTarget && detections === 0) basis.push("Multimessenger counterpart pending: no optical detection is retained.");
      if (opticalTarget && !classified) basis.push("No public classification is retained.");
      if (conflict) basis.push("Active classification assertions disagree.");
      if (opticalTarget && !hasSpectrum) basis.push("No public spectrum is retained.");
      if (latestObservation) basis.push("No retained observation since " + dateOf(latestObservation) + ".");
      else if (opticalTarget) basis.push("No retained follow-up observation.");
      if (!coordinates) basis.push("No valid coordinates are retained.");
    }

    var objectives = [];
    if (isTarget) {
      if (counterpartTarget) objectives.push({kind: "counterpart", text: "Image the reported position for an optical counterpart, or record a limiting magnitude.",
        closes_gap: "follow-up-photometry", suggested_evidence_type: EVIDENCE_TYPES["follow-up-photometry"]});
      if (opticalTarget) {
        objectives.push({kind: "imaging", text: "Confirm persistence and current brightness with calibrated multi-band imaging (g, r, i or V, R).",
          closes_gap: observationCount ? null : "follow-up-photometry", suggested_evidence_type: EVIDENCE_TYPES["follow-up-photometry"]});
        if (!classified || !hasSpectrum || conflict) {
          var gap = hasSpectrum ? "classification" : "spectrum";
          objectives.push({kind: "spectroscopy", text: "Obtain a classification-quality spectrum" + (conflict ? " to resolve the disagreeing classifications." :
            !hasSpectrum ? "." : "; the retained spectrum has not yielded a public classification."), closes_gap: gap, suggested_evidence_type: EVIDENCE_TYPES[gap]});
        } else {
          objectives.push({kind: "monitoring", text: "Continue multi-band photometric monitoring of the classified transient's light curve.",
            closes_gap: null, suggested_evidence_type: EVIDENCE_TYPES["follow-up-photometry"]});
        }
      }
    }
    objectives.forEach(function (row, index) { row.priority = index + 1; });
    var kinds = {};
    objectives.forEach(function (row) { kinds[row.kind] = 1; });
    var cadence = !objectives.length ? "none" : kinds.counterpart ? "rapid" : kinds.spectroscopy ? "nightly" : "weekly";

    var reasons = [];
    if (heldOut) reasons.push("Status " + status + ": held out of the follow-up leaderboard.");
    else if (role === "localization-region-alert") reasons.push("Localization-region alert: a counterpart search, not a classified optical target.");
    else if (TARGET_ROLES[role]) reasons.push("Follow-up target candidate in the " + (humanRole(channel) || "unknown") + " channel.");
    else reasons.push("Record role " + (humanRole(role) || "unknown") + " is not a follow-up target.");
    reasons.push(coordinates ? "Coordinates retained." : "No valid coordinates are retained; windows and exposure estimates are not possible.");
    reasons.push(magnitude.basis === "latest-retained-detection" ? "Latest magnitude is the newest retained detection."
      : magnitude.basis === "discovery-magnitude" ? "Latest magnitude falls back to the source-reported discovery magnitude."
      : "No magnitude is retained; exposure estimates are not possible.");

    return {
      schema: SCHEMA, basis: basis, objectives: objectives, cadence: {code: cadence, text: CADENCES[cadence]},
      latest_magnitude: magnitude,
      eligibility: {is_target: Boolean(isTarget), status: status, record_role: role, ranking_channel: channel,
        coordinates_present: Boolean(coordinates), magnitude_present: magnitude.value !== null,
        newest_evidence_at: newest, follow_up_window_days: FOLLOW_UP_WINDOW_DAYS, reasons: reasons},
      claim_boundary: CLAIM
    };
  }

  // The block to show: the published one when the release carries it, else the same rules
  // run here (older releases), and the reader is told which.
  function blockFor(candidate) {
    var published = candidate && candidate.suggested_follow_up;
    if (published && published.schema === SCHEMA) return {block: published, source: "published"};
    return {block: derive(candidate), source: "browser"};
  }

  // ------------------------------------------------------------ tonight at each telescope
  // The UTC date whose 00-24 h span holds tonight's dark hours in the Mountain time zone
  // (dark runs from about 01 UTC to 13 UTC), for the night that starts next.
  function tonightDate(now) { return new Date(Number(now) + 12 * 3600000).toISOString().slice(0, 10); }
  function localClock(utc, timezone) {
    try {
      return new Intl.DateTimeFormat("en-US", {timeZone: timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(new Date(utc));
    } catch (_) { return text(utc).slice(11, 16) + " UTC"; }
  }
  function moonIllumination(observability, when) {
    var sun = observability.sunRaDec(when), moon = observability.moonRaDec(when);
    var elongation = observability.separationDeg(sun.ra_deg, sun.dec_deg, moon.ra_deg, moon.dec_deg) * Math.PI / 180;
    return (1 - Math.cos(elongation)) / 2;
  }
  function siteWindow(candidate, site, observability, now) {
    var date = tonightDate(now);
    var result = observability.evaluate(candidate, {date: date, latitude_deg: site.latitude_deg, longitude_deg: site.longitude_deg,
      min_altitude_deg: MIN_ALTITUDE_DEG, max_airmass: 40, max_sun_altitude_deg: TWILIGHT_DEG, min_moon_separation_deg: 0});
    if (result.status !== "COMPLETE") return {site: site, date: date, status: result.status, reason: result.reason, hours: 0, intervals: []};
    var dark = result.rows.filter(function (row) { return row.sun_altitude_deg <= TWILIGHT_DEG; });
    var best = null;
    dark.forEach(function (row) { if (!best || row.altitude_deg > best.altitude_deg) best = row; });
    var hours = result.intervals.reduce(function (sum, row) { return sum + row.duration_minutes; }, 0) / 60;
    var illumination = best ? moonIllumination(observability, best.utc) : null;
    return {
      site: site, date: date, status: "COMPLETE", hours: Math.round(hours * 10) / 10,
      intervals: result.intervals.map(function (row) {
        return {start_utc: row.start_utc, end_utc: row.end_utc, minutes: Math.round(row.duration_minutes),
          start_local: localClock(row.start_utc, site.timezone), end_local: localClock(row.end_utc, site.timezone)};
      }),
      dark_from_local: dark.length ? localClock(dark[0].utc, site.timezone) : null,
      dark_until_local: dark.length ? localClock(dark[dark.length - 1].utc, site.timezone) : null,
      max_altitude_deg: best ? Math.round(best.altitude_deg) : null,
      best_utc: best ? best.utc : null, best_local: best ? localClock(best.utc, site.timezone) : null,
      best_airmass: best && best.airmass !== null ? Math.round(best.airmass * 100) / 100 : null,
      moon_separation_deg: best ? Math.round(best.moon_separation_deg) : null,
      moon_illumination: illumination === null ? null : Math.round(illumination * 100) / 100,
      sky_preset: illumination === null ? "grey" : illumination < 0.25 ? "dark" : illumination < 0.75 ? "grey" : "bright",
      claim_boundary: result.claim_boundary
    };
  }
  function tonight(candidate, options) {
    options = options || {};
    var observability = options.observability || (typeof window !== "undefined" ? window.CTASObservability : null);
    var sites = options.sites || SITES, now = options.now === undefined ? Date.now() : options.now;
    if (!observability || !finite(candidate.ra_deg) || !finite(candidate.dec_deg)) return null;
    return sites.map(function (site) { return siteWindow(candidate, site, observability, now); });
  }

  // ------------------------------------------------------------ exposure estimates
  function roundExposure(seconds) {
    if (!isFinite(seconds) || seconds <= 0) return null;
    if (seconds < 1) return Math.round(seconds * 100) / 100;
    if (seconds < 10) return Math.round(seconds * 10) / 10;
    if (seconds < 100) return Math.round(seconds);
    if (seconds < 1000) return Math.round(seconds / 10) * 10;
    return Math.round(seconds / 60) * 60;
  }
  function formatSeconds(seconds) {
    if (seconds === null) return "—";
    if (seconds < 1) return "<1 s";
    if (seconds < 120) return seconds + " s";
    if (seconds < 3600) return Math.round(seconds / 60) + " min";
    return (seconds / 3600).toFixed(1) + " h";
  }
  function imagingPlan(telescope, filterId, magnitude, ccd, airmass, skyPreset) {
    var filter = ccd.FILTERS.find(function (row) { return row.id === filterId; });
    if (!filter || !telescope.throughput || !telescope.extinction || !finite(telescope.throughput[filterId])) return null;
    var params = {mag: magnitude, snr: SNR_TARGET, airmass: airmass, sky: ccd.SKY[filterId][skyPreset], diameter: telescope.diameter,
      obstruction: telescope.obstruction, throughput: telescope.throughput[filterId], extinction: telescope.extinction[filterId],
      plateScale: telescope.plateScale, seeing: telescope.seeing, apertureFactor: 1, gain: telescope.gain, readNoise: telescope.readNoise,
      dark: telescope.dark, fullWell: telescope.fullWell, zeroPoint: filter.zeroPoint};
    var out = ccd.solve("time", params), seconds = roundExposure(out.t);
    var exposures = seconds === null ? null : Math.max(1, Math.ceil(seconds / MAX_EXPOSURE_S));
    var saturated = seconds !== null && seconds <= 1 && Boolean(ccd.peakPixel) && ccd.peakPixel(1, out.r, params).saturated;
    return {filter: filterId, system: filter.system, seconds: seconds, text: formatSeconds(seconds), exposures: exposures,
      feasible: seconds !== null && seconds <= MAX_TOTAL_S, saturation_risk: Boolean(saturated), sky: params.sky, airmass: airmass};
  }
  function spectroscopyPlan(telescope, magnitude) {
    var profile = SPECTROSCOPY[telescope.id] || {limit_mag: null, placeholder: true, text: "no spectroscopic profile"};
    if (profile.limit_mag === null) return {state: "not-available", margin: null, text: profile.text, placeholder: profile.placeholder, limit_mag: null};
    if (!finite(magnitude)) return {state: "unknown", margin: null, text: "no magnitude to compare with the " + profile.limit_mag.toFixed(1) + " mag threshold", placeholder: profile.placeholder, limit_mag: profile.limit_mag};
    var margin = Math.round((profile.limit_mag - Number(magnitude)) * 100) / 100;
    return {state: margin >= 0.5 ? "feasible" : margin >= 0 ? "marginal" : "too-faint", margin: margin, limit_mag: profile.limit_mag,
      placeholder: profile.placeholder, text: profile.text + "; margin " + (margin >= 0 ? "+" : "") + margin.toFixed(2) + " mag"};
  }
  // One row per telescope profile in ccd.TELESCOPES, in that order. `windows` (from
  // tonight()) supplies each site's best airmass and sky brightness; without them an
  // airmass of 1.5 and a grey sky are assumed and said so.
  function exposurePlan(block, ccd, options) {
    options = options || {};
    if (!ccd || !Array.isArray(ccd.TELESCOPES)) return null;
    var magnitude = block && block.latest_magnitude ? block.latest_magnitude.value : null;
    var kinds = {};
    ((block || {}).objectives || []).forEach(function (row) { kinds[row.kind] = 1; });
    return ccd.TELESCOPES.map(function (telescope) {
      var window_ = (options.windows || []).find(function (row) { return row.site && row.site.telescope === telescope.id; }) || null;
      var airmass = window_ && window_.best_airmass ? Math.max(1, window_.best_airmass) : 1.5;
      var sky = window_ && window_.sky_preset ? window_.sky_preset : "grey";
      var imaging = finite(magnitude) && (kinds.imaging || kinds.counterpart || kinds.monitoring)
        ? IMAGING_FILTERS.map(function (filterId) { return imagingPlan(telescope, filterId, Number(magnitude), ccd, airmass, sky); }).filter(Boolean) : [];
      return {id: telescope.id, name: telescope.name, placeholder: Boolean(telescope.placeholder), airmass: airmass, sky_preset: sky,
        assumed_geometry: !window_, imaging: imaging, imaging_feasible: imaging.length ? imaging.some(function (row) { return row.feasible; }) : null,
        spectroscopy: kinds.spectroscopy ? spectroscopyPlan(telescope, magnitude) : null};
    });
  }
  // Short per-telescope verdicts for the Tonight planner's rows.
  function feasibility(block, ccd, options) {
    var plan = exposurePlan(block, ccd, options) || [];
    return plan.map(function (row) {
      var parts = [];
      if (row.imaging_feasible === true) parts.push("imaging");
      if (row.spectroscopy && row.spectroscopy.state === "feasible") parts.push("spectrum");
      else if (row.spectroscopy && row.spectroscopy.state === "marginal") parts.push("spectrum (marginal)");
      return {id: row.id, label: row.id.toUpperCase(), feasible: parts.length > 0, text: parts.join(" + ") || "—", placeholder: row.placeholder};
    });
  }

  // ------------------------------------------------------------ links
  function plannerHref(candidate, site, base) {
    var params = new URLSearchParams({site: site, list: "ctas", name: text(candidate.name), event: text(candidate.event_id)});
    if (finite(candidate.ra_deg) && finite(candidate.dec_deg)) { params.set("ra", String(Number(candidate.ra_deg))); params.set("dec", String(Number(candidate.dec_deg))); }
    var block = blockFor(candidate).block;
    if (finite(block.latest_magnitude.value)) params.set("mag", String(block.latest_magnitude.value));
    return (base || "") + "tonight/?" + params.toString();
  }
  function calculatorHref(candidate, telescope, filterId, airmass, base, skyPreset) {
    var block = blockFor(candidate).block, params = new URLSearchParams({telescope: telescope, filter: filterId, mode: "time", snr: String(SNR_TARGET), target: text(candidate.name)});
    if (finite(block.latest_magnitude.value)) params.set("mag", String(block.latest_magnitude.value));
    if (finite(airmass)) params.set("airmass", String(Math.round(Number(airmass) * 100) / 100));
    if (skyPreset) params.set("sky", String(skyPreset));
    return (base || "") + "tools/exposure/?" + params.toString();
  }

  // ------------------------------------------------------------ the card
  function ageText(isoValue, now) {
    var ms = Date.parse(isoValue || "");
    if (!isFinite(ms)) return "";
    var days = Math.max(0, (Number(now) - ms) / 86400000);
    if (days < 1) return Math.max(1, Math.round(days * 24)) + " h ago";
    if (days < 60) return Math.round(days) + " d ago";
    return Math.round(days / 30.44) + " months ago";
  }
  function archival(block, now) {
    var newest = Date.parse(((block || {}).eligibility || {}).newest_evidence_at || "");
    return isFinite(newest) && (Number(now) - newest) / 86400000 > FOLLOW_UP_WINDOW_DAYS;
  }
  function renderWindow(window_) {
    if (!window_) return "<p>Tonight's window needs retained coordinates.</p>";
    if (window_.status !== "COMPLETE") return "<p><strong>" + esc(window_.status) + ":</strong> " + esc(window_.reason) + "</p>";
    if (!window_.intervals.length) {
      return "<p>Not above " + MIN_ALTITUDE_DEG + "° during tonight's dark hours (" + esc(window_.dark_from_local || "—") + "–" + esc(window_.dark_until_local || "—") +
        " local). Highest " + esc(window_.max_altitude_deg === null ? "—" : window_.max_altitude_deg + "°") + " at " + esc(window_.best_local || "—") + ".</p>";
    }
    return "<p><strong>" + window_.hours.toFixed(1) + " h</strong> above " + MIN_ALTITUDE_DEG + "° in the dark: " + window_.intervals.map(function (row) {
      return esc(row.start_local) + "–" + esc(row.end_local);
    }).join(", ") + " local (" + esc(window_.site.timezone) + "). Highest " + esc(window_.max_altitude_deg) + "° at " + esc(window_.best_local) +
      " (airmass " + esc(window_.best_airmass === null ? "—" : window_.best_airmass.toFixed(2)) + "), Moon " + esc(window_.moon_separation_deg) + "° away, " +
      Math.round((window_.moon_illumination || 0) * 100) + "% lit (" + esc(window_.sky_preset) + " sky).</p>";
  }
  function renderTelescope(candidate, plan, window_, block, base) {
    var site = window_ ? window_.site : SITES.find(function (row) { return row.telescope === plan.id; });
    var links = '<p class="ctas-followup__links"><a href="' + esc(plannerHref(candidate, plan.id, base)) + '">Open in the Tonight planner</a>' +
      (finite(block.latest_magnitude.value) ? ' · <a href="' + esc(calculatorHref(candidate, plan.id, "r", plan.airmass, base, plan.sky_preset)) + '">Open the exposure calculator</a>' : "") + "</p>";
    var imaging = "";
    if (plan.imaging.length) {
      imaging = '<table class="ctas-followup__exposures"><caption>Exposure for S/N ' + SNR_TARGET + " at " + esc(block.latest_magnitude.value.toFixed(2)) +
        " mag in every filter, airmass " + esc(plan.airmass.toFixed(2)) + ", " + esc(plan.sky_preset) + " sky" + (plan.assumed_geometry ? " (assumed)" : "") +
        '</caption><thead><tr><th scope="col">Filter</th><th scope="col">Time</th><th scope="col">Within ' + MAX_EXPOSURES + "×" + MAX_EXPOSURE_S + " s?</th></tr></thead><tbody>" +
        plan.imaging.map(function (row) {
          return '<tr><th scope="row">' + esc(row.filter) + " <small>" + esc(row.system) + "</small></th><td>" + esc(row.text) +
            (row.exposures > 1 && row.feasible ? " <small>(" + row.exposures + " × ≤" + MAX_EXPOSURE_S + " s)</small>" : "") + "</td><td>" +
            (row.feasible ? "yes" : "no") + (row.saturation_risk ? " <small>(bright: watch saturation)</small>" : "") + "</td></tr>";
        }).join("") + "</tbody></table>";
    } else if (block.objectives.length && !finite(block.latest_magnitude.value)) {
      imaging = "<p>No magnitude is retained, so no exposure estimate is possible.</p>";
    }
    var spectroscopy = plan.spectroscopy ? '<p class="ctas-followup__spectroscopy"><strong>Spectroscopy:</strong> ' +
      esc({feasible: "feasible", marginal: "marginal", "too-faint": "too faint", "not-available": "not available", unknown: "undetermined"}[plan.spectroscopy.state]) +
      " — " + esc(plan.spectroscopy.text) + (plan.spectroscopy.placeholder ? " <small>(placeholder " + (plan.spectroscopy.limit_mag === null ? "profile" : "threshold") + ")</small>" : "") + "</p>" : "";
    return '<section class="ctas-followup__telescope" data-followup-telescope="' + esc(plan.id) + '"><h5>' + esc(site ? site.name : plan.name) + "</h5>" +
      renderWindow(window_) + imaging + spectroscopy + links + "</section>";
  }

  function render(candidate, options) {
    options = options || {};
    var now = options.now === undefined ? Date.now() : options.now, base = options.base || "";
    var found = blockFor(candidate), block = found.block;
    var ccd = options.ccd || (typeof window !== "undefined" ? window.CTASExposureModel : null);
    var windows = options.windows === undefined ? tonight(candidate, options) : options.windows;
    var plan = exposurePlan(block, ccd, {windows: windows || []});
    var isArchival = archival(block, now);
    var magnitude = block.latest_magnitude;
    var magnitudeText = finite(magnitude.value)
      ? magnitude.value.toFixed(2) + (magnitude.band ? " " + magnitude.band : "") + (magnitude.system ? " (" + magnitude.system + ")" : "") +
        (magnitude.observed_at ? ", " + ageText(magnitude.observed_at, now) : "") + " · " +
        (magnitude.basis === "latest-retained-detection" ? "latest retained detection" + (magnitude.provider ? " via " + magnitude.provider : "") : "source-reported discovery magnitude")
      : "none retained";
    var objectives = block.objectives.length ? "<ol>" + block.objectives.map(function (row) {
      return '<li><span class="pill">' + esc(row.kind) + "</span> " + esc(row.text) + " <small>Closes: " + esc(row.suggested_evidence_type) + "</small></li>";
    }).join("") + "</ol>" : "<p>No objective: " + esc(block.eligibility.reasons[0]) + "</p>";
    var telescopes = plan ? plan.map(function (row) {
      return renderTelescope(candidate, row, (windows || []).find(function (w) { return w.site.telescope === row.id; }) || null, block, base);
    }).join("") : '<p data-followup-exposure-pending>' + (block.objectives.length ? "Loading the exposure model…" : "") + "</p>";
    var placeholder = plan && plan.some(function (row) { return row.placeholder; }) || block.objectives.some(function (row) { return row.kind === "spectroscopy"; });
    return '<section class="ctas-followup" data-followup-card data-followup-event="' + esc(candidate.event_id) + '" data-followup-source="' + esc(found.source) + '" aria-labelledby="ctas-followup-title">' +
      '<div class="ctas-followup__head"><h4 id="ctas-followup-title">Suggested follow-up</h4>' +
      '<span class="pill ctas-followup__cadence">' + esc(block.cadence.code) + "</span>" +
      (placeholder ? '<a class="ctas-followup__badge" href="' + esc(base) + 'tools/exposure/#placeholder-note" data-followup-placeholder-badge>placeholder instrument values</a>' : "") + "</div>" +
      (isArchival ? '<p class="ctas-followup__archival"><strong>Archival:</strong> the newest retained evidence is ' + esc(ageText(block.eligibility.newest_evidence_at, now)) +
        ", beyond the " + FOLLOW_UP_WINDOW_DAYS + "-day follow-up window, so these objectives describe what the record lacks rather than tonight's priority.</p>" : "") +
      '<div class="ctas-followup__grid"><section><h5>Why</h5>' + (block.basis.length ? "<ul>" + block.basis.map(function (row) { return "<li>" + esc(row) + "</li>"; }).join("") + "</ul>" : "<p>No gap is recorded.</p>") + "</section>" +
      "<section><h5>Objectives</h5>" + objectives + "<p><strong>Cadence:</strong> " + esc(block.cadence.text) + "</p></section>" +
      "<section><h5>Target</h5><p><strong>Latest magnitude:</strong> " + esc(magnitudeText) + "</p><ul>" + block.eligibility.reasons.map(function (row) { return "<li>" + esc(row) + "</li>"; }).join("") + "</ul></section></div>" +
      (block.objectives.length ? '<div class="ctas-followup__telescopes">' + telescopes + "</div>" : "") +
      // Track 3a: the GW counterpart ranking and the WIRO / Red Buttes tiling drafts (ctas/screens.js).
      (typeof globalThis !== "undefined" && globalThis.CTASScreens ? globalThis.CTASScreens.gwFollowup(candidate) : "") +
      '<p class="ctas-claim-boundary">' + esc(block.claim_boundary) + " Windows are a browser-local geometric estimate (no weather or telescope state); exposure times come from the site's CCD model with " +
      (placeholder ? "placeholder WIRO and Red Buttes instrument values" : "the preset instrument values") + ". Nothing here is sent anywhere or requests telescope time." +
      (found.source === "browser" ? " This release predates the published draft, so the browser derived it with the same rules." : "") + "</p></section>";
  }

  // ------------------------------------------------------------ page glue
  var exposureModelPromise = null;
  function loadExposureModel(url) {
    if (typeof window === "undefined") return Promise.resolve(null);
    if (window.CTASExposureModel) return Promise.resolve(window.CTASExposureModel);
    if (!exposureModelPromise) {
      // An absolute URL: a bare "tools/..." specifier would be refused as a module name.
      var absolute = new URL(url, document.baseURI).href;
      exposureModelPromise = import(absolute).then(function (module_) { window.CTASExposureModel = module_; return module_; }).catch(function () { return null; });
    }
    return exposureModelPromise;
  }
  var sitesPromise = null;
  function loadSites(url) {
    if (typeof window === "undefined" || typeof fetch !== "function") return Promise.resolve(SITES);
    if (!sitesPromise) {
      sitesPromise = fetch(url, {cache: "force-cache"}).then(function (response) { return response.ok ? response.json() : null; }).then(function (document_) {
        var rows = (document_ && document_.sites) || [];
        return SITES.map(function (site) {
          var published = rows.find(function (row) { return row.id === site.observatory_id; });
          return published ? Object.assign({}, site, {latitude_deg: Number(published.latitude_deg), longitude_deg: Number(published.longitude_deg), timezone: published.timezone || site.timezone}) : site;
        });
      }).catch(function () { return SITES; });
    }
    return sitesPromise;
  }
  // Re-draw the card once the exposure model and the published site coordinates are in.
  function complete(candidate, options) {
    options = options || {};
    return Promise.all([loadExposureModel(options.ccdUrl || "tools/exposure/ccd.js?v=20261017"), loadSites(options.sitesUrl || "ctas/data/observatories.json")]).then(function (loaded) {
      // Only the card of the dossier that asked: the reader may have opened another since.
      var card = typeof document !== "undefined" && candidate
        ? document.querySelector('[data-followup-card][data-followup-event="' + String(candidate.event_id).replace(/["\\]/g, "") + '"]') : null;
      if (!card) return null;
      var html = render(candidate, Object.assign({}, options, {ccd: loaded[0], sites: loaded[1]}));
      var holder = document.createElement("div");
      holder.innerHTML = html;
      card.replaceWith(holder.firstElementChild);
      return html;
    });
  }
  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("ctas:candidate-opened", function (event) {
      var candidate = event && event.detail && event.detail.candidate;
      if (candidate) complete(candidate).catch(function () { /* the synchronous card stays */ });
    });
  }

  return {SCHEMA: SCHEMA, CLAIM: CLAIM, SITES: SITES, SPECTROSCOPY: SPECTROSCOPY, IMAGING_FILTERS: IMAGING_FILTERS, SNR_TARGET: SNR_TARGET,
    MAX_TOTAL_S: MAX_TOTAL_S, derive: derive, blockFor: blockFor, tonight: tonight, tonightDate: tonightDate, exposurePlan: exposurePlan,
    feasibility: feasibility, plannerHref: plannerHref, calculatorHref: calculatorHref, render: render, complete: complete, isoText: isoText,
    roundExposure: roundExposure, archival: archival};
}));
