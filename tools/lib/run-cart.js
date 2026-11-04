// The run list ("cart"): one ordered list of observing targets shared by CTAS, WorldsIndex,
// the Tonight planner and the printable run sheet. It lives in this browser's localStorage
// under one key; nothing is sent anywhere. When storage is blocked (private windows, strict
// settings) the list lives in memory for the page and can still travel in a share link.
//
// A classic script: it registers globalThis.RunCart, so the CTAS page (classic scripts), the
// ES-module pages (which import it for its side effect) and Node tests all use the same code.
// Items: {kind: "ctas"|"planet", id, name, ra, dec, addedAt, note?, transitWindow?}.
(function (root) {
  "use strict";
  var KEY = "jm.runlist.v1";
  var MAX_ITEMS = 50;
  var KINDS = { ctas: 1, planet: 1 };
  var EVENT = "runcart:change";
  var memory = null;      // used when storage is not available
  var listeners = [];

  function finite(value) { return typeof value === "number" && isFinite(value); }
  function text(value, max) { return value === null || value === undefined ? "" : String(value).replace(/[\u0000-\u001f]/g, " ").trim().slice(0, max); }
  function round(value, places) { var f = Math.pow(10, places); return Math.round(value * f) / f; }

  // A transit window as the WorldsIndex planner computes it (UTC milliseconds); anything else is dropped.
  function cleanWindow(w) {
    if (!w || typeof w !== "object") return null;
    var out = {};
    ["ingressMs", "midMs", "egressMs", "windowStartMs", "windowEndMs", "sigmaMinutes", "altMid"].forEach(function (k) { if (finite(w[k])) out[k] = w[k]; });
    if (!finite(out.midMs)) return null;
    var site = text(w.site, 40); if (site) out.site = site;
    var state = text(w.state, 20); if (state) out.state = state;
    return out;
  }

  // One item, validated; null when it cannot be a target.
  function clean(item) {
    if (!item || typeof item !== "object" || !KINDS[item.kind]) return null;
    var id = text(item.id, 120); if (!id) return null;
    var ra = Number(item.ra), dec = Number(item.dec);
    var out = { kind: item.kind, id: id, name: text(item.name, 120) || id,
      ra: finite(ra) && ra >= 0 && ra < 360 ? round(ra, 6) : null, dec: finite(dec) && dec >= -90 && dec <= 90 ? round(dec, 6) : null,
      addedAt: finite(item.addedAt) ? item.addedAt : Date.now() };
    var note = text(item.note, 200); if (note) out.note = note;
    var win = cleanWindow(item.transitWindow); if (win) out.transitWindow = win;
    return out;
  }
  function sameItem(a, b) { return a.kind === b.kind && a.id === b.id; }

  // An ordered, de-duplicated, capped list of clean items.
  function normalise(items) {
    var out = [];
    (Array.isArray(items) ? items : []).forEach(function (raw) {
      var item = clean(raw);
      if (item && !out.some(function (x) { return sameItem(x, item); }) && out.length < MAX_ITEMS) out.push(item);
    });
    return out;
  }

  function storage() {
    try { var s = root.localStorage; var probe = "jm.runlist.probe"; s.setItem(probe, "1"); s.removeItem(probe); return s; } catch (_) { return null; }
  }
  function read() {
    var s = storage();
    if (!s) return normalise(memory || []);
    try { var raw = s.getItem(KEY); return raw ? normalise(JSON.parse(raw)) : []; } catch (_) { return []; }
  }
  function write(items) {
    var list = normalise(items), s = storage();
    if (s) { try { s.setItem(KEY, JSON.stringify(list)); memory = null; } catch (_) { memory = list; } }
    else memory = list;
    notify(list, "local");
    return list;
  }
  function notify(list, origin) {
    listeners.slice().forEach(function (fn) { try { fn(list, origin); } catch (_) { /* a listener must not break the others */ } });
    if (root.dispatchEvent && typeof root.CustomEvent === "function") {
      try { root.dispatchEvent(new root.CustomEvent(EVENT, { detail: { items: list, origin: origin } })); } catch (_) { /* best effort */ }
    }
  }

  function find(list, kind, id) { for (var i = 0; i < list.length; i++) if (list[i].kind === kind && list[i].id === String(id)) return i; return -1; }

  var api = {
    KEY: KEY, MAX_ITEMS: MAX_ITEMS, EVENT: EVENT,
    clean: clean, normalise: normalise,
    list: function () { return read(); },
    count: function () { return read().length; },
    has: function (kind, id) { return find(read(), kind, id) !== -1; },
    persistent: function () { return Boolean(storage()); },
    // Adds at the end; an item already in the list keeps its place (its window or note may be refreshed).
    // Returns {ok, reason} — reason "full" when the list holds MAX_ITEMS, "invalid" for a bad item.
    add: function (item) {
      var next = clean(item); if (!next) return { ok: false, reason: "invalid" };
      var list = read(), at = find(list, next.kind, next.id);
      if (at !== -1) {
        if (next.transitWindow) list[at].transitWindow = next.transitWindow;
        write(list); return { ok: true, reason: "present" };
      }
      if (list.length >= MAX_ITEMS) return { ok: false, reason: "full" };
      list.push(next); write(list); return { ok: true, reason: "added" };
    },
    addMany: function (items) {
      var list = read(), added = 0, full = 0;
      (items || []).forEach(function (raw) {
        var item = clean(raw); if (!item || find(list, item.kind, item.id) !== -1) return;
        if (list.length >= MAX_ITEMS) { full += 1; return; }
        list.push(item); added += 1;
      });
      write(list); return { added: added, full: full };
    },
    remove: function (kind, id) { var list = read(), at = find(list, kind, id); if (at !== -1) { list.splice(at, 1); write(list); } return at !== -1; },
    toggle: function (item) { var c = clean(item); if (!c) return { ok: false, reason: "invalid" }; return api.has(c.kind, c.id) ? (api.remove(c.kind, c.id), { ok: true, reason: "removed" }) : api.add(c); },
    // Moves an item by `delta` places (−1 up, +1 down), clamped to the list.
    move: function (kind, id, delta) {
      var list = read(), at = find(list, kind, id); if (at === -1) return false;
      var to = Math.max(0, Math.min(list.length - 1, at + delta)); if (to === at) return false;
      var item = list.splice(at, 1)[0]; list.splice(to, 0, item); write(list); return true;
    },
    setNote: function (kind, id, note) { var list = read(), at = find(list, kind, id); if (at === -1) return false; var n = text(note, 200); if (n) list[at].note = n; else delete list[at].note; write(list); return true; },
    update: function (kind, id, fields) { var list = read(), at = find(list, kind, id); if (at === -1) return false; var merged = clean(Object.assign({}, list[at], fields, { kind: list[at].kind, id: list[at].id })); if (!merged) return false; list[at] = merged; write(list); return true; },
    replace: function (items) { return write(items); },
    clear: function () { return write([]); },
    onChange: function (fn) { listeners.push(fn); return function () { listeners = listeners.filter(function (x) { return x !== fn; }); }; },

    // Share links: the list in the address, so it moves between devices without a server.
    // A compact JSON array of [kind letter, id, name, ra, dec, note?] rows, base64url-encoded.
    encode: function (items) {
      var rows = normalise(items).map(function (x) {
        var row = [x.kind === "planet" ? "p" : "c", x.id, x.name, x.ra, x.dec];
        if (x.note) row.push(x.note);
        return row;
      });
      var json = JSON.stringify(rows), bytes = unescape(encodeURIComponent(json));
      var b64 = typeof btoa === "function" ? btoa(bytes) : Buffer.from(bytes, "binary").toString("base64");
      return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    },
    decode: function (value) {
      try {
        var b64 = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
        while (b64.length % 4) b64 += "=";
        var bytes = typeof atob === "function" ? atob(b64) : Buffer.from(b64, "base64").toString("binary");
        var rows = JSON.parse(decodeURIComponent(escape(bytes)));
        if (!Array.isArray(rows)) return [];
        return normalise(rows.map(function (r) {
          return Array.isArray(r) ? { kind: r[0] === "p" ? "planet" : r[0] === "c" ? "ctas" : null, id: r[1], name: r[2], ra: r[3], dec: r[4], note: r[5] } : null;
        }));
      } catch (_) { return []; }
    },
    // The run sheet address carrying `items` (absolute `sheetUrl`, e.g. https://…/tonight/runsheet.html).
    shareUrl: function (sheetUrl, items, params) {
      var url = new URL(sheetUrl);
      Object.keys(params || {}).forEach(function (k) { if (params[k]) url.searchParams.set(k, params[k]); });
      url.searchParams.set("list", api.encode(items === undefined ? read() : items));
      return url.href;
    }
  };

  // Other tabs: the browser fires "storage" there when this list changes.
  if (root.addEventListener) {
    root.addEventListener("storage", function (event) {
      if (event.key === KEY || event.key === null) notify(read(), "storage");
    });
  }
  root.RunCart = api;
  if (typeof module === "object" && module && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
