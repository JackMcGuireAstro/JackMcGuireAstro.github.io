/* CTAS light-curve arithmetic: turn retained (or live) photometry rows into plottable
 * points and a few rough, clearly labelled numbers — peak per band, observed rise time,
 * early decline rate and g−r colour near peak. Pure functions only (no page, no network),
 * unit-tested in scripts/test_ctas_lightcurve_summary.js with synthetic light curves.
 *
 * These are simple estimates from the points CTAS retains, not model fits: a peak is the
 * brightest retained detection that another detection in the same band backs up (within 3
 * days and 0.75 mag), a rise is first detection to that point, a decline is an unweighted
 * straight line through the detections in the 15 days after it. Only the episode of
 * detections around discovery is used; years-old activity at the same position is not.
 */
(function (root) {
  "use strict";

  var DAY = 86400000;
  var DECLINE_WINDOW_DAYS = 15;
  var COLOUR_PAIR_DAYS = 1;
  var COLOUR_NEAR_PEAK_DAYS = 10;
  // Detections separated by more than this many days belong to different episodes (a host
  // or earlier flare years before, a re-brightening later); estimates use only the episode
  // around day 0, while the plot shows everything.
  var EPISODE_GAP_DAYS = 100;
  // A peak must be backed by another detection in the same band within 3 days and 0.75 mag,
  // so one spurious point (a bad subtraction, a satellite) does not become the peak.
  var SUPPORT_DAYS = 3, SUPPORT_MAG = 0.75;
  // AB zero points for flux densities, so a positive flux can be drawn as a magnitude.
  var ZERO_POINTS = {ujy: 23.9, "µjy": 23.9, "μjy": 23.9, njy: 31.4, mjy: 16.4, jy: 8.9};

  function finite(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }
  function truthy(value) { return value === true || value === 1 || value === "1" || value === "t" || value === "true"; }
  function plausibleMagnitude(value) { return value !== null && value > -30 && value < 40; }
  function timeOf(row) {
    var mjd = finite(row.mjd);
    if (mjd !== null && mjd > 15000 && mjd < 120000) return (mjd - 40587) * DAY;
    var jd = finite(row.jd);
    if (jd !== null && jd > 2415000 && jd < 2520000) return (jd - 2440587.5) * DAY;
    var parsed = Date.parse(String(row.observed_at || "").replace(/(\.\d{3})\d+/, "$1"));
    return isFinite(parsed) ? parsed : null;
  }
  function zeroPoint(unit) {
    var key = String(unit || "").trim().toLowerCase().replace(/\s+/g, "");
    return Object.prototype.hasOwnProperty.call(ZERO_POINTS, key) ? ZERO_POINTS[key] : null;
  }
  function referenceTime(value) {
    if (value === null || value === undefined || value === "") return null;
    var parsed = Date.parse(String(value).replace(/(\.\d{3})\d+/, "$1"));
    return isFinite(parsed) ? parsed : null;
  }

  // ------------------------------------------------------------------ points
  // Rows -> {points, reference, skipped}. Superseded revisions are left out; a detection
  // without a magnitude is converted from a positive flux in a known unit (never from a
  // zero or negative flux); a limit needs the provider's own limiting magnitude.
  function toPoints(rows, options) {
    options = options || {};
    var out = [], skipped = {superseded: 0, unplottable: 0};
    (rows || []).forEach(function (row, index) {
      if (!row || typeof row !== "object") return;
      if (truthy(row.superseded)) { skipped.superseded += 1; return; }
      var time = timeOf(row);
      if (time === null) { skipped.unplottable += 1; return; }
      var magnitude = finite(row.magnitude), error = finite(row.magnitude_error), limit = finite(row.limiting_magnitude);
      var stated = row.detection === undefined || row.detection === null || row.detection === "" ? null : truthy(row.detection);
      var detected = stated === null ? magnitude !== null : stated;
      var converted = false;
      if (detected && magnitude === null) {
        var zp = zeroPoint(row.flux_unit), flux = finite(row.flux), fluxError = finite(row.flux_error);
        if (zp !== null && flux !== null && flux > 0) {
          magnitude = zp - 2.5 * Math.log10(flux);
          error = fluxError !== null && fluxError > 0 ? 1.0857 * fluxError / flux : null;
          converted = true;
        }
      }
      var method = String(row.photometry_method || row.pipeline || "");
      var base = {
        time: time, band: String(row.band || row.original_band || "").trim() || "?", forced: /forced/i.test(method),
        provider: String(row.provider || ""), method: method, system: String(row.magnitude_system || ""), index: index,
      };
      if (detected && plausibleMagnitude(magnitude)) {
        base.kind = "detection"; base.magnitude = magnitude; base.converted = converted;
        base.error = error !== null && error >= 0 && error < 5 ? error : null;
      } else if (!detected && plausibleMagnitude(limit)) {
        base.kind = "limit"; base.magnitude = limit; base.error = null; base.converted = false;
      } else { skipped.unplottable += 1; return; }
      out.push(base);
    });
    out.sort(function (a, b) { return a.time - b.time || a.index - b.index; });
    var discovery = referenceTime(options.discovery), basis = "discovery";
    if (discovery === null) {
      var first = out.filter(function (p) { return p.kind === "detection"; })[0] || out[0];
      discovery = first ? first.time : null;
      basis = first ? (first.kind === "detection" ? "first detection" : "first measurement") : "none";
    }
    out.forEach(function (point) { point.days = discovery === null ? 0 : (point.time - discovery) / DAY; });
    return {points: out, reference: {time: discovery, iso: discovery === null ? null : new Date(discovery).toISOString(), basis: basis}, skipped: skipped};
  }

  // The same alert can reach CTAS through two brokers (Fink and Lasair both relay ZTF);
  // detections in one band within ~90 s and 0.02 mag of each other count once.
  function uniqueDetections(points) {
    var kept = [], byBand = {}, duplicates = 0;
    points.filter(function (p) { return p.kind === "detection"; }).forEach(function (point) {
      var previous = byBand[point.band] || [];
      var twin = previous.some(function (other) {
        return Math.abs(other.time - point.time) <= 0.001 * DAY && Math.abs(other.magnitude - point.magnitude) <= 0.02;
      });
      if (twin) { duplicates += 1; return; }
      previous.push(point); byBand[point.band] = previous; kept.push(point);
    });
    return {points: kept, duplicates: duplicates};
  }

  function linearFit(points) {
    var n = points.length;
    if (n < 2) return null;
    var meanX = 0, meanY = 0;
    points.forEach(function (p) { meanX += p.days; meanY += p.magnitude; });
    meanX /= n; meanY /= n;
    var sxx = 0, sxy = 0;
    points.forEach(function (p) { sxx += (p.days - meanX) * (p.days - meanX); sxy += (p.days - meanX) * (p.magnitude - meanY); });
    if (!(sxx > 0)) return null;
    var slope = sxy / sxx, intercept = meanY - slope * meanX, residual = 0;
    points.forEach(function (p) { var r = p.magnitude - (intercept + slope * p.days); residual += r * r; });
    return {slope: slope, intercept: intercept, slopeError: n > 2 ? Math.sqrt(residual / (n - 2) / sxx) : null};
  }

  function supported(point, own) {
    return own.some(function (other) {
      return other !== point && Math.abs(other.time - point.time) <= SUPPORT_DAYS * DAY && Math.abs(other.magnitude - point.magnitude) <= SUPPORT_MAG;
    });
  }
  // The brightest detection with a neighbour (see SUPPORT_DAYS); if none has one, the
  // brightest detection, marked unconfirmed. `skipped` is the brightest unbacked point
  // passed over, so the page can say it was not used.
  function choosePeak(own) {
    var byBrightness = own.slice().sort(function (a, b) { return a.magnitude - b.magnitude || a.time - b.time; });
    for (var i = 0; i < byBrightness.length; i += 1) {
      if (supported(byBrightness[i], own)) return {peak: byBrightness[i], confirmed: true, skipped: i ? byBrightness[0] : null};
    }
    return {peak: byBrightness[0] || null, confirmed: false, skipped: null};
  }

  function describe(point) {
    return point ? {magnitude: point.magnitude, error: point.error, time: point.time, iso: new Date(point.time).toISOString(),
      days: point.days, band: point.band, provider: point.provider, forced: point.forced} : null;
  }

  // g−r (or any blue−red pair) from detections within a day of each other, choosing the
  // pair closest to the peak time and requiring it within ±10 days of that peak.
  function colourNearPeak(detections, blue, red, peakTime) {
    var blues = detections.filter(function (p) { return p.band === blue; }), reds = detections.filter(function (p) { return p.band === red; });
    var best = null;
    blues.forEach(function (b) {
      reds.forEach(function (r) {
        var separation = Math.abs(b.time - r.time) / DAY;
        if (separation > COLOUR_PAIR_DAYS) return;
        var offset = ((b.time + r.time) / 2 - peakTime) / DAY;
        if (!best || Math.abs(offset) < Math.abs(best.offsetDays) || (Math.abs(offset) === Math.abs(best.offsetDays) && separation < best.separationDays)) {
          best = {value: b.magnitude - r.magnitude, error: b.error !== null && r.error !== null ? Math.sqrt(b.error * b.error + r.error * r.error) : null,
            blue: describe(b), red: describe(r), separationDays: separation, offsetDays: offset};
        }
      });
    });
    return best && Math.abs(best.offsetDays) <= COLOUR_NEAR_PEAK_DAYS ? best : null;
  }

  // Split time-ordered detections at gaps longer than EPISODE_GAP_DAYS and choose the one
  // that contains day 0 (or is nearest to it); without a discovery time, the episode with
  // the most detections (the latest on a tie).
  function episodes(detections) {
    var out = [];
    detections.forEach(function (point) {
      var current = out[out.length - 1];
      if (current && point.time - current.end <= EPISODE_GAP_DAYS * DAY) { current.end = point.time; current.count += 1; }
      else out.push({start: point.time, end: point.time, count: 1});
    });
    return out;
  }
  function chooseEpisode(list, reference) {
    if (!list.length) return null;
    var best = null;
    list.forEach(function (episode) {
      var score = reference.basis === "discovery"
        ? (reference.time >= episode.start && reference.time <= episode.end ? 0 : Math.min(Math.abs(episode.start - reference.time), Math.abs(episode.end - reference.time)))
        : -episode.count;
      if (!best || score <= best.score) best = {episode: episode, score: score};
    });
    return best.episode;
  }

  function summarise(rows, options) {
    var prepared = toPoints(rows, options), everything = prepared.points;
    var unique = uniqueDetections(everything), allDetections = unique.points;
    var episodeList = episodes(allDetections), episode = chooseEpisode(episodeList, prepared.reference);
    var detections = episode ? allDetections.filter(function (p) { return p.time >= episode.start && p.time <= episode.end; }) : [];
    // limits count with the episode when they fall inside it or in the gap allowance before it
    var all = episode ? everything.filter(function (p) {
      return p.time >= episode.start - EPISODE_GAP_DAYS * DAY && p.time <= episode.end + (p.kind === "limit" ? EPISODE_GAP_DAYS * DAY : 0);
    }) : everything;
    var bandNames = [];
    all.forEach(function (p) { if (bandNames.indexOf(p.band) === -1) bandNames.push(p.band); });
    var bands = bandNames.map(function (band) {
      var own = detections.filter(function (p) { return p.band === band; });
      var limits = all.filter(function (p) { return p.band === band && p.kind === "limit"; }).length;
      var choice = choosePeak(own);
      var peak = describe(choice.peak);
      if (peak) peak.confirmed = choice.confirmed;
      return {band: band, detections: own.length, limits: limits, forced: all.filter(function (p) { return p.band === band && p.forced; }).length,
        peak: peak, unconfirmedBrighter: describe(choice.skipped), first: describe(own[0]), last: describe(own[own.length - 1]), points: own};
    }).sort(function (a, b) {
      return b.detections - a.detections || (a.peak && b.peak ? a.peak.magnitude - b.peak.magnitude : (b.peak ? 1 : 0) - (a.peak ? 1 : 0)) || (a.band < b.band ? -1 : 1);
    });

    var peak = bands.reduce(function (best, band) { return band.peak && (!best || band.peak.magnitude < best.magnitude) ? band.peak : best; }, null);

    var rise = null, lead = bands[0] && bands[0].detections ? bands[0] : null;
    if (lead) {
      var firstPoint = lead.points[0], peakPoint = lead.points.filter(function (p) { return p.time === lead.peak.time && p.magnitude === lead.peak.magnitude; })[0];
      var before = all.filter(function (p) { return p.band === lead.band && p.kind === "limit" && p.time < firstPoint.time; });
      var lastLimit = before[before.length - 1] || null;
      rise = {band: lead.band, detections: lead.detections, days: (peakPoint.time - firstPoint.time) / DAY,
        first: describe(firstPoint), peak: describe(peakPoint),
        // within a night of the first (or last) detection counts as at it: surveys such as
        // ATLAS take several exposures a night, and the brightest need not be the first
        peakIsFirst: peakPoint.time - firstPoint.time < DAY, peakIsLast: lead.points[lead.points.length - 1].time - peakPoint.time < DAY,
        lastLimitBefore: lastLimit ? {magnitude: lastLimit.magnitude, days: lastLimit.days, daysBeforeFirst: (firstPoint.time - lastLimit.time) / DAY} : null};
    }

    var decline = [];
    bands.forEach(function (band) {
      if (!band.peak) return;
      var window = band.points.filter(function (p) { return p.time >= band.peak.time && p.time <= band.peak.time + DECLINE_WINDOW_DAYS * DAY; });
      if (window.length < 3) return;
      var span = (window[window.length - 1].time - window[0].time) / DAY;
      if (span < 1) return;
      var fit = linearFit(window);
      if (!fit) return;
      decline.push({band: band.band, rate: fit.slope, rateError: fit.slopeError, points: window.length, spanDays: span,
        fromDays: window[0].days, toDays: window[window.length - 1].days});
    });

    var colourBand = bands.filter(function (b) { return b.band === "r" && b.peak; })[0] || bands.filter(function (b) { return b.band === "g" && b.peak; })[0];
    var colour = colourBand ? colourNearPeak(detections, "g", "r", colourBand.peak.time) : null;
    if (colour) colour.referenceBand = colourBand.band;

    bands.forEach(function (band) { delete band.points; });
    return {
      reference: prepared.reference,
      episode: episode ? {fromDays: detections[0].days, toDays: detections[detections.length - 1].days, detections: detections.length,
        episodes: episodeList.length, leftOut: allDetections.length - detections.length} : null,
      counts: {
        points: everything.length, detections: allDetections.length, limits: everything.filter(function (p) { return p.kind === "limit"; }).length,
        forced: everything.filter(function (p) { return p.forced; }).length, converted: everything.filter(function (p) { return p.converted; }).length,
        duplicates: unique.duplicates, superseded: prepared.skipped.superseded, unplottable: prepared.skipped.unplottable,
      },
      bands: bands, peak: peak, rise: rise, decline: decline, colour: colour,
    };
  }

  // ------------------------------------------------------------------ axes
  // A symmetric logarithm keeps days before discovery, day 0 and years later on one axis.
  function symlog(days) { return days < 0 ? -Math.log10(1 - days) : Math.log10(1 + days); }
  function symlogInverse(value) { return value < 0 ? 1 - Math.pow(10, -value) : Math.pow(10, value) - 1; }
  function niceTicks(min, max, count) {
    if (!(max > min)) return [min];
    var raw = (max - min) / Math.max(1, count || 5), power = Math.pow(10, Math.floor(Math.log10(raw))), unit = raw / power;
    var step = (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10) * power, out = [];
    for (var value = Math.ceil(min / step) * step; value <= max + step * 1e-9; value += step) out.push(Number(value.toPrecision(12)));
    return out;
  }
  var LOG_TICKS = [-3000, -1000, -300, -100, -30, -10, -3, -1, 0, 1, 3, 10, 30, 100, 300, 1000, 3000, 10000];
  function dayTicks(min, max, logAxis, count) {
    if (!logAxis) return niceTicks(min, max, count || 6);
    var ticks = LOG_TICKS.filter(function (value) { return value >= min && value <= max; });
    while (ticks.length > (count || 7) + 1) {
      var thinner = ticks.filter(function (value, index) { return value === 0 || index % 2 === 0; });
      if (thinner.length === ticks.length) break;
      ticks = thinner;
    }
    return ticks;
  }

  var api = {
    toPoints: toPoints, uniqueDetections: uniqueDetections, episodes: episodes, chooseEpisode: chooseEpisode, summarise: summarise, linearFit: linearFit, colourNearPeak: colourNearPeak,
    symlog: symlog, symlogInverse: symlogInverse, niceTicks: niceTicks, dayTicks: dayTicks, zeroPoint: zeroPoint,
    DECLINE_WINDOW_DAYS: DECLINE_WINDOW_DAYS, EPISODE_GAP_DAYS: EPISODE_GAP_DAYS, SUPPORT_DAYS: SUPPORT_DAYS, SUPPORT_MAG: SUPPORT_MAG, choosePeak: choosePeak, COLOUR_PAIR_DAYS: COLOUR_PAIR_DAYS, COLOUR_NEAR_PEAK_DAYS: COLOUR_NEAR_PEAK_DAYS,
  };
  root.CTASLightcurve = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
