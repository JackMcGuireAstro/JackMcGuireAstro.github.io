// Unit checks for the shared run list (tools/lib/run-cart.js) and the run sheet that prints it
// (tonight/runsheet.js): ordering, de-duplication, the 50-target cap, blocked storage, the share
// code's round trip, the change events (this page and other tabs), the sheet's choice of list,
// and a planet's transit window tonight. No network, no DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

let passed = 0;
const test = async (name, fn) => { await fn(); passed += 1; console.log(`ok  ${name}`); };

// A minimal browser: an EventTarget for window events and a Map-backed localStorage.
const target = new EventTarget();
globalThis.addEventListener = target.addEventListener.bind(target);
globalThis.removeEventListener = target.removeEventListener.bind(target);
globalThis.dispatchEvent = target.dispatchEvent.bind(target);
class MemoryStorage {
  constructor() { this.map = new Map(); this.blocked = false; }
  check() { if (this.blocked) throw new Error("SecurityError: storage is disabled"); }
  getItem(k) { this.check(); return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.check(); this.map.set(k, String(v)); }
  removeItem(k) { this.check(); this.map.delete(k); }
}
const store = new MemoryStorage();
Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { if (store.blocked) throw new Error("SecurityError"); return store; } });

await import("../tools/lib/run-cart.js");
const Cart = globalThis.RunCart;
const a = { kind: "ctas", id: "123e4567-e89b-42d3-a456-426614174000", name: "SN2026abc", ra: 300.123456789, dec: 40.5 };
const b = { kind: "planet", id: "wi-hd-209458-b", name: "HD 209458 b", ra: 330.7950, dec: 18.8843 };
const c = { kind: "ctas", id: "223e4567-e89b-42d3-a456-426614174000", name: "AT2026xyz", ra: 10, dec: -20 };

await test("add keeps order, de-duplicates and validates", () => {
  Cart.clear();
  assert.deepEqual(Cart.add(a), { ok: true, reason: "added" });
  assert.deepEqual(Cart.add(b), { ok: true, reason: "added" });
  assert.deepEqual(Cart.add({ ...a, name: "renamed" }), { ok: true, reason: "present" });
  assert.deepEqual(Cart.list().map((x) => x.id), [a.id, b.id]);
  assert.equal(Cart.list()[0].name, "SN2026abc", "an item already listed keeps its entry");
  assert.equal(Cart.list()[0].ra, 300.123457, "positions are kept to 6 decimals");
  assert.deepEqual(Cart.add({ kind: "star", id: "x" }), { ok: false, reason: "invalid" });
  assert.deepEqual(Cart.add({ kind: "ctas", id: "" }), { ok: false, reason: "invalid" });
  assert.equal(Cart.clean({ kind: "ctas", id: "x", ra: 400, dec: 95 }).ra, null, "an impossible position is dropped, not kept");
  assert.equal(JSON.parse(store.getItem(Cart.KEY)).length, 2, "kept under one localStorage key");
});

await test("move, notes, toggle, remove and clear", () => {
  Cart.replace([a, b, c]);
  assert.equal(Cart.move("ctas", c.id, -1), true);
  assert.deepEqual(Cart.list().map((x) => x.name), ["SN2026abc", "AT2026xyz", "HD 209458 b"]);
  assert.equal(Cart.move("ctas", a.id, -1), false, "the first item cannot move up");
  assert.equal(Cart.move("planet", b.id, 5), false, "moves are clamped to the list");
  Cart.setNote("planet", b.id, "  egress only  ");
  assert.equal(Cart.list().find((x) => x.id === b.id).note, "egress only");
  Cart.setNote("planet", b.id, "");
  assert.equal("note" in Cart.list().find((x) => x.id === b.id), false);
  assert.deepEqual(Cart.toggle(c), { ok: true, reason: "removed" });
  assert.equal(Cart.has("ctas", c.id), false);
  assert.deepEqual(Cart.toggle(c), { ok: true, reason: "added" });
  assert.equal(Cart.remove("ctas", a.id), true);
  assert.equal(Cart.count(), 2);
  Cart.clear();
  assert.equal(Cart.count(), 0);
});

await test("a planet's transit window is kept and refreshed", () => {
  Cart.clear();
  Cart.add({ ...b, transitWindow: { site: "wiro", midMs: 1790000000000, ingressMs: 1789995000000, egressMs: 1790005000000, sigmaMinutes: 1.2, junk: "x" } });
  assert.deepEqual(Cart.list()[0].transitWindow, { ingressMs: 1789995000000, midMs: 1790000000000, egressMs: 1790005000000, sigmaMinutes: 1.2, site: "wiro" });
  Cart.update("planet", b.id, { transitWindow: { site: "rbo", midMs: 1790100000000 } });
  assert.equal(Cart.list()[0].transitWindow.site, "rbo");
  assert.equal(Cart.clean({ ...b, transitWindow: { site: "wiro" } }).transitWindow, undefined, "a window needs its mid-time");
});

await test("the list holds at most 50 targets", () => {
  Cart.clear();
  const many = Array.from({ length: 60 }, (_, i) => ({ kind: "ctas", id: `id-${i}`, name: `T${i}`, ra: i, dec: 0 }));
  const out = Cart.addMany(many);
  assert.deepEqual(out, { added: 50, full: 10 });
  assert.equal(Cart.count(), Cart.MAX_ITEMS);
  assert.deepEqual(Cart.add({ kind: "ctas", id: "one-more", ra: 1, dec: 1 }), { ok: false, reason: "full" });
  assert.equal(Cart.normalise(many).length, 50);
  Cart.clear();
});

await test("change events reach this page and follow other tabs", () => {
  Cart.clear();
  const seen = [];
  const off = Cart.onChange((list, origin) => seen.push([list.length, origin]));
  const events = [];
  const listener = (e) => events.push(e.detail.items.length);
  globalThis.addEventListener(Cart.EVENT, listener);
  Cart.add(a);
  // Another tab writes the list; the browser fires "storage" here.
  store.setItem(Cart.KEY, JSON.stringify([a, b]));
  const storageEvent = new Event("storage");
  Object.defineProperty(storageEvent, "key", { value: Cart.KEY });
  globalThis.dispatchEvent(storageEvent);
  const unrelated = new Event("storage");
  Object.defineProperty(unrelated, "key", { value: "something-else" });
  globalThis.dispatchEvent(unrelated);
  off(); globalThis.removeEventListener(Cart.EVENT, listener);
  assert.deepEqual(seen, [[1, "local"], [2, "storage"]]);
  assert.deepEqual(events, [1, 2]);
});

await test("blocked storage: the list lives in memory and the page keeps working", () => {
  Cart.clear();
  store.blocked = true;
  try {
    assert.equal(Cart.persistent(), false);
    assert.deepEqual(Cart.add(a), { ok: true, reason: "added" });
    Cart.add(b);
    assert.deepEqual(Cart.list().map((x) => x.id), [a.id, b.id]);
    Cart.move("planet", b.id, -1);
    assert.equal(Cart.list()[0].id, b.id);
  } finally { store.blocked = false; }
  assert.equal(Cart.persistent(), true);
  Cart.clear();
});

await test("share codes round-trip, including notes and non-ASCII names, and reject junk", () => {
  const items = [{ ...a, note: "spectrum — 2nd night" }, { ...b, name: "Kepler-1658 b ☉" }];
  const code = Cart.encode(items);
  assert.match(code, /^[A-Za-z0-9_-]+$/, "URL-safe");
  const back = Cart.decode(code);
  assert.deepEqual(back.map((x) => [x.kind, x.id, x.name, x.ra, x.dec, x.note ?? null]),
    [["ctas", a.id, "SN2026abc", 300.123457, 40.5, "spectrum — 2nd night"], ["planet", b.id, "Kepler-1658 b ☉", 330.795, 18.8843, null]]);
  assert.deepEqual(Cart.decode("not base64 !!"), []);
  assert.deepEqual(Cart.decode(Buffer.from(JSON.stringify({ x: 1 })).toString("base64url")), []);
  const url = new URL(Cart.shareUrl("https://example.org/tonight/runsheet.html", items, { site: "wiro", date: "2026-10-03" }));
  assert.equal(url.searchParams.get("site"), "wiro");
  assert.equal(Cart.decode(url.searchParams.get("list")).length, 2);
});

// ------------------------------------------------------------------ the run sheet
const runsheet = await import("../tonight/runsheet.js");
const planner = await import("../tonight/planner.js");
const { nightGrid, visibility, localNoonUtc } = await import("../tonight/astro.js");

await test("run sheet query: links carry events, planets or a share code; otherwise the top list", () => {
  const q = runsheet.parseQuery(new URLSearchParams(`events=${a.id}&planets=wi-hd-209458-b,bad id&site=rbo&alt=40`));
  assert.deepEqual(q.events, [a.id]);
  assert.deepEqual(q.planets, ["wi-hd-209458-b"]);
  assert.equal(q.top, null);
  const shared = runsheet.parseQuery(new URLSearchParams(`list=${Cart.encode([b, a])}`));
  assert.deepEqual(shared.list.map((x) => x.id), [b.id, a.id]);
  assert.equal(runsheet.parseQuery(new URLSearchParams("")).top, runsheet.DEFAULT_TOP);
});

await test("run sheet source: a link's own list first, then the run list, then the top targets", () => {
  const link = runsheet.sheetSource(runsheet.parseQuery(new URLSearchParams(`list=${Cart.encode([b])}&events=${a.id},${a.id}`)), [c]);
  assert.equal(link.mode, "link");
  assert.deepEqual(link.items.map((x) => x.id), [b.id, a.id]);
  const cart = runsheet.sheetSource(runsheet.parseQuery(new URLSearchParams("")), [c, a]);
  assert.deepEqual([cart.mode, cart.items.map((x) => x.id)], ["cart", [c.id, a.id]]);
  assert.equal(runsheet.sheetSource(runsheet.parseQuery(new URLSearchParams("")), []).mode, "top");
});

const fixture = JSON.parse(readFileSync(new URL("../tests/fixtures/tonight-transits.json", import.meta.url)));
const full = fixture.transits.find((t) => t.ephemeris.name === "Fixture Full b");
const laramie = { id: "laramie", name: "Laramie", ...fixture.sites.laramie };

await test("run sheet items: listed order, CTAS rows of any role, planets with or without an ephemeris", () => {
  const ctas = new Map([[a.id, { kind: "ctas", id: a.id, name: "SN2026abc", ra: 300, dec: 40, detail: "SN Ia", followup: null, href: "x" }]]);
  const eph = { ...full.ephemeris, hostMag: 10.2, hostMagBand: "V" };
  const ephemerides = new Map([[eph.objectId, eph]]);
  const items = runsheet.resolveItems([
    { kind: "planet", id: eph.objectId, name: "listed name", note: "baseline 1 h" },
    { kind: "ctas", id: a.id },
    { kind: "ctas", id: "missing", name: "Old one", ra: 12, dec: 3 },
    { kind: "planet", id: "no-ephemeris", name: "Astrometric b", ra: 50, dec: 10 },
    { kind: "ctas", id: "no-position" },
  ], { ctas, ephemerides });
  assert.deepEqual(items.map((t) => [t.kind, t.name]), [["planet", "Fixture Full b"], ["ctas", "SN2026abc"], ["ctas", "Old one"], ["planet", "Astrometric b"]]);
  assert.equal(items[0].note, "baseline 1 h");
  assert.match(items[2].detail, /not in the current CTAS summary/);
  assert.equal(items[3].eph, null);
});

await test("a planet's sheet entry: tonight's window from the WorldsIndex rules, or the next one", () => {
  const noonUtcMs = localNoonUtc(laramie.date, laramie.tz);
  const grid = nightGrid({ noonUtcMs, latDeg: laramie.lat, lonDeg: laramie.lon, twilightDeg: -12 });
  const eph = { ...full.ephemeris, hostMag: 10.2, hostMagBand: "V" };
  const [planet] = runsheet.resolveItems([{ kind: "planet", id: eph.objectId }], { ephemerides: new Map([[eph.objectId, eph]]) });
  planet.vis = visibility(grid, planet.ra, planet.dec, laramie.lat, 30);
  const { tonight } = runsheet.planetWindows(planet, { site: laramie, noonUtcMs, altLimit: 30 });
  assert.equal(tonight.length, 1);
  assert.ok(Math.abs(tonight[0].midMs - Date.parse(full.expected.midUtc)) < 60000, "mid-transit agrees with the astropy reference to a minute");
  const html = runsheet.planetHtml(planet, 0, { site: laramie, grid, altLimit: 30, noonUtcMs });
  assert.match(html, /data-sheet-kind="planet"/);
  assert.match(html, /Tonight’s transit window/);
  assert.match(html, /class="transit"/, "the transit is shaded on the altitude chart");
  assert.match(html, /Host star in V/);
  // The next night has no transit of a 2.5-day planet: the sheet says when the next one is.
  const nextNoon = localNoonUtc("2026-10-04", laramie.tz);
  const nextGrid = nightGrid({ noonUtcMs: nextNoon, latDeg: laramie.lat, lonDeg: laramie.lon, twilightDeg: -12 });
  const nextHtml = runsheet.planetHtml({ ...planet, vis: visibility(nextGrid, planet.ra, planet.dec, laramie.lat, 30) }, 0, { site: laramie, grid: nextGrid, altLimit: 30, noonUtcMs: nextNoon });
  assert.match(nextHtml, /No transit tonight/);
  assert.match(nextHtml, /the next is/);
});

await test("host-star exposures: V or TESS (as I) magnitudes, saturation-limited frames, placeholders marked", () => {
  const out = runsheet.hostExposures({ hostMag: 10.2, hostMagBand: "V" }, { airmass: 1.1, moonIllumination: 0.1 });
  assert.equal(out.reason, null);
  assert.deepEqual(out.rows.map((r) => r.id), ["wiro", "rbo"]);
  for (const r of out.rows) {
    assert.ok(r.seconds > 0 && r.seconds <= runsheet.HOST_EXPOSURE_CAP_S);
    assert.ok(r.snr > 100, `${r.id}: a 10th-magnitude star gives a high S/N`);
    assert.equal(r.placeholder, true, "Johnson V is still a placeholder in both profiles");
  }
  assert.ok(out.rows[0].seconds < out.rows[1].seconds, "the 2.3 m saturates sooner than the 0.6 m");
  const tess = runsheet.hostExposures({ hostMag: 9.8, hostMagBand: "TESS" }, {});
  assert.equal(tess.filter, "I");
  assert.match(tess.note, /stands in for Cousins I/);
  assert.equal(tess.rows.length, 2);
  assert.match(runsheet.hostExposures({ hostMag: 9.8, hostMagBand: "Gaia G" }, {}).reason, /do not cover/);
  assert.match(runsheet.hostExposures({ hostMag: null }, {}).reason, /no host magnitude/);
});

await test("the run list panel: order, notes, move limits and the empty-list help", () => {
  const html = runsheet.cartPanelHtml([{ ...a, note: "R < 19" }, b]);
  assert.match(html, /data-cart-item="ctas:123e4567/);
  assert.match(html, /value="R &lt; 19"/);
  assert.match(html, /aria-label="Move SN2026abc up" disabled/);
  assert.match(html, /aria-label="Move HD 209458 b down" disabled/);
  assert.match(runsheet.cartPanelHtml([]), /Your run list is empty/);
  assert.match(runsheet.cartPanelHtml([a], { persistent: false }), /not keeping the list/);
});

await test("the planner's row buttons carry the item and a transit's window for the site", () => {
  const btn = planner.runListButton({ kind: "transit", objectId: "wi-x-b", name: "X b", raDeg: 10, decDeg: 20, ingressMs: 1, midMs: 2, egressMs: 3, sigmaMinutes: 4, altMid: 50, full: true }, "wiro");
  assert.match(btn, /data-kind="planet" data-id="wi-x-b"/);
  assert.match(btn, /data-window="\{&quot;site&quot;:&quot;wiro&quot;/);
  assert.equal(planner.runListButton({ kind: "ctas", id: "x", name: "No position", ra: null, dec: null }, "wiro"), "");
  const many = planner.runListAddMany([{ kind: "ctas", id: "x", name: "A", ra: 1, dec: 2 }, { kind: "ctas", id: "y", name: "B", ra: null, dec: 2 }], "wiro", "Add these");
  assert.match(many, /data-runcart-add-many="\[\{&quot;kind&quot;:&quot;ctas&quot;,&quot;id&quot;:&quot;x&quot;/);
  assert.doesNotMatch(many, /&quot;y&quot;/);
});

console.log(`${passed} run list checks passed`);
