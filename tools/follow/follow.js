// The Follow page (Framework v2 Track 5): watchlist, sky regions, saved filters and the
// gallery, evaluated in this browser against the latest public releases. The visitor's lists
// live in localStorage only (tools/lib/visitor-data.js); nothing is sent anywhere.
import {
  FIELDS, OPS, FILTER_SCHEMA, ID_FIELD, POSITION, validateFilter, applyFilter, describeFilter, parseWatchLine,
  watchMatches, newSince, encodeFilterForUrl, decodeFilterFromUrl, exportState, importState,
} from "../lib/visitor-filters.js?v=20261029";
import { parseMoc, mocContains, mocAreaSqDeg, mocToAscii, coneMoc } from "../lib/healpix.js?v=20261029";
import {
  readState, writeState, storageWorks, readCtasWatchIds, loadCtas, loadCtasAliases, loadWorlds, loadGallery, loadProducts,
  ctasLink, worldsLink, download, esc,
} from "../lib/visitor-data.js?v=20261029";

const $ = (id) => document.getElementById(id);
const PRODUCT_LABEL = { ctas: "CTAS", worldsindex: "WorldsIndex" };
const MAX_ROWS = 50;
let state = readState();
// What was "seen" when this page opened: the comparisons of this visit are made against it,
// and the current results are stored as the new last look once shown.
const seenAtOpen = JSON.parse(JSON.stringify(state.seen || {}));

const save = () => writeState(state);
function setStatus(id, text, ok = false) { const el = $(id); el.textContent = text; el.classList.toggle("ok", ok); }

async function product(name) { return name === "ctas" ? loadCtas() : loadWorlds(); }
const idOf = (name, record) => String(record[ID_FIELD[name]]);
const linkOf = (name, id) => (name === "ctas" ? ctasLink(id) : worldsLink(id));

// "New since you last looked": compare with the look recorded before this visit, then record this one.
function compareAndRemember(key, ids) {
  const result = newSince(ids, seenAtOpen[key] || null);
  state.seen[key] = { ids: result.ids.slice(0, 20000), at: new Date().toISOString() };
  return result;
}

function recordRow(name, record, isNew) {
  const id = idOf(name, record);
  const [raKey, decKey] = POSITION[name];
  const detail = name === "ctas"
    ? [record.classification || "Unclassified", record.event_type, record.discovery_time ? String(record.discovery_time).slice(0, 10) : ""]
    : [record.normalizedStatus, record.methodCode, record.discoveryYear || ""];
  const ra = Number.isFinite(record[raKey]) ? record[raKey].toFixed(5) : "—";
  const dec = Number.isFinite(record[decKey]) ? (record[decKey] >= 0 ? "+" : "") + record[decKey].toFixed(5) : "—";
  return `<tr${isNew ? ' class="is-new"' : ""}><th scope="row"><a href="${esc(linkOf(name, id))}">${esc(record.name || id)}</a>${isNew ? ' <span class="badge-new">new</span>' : ""}</th>`
    + `<td>${esc(PRODUCT_LABEL[name])}</td>${detail.map((d) => `<td>${esc(d ?? "")}</td>`).join("")}<td class="num">${ra}</td><td class="num">${dec}</td></tr>`;
}
function table(rows, caption) {
  if (!rows.length) return "";
  return `<div class="table-wrap"><table><caption class="visually-hidden">${esc(caption)}</caption><thead><tr><th scope="col">Name</th><th scope="col">Catalog</th>`
    + `<th scope="col">Class / status</th><th scope="col">Type / method</th><th scope="col">Discovered</th><th scope="col" class="num">RA (deg)</th><th scope="col" class="num">Dec (deg)</th></tr></thead>`
    + `<tbody>${rows.join("")}</tbody></table></div>`;
}
function csvOf(entries) {
  const head = "catalog,id,name,ra_deg,dec_deg,new_since_last_look";
  const lines = entries.map(({ name, record, isNew }) => {
    const [raKey, decKey] = POSITION[name];
    return [PRODUCT_LABEL[name], idOf(name, record), record.name || "", record[raKey] ?? "", record[decKey] ?? "", isNew ? "yes" : "no"]
      .map((v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v)).join(",");
  });
  return [head, ...lines].join("\n") + "\n";
}

// ------------------------------------------------------------------ release line
async function releaseLine() {
  const parts = [];
  for (const name of ["ctas", "worldsindex"]) {
    try { const p = await product(name); parts.push(`${PRODUCT_LABEL[name]} release ${p.now ? p.now.replace("T", " ").slice(0, 16) + " UTC" : "time not recorded"} (${p.records.length.toLocaleString("en-US")} records)`); }
    catch (error) { parts.push(`${PRODUCT_LABEL[name]} release not readable (${error.message})`); }
  }
  $("release-line").textContent = parts.join(" · ");
}

// ------------------------------------------------------------------ watchlist
async function renderWatchlist() {
  const host = $("watch-results");
  const items = state.watchlist.map(parseWatchLine).filter(Boolean);
  const ctasWatch = readCtasWatchIds();
  if (!items.length && !ctasWatch.length) { host.innerHTML = '<p class="muted">Nothing on your watchlist yet.</p>'; return; }
  host.innerHTML = '<p class="muted">Checking…</p>';
  const [ctas, worlds] = await Promise.all([loadCtas().catch(() => null), loadWorlds().catch(() => null)]);
  const aliases = items.some((i) => i.kind === "name") ? await loadCtasAliases().catch(() => null) : null;
  const blocks = [];
  for (const item of items) {
    const found = [];
    if (ctas) watchMatches(item, "ctas", ctas.records, aliases).forEach((r) => found.push({ name: "ctas", record: r }));
    if (worlds) watchMatches(item, "worldsindex", worlds.records).forEach((r) => found.push({ name: "worldsindex", record: r }));
    const result = compareAndRemember(`watch:${item.text}`, found.map((f) => `${f.name}:${idOf(f.name, f.record)}`));
    const newIds = new Set(result.newIds);
    const rows = found.slice(0, MAX_ROWS).map((f) => recordRow(f.name, f.record, newIds.has(`${f.name}:${idOf(f.name, f.record)}`)));
    const what = item.kind === "name" ? `name “${esc(item.name)}”` : `within ${item.radiusArcsec}″ of RA ${item.ra.toFixed(5)}°, Dec ${item.dec.toFixed(5)}°`;
    blocks.push(`<article class="follow-item"><h3>${esc(item.text)}</h3><p class="muted">${what}: ${found.length ? `${found.length} match${found.length === 1 ? "" : "es"}` : "no match in the current releases"}`
      + `${result.firstLook ? "" : result.newIds.length ? `, <strong>${result.newIds.length} new since you last looked</strong>` : ", nothing new since you last looked"}.</p>${table(rows, item.text)}</article>`);
  }
  if (ctasWatch.length && ctas) {
    const byId = new Map(ctas.records.map((r) => [String(r.event_id), r]));
    const rows = ctasWatch.map((id) => byId.get(id)).filter(Boolean).map((r) => recordRow("ctas", r, false));
    blocks.push(`<article class="follow-item"><h3>Watched on the CTAS page</h3><p class="muted">${ctasWatch.length} event${ctasWatch.length === 1 ? "" : "s"} you marked with the CTAS page’s watch button (also kept only in this browser).</p>${table(rows, "Watched on the CTAS page")}</article>`);
  }
  host.innerHTML = blocks.join("");
  save();
}

// ------------------------------------------------------------------ regions
async function renderRegions() {
  const host = $("region-results");
  if (!state.regions.length) { host.innerHTML = '<p class="muted">No regions yet.</p>'; return; }
  host.innerHTML = '<p class="muted">Checking…</p>';
  const [ctas, worlds] = await Promise.all([loadCtas().catch(() => null), loadWorlds().catch(() => null)]);
  const blocks = state.regions.map((region, index) => {
    let moc;
    try { moc = parseMoc(region.moc); } catch (error) { return `<article class="follow-item"><h3>${esc(region.title)}</h3><p class="status">${esc(error.message)}</p></article>`; }
    const found = [];
    for (const [name, data] of [["ctas", ctas], ["worldsindex", worlds]]) {
      if (!data) continue;
      const [raKey, decKey] = POSITION[name];
      data.records.forEach((r) => { if (Number.isFinite(r[raKey]) && Number.isFinite(r[decKey]) && mocContains(moc, r[raKey], r[decKey])) found.push({ name, record: r }); });
    }
    const result = compareAndRemember(`region:${region.title}:${region.moc.length}`, found.map((f) => `${f.name}:${idOf(f.name, f.record)}`));
    const newIds = new Set(result.newIds);
    found.sort((a, b) => Number(newIds.has(`${b.name}:${idOf(b.name, b.record)}`)) - Number(newIds.has(`${a.name}:${idOf(a.name, a.record)}`)));
    const rows = found.slice(0, MAX_ROWS).map((f) => recordRow(f.name, f.record, newIds.has(`${f.name}:${idOf(f.name, f.record)}`)));
    return `<article class="follow-item"><h3>${esc(region.title)}</h3><p class="muted">${mocAreaSqDeg(moc).toFixed(2)} square degrees: ${found.length} record${found.length === 1 ? "" : "s"} inside`
      + `${result.firstLook ? "" : result.newIds.length ? `, <strong>${result.newIds.length} new since you last looked</strong>` : ", nothing new since you last looked"}${found.length > MAX_ROWS ? ` (first ${MAX_ROWS} shown, new ones first)` : ""}.</p>`
      + `${table(rows, region.title)}<div class="row-actions"><button type="button" class="button button-quiet" data-region-csv="${index}">Download CSV</button>`
      + `<button type="button" class="button button-quiet" data-region-moc="${index}">Download MOC</button><button type="button" class="button button-quiet" data-region-remove="${index}">Remove</button></div></article>`;
  });
  host.innerHTML = blocks.join("");
  host.querySelectorAll("[data-region-remove]").forEach((b) => b.addEventListener("click", () => { state.regions.splice(Number(b.dataset.regionRemove), 1); save(); renderRegions(); }));
  host.querySelectorAll("[data-region-moc]").forEach((b) => b.addEventListener("click", () => { const r = state.regions[Number(b.dataset.regionMoc)]; download(`${r.title.replace(/[^\w-]+/g, "_")}.moc.txt`, mocToAscii(parseMoc(r.moc)) + "\n", "text/plain"); }));
  host.querySelectorAll("[data-region-csv]").forEach((b) => b.addEventListener("click", async () => {
    const r = state.regions[Number(b.dataset.regionCsv)], moc = parseMoc(r.moc), entries = [];
    for (const [name, data] of [["ctas", ctas], ["worldsindex", worlds]]) {
      if (!data) continue;
      const [raKey, decKey] = POSITION[name];
      data.records.forEach((rec) => { if (Number.isFinite(rec[raKey]) && Number.isFinite(rec[decKey]) && mocContains(moc, rec[raKey], rec[decKey])) entries.push({ name, record: rec, isNew: false }); });
    }
    download(`${r.title.replace(/[^\w-]+/g, "_")}.csv`, csvOf(entries), "text/csv");
  }));
  save();
}

function addRegion() {
  const title = $("region-name").value.trim() || `Region ${state.regions.length + 1}`;
  const text = $("region-text").value.trim();
  if (!text) { setStatus("region-status", "Paste a MOC or a circle, or choose a MOC file."); return; }
  let moc;
  try {
    const circle = text.match(/^([+-]?\d+(?:\.\d+)?)[\s,]+([+-]?\d+(?:\.\d+)?)[\s,]+(\d+(?:\.\d+)?)$/);
    if (circle) {
      const [ra, dec, r] = circle.slice(1).map(Number);
      if (!(ra >= 0 && ra < 360 && dec >= -90 && dec <= 90 && r > 0 && r <= 30)) throw new Error("A circle needs RA 0–360, Dec −90–90 and a radius up to 30 degrees.");
      moc = coneMoc(ra, dec, r, r > 10 ? 6 : 8);
    } else moc = parseMoc(text);
  } catch (error) { setStatus("region-status", error.message); $("region-text").setAttribute("aria-invalid", "true"); return; }
  $("region-text").removeAttribute("aria-invalid");
  const ascii = mocToAscii(moc);
  if (ascii.length > 200000) { setStatus("region-status", "That MOC is too detailed to keep here (over 200,000 characters); use a coarser order."); return; }
  state.regions.push({ title, moc: ascii });
  save();
  setStatus("region-status", `Added “${title}” (${mocAreaSqDeg(moc).toFixed(2)} square degrees).`, true);
  $("region-text").value = ""; $("region-name").value = "";
  renderRegions();
}

// ------------------------------------------------------------------ filter builder
function clauseRow(productName, clause = {}) {
  const fields = FIELDS[productName];
  const row = document.createElement("div");
  row.className = "clause-row";
  const n = document.querySelectorAll(".clause-row").length + 1;
  row.innerHTML = `<label class="visually-hidden" for="cf-${n}">Field</label><select id="cf-${n}" class="cf">${Object.entries(fields).map(([k, [, label]]) => `<option value="${k}">${esc(label)}</option>`).join("")}</select>`
    + `<label class="visually-hidden" for="co-${n}">Test</label><select id="co-${n}" class="co"></select>`
    + `<label class="visually-hidden" for="cv-${n}">Value</label><input id="cv-${n}" class="cv" placeholder="value">`
    + `<button type="button" class="button button-quiet" aria-label="Remove this condition">×</button>`;
  const f = row.querySelector(".cf"), o = row.querySelector(".co"), v = row.querySelector(".cv");
  const ops = { text: ["eq", "ne", "in", "contains", "exists", "missing"], list: ["contains", "exists", "missing"],
                number: ["gte", "lte", "gt", "lt", "eq", "ne", "exists", "missing"], time: ["within_days", "after", "before", "exists", "missing"] };
  const fillOps = () => { o.innerHTML = ops[fields[f.value][0]].map((op) => `<option value="${op}">${esc(OPS[op])}</option>`).join(""); };
  f.addEventListener("change", () => { fillOps(); v.disabled = ["exists", "missing"].includes(o.value); });
  o.addEventListener("change", () => { v.disabled = ["exists", "missing"].includes(o.value); v.placeholder = o.value === "in" ? "values, comma-separated" : o.value === "within_days" ? "days" : "value"; });
  row.querySelector("button").addEventListener("click", () => row.remove());
  if (clause.field && fields[clause.field]) f.value = clause.field;
  fillOps();
  if (clause.op) o.value = clause.op;
  if (clause.value !== undefined) v.value = Array.isArray(clause.value) ? clause.value.join(", ") : clause.value;
  v.disabled = ["exists", "missing"].includes(o.value);
  return row;
}
function resetBuilder(productName) { const host = $("filter-clauses"); host.innerHTML = ""; host.appendChild(clauseRow(productName)); }
function builderFilter() {
  const productName = $("filter-product").value, fields = FIELDS[productName];
  const clauses = [...document.querySelectorAll(".clause-row")].map((row) => {
    const field = row.querySelector(".cf").value, op = row.querySelector(".co").value, raw = row.querySelector(".cv").value.trim();
    const clause = { field, op };
    if (!["exists", "missing"].includes(op)) {
      if (op === "in") clause.value = raw.split(",").map((s) => s.trim()).filter(Boolean);
      else if (fields[field][0] === "number" || op === "within_days") clause.value = raw === "" ? raw : Number(raw);
      else clause.value = raw;
    }
    return clause;
  });
  return { schema: FILTER_SCHEMA, id: `mine-${Date.now().toString(36)}`, title: $("filter-name").value.trim() || "Untitled filter", product: productName, match: $("filter-match").value, clauses };
}
function saveBuilderFilter() {
  const filter = builderFilter(), problems = validateFilter(filter);
  if (problems.length) { setStatus("filter-status", problems.join(" ")); return; }
  state.filters.push(filter);
  save();
  setStatus("filter-status", `Saved “${filter.title}”.`, true);
  $("filter-name").value = "";
  resetBuilder($("filter-product").value);
  renderFilters();
}

async function renderFilters() {
  const host = $("filter-results");
  if (!state.filters.length) { host.innerHTML = '<p class="muted">No saved filters yet. Start from the gallery below, or build one above.</p>'; return; }
  host.innerHTML = '<p class="muted">Checking…</p>';
  const blocks = [];
  for (const [index, filter] of state.filters.entries()) {
    let data;
    try { data = await product(filter.product); } catch (error) { blocks.push(`<article class="follow-item"><h3>${esc(filter.title)}</h3><p class="status">${esc(error.message)}</p></article>`); continue; }
    let matched;
    try { matched = applyFilter(filter, data.records, { now: data.now }); } catch (error) { blocks.push(`<article class="follow-item"><h3>${esc(filter.title)}</h3><p class="status">${esc(error.message)}</p></article>`); continue; }
    const result = compareAndRemember(`filter:${filter.product}:${JSON.stringify(filter.clauses)}:${filter.match}`, matched.map((r) => idOf(filter.product, r)));
    const newIds = new Set(result.newIds);
    const sorted = matched.slice().sort((a, b) => Number(newIds.has(idOf(filter.product, b))) - Number(newIds.has(idOf(filter.product, a))));
    const rows = sorted.slice(0, MAX_ROWS).map((r) => recordRow(filter.product, r, newIds.has(idOf(filter.product, r))));
    const share = new URL(`#filter=${encodeFilterForUrl(filter)}`, window.location.href.split("#")[0]).href;
    blocks.push(`<article class="follow-item"><h3>${esc(filter.title)} <small>${esc(PRODUCT_LABEL[filter.product])}</small></h3><p class="muted">${esc(describeFilter(filter))}: ${matched.length.toLocaleString("en-US")} record${matched.length === 1 ? "" : "s"}`
      + `${result.firstLook ? "" : result.newIds.length ? `, <strong>${result.newIds.length} new since you last looked</strong>` : ", nothing new since you last looked"}${matched.length > MAX_ROWS ? ` (first ${MAX_ROWS} shown, new ones first)` : ""}.</p>${table(rows, filter.title)}`
      + `<div class="row-actions"><button type="button" class="button button-quiet" data-filter-csv="${index}">Download CSV</button>`
      + `<button type="button" class="button button-quiet" data-filter-share="${esc(share)}">Copy share link</button><button type="button" class="button button-quiet" data-filter-remove="${index}">Remove</button></div></article>`);
  }
  host.innerHTML = blocks.join("");
  host.querySelectorAll("[data-filter-remove]").forEach((b) => b.addEventListener("click", () => { state.filters.splice(Number(b.dataset.filterRemove), 1); save(); renderFilters(); }));
  host.querySelectorAll("[data-filter-share]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.filterShare); setStatus("filter-status", "Share link copied. It carries the filter only, never your lists.", true); }
    catch { window.prompt("Copy this link (it carries the filter only):", b.dataset.filterShare); }
  }));
  host.querySelectorAll("[data-filter-csv]").forEach((b) => b.addEventListener("click", async () => {
    const filter = state.filters[Number(b.dataset.filterCsv)], data = await product(filter.product);
    const entries = applyFilter(filter, data.records, { now: data.now }).map((record) => ({ name: filter.product, record, isNew: false }));
    download(`${filter.title.replace(/[^\w-]+/g, "_")}.csv`, csvOf(entries), "text/csv");
  }));
  save();
}

// ------------------------------------------------------------------ gallery
async function renderGallery() {
  const host = $("gallery");
  let gallery, products;
  try { [gallery, products] = await Promise.all([loadGallery(), loadProducts()]); }
  catch (error) { host.innerHTML = `<p class="status">The gallery is not readable (${esc(error.message)}).</p>`; return; }
  const feeds = new Map(((products && products.feeds) || []).map((f) => [f.id, f]));
  host.innerHTML = `<ul class="gallery-list">${gallery.filters.map((f, i) => {
    const feed = feeds.get(f.id);
    const feedLinks = feed ? ` · ${feed.match_count.toLocaleString("en-US")} now · feed: <a href="../../${esc(feed.json.path)}">JSON</a>, <a href="../../${esc(feed.atom.path)}">Atom</a>`
      : " · feeds are not available in this deployment";
    return `<li id="gallery-${esc(f.id)}"><div><strong>${esc(f.title)}</strong> <small>${esc(PRODUCT_LABEL[f.product])}</small><br><span class="muted">${esc(describeFilter(f))}${feedLinks}</span></div>`
      + `<button type="button" class="button button-quiet" data-gallery="${i}">Add to my filters</button></li>`;
  }).join("")}</ul>`;
  host.querySelectorAll("[data-gallery]").forEach((b) => b.addEventListener("click", () => {
    const f = gallery.filters[Number(b.dataset.gallery)];
    if (state.filters.some((x) => x.id === f.id)) { setStatus("filter-status", `“${f.title}” is already among your filters.`); return; }
    state.filters.push(JSON.parse(JSON.stringify(f)));
    save();
    setStatus("filter-status", `Added “${f.title}”.`, true);
    renderFilters();
  }));
}

// ------------------------------------------------------------------ files and shared links
function wireFiles() {
  $("export-state").addEventListener("click", () => download("follow-lists.json", exportState(state), "application/json"));
  $("import-state").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      const imported = importState(await file.text());
      const lines = new Set(state.watchlist);
      imported.watchlist.forEach((l) => lines.add(l));
      state.watchlist = [...lines];
      state.regions = state.regions.concat(imported.regions.filter((r) => !state.regions.some((x) => x.moc === r.moc)));
      state.filters = state.filters.concat(imported.filters.filter((f) => !state.filters.some((x) => JSON.stringify(x.clauses) === JSON.stringify(f.clauses) && x.product === f.product)));
      save();
      $("watch-text").value = state.watchlist.join("\n");
      setStatus("files-status", `Imported ${imported.watchlist.length} watchlist line(s), ${imported.regions.length} region(s) and ${imported.filters.length} filter(s).`, true);
      renderAll();
    } catch (error) { setStatus("files-status", error.message); }
    event.target.value = "";
  });
  $("forget-seen").addEventListener("click", () => { state.seen = {}; Object.keys(seenAtOpen).forEach((k) => delete seenAtOpen[k]); save(); setStatus("files-status", "Forgotten: the next look counts as the first.", true); });
}
function readSharedFilter() {
  const match = window.location.hash.match(/^#filter=([A-Za-z0-9_-]+)$/);
  if (match) {
    try {
      const f = decodeFilterFromUrl(match[1]);
      $("filter-name").value = f.title || "";
      $("filter-product").value = f.product;
      $("filter-match").value = f.match || "all";
      const host = $("filter-clauses"); host.innerHTML = "";
      f.clauses.forEach((c) => host.appendChild(clauseRow(f.product, c)));
      setStatus("filter-status", "A shared filter is loaded in the builder above: check it and press Save filter to keep it.", true);
      $("filter-title").scrollIntoView();
    } catch (error) { setStatus("filter-status", `The shared filter could not be read: ${error.message}`); }
  }
  const gallery = window.location.hash.match(/^#gallery=([\w-]+)$/);
  if (gallery) setTimeout(() => { const el = document.getElementById(`gallery-${gallery[1]}`); if (el) el.scrollIntoView(); }, 300);
}

function renderAll() { renderWatchlist(); renderRegions(); renderFilters(); }

function start() {
  if (!storageWorks()) $("storage-warning").hidden = false;
  $("watch-text").value = state.watchlist.join("\n");
  $("watch-save").addEventListener("click", () => {
    const lines = $("watch-text").value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).slice(0, 2000);
    state.watchlist = lines;
    save();
    setStatus("watch-status", `${lines.length} line${lines.length === 1 ? "" : "s"} saved in this browser.`, true);
    renderWatchlist();
  });
  $("region-add").addEventListener("click", addRegion);
  $("region-file").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (file.size > 2_000_000) { setStatus("region-status", "That file is over 2 MB; use a coarser MOC."); return; }
    $("region-text").value = (await file.text()).trim();
    if (!$("region-name").value) $("region-name").value = file.name.replace(/\.[^.]+$/, "");
  });
  $("filter-product").addEventListener("change", () => resetBuilder($("filter-product").value));
  $("filter-add-clause").addEventListener("click", () => $("filter-clauses").appendChild(clauseRow($("filter-product").value)));
  $("filter-save").addEventListener("click", saveBuilderFilter);
  resetBuilder("ctas");
  wireFiles();
  readSharedFilter();
  releaseLine();
  renderGallery();
  renderAll();
}
start();
