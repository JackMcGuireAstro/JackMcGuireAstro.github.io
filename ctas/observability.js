(function (root, factory) {
  "use strict";
  var model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  root.CTASObservability = model;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var D2R = Math.PI / 180;
  var R2D = 180 / Math.PI;

  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
  function normalizeDegrees(value) { return ((Number(value) % 360) + 360) % 360; }
  function julianDate(value) { return new Date(value).getTime() / 86400000 + 2440587.5; }
  function iso(value) { return new Date(value).toISOString().replace(/\.000Z$/, "Z"); }

  function gmstDegrees(value) {
    var jd = julianDate(value), t = (jd - 2451545.0) / 36525;
    return normalizeDegrees(280.46061837 + 360.98564736629 * (jd - 2451545.0) +
      0.000387933 * t * t - t * t * t / 38710000);
  }

  function altitudeDeg(raDeg, decDeg, value, latitudeDeg, longitudeDeg) {
    var hourAngle = normalizeDegrees(gmstDegrees(value) + Number(longitudeDeg) - Number(raDeg));
    if (hourAngle > 180) hourAngle -= 360;
    var lat = Number(latitudeDeg) * D2R, dec = Number(decDeg) * D2R, hour = hourAngle * D2R;
    return Math.asin(clamp(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(hour), -1, 1)) * R2D;
  }

  function eclipticToEquatorial(longitudeDeg, latitudeDeg, obliquityDeg) {
    var lon = longitudeDeg * D2R, lat = latitudeDeg * D2R, e = obliquityDeg * D2R;
    var x = Math.cos(lon) * Math.cos(lat);
    var y = Math.sin(lon) * Math.cos(lat) * Math.cos(e) - Math.sin(lat) * Math.sin(e);
    var z = Math.sin(lon) * Math.cos(lat) * Math.sin(e) + Math.sin(lat) * Math.cos(e);
    return {ra_deg: normalizeDegrees(Math.atan2(y, x) * R2D), dec_deg: Math.asin(clamp(z, -1, 1)) * R2D};
  }

  function sunRaDec(value) {
    var n = julianDate(value) - 2451545.0;
    var meanLongitude = normalizeDegrees(280.460 + 0.9856474 * n);
    var anomaly = normalizeDegrees(357.528 + 0.9856003 * n) * D2R;
    var longitude = meanLongitude + 1.915 * Math.sin(anomaly) + 0.020 * Math.sin(2 * anomaly);
    return eclipticToEquatorial(normalizeDegrees(longitude), 0, 23.439 - 0.0000004 * n);
  }

  // Low-precision lunar position (sufficient for a clearly labelled planning
  // display, never for telescope commanding or precision ephemerides).
  function moonRaDec(value) {
    var d = julianDate(value) - 2451543.5;
    var node = normalizeDegrees(125.1228 - 0.0529538083 * d) * D2R;
    var inclination = 5.1454 * D2R;
    var perigee = normalizeDegrees(318.0634 + 0.1643573223 * d) * D2R;
    var semiMajor = 60.2666;
    var eccentricity = 0.0549;
    var anomaly = normalizeDegrees(115.3654 + 13.0649929509 * d) * D2R;
    var eccentricAnomaly = anomaly + eccentricity * Math.sin(anomaly) * (1 + eccentricity * Math.cos(anomaly));
    var x = semiMajor * (Math.cos(eccentricAnomaly) - eccentricity);
    var y = semiMajor * Math.sqrt(1 - eccentricity * eccentricity) * Math.sin(eccentricAnomaly);
    var trueAnomaly = Math.atan2(y, x), radius = Math.sqrt(x * x + y * y);
    var lon = trueAnomaly + perigee;
    var xe = radius * (Math.cos(node) * Math.cos(lon) - Math.sin(node) * Math.sin(lon) * Math.cos(inclination));
    var ye = radius * (Math.sin(node) * Math.cos(lon) + Math.cos(node) * Math.sin(lon) * Math.cos(inclination));
    var ze = radius * Math.sin(lon) * Math.sin(inclination);
    var eclipticLongitude = normalizeDegrees(Math.atan2(ye, xe) * R2D);
    var eclipticLatitude = Math.atan2(ze, Math.sqrt(xe * xe + ye * ye)) * R2D;
    return eclipticToEquatorial(eclipticLongitude, eclipticLatitude, 23.4393 - 3.563e-7 * d);
  }

  function separationDeg(ra1, dec1, ra2, dec2) {
    var first = dec1 * D2R, second = dec2 * D2R, delta = (ra1 - ra2) * D2R;
    return Math.acos(clamp(Math.sin(first) * Math.sin(second) + Math.cos(first) * Math.cos(second) * Math.cos(delta), -1, 1)) * R2D;
  }

  function airmass(altitude) {
    if (altitude <= 0) return null;
    var z = 90 - altitude;
    return 1 / (Math.cos(z * D2R) + 0.50572 * Math.pow(96.07995 - z, -1.6364));
  }

  function evaluate(target, options) {
    options = options || {};
    var ra = Number(target && target.ra_deg), dec = Number(target && target.dec_deg);
    var latitude = Number(options.latitude_deg), longitude = Number(options.longitude_deg);
    if (![ra, dec, latitude, longitude].every(isFinite) || ra < 0 || ra >= 360 || dec < -90 || dec > 90 || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
      return {status: "INSUFFICIENT_DATA", reason: "A valid ICRS position and observer latitude/longitude are required.", rows: [], intervals: []};
    }
    var date = /^\d{4}-\d{2}-\d{2}$/.test(String(options.date || "")) ? String(options.date) : new Date().toISOString().slice(0, 10);
    var start = Date.parse(date + "T00:00:00Z"), requestedStep = Number(options.step_minutes || 10);
    if (!Number.isFinite(start) || !Number.isFinite(requestedStep)) {
      return {status: "INSUFFICIENT_DATA", reason: "A valid UTC date and numeric sampling interval are required.", rows: [], intervals: []};
    }
    var stepMinutes = clamp(requestedStep, 2, 60);
    var minAltitude = Number(options.min_altitude_deg === undefined ? 30 : options.min_altitude_deg);
    var maxAirmass = Number(options.max_airmass === undefined ? 3 : options.max_airmass);
    var twilight = Number(options.max_sun_altitude_deg === undefined ? -12 : options.max_sun_altitude_deg);
    var minMoon = Number(options.min_moon_separation_deg === undefined ? 20 : options.min_moon_separation_deg);
    if (![minAltitude, maxAirmass, twilight, minMoon].every(isFinite) ||
        minAltitude < -90 || minAltitude > 90 || maxAirmass < 1 ||
        twilight < -90 || twilight > 90 || minMoon < 0 || minMoon > 180) {
      return {status: "INSUFFICIENT_DATA", reason: "The altitude, airmass, twilight, and Moon-separation constraints must be valid numeric ranges.", rows: [], intervals: []};
    }
    var rows = [];
    for (var minute = 0; minute <= 1440; minute += stepMinutes) {
      var when = new Date(start + minute * 60000), sun = sunRaDec(when), moon = moonRaDec(when);
      var altitude = altitudeDeg(ra, dec, when, latitude, longitude);
      var air = airmass(altitude), sunAltitude = altitudeDeg(sun.ra_deg, sun.dec_deg, when, latitude, longitude);
      var moonSeparation = separationDeg(ra, dec, moon.ra_deg, moon.dec_deg);
      var passes = altitude >= minAltitude && air !== null && air <= maxAirmass && sunAltitude <= twilight && moonSeparation >= minMoon;
      rows.push({utc: iso(when), altitude_deg: altitude, airmass: air, sun_altitude_deg: sunAltitude,
        moon_separation_deg: moonSeparation, observable: passes});
    }
    var intervals = [], open = null;
    rows.forEach(function (row, index) {
      if (row.observable && !open) open = {start_utc: row.utc, start_index: index};
      if (open && (!row.observable || index === rows.length - 1)) {
        var endRow = row.observable && index === rows.length - 1 ? row : rows[Math.max(open.start_index, index - 1)];
        intervals.push({start_utc: open.start_utc, end_utc: endRow.utc,
          duration_minutes: Math.max(0, (Date.parse(endRow.utc) - Date.parse(open.start_utc)) / 60000)});
        open = null;
      }
    });
    return {status: "COMPLETE", method: "ctas-browser-observability-approx-1.0.0", date: date,
      observer: {latitude_deg: latitude, longitude_deg: longitude}, constraints: {min_altitude_deg: minAltitude,
        max_airmass: maxAirmass, max_sun_altitude_deg: twilight, min_moon_separation_deg: minMoon},
      rows: rows, intervals: intervals,
      claim_boundary: "Browser-local geometric planning estimate; no weather, dome, instrument, schedule, or telescope-command claim."};
  }

  return {airmass: airmass, altitudeDeg: altitudeDeg, evaluate: evaluate, gmstDegrees: gmstDegrees,
    moonRaDec: moonRaDec, separationDeg: separationDeg, sunRaDec: sunRaDec};
}));
