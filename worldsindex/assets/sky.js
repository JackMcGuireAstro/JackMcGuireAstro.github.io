/* WorldsIndex sky tools (Framework v2 Track 1, part D, 2026-10-20).
 *
 * A "Sky" panel on every object workspace that has coordinates, and an interactive atlas on the
 * Sky (catalog) page. Everything here is additive and driven by the release data already on the
 * page; nothing loads before a reader opens a panel, and nothing a reader does is stored anywhere.
 *
 *   - Aladin Lite v3 from the CDS CDN, pinned to one version (the same build the CTAS pages pin),
 *     with four background layers: DSS2 colour, the Legacy Surveys DR10 colour HiPS, Pan-STARRS DR1
 *     colour (north of Dec −30°) and 2MASS colour. A layer that cannot be loaded falls back to DSS2
 *     and says so. Aladin Lite is CDS software under LGPL-3.0-or-later; its logo and link stay in the
 *     view (WORLDSINDEX-AUTOMATION.md, "Sky panel").
 *   - the host marker at the catalog coordinate, and the other catalog objects within 5′ (planets of
 *     the same host and nearby hosts) as overlay markers, found in the catalog index already in the
 *     browser (client-side; no query leaves the page for this).
 *   - a printable finder chart at 2′ and 10′ fetched by the browser from the Legacy Surveys viewer
 *     (DR10, then DR9), the Pan-STARRS cutout service north of Dec −30°, and CDS HiPS2FITS (DSS2) as
 *     the last fallback, with north/east arrows, crosshair, scale bar, name, ICRS coordinates and
 *     epoch; a print-friendly view opens in a new tab.
 *   - Web SAMP: coord.pointAt.sky for the catalog position and table.load.votable with the object's
 *     rows (a VOTable built in the browser from the dossier's catalog values and packaged native rows,
 *     handed to the hub as a data: URL, also downloadable); on the catalog page the published objects
 *     VOTable export. Works in Chrome, Firefox and Edge with a running hub (TOPCAT, Aladin); not in
 *     Safari.
 *
 * Objects without coordinates (nine in the 2026-10 releases) get no panel: the tab stays hidden.
 */

export const ALADIN_VERSION = '3.6.1';
const ALADIN_CDN = 'https://aladin.cds.unistra.fr/AladinLite/api/v3/';
export const ALADIN_URL = `${ALADIN_CDN}${ALADIN_VERSION}/aladin.js`;
const ALADIN_FALLBACK_URL = `${ALADIN_CDN}latest/aladin.js`;
export const LAYERS = [
  { id: 'dss2', label: 'DSS2 colour', hips: 'CDS/P/DSS2/color', decMin: -90, credit: 'DSS2 (STScI/ESO) via CDS' },
  { id: 'ls-dr10', label: 'Legacy Surveys DR10', hips: 'CDS/P/DESI-Legacy-Surveys/DR10/color', decMin: -90, credit: 'DESI Legacy Imaging Surveys DR10 via CDS' },
  { id: 'ps1', label: 'Pan-STARRS DR1 colour', hips: 'CDS/P/PanSTARRS/DR1/color-z-zg-g', decMin: -30, credit: 'Pan-STARRS1 DR1 (STScI) via CDS' },
  { id: '2mass', label: '2MASS colour', hips: 'CDS/P/2MASS/color', decMin: -90, credit: '2MASS (IPAC/UMass) via CDS' },
];
export const NEARBY_RADIUS_ARCMIN = 5;
export const FINDER_SIZE = 512;
export const FINDER_FIELDS = [2, 10];
const LEGACY_CUTOUT = 'https://www.legacysurvey.org/viewer/jpeg-cutout';
const PS1_FILENAMES = 'https://ps1images.stsci.edu/cgi-bin/ps1filenames.py';
const PS1_FITSCUT = 'https://ps1images.stsci.edu/cgi-bin/fitscut.cgi';
const HIPS2FITS = 'https://alasky.cds.unistra.fr/hips-image-services/hips2fits';
export const SAMP_HUB_URL = 'http://localhost:21012/';
export const SAMP_NOTE = 'Web SAMP works in Chrome, Firefox and Edge with a running hub (TOPCAT or Aladin); it does not work in Safari.';
export const REPOSITORY = 'JackMcGuireAstro/JackMcGuireAstro.github.io';
export const PROBLEM_LABEL = 'data-problem';
const CATALOG_MARKER_CAP = 5000;

const runtime = { aladinPromise: null, aladinSource: null };

function esc(value) { return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function finite(value) { return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)); }

/** The catalog coordinate of an index/atlas record, or null when the release has none for it. */
export function coordinatesOf(object) {
  if (!object || !finite(object.raDeg) || !finite(object.decDeg)) return null;
  const ra = Number(object.raDeg), dec = Number(object.decDeg);
  if (ra < 0 || ra >= 360 || dec < -90 || dec > 90) return null;
  return { ra, dec };
}

export function sexagesimal(ra, dec) {
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  const h = ra / 15, hh = Math.floor(h), mm = Math.floor((h - hh) * 60), ss = ((h - hh) * 60 - mm) * 60;
  const sign = dec < 0 ? '−' : '+', ad = Math.abs(dec), dd = Math.floor(ad), dm = Math.floor((ad - dd) * 60), ds = ((ad - dd) * 60 - dm) * 60;
  return `${pad(hh)}:${pad(mm)}:${pad(ss.toFixed(2), 5)} ${sign}${pad(dd)}:${pad(dm)}:${pad(ds.toFixed(1), 4)}`;
}

/** Great-circle separation in arcminutes (haversine; exact enough at 5′). */
export function separationArcmin(a, b) {
  const rad = Math.PI / 180;
  const dRa = (b.ra - a.ra) * rad, dDec = (b.dec - a.dec) * rad;
  const h = Math.sin(dDec / 2) ** 2 + Math.cos(a.dec * rad) * Math.cos(b.dec * rad) * Math.sin(dRa / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) * 60 / rad;
}

/**
 * Catalog objects within `radiusArcmin` of the centre, nearest first, the centre object itself left
 * out. Same-host companions are flagged so the overlay can say "same host" rather than "nearby".
 */
export function nearbyObjects(center, objects, radiusArcmin = NEARBY_RADIUS_ARCMIN, { excludeId = null, hostName = null, limit = 200 } = {}) {
  const found = [];
  const decMin = center.dec - radiusArcmin / 60, decMax = center.dec + radiusArcmin / 60;
  for (const object of objects || []) {
    if (!object || object.objectId === excludeId) continue;
    const dec = Number(object.decDeg);
    if (!Number.isFinite(dec) || dec < decMin || dec > decMax) continue; // cheap declination band first
    const position = coordinatesOf(object);
    if (!position) continue;
    const separation = separationArcmin(center, position);
    if (separation <= radiusArcmin) found.push({ object, separationArcmin: separation, sameHost: hostName !== null && object.hostName === hostName });
  }
  found.sort((a, b) => a.separationArcmin - b.separationArcmin || String(a.object.name).localeCompare(String(b.object.name)));
  return found.slice(0, limit);
}

// ------------------------------------------------------------------ Aladin Lite
export function loadAladinRuntime(doc = document) {
  if (window.A && window.A.init) { runtime.aladinSource = runtime.aladinSource || 'already loaded'; return Promise.resolve(window.A); }
  if (runtime.aladinPromise) return runtime.aladinPromise;
  const inject = (url) => new Promise((resolve, reject) => {
    const script = doc.createElement('script'); script.src = url; script.charset = 'utf-8';
    script.onload = () => { if (window.A && window.A.init) resolve(window.A); else reject(new Error('the Aladin Lite runtime did not define A')); };
    script.onerror = () => reject(new Error(`could not load ${url}`));
    doc.head.appendChild(script);
  });
  runtime.aladinPromise = inject(ALADIN_URL).then((A) => { runtime.aladinSource = `pinned ${ALADIN_VERSION}`; return A; }).catch(() =>
    // The pinned build is the contract; "latest" is only a stated fallback when the CDN no longer serves it.
    inject(ALADIN_FALLBACK_URL).then((A) => { runtime.aladinSource = `latest (pinned ${ALADIN_VERSION} unavailable)`; return A; }))
    .then((A) => A.init.then(() => A)).catch((error) => { runtime.aladinPromise = null; throw error; });
  return runtime.aladinPromise;
}
function hipsLayer(A, layer) {
  const make = A.HiPS || A.imageHiPS;
  if (typeof make !== 'function') return layer.hips;
  try { return make(layer.hips, { name: layer.label }); } catch { return layer.hips; }
}
function setLayer(A, aladin, layer, status, credit) {
  try {
    aladin.setBaseImageLayer(hipsLayer(A, layer));
    if (credit) credit.textContent = `Background: ${layer.credit}. A blank field means the survey does not cover it.`;
    return true;
  } catch (error) {
    try { aladin.setBaseImageLayer(hipsLayer(A, LAYERS[0])); } catch { /* keep the current layer */ }
    if (status) status.textContent = `${layer.label} could not be loaded (${error.message}); showing DSS2 colour instead.`;
    return false;
  }
}
export function layerButtons(dec) {
  return LAYERS.map((layer, index) => {
    const covered = dec === null || dec === undefined || Number(dec) >= layer.decMin;
    return `<button type="button" data-sky-layer="${esc(layer.id)}" aria-pressed="${index === 0 ? 'true' : 'false'}"${covered ? '' : ` title="Survey footprint ends at Dec ${layer.decMin}°"`}>${esc(layer.label)}${covered ? '' : ' (outside footprint)'}</button>`;
  }).join('');
}
function atlasToolbar(dec) {
  return `<div class="sky-tools-layers" role="group" aria-label="Background survey layer"><span>Background</span>${layerButtons(dec)}</div>` +
    `<p class="fineprint sky-tools-credit" data-sky-credit>Background: ${esc(LAYERS[0].credit)}. Aladin Lite is CDS software (LGPL-3.0-or-later); its logo and link stay in the view.</p>`;
}
function bindLayerButtons(host, A, aladin, status) {
  const credit = host.querySelector('[data-sky-credit]');
  for (const button of host.querySelectorAll('[data-sky-layer]')) {
    button.addEventListener('click', () => {
      const layer = LAYERS.find((row) => row.id === button.dataset.skyLayer);
      if (!layer) return;
      const ok = setLayer(A, aladin, layer, status, credit);
      for (const other of host.querySelectorAll('[data-sky-layer]')) other.setAttribute('aria-pressed', String(ok ? other === button : other.dataset.skyLayer === 'dss2'));
    });
  }
}

// ------------------------------------------------------------------ finder chart
export function legacyCutoutUrl(ra, dec, fovArcmin, layer) {
  const pixscale = (fovArcmin * 60) / FINDER_SIZE;
  return `${LEGACY_CUTOUT}?ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&layer=${layer}&pixscale=${pixscale.toFixed(4)}&width=${FINDER_SIZE}&height=${FINDER_SIZE}`;
}
export function hips2fitsUrl(ra, dec, fovArcmin, hips) {
  return `${HIPS2FITS}?hips=${encodeURIComponent(hips)}&width=${FINDER_SIZE}&height=${FINDER_SIZE}&fov=${(fovArcmin / 60).toFixed(6)}&projection=TAN&coordsys=icrs&ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&format=jpg`;
}
export function ps1FilenamesUrl(ra, dec) { return `${PS1_FILENAMES}?ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&filters=grizy&type=stack&sep=comma`; }
/** The PS1 image list answers one row per filter; a colour cutout takes three of them (y, i, g or what exists) at 0.25″/pixel. */
export function ps1CutoutUrl(ra, dec, fovArcmin, listing) {
  const rows = String(listing ?? '').trim().split(/\r?\n/).map((line) => line.split(','));
  if (rows.length < 2) return null;
  const at = {}; rows[0].forEach((name, index) => { at[name.trim()] = index; });
  if (at.filename === undefined || at.filter === undefined) return null;
  const byFilter = {};
  for (const row of rows.slice(1)) if (row[at.filter]) byFilter[row[at.filter].trim()] = row[at.filename].trim();
  const chosen = ['y', 'i', 'g', 'z', 'r'].filter((band) => byFilter[band]);
  if (!chosen.length) return null;
  const size = Math.round(fovArcmin * 60 / 0.25);
  const params = `&format=jpeg&size=${size}&ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&output_size=${FINDER_SIZE}&autoscale=99.5`;
  if (chosen.length >= 3) return `${PS1_FITSCUT}?red=${byFilter[chosen[0]]}&green=${byFilter[chosen[1]]}&blue=${byFilter[chosen[2]]}${params}`;
  return `${PS1_FITSCUT}?red=${byFilter[chosen[0]]}${params}`;
}
/** The ordered image sources for one field; each `url()` resolves to a URL or null. */
export function finderSources(ra, dec, fovArcmin, fetchImpl = (...args) => fetch(...args)) {
  const sources = [
    { id: 'ls-dr10', label: 'Legacy Surveys DR10 (legacysurvey.org jpeg-cutout)', url: () => Promise.resolve(legacyCutoutUrl(ra, dec, fovArcmin, 'ls-dr10')) },
    { id: 'ls-dr9', label: 'Legacy Surveys DR9 (legacysurvey.org jpeg-cutout)', url: () => Promise.resolve(legacyCutoutUrl(ra, dec, fovArcmin, 'ls-dr9')) },
  ];
  if (dec > -30) sources.push({ id: 'ps1', label: 'Pan-STARRS DR1 (STScI fitscut)', url: () => fetchImpl(ps1FilenamesUrl(ra, dec), { mode: 'cors' })
    .then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.text(); })
    .then((listing) => ps1CutoutUrl(ra, dec, fovArcmin, listing)).catch(() => null) });
  sources.push({ id: 'dss2', label: 'DSS2 colour (CDS HiPS2FITS)', url: () => Promise.resolve(hips2fitsUrl(ra, dec, fovArcmin, 'CDS/P/DSS2/color')) });
  return sources;
}
export function scaleBar(fovArcmin) {
  const quarter = fovArcmin * 60 / 4, steps = [5, 10, 15, 20, 30, 60, 120, 180, 300, 600, 900];
  const arcsec = steps.filter((step) => step <= quarter).pop() || steps[0];
  return { arcsec, label: arcsec < 60 ? `${arcsec}″` : `${arcsec / 60}′` };
}
/** North up, east left: every source above draws a TAN image in ICRS. */
export function finderOverlaySvg(fovArcmin, size = FINDER_SIZE) {
  const c = size / 2, perArcsec = size / (fovArcmin * 60), rin = size * .035, rout = size * .11;
  const ox = size * .88, oy = size * .18, arm = size * .1, head = size * .02, bar = scaleBar(fovArcmin), barPx = bar.arcsec * perArcsec;
  return `<svg class="finder-overlay" viewBox="0 0 ${size} ${size}" aria-hidden="true" focusable="false"><g class="finder-marks">` +
    `<line x1="${c}" y1="${c - rin}" x2="${c}" y2="${c - rout}"/><line x1="${c}" y1="${c + rin}" x2="${c}" y2="${c + rout}"/>` +
    `<line x1="${c - rin}" y1="${c}" x2="${c - rout}" y2="${c}"/><line x1="${c + rin}" y1="${c}" x2="${c + rout}" y2="${c}"/>` +
    `<line x1="${ox}" y1="${oy}" x2="${ox}" y2="${oy - arm}"/><path d="M${ox},${oy - arm - head} L${ox - head * .7},${oy - arm + head * .4} L${ox + head * .7},${oy - arm + head * .4} Z"/>` +
    `<text x="${ox}" y="${oy - arm - head - 6}" text-anchor="middle">N</text>` +
    `<line x1="${ox}" y1="${oy}" x2="${ox - arm}" y2="${oy}"/><path d="M${ox - arm - head},${oy} L${ox - arm + head * .4},${oy - head * .7} L${ox - arm + head * .4},${oy + head * .7} Z"/>` +
    `<text x="${ox - arm - head - 6}" y="${oy + 5}" text-anchor="end">E</text>` +
    `<line x1="${size * .06}" y1="${size * .93}" x2="${size * .06 + barPx}" y2="${size * .93}"/>` +
    `<text x="${size * .06 + barPx / 2}" y="${size * .93 - 8}" text-anchor="middle">${bar.label}</text></g></svg>`;
}
function finderFigure(object, fovArcmin) {
  return `<figure class="finder-field" data-finder-field="${fovArcmin}"><div class="finder-frame">` +
    `<img data-finder-image alt="${esc(`${fovArcmin} arcminute finder field centred on ${object.name}`)}" decoding="async" referrerpolicy="no-referrer" hidden>` +
    `${finderOverlaySvg(fovArcmin)}<p class="finder-wait" data-finder-wait>Fetching the ${fovArcmin}′ field…</p></div>` +
    `<figcaption><strong>${fovArcmin}′ field</strong> · north up, east left · <span data-finder-source>image source pending</span></figcaption></figure>`;
}
export function finderPanelHtml(object, position) {
  return `<details class="sky-finder" data-finder><summary>Printable finder chart <small>2′ and 10′ fields · fetched when opened</small></summary><div class="sky-finder-body">` +
    `<p class="sky-finder-head"><strong>${esc(object.name)}</strong> · ICRS ${esc(sexagesimal(position.ra, position.dec))} (${position.ra.toFixed(5)}, ${position.dec >= 0 ? '+' : ''}${position.dec.toFixed(5)}) · epoch J2000</p>` +
    `<div class="sky-finder-fields">${FINDER_FIELDS.map((fov) => finderFigure(object, fov)).join('')}</div>` +
    `<div class="sky-tools-actions"><button class="button" type="button" data-finder-print disabled>Open print view</button></div>` +
    `<p class="fineprint" data-finder-status role="status" aria-live="polite">Images are requested by your browser from the Legacy Surveys viewer (DR10, then DR9), the Pan-STARRS cutout service north of Dec −30°, and CDS HiPS2FITS (DSS2) as the last fallback. Nothing is stored. The crosshair marks the catalog coordinate of the host; the planet is not resolved in these images.</p>` +
    `</div></details>`;
}
function loadFinderField(figure, position, fetchImpl) {
  const fov = Number(figure.dataset.finderField);
  const image = figure.querySelector('[data-finder-image]'), wait = figure.querySelector('[data-finder-wait]'), label = figure.querySelector('[data-finder-source]');
  const sources = finderSources(position.ra, position.dec, fov, fetchImpl);
  return new Promise((resolve) => {
    const attempt = (index) => {
      if (index >= sources.length) { wait.textContent = 'No archival image could be fetched for this field.'; label.textContent = 'no image source answered'; figure.dataset.finderState = 'failed'; resolve(null); return; }
      const source = sources[index];
      source.url().then((url) => {
        if (!url) { attempt(index + 1); return; }
        image.onload = () => { image.hidden = false; wait.hidden = true; label.textContent = source.label; figure.dataset.finderState = 'loaded'; figure.dataset.finderSourceId = source.id; resolve({ source, url }); };
        image.onerror = () => attempt(index + 1);
        image.src = url;
      }).catch(() => attempt(index + 1));
    };
    attempt(0);
  });
}
export function printDocument(object, position, fields, release) {
  const figures = fields.map((field) => `<figure><div class="frame"><img src="${esc(field.url)}" alt="${esc(`${field.fov} arcminute field`)}">${finderOverlaySvg(field.fov)}</div><figcaption>${field.fov}′ field · north up, east left · scale bar ${esc(scaleBar(field.fov).label)} · ${esc(field.label)}</figcaption></figure>`).join('');
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Finder chart: ${esc(object.name)}</title><style>` +
    'body{font:14px/1.4 system-ui,sans-serif;color:#000;background:#fff;margin:1.2cm}h1{font-size:20px;margin:0 0 .2em}p{margin:.2em 0}.fields{display:flex;gap:1cm;flex-wrap:wrap;margin-top:.6cm}figure{margin:0;width:9cm;break-inside:avoid}.frame{position:relative;width:9cm;height:9cm;border:1px solid #000;background:#000}.frame img{display:block;width:100%;height:100%;object-fit:cover}.frame svg{position:absolute;inset:0;width:100%;height:100%}.frame line,.frame path{stroke:#ff0;stroke-width:2;fill:#ff0}.frame text{fill:#ff0;font:bold 22px sans-serif;paint-order:stroke;stroke:#000;stroke-width:3px}figcaption{font-size:12px;margin-top:.2cm}footer{margin-top:.8cm;font-size:11px;color:#333}@media print{button{display:none}}' +
    `</style></head><body><h1>${esc(object.name)}</h1><p>Host ${esc(object.hostName || 'not supplied')} · ICRS ${esc(sexagesimal(position.ra, position.dec))} = ${position.ra.toFixed(6)}, ${position.dec >= 0 ? '+' : ''}${position.dec.toFixed(6)} (degrees), epoch J2000</p>` +
    `<p>${esc([object.normalizedStatus, object.methodCode, object.discoveryYear ? `discovered ${object.discoveryYear}` : ''].filter(Boolean).join(' · '))}</p>` +
    `<p><button type="button" onclick="window.print()">Print</button></p><div class="fields">${figures}</div>` +
    `<footer>WorldsIndex (Jack McGuire) finder chart${release ? `, release ${esc(String(release).slice(0, 16))}…` : ''}. The crosshair marks the catalog coordinate of the host star; images are archival survey data from the named provider, fetched by your browser and not stored by WorldsIndex. Stable object id ${esc(object.objectId)}.</footer></body></html>`;
}

// ------------------------------------------------------------------ Web SAMP
function xmlEsc(value) { return String(value ?? '').replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c])); }
function xmlRpcValue(value) {
  if (typeof value === 'string') return `<value><string>${xmlEsc(value)}</string></value>`;
  if (Array.isArray(value)) return `<value><array><data>${value.map(xmlRpcValue).join('')}</data></array></value>`;
  if (value && typeof value === 'object') return `<value><struct>${Object.keys(value).map((key) => `<member><name>${xmlEsc(key)}</name>${xmlRpcValue(value[key])}</member>`).join('')}</struct></value>`;
  return xmlRpcValue(String(value ?? ''));
}
export function sampEnvelope(method, params) {
  return `<?xml version="1.0"?><methodCall><methodName>${xmlEsc(method)}</methodName><params>${params.map((value) => `<param>${xmlRpcValue(value)}</param>`).join('')}</params></methodCall>`;
}
function sampCall(method, params, fetchImpl) {
  return fetchImpl(SAMP_HUB_URL, { method: 'POST', headers: { 'Content-Type': 'text/xml' }, body: sampEnvelope(method, params) })
    .then((response) => { if (!response.ok) throw new Error(`hub returned HTTP ${response.status}`); return response.text(); });
}
export function sampPrivateKey(raw) {
  const parsed = new DOMParser().parseFromString(raw, 'application/xml');
  for (const member of parsed.querySelectorAll('member')) {
    if (member.querySelector('name')?.textContent === 'samp.private-key') return member.querySelector('value string')?.textContent || null;
  }
  return null;
}
function sampRegister(fetchImpl) {
  return sampCall('samp.webhub.register', [{ 'samp.name': 'WorldsIndex' }], fetchImpl).then((raw) => {
    const key = sampPrivateKey(raw);
    if (!key) throw new Error('the SAMP hub did not return a private registration key');
    return key;
  });
}
export function sendPointAt(position, status, fetchImpl = (...args) => fetch(...args)) {
  if (status) status.textContent = 'Contacting a SAMP Web Profile hub on this computer because you asked…';
  return sampRegister(fetchImpl)
    .then((key) => sampCall('samp.hub.notifyAll', [key, { 'samp.mtype': 'coord.pointAt.sky', 'samp.params': { ra: String(position.ra), dec: String(position.dec) } }], fetchImpl))
    .then(() => { if (status) status.textContent = `Pointed the connected SAMP clients (TOPCAT, Aladin) at ${sexagesimal(position.ra, position.dec)}.`; })
    .catch((error) => { if (status) status.textContent = `SAMP send was unavailable (${error.message}). ${SAMP_NOTE}`; });
}
export function sendVotable(url, name, status, fetchImpl = (...args) => fetch(...args), { fallbackHref = null } = {}) {
  if (status) status.textContent = 'Contacting a SAMP Web Profile hub on this computer because you asked…';
  return sampRegister(fetchImpl)
    .then((key) => sampCall('samp.hub.notifyAll', [key, { 'samp.mtype': 'table.load.votable', 'samp.params': { url, name } }], fetchImpl))
    .then(() => { if (status) status.textContent = `Sent "${name}" to the running SAMP hub as table.load.votable.${url.startsWith('data:') ? ' The table travels as a data: URL; if your client reports it cannot open one, use the download beside this button and open the file.' : ''}`; })
    .catch((error) => { if (status) status.innerHTML = `SAMP send was unavailable (${esc(error.message)}). ${esc(SAMP_NOTE)}${fallbackHref ? ` <a href="${esc(fallbackHref)}" download>Download the VOTable instead</a>.` : ''}`; });
}

// ------------------------------------------------------------------ the object's rows as a VOTable
const POPULATION_UNITS = { orbitalPeriodDays: 'd', radiusEarth: 'Rgeo', massEarth: 'Mgeo', minimumMassEarth: 'Mgeo', estimatedMassEarth: 'Mgeo', eccentricity: '', equilibriumTemperatureK: 'K', insolationEarth: '' };
/**
 * One row per value this dossier carries: the release's selected catalog values (with their source
 * record) and every non-empty field of every packaged native row. Columns mirror the published
 * measurements export (object id, source, record, record type, column, value, unit, reference);
 * values stay as the source states them, nothing converted or averaged.
 */
export function objectVotableRows(object, records = []) {
  const rows = [];
  for (const [key, value] of Object.entries(object.population || {})) {
    if (!value || value.value === null || value.value === undefined) continue;
    rows.push({ object_id: object.objectId, name: object.name, host_name: object.hostName || '', source_id: value.sourceId || '', source_record_id: value.sourceRecordId || '', record_type: 'release selection', column: key, value: String(value.value), unit: value.unit || POPULATION_UNITS[key] || '', limit_type: value.limitType || 'NONE', reference: '' });
  }
  for (const record of records) {
    const reference = record.reference?.label || '';
    for (const [column, raw] of Object.entries(record.values || {})) {
      if (raw === null || raw === undefined || raw === '') continue;
      rows.push({ object_id: object.objectId, name: object.name, host_name: object.hostName || '', source_id: record.sourceId || '', source_record_id: record.sourceRecordId || '', record_type: record.recordType || '', column, value: String(raw), unit: '', limit_type: '', reference: reference || record.references?.[column]?.label || '' });
    }
  }
  return rows;
}
export function objectVotable(object, records = [], manifest = null) {
  const rows = objectVotableRows(object, records);
  const fields = [
    ['object_id', 'char', 'meta.id;meta.main', 'Stable WorldsIndex object id'], ['name', 'char', 'meta.id', 'Object name in this release'], ['host_name', 'char', 'meta.id.parent', 'Host star name'],
    ['source_id', 'char', 'meta.ref', 'Declared source id'], ['source_record_id', 'char', 'meta.id', 'Source record id'], ['record_type', 'char', 'meta.code', 'Row kind'],
    ['column', 'char', 'meta.id', "The source's own column or the release selection key"], ['value', 'char', 'meta.record', "Value as the source states it"], ['unit', 'char', 'meta.unit', 'Unit where the release knows it'],
    ['limit_type', 'char', 'meta.code', 'NONE, UPPER_LIMIT or LOWER_LIMIT for release selections'], ['reference', 'char', 'meta.bib', 'Reference label where the source states one'],
  ];
  const description = `WorldsIndex rows for ${object.name} (${object.objectId}): the release's selected catalog values and every packaged native row, one row per value, as the sources state them. Release atlas ${manifest?.atlasGeneratedAt || 'unknown'}, atlas SHA-256 ${manifest?.atlasSha256 || 'unknown'}. Not a merged or averaged table.`;
  return `<?xml version="1.0" encoding="UTF-8"?>\n<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3"><RESOURCE type="results"><DESCRIPTION>${xmlEsc(description)}</DESCRIPTION><TABLE name="worldsindex_${xmlEsc(object.objectId)}" nrows="${rows.length}">` +
    fields.map(([name, datatype, ucd, text]) => `<FIELD name="${name}" datatype="${datatype}" arraysize="*" ucd="${ucd}"><DESCRIPTION>${xmlEsc(text)}</DESCRIPTION></FIELD>`).join('') +
    `<DATA><TABLEDATA>${rows.map((row) => `<TR>${fields.map(([name]) => `<TD>${xmlEsc(row[name])}</TD>`).join('')}</TR>`).join('')}</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>\n`;
}
export function votableDataUrl(votable) {
  const bytes = new TextEncoder().encode(votable);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:application/x-votable+xml;base64,${btoa(binary)}`;
}

// ------------------------------------------------------------------ report a problem
/**
 * A prefilled GitHub issue on the website repository: title "<name> (<object id>)", label
 * data-problem (the deploy workflow keeps the label), body with the stable object id, the atlas
 * projection id, the release checksum and the panel. Nothing is sent until the reader posts it.
 */
export function reportProblemUrl(object, { projectionId = null, manifest = null, panel = null, pageUrl = null } = {}) {
  const body = [
    '<!-- Thank you. Keep the lines below so the report can be matched to the exact release. -->',
    `Stable object id: ${object.objectId}`, `Name shown: ${object.name}`, `Host: ${object.hostName || 'not supplied'}`,
    `Atlas projection id: ${projectionId || 'unknown'}`, `Release checksum (atlas SHA-256): ${manifest?.atlasSha256 || 'unknown'}`,
    `Release built: ${manifest?.generatedAt || 'unknown'} (atlas ${manifest?.atlasGeneratedAt || 'unknown'})`,
    `Panel: ${panel || 'whole dossier'}`, `Dossier: ${pageUrl || ''}`, '', 'What is wrong (the value you expected, the source that says so):', '',
  ].join('\n');
  return `https://github.com/${REPOSITORY}/issues/new?title=${encodeURIComponent(`${object.name} (${object.objectId})`)}&labels=${encodeURIComponent(PROBLEM_LABEL)}&body=${encodeURIComponent(body)}`;
}

// ------------------------------------------------------------------ object panel
function markerCatalog(A, name, color, size) { return A.catalog({ name, sourceSize: size, color, shape: 'circle', onClick: 'showPopup' }); }
function describeNearby(nearby) {
  if (!nearby.length) return `No other catalog object lies within ${NEARBY_RADIUS_ARCMIN}′ of this position in the release index.`;
  const same = nearby.filter((item) => item.sameHost).length, other = nearby.length - same;
  return `${nearby.length} other catalog object${nearby.length === 1 ? '' : 's'} within ${NEARBY_RADIUS_ARCMIN}′: ${same} on the same host, ${other} on other hosts (release index; a near neighbour is not a physical association).`;
}
export function objectSkyHtml(object, position, nearby) {
  const rows = nearby.slice(0, 40).map((item) => `<tr><td><button type="button" class="text-link" data-object="${esc(item.object.objectId)}">${esc(item.object.name)}</button></td><td>${esc(item.object.hostName || '')}</td><td>${esc(item.object.normalizedStatus || '')}</td><td>${item.separationArcmin < 0.05 ? '< 3″' : `${item.separationArcmin.toFixed(2)}′`}</td><td>${item.sameHost ? 'same host' : 'nearby host'}</td></tr>`).join('');
  return `<div class="sky-panel" data-sky-panel="${esc(object.objectId)}">` +
    `<h2>Sky at the catalog position</h2>` +
    `<p class="muted">${esc(object.hostName || object.name)} · ICRS ${esc(sexagesimal(position.ra, position.dec))} · ${position.ra.toFixed(5)}°, ${position.dec >= 0 ? '+' : ''}${position.dec.toFixed(5)}° (J2000, the release's catalog coordinate; see Identity &amp; provenance for every source's claim). Nothing below loads until you open it.</p>` +
    `<details class="sky-atlas" data-sky-atlas><summary>Interactive sky atlas <small>Aladin Lite · survey imagery loads only when opened</small></summary><div class="sky-atlas-body">` +
    `<div class="sky-tools" data-sky-atlas-tools>${atlasToolbar(position.dec)}</div><div class="sky-atlas-stage" data-sky-atlas-stage></div>` +
    `<p class="fineprint" data-sky-atlas-status role="status" aria-live="polite">The yellow marker is the catalog position of the host; cyan markers are the other catalog objects within ${NEARBY_RADIUS_ARCMIN}′.</p></div></details>` +
    `<h3>Nearby catalog objects</h3><p class="muted" data-sky-nearby-summary>${esc(describeNearby(nearby))}</p>` +
    (nearby.length ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Nearby catalog objects"><table><thead><tr><th>Object</th><th>Host</th><th>Status</th><th>Separation</th><th>Relation</th></tr></thead><tbody>${rows}</tbody></table></div>` : '') +
    finderPanelHtml(object, position) +
    `<h3>Send to desktop tools</h3><div class="sky-tools-actions"><button class="button" type="button" data-samp-point>Point TOPCAT/Aladin here (SAMP)</button><button class="button" type="button" data-samp-table>Send this object's rows to TOPCAT (SAMP, VOTable)</button><a class="button" href="#" data-votable-download download="${esc(object.objectId)}.vot">Download the VOTable</a></div>` +
    `<p class="fineprint">${esc(SAMP_NOTE)} The first button sends coord.pointAt.sky for the catalog position; the second sends table.load.votable with this object's rows (the release's selected values and its packaged native rows, built in your browser); nothing else leaves your computer.</p><p class="fineprint" data-samp-status role="status" aria-live="polite"></p>` +
    `</div>`;
}

/**
 * Mounts the panel into `container` for `object`; returns false (container emptied) when the object
 * has no coordinates. `context`: { catalogObjects, records (array or function, read when a VOTable is built), manifest }.
 */
export function mountObjectSky(container, object, context = {}) {
  if (!container) return false;
  const position = coordinatesOf(object);
  if (!position) { container.innerHTML = ''; container.dataset.skyMounted = ''; return false; }
  if (container.dataset.skyMounted === object.objectId) return true;
  const nearby = nearbyObjects(position, context.catalogObjects || [], NEARBY_RADIUS_ARCMIN, { excludeId: object.objectId, hostName: object.hostName || null });
  container.innerHTML = objectSkyHtml(object, position, nearby);
  container.dataset.skyMounted = object.objectId;
  const fetchImpl = context.fetchImpl || ((...args) => fetch(...args));
  const atlas = container.querySelector('[data-sky-atlas]');
  atlas.addEventListener('toggle', () => {
    if (!atlas.open || atlas.dataset.started) return;
    atlas.dataset.started = '1';
    const stage = atlas.querySelector('[data-sky-atlas-stage]'), status = atlas.querySelector('[data-sky-atlas-status]'), tools = atlas.querySelector('[data-sky-atlas-tools]');
    status.textContent = `Loading the CDS Aladin Lite runtime (pinned ${ALADIN_VERSION}) and public sky imagery…`;
    loadAladinRuntime().then((A) => {
      const aladin = A.aladin(stage, { target: `${position.ra.toFixed(7)} ${position.dec.toFixed(7)}`, fov: 0.2, cooFrame: 'ICRSd', survey: LAYERS[0].hips, showReticle: true, showCooGrid: true });
      const host = markerCatalog(A, 'WorldsIndex catalog position', '#ffd166', 16);
      host.addSources([A.marker(position.ra, position.dec, { popupTitle: object.hostName || object.name, popupDesc: `Catalog position of ${object.name} (${object.objectId})` })]);
      aladin.addCatalog(host);
      if (nearby.length) {
        const others = markerCatalog(A, `Catalog objects within ${NEARBY_RADIUS_ARCMIN}′`, '#78d8f5', 11);
        others.addSources(nearby.map((item) => { const p = coordinatesOf(item.object); return A.source(p.ra, p.dec, { name: item.object.name, host: item.object.hostName || '', status: item.object.normalizedStatus || '', separation_arcmin: item.separationArcmin.toFixed(3), relation: item.sameHost ? 'same host' : 'nearby host', object_id: item.object.objectId }); }));
        aladin.addCatalog(others);
      }
      bindLayerButtons(tools, A, aladin, status);
      status.textContent = `Interactive CDS Aladin Lite (${runtime.aladinSource}) centred on the catalog position; ${nearby.length} other catalog object${nearby.length === 1 ? '' : 's'} within ${NEARBY_RADIUS_ARCMIN}′ drawn in cyan. External layers keep their provider attribution.`;
    }).catch((error) => { status.textContent = `Aladin Lite could not be loaded (${error.message}). The coordinates, the finder chart and the nearby list remain usable.`; atlas.dataset.started = ''; });
  });
  const finder = container.querySelector('[data-finder]');
  finder.addEventListener('toggle', () => {
    if (!finder.open || finder.dataset.started) return;
    finder.dataset.started = '1';
    const print = finder.querySelector('[data-finder-print]');
    Promise.all([...finder.querySelectorAll('[data-finder-field]')].map((figure) => loadFinderField(figure, position, fetchImpl))).then((results) => { if (results.some(Boolean)) print.disabled = false; });
  });
  finder.querySelector('[data-finder-print]').addEventListener('click', () => {
    const fields = [...finder.querySelectorAll('[data-finder-field]')].filter((figure) => figure.dataset.finderState === 'loaded').map((figure) => ({ fov: Number(figure.dataset.finderField), url: figure.querySelector('[data-finder-image]').src, label: figure.querySelector('[data-finder-source]').textContent }));
    const status = finder.querySelector('[data-finder-status]');
    if (!fields.length) { status.textContent = 'No field has loaded yet, so there is nothing to print.'; return; }
    const blob = new Blob([printDocument(object, position, fields, context.manifest?.atlasSha256)], { type: 'text/html' }), url = URL.createObjectURL(blob);
    const opened = window.open(url, '_blank');
    if (!opened) status.textContent = 'The print view was blocked by the browser; allow pop-ups for this page and try again.';
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  });
  const sampStatus = container.querySelector('[data-samp-status]');
  container.querySelector('[data-samp-point]').addEventListener('click', () => sendPointAt(position, sampStatus, fetchImpl));
  const votable = () => objectVotable(object, (typeof context.records === 'function' ? context.records() : context.records) || [], context.manifest || null);
  const download = container.querySelector('[data-votable-download]');
  download.addEventListener('click', (event) => { event.preventDefault(); const blob = new Blob([votable()], { type: 'application/x-votable+xml' }), url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = `${object.objectId}.vot`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); });
  container.querySelector('[data-samp-table]').addEventListener('click', () => sendVotable(votableDataUrl(votable()), `WorldsIndex ${object.name}`, sampStatus, fetchImpl, { fallbackHref: null }));
  return true;
}

// ------------------------------------------------------------------ catalog page atlas
const catalogState = { instance: null, catalog: null, open: false, getObjects: null, status: null };
export function catalogSkyHtml(objectsVotableHref) {
  return `<details class="sky-atlas catalog-sky-atlas" data-catalog-sky><summary>Interactive atlas of the filtered objects <small>Aladin Lite · survey imagery loads only when opened</small></summary><div class="sky-atlas-body">` +
    `<div class="sky-tools" data-sky-atlas-tools>${atlasToolbar(null)}</div><div class="sky-atlas-stage" data-sky-atlas-stage></div>` +
    `<p class="fineprint" data-sky-atlas-status role="status" aria-live="polite">The objects of the current filters are drawn as markers (first ${CATALOG_MARKER_CAP.toLocaleString()} when more match); select one for its name and status. Background imagery is CDS Aladin Lite (LGPL-3.0-or-later); the logo and link stay in the view.</p>` +
    `<div class="sky-tools-actions"><button class="button" type="button" data-samp-objects>Send the objects table to TOPCAT (SAMP)</button><a class="button" href="${esc(objectsVotableHref)}" download>Download objects.vot.gz</a></div>` +
    `<p class="fineprint">${esc(SAMP_NOTE)} This sends table.load.votable with the published objects export of this release (every catalog object, not only the filtered ones; the hub fetches it from this site).</p><p class="fineprint" data-samp-status role="status" aria-live="polite"></p></div></details>`;
}
export function mountCatalogSky(container, { getObjects, objectsVotableHref, fetchImpl = (...args) => fetch(...args) } = {}) {
  if (!container || container.querySelector('[data-catalog-sky]')) return;
  container.insertAdjacentHTML('beforeend', catalogSkyHtml(objectsVotableHref));
  catalogState.getObjects = getObjects;
  const details = container.querySelector('[data-catalog-sky]');
  const stage = details.querySelector('[data-sky-atlas-stage]'), status = details.querySelector('[data-sky-atlas-status]'), tools = details.querySelector('[data-sky-atlas-tools]');
  catalogState.status = status;
  details.addEventListener('toggle', () => {
    catalogState.open = details.open;
    if (!details.open) return;
    if (catalogState.instance) { refreshCatalogSky(); return; }
    status.textContent = `Loading the CDS Aladin Lite runtime (pinned ${ALADIN_VERSION})…`;
    loadAladinRuntime().then((A) => {
      const first = (getObjects() || []).map(coordinatesOf).find(Boolean);
      const aladin = A.aladin(stage, { target: first ? `${first.ra.toFixed(5)} ${first.dec.toFixed(5)}` : '0 0', fov: 180, cooFrame: 'ICRSd', survey: LAYERS[0].hips, showReticle: false, showCooGrid: true });
      catalogState.instance = { A, aladin };
      bindLayerButtons(tools, A, aladin, status);
      refreshCatalogSky();
    }).catch((error) => { status.textContent = `Aladin Lite could not be loaded (${error.message}). The canvas sky map above remains usable.`; catalogState.open = false; });
  });
  const sampStatus = details.querySelector('[data-samp-status]');
  details.querySelector('[data-samp-objects]').addEventListener('click', () => sendVotable(new URL(objectsVotableHref, location.href).href, 'WorldsIndex objects', sampStatus, fetchImpl, { fallbackHref: objectsVotableHref }));
}
export function refreshCatalogSky() {
  if (!catalogState.instance || !catalogState.open) return;
  const { A, aladin } = catalogState.instance, objects = (catalogState.getObjects?.() || []).filter(coordinatesOf);
  try { if (catalogState.catalog) aladin.removeLayer(catalogState.catalog); } catch { /* older runtime */ }
  catalogState.catalog = markerCatalog(A, 'WorldsIndex filtered objects', '#ffd166', 9);
  catalogState.catalog.addSources(objects.slice(0, CATALOG_MARKER_CAP).map((object) => { const p = coordinatesOf(object); return A.source(p.ra, p.dec, { name: object.name, host: object.hostName || '', status: object.normalizedStatus || '', object_id: object.objectId }); }));
  aladin.addCatalog(catalogState.catalog);
  if (catalogState.status) catalogState.status.textContent = `${Math.min(objects.length, CATALOG_MARKER_CAP).toLocaleString()} of ${objects.length.toLocaleString()} filtered objects with coordinates drawn as markers (${runtime.aladinSource}); the filters above change the set.`;
}
