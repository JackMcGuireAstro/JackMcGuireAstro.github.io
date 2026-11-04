// Run-list helpers for the tool pages (Follow, cone search, SQL, finder chart, exposure
// calculator): the button markup that tools/lib/run-cart-ui.js wires, and a "Load from run
// list" picker. Everything stays in this browser; nothing is sent anywhere.
// The pages load tools/lib/run-cart.js and run-cart-ui.js as classic scripts first.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cart = () => globalThis.RunCart || null;
export const MAX_OFFER = 50;  // the run list holds 50 targets

// A run-list item for a record of a catalog ("ctas" or "worldsindex"), or a plain position.
export function cartItem(product, { id, name, ra, dec, mag = null, magBand = null } = {}) {
  const num = (v) => (v === null || v === undefined || v === "" ? NaN : Number(v));
  const r = num(ra), d = num(dec);
  const pos = Number.isFinite(r) && Number.isFinite(d) ? { ra: r, dec: d } : { ra: null, dec: null };
  if (product === "ctas") return { kind: "ctas", id: String(id), name: name || String(id), ...pos };
  if (product === "worldsindex" || product === "planet") return { kind: "planet", id: String(id), name: name || String(id), ...pos };
  const C = cart();
  const tid = C && pos.ra !== null ? C.targetId(pos.ra, pos.dec) : "";
  if (!tid) return null;
  const item = { kind: "target", id: tid, name: name || tid, ...pos };
  if (Number.isFinite(Number(mag)) && mag !== null && mag !== "" && magBand) { item.mag = Number(mag); item.magBand = magBand; }
  return item;
}

const dataAttrs = (item) => `data-kind="${esc(item.kind)}" data-id="${esc(item.id)}" data-name="${esc(item.name)}"` +
  ` data-ra="${item.ra ?? ""}" data-dec="${item.dec ?? ""}"` +
  (item.mag !== undefined ? ` data-mag="${item.mag}" data-mag-band="${esc(item.magBand)}"` : "");

// One add/remove toggle (labelled "+ Run list" / "✓ Run list" when compact).
export function toggleButton(item, { compact = true } = {}) {
  if (!item || !item.id) return "";
  return `<button type="button" class="runcart-toggle button button-quiet" data-runcart-toggle${compact ? " data-runcart-compact" : ""} ${dataAttrs(item)}>${compact ? "+ Run list" : "Add to run list"}</button>`;
}

// "Add these N to my run list": the first MAX_OFFER items, in the order given; `total` is how many
// matched in all, so the status can say when only the first were offered.
export function addManyButton(items, { label = null, total = null } = {}) {
  const usable = (items || []).filter((x) => x && x.id && (x.kind !== "target" || x.ra !== null));
  if (!usable.length) return "";
  const offered = usable.slice(0, MAX_OFFER), all = total ?? usable.length;
  const text = label || (all > offered.length ? `Add the first ${offered.length} to my run list` : `Add ${offered.length === 1 ? "it" : `all ${offered.length}`} to my run list`);
  return `<button type="button" class="button button-quiet" data-runcart-add-many="${esc(JSON.stringify(offered))}"${all > offered.length ? ` data-runcart-total="${all}"` : ""}>${esc(text)}</button>` +
    `<span class="runcart-status" data-runcart-status role="status" aria-live="polite"></span>`;
}

// Rows of a result as run-list items, when its columns allow it: event_id (a CTAS transient) or
// object_id (a WorldsIndex planet), or else a position (ra_deg/dec_deg or ra/dec) as a plain
// target named by a "name" column. Returns {items} or {reason}.
export function runListItems(columns, rowAt, n) {
  const has = (c) => columns.includes(c);
  const ra = ["ra_deg", "ra", "raDeg"].find(has), dec = ["dec_deg", "dec", "decDeg"].find(has);
  const kind = has("event_id") ? "ctas" : has("object_id") ? "worldsindex" : (ra && dec ? "target" : null);
  if (!kind) return { reason: "To put these rows on your run list, select event_id (CTAS) or object_id (WorldsIndex), or ra_deg and dec_deg (and a name column) for plain targets." };
  const items = [];
  for (let i = 0; i < n; i += 1) {
    const row = rowAt(i), num = (c) => (c && row[c] !== null && row[c] !== undefined ? Number(row[c]) : null);
    const id = kind === "ctas" ? row.event_id : kind === "worldsindex" ? row.object_id : null;
    const item = cartItem(kind, { id: id === null || id === undefined ? undefined : String(id), name: has("name") && row.name ? String(row.name) : undefined, ra: num(ra), dec: num(dec) });
    if (item && item.id && item.id !== "undefined") items.push(item);
  }
  return items.length ? { items } : { reason: "None of these rows has an identifier or a position to put on the run list." };
}

// "Load from run list": a select of the run list's targets and a button; `onPick(item)` gets the
// chosen item. The options follow the run list in this and other tabs. Returns an updater.
export function mountPicker(host, { onPick, label = "Load a target from your run list", sheetHref = "../../tonight/runsheet.html" } = {}) {
  if (!host) return () => {};
  const C = cart();
  const id = `runcart-pick-${Math.random().toString(36).slice(2, 8)}`;
  host.classList.add("runcart-picker");
  const render = () => {
    const items = C ? C.list() : [];
    if (!items.length) {
      host.innerHTML = `<p class="muted">Your <a href="${esc(sheetHref)}">run list</a> is empty. Add targets from CTAS, WorldsIndex, Tonight, Follow, the cone search or here.</p>`;
      return;
    }
    const keep = host.querySelector("select")?.value;
    host.innerHTML = `<label for="${id}">${esc(label)}</label> <select id="${id}">${items.map((x, i) => `<option value="${i}">${esc(`${i + 1}. ${x.name}`)}${x.kind === "planet" ? " (planet)" : x.kind === "ctas" ? " (CTAS)" : ""}</option>`).join("")}</select> ` +
      `<button type="button" class="button button-quiet" data-runcart-load>Load</button>`;
    if (keep && Number(keep) < items.length) host.querySelector("select").value = keep;
    host.querySelector("[data-runcart-load]").addEventListener("click", () => {
      const item = (C ? C.list() : [])[Number(host.querySelector("select").value)];
      if (item && onPick) onPick(item);
    });
  };
  render();
  if (C) C.onChange(() => { if (!host.contains(document.activeElement)) render(); });
  return render;
}

// The magnitude to use for a run-list item in the exposure calculator: the item's own (a plain
// target), the latest CTAS magnitude, or a WorldsIndex host-star magnitude, with the band and a
// note saying where it came from. Null when none is known. `base` is the site root ("../../").
export async function magnitudeFor(item, base = "../../") {
  if (!item) return null;
  if (item.mag !== undefined && item.magBand) return { mag: item.mag, band: item.magBand, note: `the magnitude kept with ${item.name} in your run list (${item.magBand})` };
  try {
    if (item.kind === "ctas") {
      const r = await fetch(`${base}ctas/data/live-summary.json`, { cache: "no-cache" });
      if (!r.ok) return null;
      const s = await r.json(), at = Object.fromEntries((s.candidate_columns || []).map((c, i) => [c, i]));
      const row = (s.candidate_rows || []).find((x) => x[at.event_id] === item.id);
      const mag = row ? Number(row[at.discovery_magnitude]) : NaN;
      return Number.isFinite(mag) ? { mag, band: "r", note: `the latest magnitude CTAS retained for ${item.name}, taken as r (CTAS follow-up plans in r; the survey's own band may differ)` } : null;
    }
    if (item.kind === "planet") {
      const r = await fetch(`${base}worldsindex/data/transit-ephemerides.json.gz`, { cache: "no-cache" });
      if (!r.ok) return null;
      const text = r.headers.get("content-encoding") === "gzip" ? await r.text() : await new Response(r.body.pipeThrough(new DecompressionStream("gzip"))).text();
      const a = JSON.parse(text), at = Object.fromEntries(a.columns.map((c, i) => [c, i]));
      const row = (a.rows || []).find((x) => x[at.objectId] === item.id);
      if (!row) return null;
      const mag = Number(row[at.hostMag]), band = row[at.hostMagBand];
      if (!Number.isFinite(mag)) return null;
      if (band === "V") return { mag, band: "V", note: `the host star's V magnitude from the WorldsIndex ephemeris` };
      if (band === "TESS") return { mag, band: "I", note: `the host star's TESS magnitude, standing in for Cousins I (similar bands; an approximation)` };
      return null;
    }
  } catch (_) { return null; }
  return null;
}
