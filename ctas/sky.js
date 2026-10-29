/* CTAS sky tools (Framework v2 Track 1, part C, 2026-10-19).

   Adds to the dossier's existing "Sky at the reported position" panel and to the
   catalog page's Sky section, without moving or changing anything that was there:

   - Aladin Lite v3, pinned to one version from the CDS CDN the workbench already used
     (loaded only when a reader opens an atlas; the Aladin logo and link stay as the
     LGPL-3.0-or-later licence asks), with four background layers: DSS2 colour, the
     Legacy Surveys DR10 colour HiPS, Pan-STARRS DR1 colour and 2MASS colour. A layer
     that cannot be loaded falls back to DSS2 and says so.
   - the event marker, and for multimessenger records an approximate localisation
     region: a circle whose area equals the retained 90% sky area (or whose radius is
     the retained coordinate uncertainty), drawn and labelled as approximate until the
     collector publishes MOCs (Track 3). No MOC product exists in ctas/data today.
   - a printable finder chart: 2' and 10' JPEG cutouts fetched by the browser from the
     Legacy Surveys viewer (DR10, then DR9), then the Pan-STARRS fitscut service north
     of Dec -30, then CDS HiPS2FITS DSS2, with north/east arrows, crosshair, scale bar,
     name, ICRS coordinates and epoch; a print-friendly view opens in a new tab.
     Nothing is stored anywhere.
   - a Web SAMP "send to TOPCAT/Aladin" button (coord.pointAt.sky, and the all-events
     VOTable from the catalog page), with the note that it works in Chrome, Firefox and
     Edge and not in Safari.

   Every panel renders only when coordinates exist. Nothing here loads before a reader
   opens a panel: the dossier's DSS2 preview (workbench.js) is unchanged. */
(function () {
  "use strict";

  var ALADIN_VERSION = "3.6.1";
  var ALADIN_CDN = "https://aladin.cds.unistra.fr/AladinLite/api/v3/";
  var ALADIN_URL = ALADIN_CDN + ALADIN_VERSION + "/aladin.js";
  var ALADIN_FALLBACK_URL = ALADIN_CDN + "latest/aladin.js";
  var LAYERS = [
    {id: "dss2", label: "DSS2 colour", hips: "CDS/P/DSS2/color", decMin: -90, credit: "DSS2 (STScI/ESO) via CDS"},
    {id: "ls-dr10", label: "Legacy Surveys DR10", hips: "CDS/P/DESI-Legacy-Surveys/DR10/color", decMin: -90, credit: "DESI Legacy Imaging Surveys DR10 via CDS"},
    {id: "ps1", label: "Pan-STARRS DR1 colour", hips: "CDS/P/PanSTARRS/DR1/color-z-zg-g", decMin: -30, credit: "Pan-STARRS1 DR1 (STScI) via CDS"},
    {id: "2mass", label: "2MASS colour", hips: "CDS/P/2MASS/color", decMin: -90, credit: "2MASS (IPAC/UMass) via CDS"}
  ];
  var FINDER_SIZE = 512;
  var FINDER_FIELDS = [2, 10];
  var LEGACY_CUTOUT = "https://www.legacysurvey.org/viewer/jpeg-cutout";
  var PS1_FILENAMES = "https://ps1images.stsci.edu/cgi-bin/ps1filenames.py";
  var PS1_FITSCUT = "https://ps1images.stsci.edu/cgi-bin/fitscut.cgi";
  var HIPS2FITS = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits";
  var SAMP_NOTE = "Web SAMP works in Chrome, Firefox and Edge with a running hub (TOPCAT or Aladin); it does not work in Safari.";
  var state = {aladinPromise: null, aladinSource: null, atlasOpen: false, atlasInstance: null, atlasCatalog: null};

  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (character) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character];
    });
  }
  function finite(value) { return value !== null && value !== undefined && value !== "" && isFinite(Number(value)); }
  function coordinates(candidate) {
    if (!candidate || !finite(candidate.ra_deg) || !finite(candidate.dec_deg)) return null;
    var ra = Number(candidate.ra_deg), dec = Number(candidate.dec_deg);
    if (ra < 0 || ra >= 360 || dec < -90 || dec > 90) return null;
    return {ra: ra, dec: dec};
  }
  function sexagesimal(ra, dec) {
    if (window.CTASCatalogModel && window.CTASCatalogModel.sexagesimal) return window.CTASCatalogModel.sexagesimal(ra, dec);
    return Number(ra).toFixed(5) + " " + (dec >= 0 ? "+" : "") + Number(dec).toFixed(5);
  }

  // ------------------------------------------------------------ localisation
  // The approximate region drawn until MOCs are published: the newest active messenger
  // notice with a 90% sky area gives a circle of equal area; otherwise the retained
  // coordinate uncertainty gives its radius. Optical records without notices get none.
  function localisation(candidate) {
    var position = coordinates(candidate); if (!position) return null;
    var signals = ((candidate.follow_up || {}).messenger_signals || []).filter(function (row) {
      return row && !row.retracted && !row.superseded && finite(row.sky_area_90_sq_deg) && Number(row.sky_area_90_sq_deg) > 0;
    }).sort(function (a, b) { return text(b.observed_at || b.event_time).localeCompare(text(a.observed_at || a.event_time)); });
    if (signals.length) {
      var area = Number(signals[0].sky_area_90_sq_deg);
      return {radius_deg: Math.sqrt(area / Math.PI), basis: "90% sky area of " + area.toLocaleString() + " square degrees from the newest retained notice, drawn as a circle of equal area",
        area_sq_deg: area, skymap_url: /^https:\/\//.test(text(signals[0].skymap_url)) ? signals[0].skymap_url : null, approximate: true};
    }
    var messenger = text(candidate.primary_messenger).toLowerCase();
    var multimessenger = (candidate.record_role === "localization-region-alert") || (messenger && messenger !== "electromagnetic") ||
      Number((candidate.follow_up_counts || {}).messenger_signals || 0) > 0;
    if (multimessenger && finite(candidate.coordinate_error_arcsec) && Number(candidate.coordinate_error_arcsec) > 0) {
      return {radius_deg: Number(candidate.coordinate_error_arcsec) / 3600, basis: "retained coordinate uncertainty of " + Number(candidate.coordinate_error_arcsec).toLocaleString() + " arcseconds, drawn as the circle radius",
        area_sq_deg: null, skymap_url: null, approximate: true};
    }
    return null;
  }

  // ------------------------------------------------------------ Aladin Lite
  function loadAladinRuntime() {
    if (window.A && window.A.init) { state.aladinSource = state.aladinSource || "already loaded"; return Promise.resolve(window.A); }
    if (state.aladinPromise) return state.aladinPromise;
    function inject(url) {
      return new Promise(function (resolve, reject) {
        var script = document.createElement("script"); script.src = url; script.charset = "utf-8";
        script.onload = function () { if (window.A && window.A.init) resolve(window.A); else reject(new Error("the Aladin Lite runtime did not define A")); };
        script.onerror = function () { reject(new Error("could not load " + url)); };
        document.head.appendChild(script);
      });
    }
    state.aladinPromise = inject(ALADIN_URL).then(function (A) { state.aladinSource = "pinned " + ALADIN_VERSION; return A; }).catch(function () {
      // The pinned build is the contract; "latest" is only a stated fallback when the CDN
      // no longer serves that version, so the panel still works and says why.
      return inject(ALADIN_FALLBACK_URL).then(function (A) { state.aladinSource = "latest (pinned " + ALADIN_VERSION + " unavailable)"; return A; });
    }).then(function (A) { return A.init.then(function () { return A; }); }).catch(function (error) { state.aladinPromise = null; throw error; });
    return state.aladinPromise;
  }

  function hipsLayer(A, layer) {
    var make = A.HiPS || A.imageHiPS;
    if (typeof make !== "function") return layer.hips;
    try { return make(layer.hips, {name: layer.label}); } catch (_) { return layer.hips; }
  }

  function setLayer(A, aladin, layer, status, credit) {
    try {
      aladin.setBaseImageLayer(hipsLayer(A, layer));
      if (credit) credit.textContent = "Background: " + layer.credit + ". A blank field means the survey does not cover it.";
      return true;
    } catch (error) {
      try { aladin.setBaseImageLayer(hipsLayer(A, LAYERS[0])); } catch (_) { /* leave the current layer */ }
      if (status) status.textContent = layer.label + " could not be loaded (" + error.message + "); showing DSS2 colour instead.";
      return false;
    }
  }

  function layerButtons(dec) {
    return LAYERS.map(function (layer, index) {
      var covered = dec === null || dec === undefined || Number(dec) >= layer.decMin;
      return '<button type="button" data-sky-layer="' + esc(layer.id) + '" aria-pressed="' + (index === 0 ? "true" : "false") + '"' +
        (covered ? "" : ' title="Survey footprint ends at Dec ' + layer.decMin + '°"') + '>' + esc(layer.label) + (covered ? "" : " (outside footprint)") + '</button>';
    }).join("");
  }

  function atlasToolbar(dec, region) {
    return '<div class="ctas-sky-tools__layers" role="group" aria-label="Background survey layer"><span>Background</span>' + layerButtons(dec) + '</div>' +
      (region ? '<label class="ctas-sky-tools__region"><input type="checkbox" data-sky-region checked> Show the approximate localisation region (' + esc(region.basis) + '). Approximate until the collector publishes MOCs.' +
        (region.skymap_url ? ' <a href="' + esc(region.skymap_url) + '" target="_blank" rel="noopener">Provider sky map<span class="sr-only"> (opens in a new tab)</span></a>' : "") + '</label>' : "") +
      '<p class="ctas-sky-tools__credit" data-sky-credit>Background: ' + esc(LAYERS[0].credit) + '. Aladin Lite is CDS software (LGPL-3.0-or-later); its logo and link stay in the view.</p>';
  }

  function bindAtlasToolbar(host, A, aladin, status, overlay) {
    var credit = host.querySelector("[data-sky-credit]");
    Array.prototype.forEach.call(host.querySelectorAll("[data-sky-layer]"), function (button) {
      button.addEventListener("click", function () {
        var layer = LAYERS.filter(function (row) { return row.id === button.getAttribute("data-sky-layer"); })[0];
        if (!layer) return;
        var ok = setLayer(A, aladin, layer, status, credit);
        Array.prototype.forEach.call(host.querySelectorAll("[data-sky-layer]"), function (other) { other.setAttribute("aria-pressed", other === button && ok ? "true" : (ok ? "false" : (other.getAttribute("data-sky-layer") === "dss2" ? "true" : "false"))); });
      });
    });
    var regionToggle = host.querySelector("[data-sky-region]");
    if (regionToggle && overlay) regionToggle.addEventListener("change", function () {
      try { if (regionToggle.checked) overlay.show(); else overlay.hide(); } catch (_) { /* older runtime */ }
    });
  }

  // The dossier atlas: called by workbench.js's "Open interactive sky atlas" button.
  function openDossierAtlas(candidate, button) {
    var panel = button.closest(".ctas-aladin"), stage = panel.querySelector("[data-aladin-stage]"), status = panel.querySelector("[data-aladin-status]");
    var position = coordinates(candidate); if (!position) return;
    var fov = Number(panel.getAttribute("data-current-fov")) || .2;
    var region = localisation(candidate);
    button.disabled = true; stage.hidden = false;
    status.textContent = "Loading the CDS Aladin Lite runtime (pinned " + ALADIN_VERSION + ") and public sky imagery…";
    var tools = panel.querySelector("[data-sky-atlas-tools]");
    if (!tools) { tools = document.createElement("div"); tools.className = "ctas-sky-tools"; tools.setAttribute("data-sky-atlas-tools", ""); stage.parentNode.insertBefore(tools, stage); }
    tools.innerHTML = atlasToolbar(position.dec, region);
    loadAladinRuntime().then(function (A) {
      var aladin = A.aladin(stage, {target: position.ra.toFixed(7) + " " + position.dec.toFixed(7), fov: region ? Math.max(fov, region.radius_deg * 3) : fov,
        cooFrame: "ICRSd", survey: LAYERS[0].hips, showReticle: true, showCooGrid: true});
      var markerCatalog = A.catalog({name: "CTAS reported position", sourceSize: 14, color: "#ffd166"});
      markerCatalog.addSources([A.marker(position.ra, position.dec, {popupTitle: candidate.name, popupDesc: "Reported CTAS ICRS position"})]);
      aladin.addCatalog(markerCatalog);
      var overlay = null;
      if (region && A.graphicOverlay && A.circle) {
        overlay = A.graphicOverlay({name: "Approximate localisation", color: "#ff9b9b", lineWidth: 2});
        aladin.addOverlay(overlay);
        overlay.add(A.circle(position.ra, position.dec, region.radius_deg, {color: "#ff9b9b"}));
      }
      bindAtlasToolbar(tools, A, aladin, status, overlay);
      status.textContent = "Interactive CDS Aladin Lite (" + state.aladinSource + ") centred on the retained CTAS ICRS position" +
        (region ? "; the red circle is an approximate localisation region, not a published MOC" : "") + ". External layers retain their provider attribution.";
      button.hidden = true;
    }).catch(function (error) {
      status.textContent = "Aladin Lite could not be loaded (" + error.message + "). The coordinates, finder chart and source links remain usable.";
      button.disabled = false; stage.hidden = true;
    });
  }

  // ------------------------------------------------------------ finder chart
  function legacyCutoutUrl(ra, dec, fovArcmin, layer) {
    var pixscale = (fovArcmin * 60) / FINDER_SIZE;
    return LEGACY_CUTOUT + "?ra=" + ra.toFixed(6) + "&dec=" + dec.toFixed(6) + "&layer=" + layer + "&pixscale=" + pixscale.toFixed(4) + "&width=" + FINDER_SIZE + "&height=" + FINDER_SIZE;
  }
  function hips2fitsUrl(ra, dec, fovArcmin, hips) {
    return HIPS2FITS + "?hips=" + encodeURIComponent(hips) + "&width=" + FINDER_SIZE + "&height=" + FINDER_SIZE + "&fov=" + (fovArcmin / 60).toFixed(6) +
      "&projection=TAN&coordsys=icrs&ra=" + ra.toFixed(6) + "&dec=" + dec.toFixed(6) + "&format=jpg";
  }
  function ps1FilenamesUrl(ra, dec) { return PS1_FILENAMES + "?ra=" + ra.toFixed(6) + "&dec=" + dec.toFixed(6) + "&filters=grizy&type=stack&sep=comma"; }
  // The PS1 image list (ra, dec, filters) answers with one row per filter; a colour
  // cutout takes three of them (y, i, g or what is available) at 0.25"/pixel.
  function ps1CutoutUrl(ra, dec, fovArcmin, listing) {
    var rows = text(listing).trim().split(/\r?\n/).map(function (line) { return line.split(","); });
    if (rows.length < 2) return null;
    var header = rows[0], at = {};
    header.forEach(function (name, index) { at[name.trim()] = index; });
    if (at.filename === undefined || at.filter === undefined) return null;
    var byFilter = {};
    rows.slice(1).forEach(function (row) { if (row[at.filter]) byFilter[row[at.filter].trim()] = row[at.filename].trim(); });
    var order = ["y", "i", "g", "z", "r"], chosen = order.filter(function (band) { return byFilter[band]; });
    if (!chosen.length) return null;
    var size = Math.round(fovArcmin * 60 / 0.25);
    var params = "&format=jpeg&size=" + size + "&ra=" + ra.toFixed(6) + "&dec=" + dec.toFixed(6) + "&output_size=" + FINDER_SIZE + "&autoscale=99.5";
    if (chosen.length >= 3) return PS1_FITSCUT + "?red=" + byFilter[chosen[0]] + "&green=" + byFilter[chosen[1]] + "&blue=" + byFilter[chosen[2]] + params;
    return PS1_FITSCUT + "?red=" + byFilter[chosen[0]] + params;
  }
  // The ordered image sources for one field; each resolves to a URL or null.
  function finderSources(ra, dec, fovArcmin) {
    var sources = [
      {id: "ls-dr10", label: "Legacy Surveys DR10 (legacysurvey.org jpeg-cutout)", url: function () { return Promise.resolve(legacyCutoutUrl(ra, dec, fovArcmin, "ls-dr10")); }},
      {id: "ls-dr9", label: "Legacy Surveys DR9 (legacysurvey.org jpeg-cutout)", url: function () { return Promise.resolve(legacyCutoutUrl(ra, dec, fovArcmin, "ls-dr9")); }}
    ];
    if (dec > -30) sources.push({id: "ps1", label: "Pan-STARRS DR1 (STScI fitscut)", url: function () {
      return fetch(ps1FilenamesUrl(ra, dec), {mode: "cors"}).then(function (response) { if (!response.ok) throw new Error("HTTP " + response.status); return response.text(); })
        .then(function (listing) { return ps1CutoutUrl(ra, dec, fovArcmin, listing); }).catch(function () { return null; });
    }});
    sources.push({id: "dss2", label: "DSS2 colour (CDS HiPS2FITS)", url: function () { return Promise.resolve(hips2fitsUrl(ra, dec, fovArcmin, "CDS/P/DSS2/color")); }});
    return sources;
  }
  function scaleBar(fovArcmin) {
    var quarter = fovArcmin * 60 / 4, steps = [5, 10, 15, 20, 30, 60, 120, 180, 300, 600, 900];
    var arcsec = steps.filter(function (step) { return step <= quarter; }).pop() || steps[0];
    return {arcsec: arcsec, label: arcsec < 60 ? arcsec + "″" : (arcsec / 60) + "′"};
  }
  // North up, east left, as every source above draws a TAN image in ICRS.
  function finderOverlaySvg(fovArcmin, size) {
    size = size || FINDER_SIZE;
    var c = size / 2, perArcsec = size / (fovArcmin * 60), rin = size * .035, rout = size * .11;
    var ox = size * .88, oy = size * .18, arm = size * .1, head = size * .02, bar = scaleBar(fovArcmin), barPx = bar.arcsec * perArcsec;
    return '<svg class="ctas-finder__overlay" viewBox="0 0 ' + size + ' ' + size + '" aria-hidden="true" focusable="false">' +
      '<g class="ctas-finder__marks">' +
      '<line x1="' + c + '" y1="' + (c - rin) + '" x2="' + c + '" y2="' + (c - rout) + '"/><line x1="' + c + '" y1="' + (c + rin) + '" x2="' + c + '" y2="' + (c + rout) + '"/>' +
      '<line x1="' + (c - rin) + '" y1="' + c + '" x2="' + (c - rout) + '" y2="' + c + '"/><line x1="' + (c + rin) + '" y1="' + c + '" x2="' + (c + rout) + '" y2="' + c + '"/>' +
      '<line x1="' + ox + '" y1="' + oy + '" x2="' + ox + '" y2="' + (oy - arm) + '"/><path d="M' + ox + ',' + (oy - arm - head) + ' L' + (ox - head * .7) + ',' + (oy - arm + head * .4) + ' L' + (ox + head * .7) + ',' + (oy - arm + head * .4) + ' Z"/>' +
      '<text x="' + ox + '" y="' + (oy - arm - head - 6) + '" text-anchor="middle">N</text>' +
      '<line x1="' + ox + '" y1="' + oy + '" x2="' + (ox - arm) + '" y2="' + oy + '"/><path d="M' + (ox - arm - head) + ',' + oy + ' L' + (ox - arm + head * .4) + ',' + (oy - head * .7) + ' L' + (ox - arm + head * .4) + ',' + (oy + head * .7) + ' Z"/>' +
      '<text x="' + (ox - arm - head - 6) + '" y="' + (oy + 5) + '" text-anchor="end">E</text>' +
      '<line x1="' + (size * .06) + '" y1="' + (size * .93) + '" x2="' + (size * .06 + barPx) + '" y2="' + (size * .93) + '"/>' +
      '<text x="' + (size * .06 + barPx / 2) + '" y="' + (size * .93 - 8) + '" text-anchor="middle">' + bar.label + '</text>' +
      '</g></svg>';
  }
  function finderFigure(candidate, fovArcmin) {
    return '<figure class="ctas-finder__field" data-finder-field="' + fovArcmin + '"><div class="ctas-finder__frame">' +
      '<img data-finder-image alt="' + esc(fovArcmin + " arcminute finder field centred on " + candidate.name) + '" decoding="async" referrerpolicy="no-referrer" hidden>' +
      finderOverlaySvg(fovArcmin) + '<p class="ctas-finder__wait" data-finder-wait>Fetching the ' + fovArcmin + '′ field…</p></div>' +
      '<figcaption><strong>' + fovArcmin + '′ field</strong> · north up, east left · <span data-finder-source>image source pending</span></figcaption></figure>';
  }
  function finderPanel(candidate) {
    var position = coordinates(candidate); if (!position) return "";
    var epoch = (candidate.astro_evidence && candidate.astro_evidence.target && candidate.astro_evidence.target.coordinateEpoch) || "J2000";
    var toolHref = "tools/finder/?ra=" + encodeURIComponent(position.ra.toFixed(6)) + "&dec=" + encodeURIComponent(position.dec.toFixed(6)) + "&name=" + encodeURIComponent(candidate.name) + "&fov=10";
    return '<details class="ctas-finder" data-finder="' + esc(candidate.event_id) + '"><summary>Printable finder chart <small>2′ and 10′ fields · fetched when opened</small></summary><div class="ctas-finder__body">' +
      '<p class="ctas-finder__head"><strong>' + esc(candidate.name) + '</strong> · ICRS ' + esc(sexagesimal(position.ra, position.dec)) + ' (' + position.ra.toFixed(5) + ', ' + (position.dec >= 0 ? "+" : "") + position.dec.toFixed(5) + ') · epoch ' + esc(epoch) + '</p>' +
      '<div class="ctas-finder__fields">' + FINDER_FIELDS.map(function (fov) { return finderFigure(candidate, fov); }).join("") + '</div>' +
      '<div class="ctas-evidence-tools"><button type="button" data-finder-print disabled>Open print view</button><a href="' + esc(toolHref) + '" target="_blank" rel="noopener">Other surveys in the finder tool<span class="sr-only"> (opens in a new tab)</span></a></div>' +
      '<p class="ctas-finder__note" data-finder-status role="status" aria-live="polite">Images are requested by your browser from the Legacy Surveys viewer (DR10, then DR9), the Pan-STARRS cutout service north of Dec −30°, and CDS HiPS2FITS (DSS2) as the last fallback. Nothing is stored. The crosshair marks the catalog coordinate; a nearby star or galaxy is not automatically the transient or its host.</p>' +
      '</div></details>';
  }
  function loadFinderField(figure, candidate) {
    var position = coordinates(candidate), fov = Number(figure.getAttribute("data-finder-field"));
    var image = figure.querySelector("[data-finder-image]"), wait = figure.querySelector("[data-finder-wait]"), label = figure.querySelector("[data-finder-source]");
    var sources = finderSources(position.ra, position.dec, fov);
    return new Promise(function (resolve) {
      function attempt(index) {
        if (index >= sources.length) { wait.textContent = "No archival image could be fetched for this field."; label.textContent = "no image source answered"; figure.setAttribute("data-finder-state", "failed"); resolve(null); return; }
        var source = sources[index];
        source.url().then(function (url) {
          if (!url) { attempt(index + 1); return; }
          image.onload = function () { image.hidden = false; wait.hidden = true; label.textContent = source.label; figure.setAttribute("data-finder-state", "loaded"); figure.setAttribute("data-finder-source-id", source.id); resolve({source: source, url: url}); };
          image.onerror = function () { attempt(index + 1); };
          image.src = url;
        }).catch(function () { attempt(index + 1); });
      }
      attempt(0);
    });
  }
  function printDocument(candidate, fields) {
    var position = coordinates(candidate);
    var epoch = (candidate.astro_evidence && candidate.astro_evidence.target && candidate.astro_evidence.target.coordinateEpoch) || "J2000";
    var release = (window.CTASApp && window.CTASApp.getStatus && (window.CTASApp.getStatus().static_snapshot_verification || {}).content_release_id) || "";
    var figures = fields.map(function (field) {
      return '<figure><div class="frame"><img src="' + esc(field.url) + '" alt="' + esc(field.fov + " arcminute field") + '">' + finderOverlaySvg(field.fov) + '</div><figcaption>' + field.fov + '′ field · north up, east left · scale bar ' + esc(scaleBar(field.fov).label) + ' · ' + esc(field.label) + '</figcaption></figure>';
    }).join("");
    return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Finder chart: ' + esc(candidate.name) + '</title><style>' +
      'body{font:14px/1.4 system-ui,sans-serif;color:#000;background:#fff;margin:1.2cm}h1{font-size:20px;margin:0 0 .2em}p{margin:.2em 0}.fields{display:flex;gap:1cm;flex-wrap:wrap;margin-top:.6cm}figure{margin:0;width:9cm;break-inside:avoid}.frame{position:relative;width:9cm;height:9cm;border:1px solid #000;background:#000}.frame img{display:block;width:100%;height:100%;object-fit:cover}.frame svg{position:absolute;inset:0;width:100%;height:100%}.frame line,.frame path{stroke:#ff0;stroke-width:2;fill:#ff0}.frame text{fill:#ff0;font:bold 22px sans-serif;paint-order:stroke;stroke:#000;stroke-width:3px}figcaption{font-size:12px;margin-top:.2cm}footer{margin-top:.8cm;font-size:11px;color:#333}@media print{button{display:none}}' +
      '</style></head><body><h1>' + esc(candidate.name) + '</h1><p>ICRS ' + esc(sexagesimal(position.ra, position.dec)) + ' = ' + position.ra.toFixed(6) + ', ' + (position.dec >= 0 ? "+" : "") + position.dec.toFixed(6) + ' (degrees), epoch ' + esc(epoch) + '</p>' +
      '<p>' + esc([candidate.event_type, candidate.classification || "Unclassified", candidate.discovery_survey ? "discovered by " + candidate.discovery_survey : ""].filter(Boolean).join(" · ")) + '</p>' +
      '<p><button type="button" onclick="window.print()">Print</button></p><div class="fields">' + figures + '</div>' +
      '<footer>CTAS (Cowboy Transient Alert System, Jack McGuire) finder chart' + (release ? ', release ' + esc(release.slice(0, 16)) + '…' : '') + '. The crosshair marks the catalog coordinate; images are archival survey data from the named provider, fetched by your browser and not stored by CTAS. Stable event id ' + esc(candidate.event_id) + '.</footer></body></html>';
  }
  function openPrintView(candidate, panel) {
    var fields = [];
    Array.prototype.forEach.call(panel.querySelectorAll("[data-finder-field]"), function (figure) {
      var image = figure.querySelector("[data-finder-image]");
      if (figure.getAttribute("data-finder-state") === "loaded" && image.src) fields.push({fov: Number(figure.getAttribute("data-finder-field")), url: image.src, label: figure.querySelector("[data-finder-source]").textContent});
    });
    var status = panel.querySelector("[data-finder-status]");
    if (!fields.length) { if (status) status.textContent = "No field has loaded yet, so there is nothing to print."; return null; }
    var blob = new Blob([printDocument(candidate, fields)], {type: "text/html"}), url = URL.createObjectURL(blob);
    var opened = window.open(url, "_blank");
    if (!opened && status) status.textContent = "The print view was blocked by the browser; allow pop-ups for this page and try again.";
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
    return opened;
  }
  function startFinder(panel, candidate) {
    if (panel.getAttribute("data-finder-started")) return;
    panel.setAttribute("data-finder-started", "1");
    var printButton = panel.querySelector("[data-finder-print]");
    Promise.all(Array.prototype.map.call(panel.querySelectorAll("[data-finder-field]"), function (figure) { return loadFinderField(figure, candidate); })).then(function (results) {
      if (results.some(Boolean) && printButton) printButton.disabled = false;
    });
  }

  // ------------------------------------------------------------ Web SAMP
  function sampCall(method, params) {
    if (window.CTASWorkbench && window.CTASWorkbench.sampCall) return window.CTASWorkbench.sampCall(method, params);
    return Promise.reject(new Error("the workbench SAMP client did not load"));
  }
  function sampRegister() {
    return sampCall("samp.webhub.register", [{"samp.name": "CTAS public catalog"}]).then(function (raw) {
      var document_ = new DOMParser().parseFromString(raw, "application/xml"), key = null;
      Array.prototype.forEach.call(document_.querySelectorAll("member"), function (member) {
        if (member.querySelector("name") && member.querySelector("name").textContent === "samp.private-key") key = member.querySelector("value string") && member.querySelector("value string").textContent;
      });
      if (!key) throw new Error("the SAMP hub did not return a private registration key");
      return key;
    });
  }
  function sendPointAt(candidate, status) {
    var position = coordinates(candidate); if (!position) return Promise.resolve();
    if (status) status.textContent = "Contacting a SAMP Web Profile hub on this computer because you asked…";
    return sampRegister().then(function (key) {
      return sampCall("samp.hub.notifyAll", [key, {"samp.mtype": "coord.pointAt.sky", "samp.params": {ra: String(position.ra), dec: String(position.dec)}}]);
    }).then(function () { if (status) status.textContent = "Pointed the connected SAMP clients (TOPCAT, Aladin) at " + sexagesimal(position.ra, position.dec) + "."; })
      .catch(function (error) { if (status) status.textContent = "SAMP send was unavailable (" + error.message + "). " + SAMP_NOTE; });
  }
  function sampBlock(candidate) {
    if (!coordinates(candidate)) return "";
    return '<div class="ctas-sky-tools__samp"><div class="ctas-evidence-tools"><button type="button" data-samp-point="' + esc(candidate.event_id) + '">Send to TOPCAT/Aladin (SAMP)</button></div>' +
      '<p class="ctas-sky-tools__note">' + SAMP_NOTE + ' This sends coord.pointAt.sky for the retained position; nothing leaves your computer otherwise.</p><p data-samp-status role="status" aria-live="polite"></p></div>';
  }

  // ------------------------------------------------------------ dossier wiring
  function enhanceDossier(candidate) {
    var workspace = document.getElementById("candidate-workspace"); if (!workspace) return;
    var panel = workspace.querySelector("[data-sky-context]"); if (!panel || panel.querySelector("[data-sky-tools]")) return;
    if (!coordinates(candidate)) return;
    var tools = document.createElement("div"); tools.className = "ctas-sky-tools"; tools.setAttribute("data-sky-tools", candidate.event_id);
    tools.innerHTML = finderPanel(candidate) + sampBlock(candidate);
    panel.appendChild(tools);
  }

  // ------------------------------------------------------------ catalog atlas
  function plottedCandidates() {
    var rows = window.CTASApp && window.CTASApp.getSkyCandidates ? window.CTASApp.getSkyCandidates() : [];
    return rows.filter(coordinates);
  }
  function atlasSources(A, rows) {
    return rows.slice(0, 5000).map(function (candidate) {
      return A.source(Number(candidate.ra_deg), Number(candidate.dec_deg), {name: candidate.name, classification: candidate.classification || "Unclassified",
        score: Number(candidate.ctas_score || 0).toFixed(1), event_id: candidate.event_id});
    });
  }
  function renderCatalogAtlas(snapshotCandidates) {
    var section = document.getElementById("celestial-sphere"); if (!section || document.querySelector("[data-sky-atlas]")) return;
    if (!snapshotCandidates.some(coordinates)) return;
    var body = section.querySelector(".ctas-panel__body"); if (!body) return;
    var details = document.createElement("details"); details.className = "ctas-sky-atlas"; details.setAttribute("data-sky-atlas", "");
    details.innerHTML = '<summary>Interactive atlas of the plotted candidates <small>Aladin Lite · survey imagery loads only when opened</small></summary>' +
      '<div class="ctas-sky-atlas__body"><div class="ctas-sky-tools" data-sky-atlas-tools>' + atlasToolbar(null, null) + '</div>' +
      '<div class="ctas-sky-atlas__stage" data-sky-atlas-stage></div>' +
      '<p class="ctas-sky-tools__note" data-sky-atlas-status role="status" aria-live="polite">The plotted candidates of the current window and filters are drawn as markers; click one for its name and score.</p>' +
      '<div class="ctas-evidence-tools"><button type="button" data-samp-events>Send all-events VOTable to TOPCAT (SAMP)</button></div><p class="ctas-sky-tools__note">' + SAMP_NOTE + '</p><p data-samp-status role="status" aria-live="polite"></p></div>';
    var foot = body.querySelector(".ctas-sky__foot");
    if (foot && foot.nextSibling) body.insertBefore(details, foot.nextSibling); else body.appendChild(details);
    details.addEventListener("toggle", function () { if (details.open) openCatalogAtlas(details); else state.atlasOpen = false; });
  }
  function openCatalogAtlas(details) {
    state.atlasOpen = true;
    var stage = details.querySelector("[data-sky-atlas-stage]"), status = details.querySelector("[data-sky-atlas-status]"), tools = details.querySelector("[data-sky-atlas-tools]");
    if (state.atlasInstance) { refreshCatalogAtlas(); return; }
    status.textContent = "Loading the CDS Aladin Lite runtime (pinned " + ALADIN_VERSION + ")…";
    loadAladinRuntime().then(function (A) {
      var rows = plottedCandidates(), first = rows[0];
      var aladin = A.aladin(stage, {target: first ? Number(first.ra_deg).toFixed(5) + " " + Number(first.dec_deg).toFixed(5) : "0 0", fov: 180, cooFrame: "ICRSd", survey: LAYERS[0].hips, showReticle: false, showCooGrid: true});
      state.atlasInstance = {A: A, aladin: aladin};
      bindAtlasToolbar(tools, A, aladin, status, null);
      refreshCatalogAtlas();
    }).catch(function (error) { status.textContent = "Aladin Lite could not be loaded (" + error.message + "). The canvas sky map above remains usable."; state.atlasOpen = false; });
  }
  function refreshCatalogAtlas() {
    if (!state.atlasInstance || !state.atlasOpen) return;
    var A = state.atlasInstance.A, aladin = state.atlasInstance.aladin, rows = plottedCandidates();
    var status = document.querySelector("[data-sky-atlas-status]");
    try {
      if (state.atlasCatalog) aladin.removeLayer(state.atlasCatalog);
    } catch (_) { /* older runtime without removeLayer */ }
    state.atlasCatalog = A.catalog({name: "CTAS plotted candidates", sourceSize: 10, color: "#ffd166", shape: "circle", onClick: "showPopup"});
    state.atlasCatalog.addSources(atlasSources(A, rows));
    aladin.addCatalog(state.atlasCatalog);
    if (status) status.textContent = rows.length.toLocaleString() + " plotted candidates with coordinates drawn as markers (" + state.aladinSource + "). Click one for its name and score; the Sky window buttons and filters above change the set.";
  }

  function bind() {
    document.addEventListener("click", function (event) {
      var point = event.target.closest("[data-samp-point]");
      if (point) { var candidate = window.CTASWorkbench && window.CTASWorkbench.candidateById ? window.CTASWorkbench.candidateById(point.getAttribute("data-samp-point")) : null; var status = point.closest(".ctas-sky-tools__samp").querySelector("[data-samp-status]"); if (candidate) sendPointAt(candidate, status); else if (status) status.textContent = "The dossier record is not available for SAMP."; return; }
      if (event.target.closest("[data-samp-events]")) { var host = event.target.closest(".ctas-sky-atlas__body"), eventsStatus = host && host.querySelector("[data-samp-status]"); if (window.CTASWorkbench && window.CTASWorkbench.sendSamp) window.CTASWorkbench.sendSamp(eventsStatus); return; }
      if (event.target.closest("[data-sky-days]")) { window.setTimeout(refreshCatalogAtlas, 0); return; }
      var print = event.target.closest("[data-finder-print]");
      if (print) { var finder = print.closest("[data-finder]"); var target = window.CTASWorkbench && window.CTASWorkbench.candidateById ? window.CTASWorkbench.candidateById(finder.getAttribute("data-finder")) : null; if (target) openPrintView(target, finder); return; }
    });
    document.addEventListener("toggle", function (event) {
      var finder = event.target && event.target.matches && event.target.matches("[data-finder]") ? event.target : null;
      if (!finder || !finder.open) return;
      var candidate = window.CTASWorkbench && window.CTASWorkbench.candidateById ? window.CTASWorkbench.candidateById(finder.getAttribute("data-finder")) : null;
      if (candidate) startFinder(finder, candidate);
    }, true);
    window.addEventListener("ctas:candidate-opened", function (event) { enhanceDossier(event.detail.candidate); });
    window.addEventListener("ctas:snapshot", function (event) { renderCatalogAtlas(event.detail.candidates || []); });
    window.addEventListener("ctas:filters-changed", function () { refreshCatalogAtlas(); });
    window.addEventListener("ctas:catalog-loaded", function () { refreshCatalogAtlas(); });
  }

  bind();
  window.CTASSky = {
    ALADIN_VERSION: ALADIN_VERSION, ALADIN_URL: ALADIN_URL, LAYERS: LAYERS, SAMP_NOTE: SAMP_NOTE,
    coordinates: coordinates, localisation: localisation, finderSources: finderSources, legacyCutoutUrl: legacyCutoutUrl,
    ps1CutoutUrl: ps1CutoutUrl, hips2fitsUrl: hips2fitsUrl, scaleBar: scaleBar, finderOverlaySvg: finderOverlaySvg,
    finderPanel: finderPanel, printDocument: printDocument, openDossierAtlas: openDossierAtlas, loadAladinRuntime: loadAladinRuntime,
    enhanceDossier: enhanceDossier, renderCatalogAtlas: renderCatalogAtlas, layerButtons: layerButtons
  };
}());
