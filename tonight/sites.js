// Observatory profiles shared by the Tonight planner, the run sheet and the WorldsIndex transit
// planner: the built-in sites plus the reader's own custom sites (name, latitude, longitude,
// elevation, time zone), kept only in this browser's localStorage. Nothing is sent anywhere.
export const STORAGE_KEY = "jm.observatories.v1";

// Jack's two telescopes come first; their coordinates match ctas/data/observatories.json.
export const BUILT_IN_SITES = [
  { id: "wiro", name: "WIRO (Wyoming Infrared Observatory)", lat: 41.0979, lon: -105.9767, elevation: 2943, tz: "America/Denver", telescope: "wiro" },
  { id: "rbo", name: "Red Buttes Observatory (UW)", lat: 41.1764, lon: -105.5739, elevation: 2246, tz: "America/Denver", telescope: "rbo" },
];

function storage() {
  try { return typeof localStorage !== "undefined" ? localStorage : null; } catch { return null; }
}

// A profile is kept only when every value is usable.
export function validSite(site) {
  if (!site || typeof site !== "object") return null;
  const lat = Number(site.lat), lon = Number(site.lon), elevation = site.elevation === "" || site.elevation == null ? null : Number(site.elevation);
  const name = String(site.name ?? "").trim().slice(0, 60), tz = String(site.tz ?? "").trim();
  if (!name || !(lat >= -90 && lat <= 90) || !(lon >= -180 && lon <= 180)) return null;
  if (elevation !== null && !(elevation > -500 && elevation < 9000)) return null;
  try { new Intl.DateTimeFormat("en-US", { timeZone: tz || "UTC" }); } catch { return null; }
  const id = "custom-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
  return { id, name, lat, lon, elevation, tz: tz || "UTC", custom: true };
}

export function savedSites() {
  const store = storage();
  if (!store) return [];
  try {
    const rows = JSON.parse(store.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(rows) ? rows.map(validSite).filter(Boolean) : [];
  } catch { return []; }
}

// Adds or replaces (by name) one custom site; returns the stored profile, or null when invalid or
// when this browser keeps no storage.
export function saveSite(site) {
  const valid = validSite(site), store = storage();
  if (!valid || !store) return null;
  const rows = savedSites().filter((row) => row.id !== valid.id);
  rows.push(valid);
  try { store.setItem(STORAGE_KEY, JSON.stringify(rows.slice(-20))); return valid; } catch { return null; }
}

export function removeSite(id) {
  const store = storage();
  if (!store) return;
  try { store.setItem(STORAGE_KEY, JSON.stringify(savedSites().filter((row) => row.id !== id))); } catch { /* nothing kept */ }
}
