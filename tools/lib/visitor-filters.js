// Saved filters, watchlists and "new since you last looked" for the visitor tools
// (Framework v2 Track 5). Pure functions: no network, no DOM, no storage. The page keeps
// everything in the visitor's own browser; nothing here is sent anywhere.
//
// A filter is {schema: "jm.visitor-filter@1", id, title, product: "ctas" | "worldsindex",
// match: "all" | "any", clauses: [{field, op, value}]}. scripts/visitor_filters.py is the
// same engine in Python (the gallery feeds are made with it at deploy time); the two are
// held to identical results by scripts/test_visitor_tools.mjs and scripts/test_visitor_products.py.
export const FILTER_SCHEMA = "jm.visitor-filter@1";
export const STATE_SCHEMA = "jm.visitor-state@1";

// The fields a filter may test, per product, with their type and plain label.
export const FIELDS = {
  ctas: {
    name: ["text", "Name"],
    event_type: ["text", "Event type"],
    primary_messenger: ["text", "Primary messenger"],
    messenger_channels: ["list", "Messenger channels"],
    ra_deg: ["number", "Right ascension (deg)"],
    dec_deg: ["number", "Declination (deg)"],
    discovery_time: ["time", "Discovery time"],
    discovery_survey: ["text", "Discovery survey"],
    discovery_magnitude: ["number", "Discovery magnitude"],
    status: ["text", "Status"],
    classification: ["text", "Classification"],
    ctas_score: ["number", "CTAS follow-up score"],
    redshift: ["number", "Redshift"],
    updated_at: ["time", "Updated"],
    latest_classification_at: ["time", "Latest classification"],
    latest_spectrum_at: ["time", "Latest spectrum"],
    latest_retraction_at: ["time", "Latest retraction"],
    n_observations: ["number", "Photometry points"],
    n_spectra: ["number", "Spectra"],
    conflict_count: ["number", "Conflicts between sources"],
    ranking_channel: ["text", "Ranking channel"],
    identity_state: ["text", "Identity state"],
    lc_fit_model: ["text", "Light-curve fit model"],
    lc_rise_days: ["number", "Rise time (days)"],
    lc_peak_abs_mag: ["number", "Peak absolute magnitude"],
    broker_consensus: ["text", "Broker consensus"],
  },
  worldsindex: {
    name: ["text", "Name"],
    hostName: ["text", "Host"],
    raDeg: ["number", "Right ascension (deg)"],
    decDeg: ["number", "Declination (deg)"],
    normalizedStatus: ["text", "Status"],
    methodCode: ["text", "Discovery method"],
    methodCodes: ["list", "Methods"],
    discoveryYear: ["number", "Discovery year"],
    discoveryFacility: ["text", "Discovery facility"],
    identityState: ["text", "Identity state"],
    sourceIds: ["list", "Catalogs"],
    sourceRecordCount: ["number", "Catalog records"],
  },
};
export const ID_FIELD = { ctas: "event_id", worldsindex: "objectId" };
export const POSITION = { ctas: ["ra_deg", "dec_deg"], worldsindex: ["raDeg", "decDeg"] };
export const OPS = {
  eq: "is", ne: "is not", in: "is one of", contains: "contains", gte: "≥", lte: "≤", gt: ">", lt: "<",
  exists: "is known", missing: "is not known", within_days: "within the last N days of the release", after: "after", before: "before",
};
const OPS_BY_TYPE = {
  text: ["eq", "ne", "in", "contains", "exists", "missing"],
  list: ["contains", "exists", "missing"],
  number: ["eq", "ne", "gte", "lte", "gt", "lt", "exists", "missing"],
  time: ["within_days", "after", "before", "exists", "missing"],
};

const DAY_MS = 86400000;
const isMissing = (v) => v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0);
const lower = (v) => String(v).toLowerCase();
function timeMs(v) {
  if (isMissing(v)) return null;
  const t = Date.parse(String(v));
  return Number.isFinite(t) ? t : null;
}

// Problems with a filter as a list of plain sentences (empty when usable).
export function validateFilter(filter) {
  const problems = [];
  if (!filter || typeof filter !== "object") return ["The filter is not an object."];
  if (filter.schema !== FILTER_SCHEMA) problems.push(`schema must be "${FILTER_SCHEMA}".`);
  if (!FIELDS[filter.product]) problems.push('product must be "ctas" or "worldsindex".');
  if (filter.match !== undefined && filter.match !== "all" && filter.match !== "any") problems.push('match must be "all" or "any".');
  if (!Array.isArray(filter.clauses) || !filter.clauses.length) problems.push("A filter needs at least one condition.");
  else if (filter.clauses.length > 20) problems.push("A filter takes at most 20 conditions.");
  const fields = FIELDS[filter.product] || {};
  (Array.isArray(filter.clauses) ? filter.clauses : []).forEach((c, i) => {
    const where = `Condition ${i + 1}`;
    if (!c || typeof c !== "object") { problems.push(`${where} is not an object.`); return; }
    const spec = fields[c.field];
    if (!spec) { problems.push(`${where}: "${c.field}" is not a field of ${filter.product}.`); return; }
    if (!OPS_BY_TYPE[spec[0]].includes(c.op)) { problems.push(`${where}: "${c.op}" does not apply to ${spec[1]}.`); return; }
    if (c.op === "exists" || c.op === "missing") return;
    if (c.op === "in") { if (!Array.isArray(c.value) || !c.value.length) problems.push(`${where}: give a list of values.`); return; }
    if (spec[0] === "number" && !Number.isFinite(Number(c.value))) problems.push(`${where}: ${spec[1]} needs a number.`);
    if (c.op === "within_days" && !(Number(c.value) > 0)) problems.push(`${where}: give a number of days above 0.`);
    if ((c.op === "after" || c.op === "before") && timeMs(c.value) === null) problems.push(`${where}: give a date such as 2026-10-01.`);
    if ((c.op === "contains" || c.op === "eq" || c.op === "ne") && isMissing(c.value)) problems.push(`${where}: give a value.`);
  });
  return problems;
}

function clauseMatches(clause, record, type, nowMs) {
  const v = record[clause.field];
  switch (clause.op) {
    case "exists": return !isMissing(v);
    case "missing": return isMissing(v);
    default: break;
  }
  if (isMissing(v)) return false;
  if (type === "number") {
    const x = Number(v), y = Number(clause.value);
    if (!Number.isFinite(x)) return false;
    return { eq: x === y, ne: x !== y, gte: x >= y, lte: x <= y, gt: x > y, lt: x < y }[clause.op] ?? false;
  }
  if (type === "time") {
    const t = timeMs(v);
    if (t === null) return false;
    if (clause.op === "within_days") return nowMs !== null && t >= nowMs - Number(clause.value) * DAY_MS && t <= nowMs + DAY_MS;
    if (clause.op === "after") return t > timeMs(clause.value);
    if (clause.op === "before") return t < timeMs(clause.value);
    return false;
  }
  if (type === "list") {
    const items = Array.isArray(v) ? v.map(lower) : [lower(v)];
    return clause.op === "contains" && items.includes(lower(clause.value));
  }
  const text = lower(v);
  if (clause.op === "eq") return text === lower(clause.value);
  if (clause.op === "ne") return text !== lower(clause.value);
  if (clause.op === "in") return clause.value.map(lower).includes(text);
  if (clause.op === "contains") return text.includes(lower(clause.value));
  return false;
}

// The records a filter selects. context.now is the release time (ISO), the reference of
// "within the last N days" so a filter gives the same answer for the same release anywhere.
export function applyFilter(filter, records, context = {}) {
  const problems = validateFilter(filter);
  if (problems.length) throw new Error(problems.join(" "));
  const fields = FIELDS[filter.product], nowMs = timeMs(context.now);
  const any = filter.match === "any";
  return records.filter((record) => {
    const results = filter.clauses.map((c) => clauseMatches(c, record, fields[c.field][0], nowMs));
    return any ? results.some(Boolean) : results.every(Boolean);
  });
}

export function describeFilter(filter) {
  const fields = FIELDS[filter.product] || {};
  const parts = (filter.clauses || []).map((c) => {
    const label = (fields[c.field] || [null, c.field])[1];
    if (c.op === "exists" || c.op === "missing") return `${label} ${OPS[c.op]}`;
    const value = Array.isArray(c.value) ? c.value.join(", ") : c.value;
    return c.op === "within_days" ? `${label} within the last ${value} days of the release` : `${label} ${OPS[c.op]} ${value}`;
  });
  return parts.join(filter.match === "any" ? " or " : " and ");
}

// ------------------------------------------------------------------ catalog rows
// CTAS's catalog index stores rows as arrays beside a column list.
export function inflateCtasRows(index) {
  const cols = index.candidate_columns || [];
  return (index.candidate_rows || []).map((row) => Object.fromEntries(cols.map((c, i) => [c, row[i]])));
}

// ------------------------------------------------------------------ watchlists
const NAME_FOLD = (s) => String(s ?? "").toLowerCase().replace(/[\s_-]+/g, "");

// A watchlist line: a name ("SN 2024ggi", "TRAPPIST-1 e") or a position with an optional
// radius in arcseconds ("210.91 54.31 5", "14:03:38.6 +54:18:42 10"). Positions accept
// decimal degrees or sexagesimal hours/degrees.
export function parseWatchLine(line) {
  const text = String(line ?? "").trim();
  if (!text || text.startsWith("#")) return null;
  const tokens = text.split(/[\s,]+/);
  const angle = (t, isRa) => {
    if (/^[+-]?\d+(\.\d+)?$/.test(t)) return Number(t);
    const m = t.match(/^([+-])?(\d{1,3}):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?$/);
    if (!m) return null;
    const v = Number(m[2]) + Number(m[3]) / 60 + Number(m[4] || 0) / 3600;
    return (m[1] === "-" ? -1 : 1) * (isRa ? v * 15 : v);
  };
  if (tokens.length >= 2 && tokens.length <= 3) {
    const ra = angle(tokens[0], tokens[0].includes(":")), dec = angle(tokens[1], false);
    const radius = tokens.length === 3 ? Number(tokens[2]) : 5;
    if (ra !== null && dec !== null && ra >= 0 && ra < 360 && dec >= -90 && dec <= 90 && radius > 0 && radius <= 3600) {
      return { kind: "position", ra, dec, radiusArcsec: radius, text };
    }
  }
  return { kind: "name", name: text, key: NAME_FOLD(text), text };
}

function separationArcsec(ra1, dec1, ra2, dec2) {
  const r = Math.PI / 180, a = dec1 * r, b = dec2 * r, dr = (ra2 - ra1) * r;
  const h = Math.sin((b - a) / 2) ** 2 + Math.cos(a) * Math.cos(b) * Math.sin(dr / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) * 180 / Math.PI * 3600;
}

// TNS names are matched with and without their "SN"/"AT" prefix ("SN 2024ggi" = "2024ggi").
const nameKeys = (key) => { const bare = key.replace(/^(sn|at)(?=\d{4})/, ""); return bare === key ? [key] : [key, bare]; };

// The CTAS alias index ({columns, rows}) as {foldedDesignation: [event ids]}.
export function aliasMapFromCtas(aliasIndex) {
  const cols = (aliasIndex && aliasIndex.columns) || [], map = {};
  const idAt = cols.indexOf("event_id"), nameAt = cols.indexOf("designation");
  if (idAt < 0 || nameAt < 0) return map;
  for (const row of aliasIndex.rows || []) {
    for (const key of nameKeys(NAME_FOLD(row[nameAt]))) (map[key] ||= []).push(row[idAt]);
  }
  return map;
}

// Records matching a watch item. aliases: optional {foldedName: [ids]} (aliasMapFromCtas).
export function watchMatches(item, product, records, aliases = null) {
  if (!item) return [];
  const id = ID_FIELD[product];
  if (item.kind === "name") {
    const keys = nameKeys(item.key), ids = new Set();
    if (aliases) keys.forEach((k) => (aliases[k] || []).forEach((x) => ids.add(x)));
    const hit = (value) => nameKeys(NAME_FOLD(value)).some((k) => keys.includes(k));
    return records.filter((r) => ids.has(r[id]) || hit(r.name)
      || (product === "worldsindex" && (hit(r.hostName) || (Array.isArray(r.aliases) && r.aliases.some(hit)))));
  }
  const [raKey, decKey] = POSITION[product];
  return records.filter((r) => Number.isFinite(r[raKey]) && Number.isFinite(r[decKey])
    && separationArcsec(item.ra, item.dec, r[raKey], r[decKey]) <= item.radiusArcsec);
}

// ------------------------------------------------------------------ "new since you last looked"
// seen: the ids recorded the last time the visitor looked (or null the first time). Returns
// {ids, newIds, firstLook}. The page stores `ids` back after showing the result.
export function newSince(currentIds, seen) {
  const ids = [...new Set(currentIds)].sort();
  if (!seen || !Array.isArray(seen.ids)) return { ids, newIds: [], firstLook: true };
  const before = new Set(seen.ids);
  return { ids, newIds: ids.filter((x) => !before.has(x)), firstLook: false };
}

// ------------------------------------------------------------------ sharing and files
function toBase64Url(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function fromBase64Url(text) {
  const bin = atob(String(text).replace(/-/g, "+").replace(/_/g, "/"));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}
// A filter in a URL fragment (#filter=...). Only the filter travels, never the visitor's state.
export function encodeFilterForUrl(filter) {
  const { schema, title, product, match, clauses } = filter;
  return toBase64Url(JSON.stringify({ schema, title, product, match, clauses }));
}
export function decodeFilterFromUrl(text) {
  const filter = JSON.parse(fromBase64Url(text));
  const problems = validateFilter(filter);
  if (problems.length) throw new Error(problems.join(" "));
  return filter;
}

// The visitor's state as an export file, and the checks applied when one is imported.
export function exportState(state) {
  return JSON.stringify({ schema: STATE_SCHEMA, exportedAt: new Date().toISOString(),
    watchlist: state.watchlist || [], regions: state.regions || [], filters: state.filters || [] }, null, 2);
}
export function importState(text) {
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error("The file is not JSON."); }
  if (!parsed || parsed.schema !== STATE_SCHEMA) throw new Error(`The file is not a ${STATE_SCHEMA} export.`);
  const watchlist = (Array.isArray(parsed.watchlist) ? parsed.watchlist : []).map(String).slice(0, 2000);
  const regions = (Array.isArray(parsed.regions) ? parsed.regions : [])
    .filter((r) => r && typeof r.moc === "string" && r.moc.length <= 200000).slice(0, 50)
    .map((r) => ({ title: String(r.title || "Region").slice(0, 120), moc: r.moc }));
  const filters = (Array.isArray(parsed.filters) ? parsed.filters : []).filter((f) => !validateFilter(f).length).slice(0, 100);
  return { watchlist, regions, filters };
}
