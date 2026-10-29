// Browser-side loading and storage for the visitor tools (Framework v2 Track 5).
// Reads only the site's own public files; keeps the visitor's lists in this browser's
// localStorage (never sent anywhere). Every storage call is wrapped: private windows and
// blocked storage leave the tools working, just without memory between visits.
import { inflateCtasRows, aliasMapFromCtas } from "./visitor-filters.js?v=20261029";

export const STORAGE_KEY = "jm.visitor.v1";
export const CTAS_WATCH_KEY = "ctas-browser-watchlist-v1"; // the CTAS page's own watch button

export function siteRoot() {
  // tools/<page>/ → the site root, so the pages work on GitHub Pages and on a local preview.
  return new URL("../../", document.baseURI);
}
export const ctasLink = (id) => new URL(`ctas.html?event=${encodeURIComponent(id)}`, siteRoot()).href;
export const worldsLink = (id) => new URL(`worldsindex/?object=${encodeURIComponent(id)}&section=object`, siteRoot()).href;

export function readState() {
  const empty = { watchlist: [], regions: [], filters: [], seen: {} };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return empty;
    const parsed = JSON.parse(raw);
    return { ...empty, ...parsed, seen: parsed && typeof parsed.seen === "object" && parsed.seen ? parsed.seen : {} };
  } catch {
    return empty;
  }
}
export function writeState(state) {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); return true; } catch { return false; }
}
export function storageWorks() {
  try { const k = "jm.visitor.probe"; window.localStorage.setItem(k, "1"); window.localStorage.removeItem(k); return true; } catch { return false; }
}
export function readCtasWatchIds() {
  try { const ids = JSON.parse(window.localStorage.getItem(CTAS_WATCH_KEY) || "[]"); return Array.isArray(ids) ? ids.map(String) : []; } catch { return []; }
}

async function fetchJson(path, { gzip = false, optional = false } = {}) {
  const response = await fetch(new URL(path, siteRoot()), { cache: "no-cache" });
  if (!response.ok) {
    if (optional) return null;
    throw new Error(`${path}: HTTP ${response.status}`);
  }
  if (!gzip) return response.json();
  const stream = response.body.pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

const cache = new Map();
function once(key, load) {
  if (!cache.has(key)) cache.set(key, load().catch((error) => { cache.delete(key); throw error; }));
  return cache.get(key);
}

// CTAS: every public record (the catalog index), the release time and the alias map.
export function loadCtas() {
  return once("ctas", async () => {
    const [index, status] = await Promise.all([fetchJson("ctas/data/catalog-index.json"), fetchJson("ctas/data/status.json", { optional: true })]);
    return { records: inflateCtasRows(index), now: (status && status.last_successful_update) || index.latest_record_update || null,
             checksum: index.catalog_content_checksum_sha256 || null };
  });
}
export function loadCtasAliases() {
  return once("ctas-aliases", async () => aliasMapFromCtas(await fetchJson("ctas/data/alias-index.json")));
}
// WorldsIndex: every object of the catalog index and the release time.
export function loadWorlds() {
  return once("worldsindex", async () => {
    const [manifest, catalog] = await Promise.all([fetchJson("worldsindex/data/manifest.json"), fetchJson("worldsindex/data/catalog-index.json.gz", { gzip: true })]);
    return { records: catalog.objects || [], now: manifest.generatedAt || null, checksum: manifest.catalogIndexSha256 || null };
  });
}
// The deploy-time files (tools/data/manifest.json); null when this deployment has none.
export function loadProducts() {
  return once("products", () => fetchJson("tools/data/manifest.json", { optional: true }).catch(() => null));
}
export function loadGallery() {
  return once("gallery", () => fetchJson("tools/follow/gallery.json"));
}
export function loadConeCell(product, cell) {
  return once(`cone-${product}-${cell}`, () => fetchJson(`tools/data/cone/${product}/${String(cell).padStart(3, "0")}.json`));
}

export function download(name, text, type) {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
