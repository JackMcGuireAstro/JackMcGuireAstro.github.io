#!/usr/bin/env node
/* Deterministic release-transition regressions. No database or provider access.
 * CTAS_PLAYWRIGHT_MODULE may point to an existing Playwright installation.
 * CTAS_BROWSER_CHANNEL defaults to chrome; an installed browser is required.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const {chromium} = require(process.env.CTAS_PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");
const now = new Date("2026-09-05T12:00:00Z");
const ids = [1, 2, 3, 4].map(n => `abcdef00-0000-4000-8000-${String(n).padStart(12, "0")}`);
const digest = value => crypto.createHash("sha256").update(value).digest("hex");
const raw = value => JSON.stringify(value) + "\n";
const chunkPath = id => "candidate-chunks/" + (parseInt(digest(id).slice(0, 8), 16) % 4096).toString(16).padStart(3, "0") + ".json";
function fixture(version) {
  const checksum = version.repeat(64), documents = {};
  function put(name, value) { documents[name] = raw(value); return documents[name]; }
  const candidates = ids.slice(0, version === "a" ? 3 : 4).map((id, i) => ({
    event_id: id, name: ["AT2026fresh", "AT2026sky", "AT2025archive", "AT2026new"][i] + (version === "b" ? "B" : ""),
    event_type: "optical-transient", primary_messenger: "electromagnetic", record_role: "target-candidate",
    classification: "Unclassified", ra_deg: 10 + i * 80, dec_deg: -20 + i * 10,
    discovery_time: ["2026-09-05T00:00:00Z", "2026-09-03T00:00:00Z", "2025-09-05T00:00:00Z", "2026-09-05T06:00:00Z"][i],
    updated_at: "2026-09-05T11:00:00Z", discovery_magnitude: 18, ctas_score: 60 - i,
    discovery_survey: "Fixture", detail_chunk: chunkPath(id), status: "active", links: [],
    follow_up: {}, follow_up_counts: {}, follow_up_total: 0, designations: [], source_matrix: [],
    record_completeness: {label: "Event record only"}, source_coverage: []
  }));
  const cols = ["event_id", "name", "event_type", "primary_messenger", "classification", "ra_deg", "dec_deg", "discovery_time", "updated_at", "discovery_magnitude", "ctas_score", "detail_chunk", "status", "discovery_survey", "record_role"];
  const rows = cs => cs.map(c => cols.map(k => c[k]));
  const skyCols = ["event_id", "name", "ra_deg", "dec_deg", "discovery_magnitude", "discovery_time", "ctas_score", "classification", "record_role"];
  const skyCandidates = candidates.filter((_, i) => i !== 2);
  put("live-summary.json", {
    catalog_content_checksum_sha256: checksum, candidate_count: candidates.length,
    catalog_as_of: "2026-09-05T11:00:00Z", candidate_columns: cols, candidate_rows: rows([candidates[0]]),
    sky: {columns: skyCols, rows: skyCandidates.map(c => skyCols.map(k => c[k])), counts: {"7d": {plotted: skyCandidates.length, unlocalized: 0}}},
    leaderboard: {event_ids: [ids[0]]}, source_universe: {contract_set_checksum_sha256: "u"}, statistics: {}
  });
  put("status.json", {catalog_content_checksum_sha256: checksum, publication_state_checksum_sha256: checksum,
    candidate_count: candidates.length, pipeline_status: "operational", last_successful_update: "2026-09-05T11:55:00Z", valid_until: "2026-09-05T12:25:00Z"});
  put("source-universe.json", {contract_set_checksum_sha256: "u", sources: []});
  put("release-history.json", {releases: []});
  put("source-matrix-patterns.json", {catalog_content_checksum_sha256: checksum, patterns: {}});
  const chunks = candidates.map(c => {
    const body = put(c.detail_chunk, {candidate_count: 1, candidates: [c]});
    return {path: "ctas/data/" + c.detail_chunk, bytes: Buffer.byteLength(body), sha256: digest(body), candidate_count: 1};
  });
  put("candidate-chunks/manifest.json", {catalog_content_checksum_sha256: checksum, candidate_count: candidates.length, chunk_count: 4096, chunks});
  const pages = [candidates.slice(0, 2), candidates.slice(2)].map((cs, i) => {
    const body = put("catalog-pages/" + String(i).padStart(4, "0") + ".json", {candidate_rows: rows(cs)});
    return {page: i, bytes: Buffer.byteLength(body), sha256: digest(body)};
  });
  put("catalog-pages/manifest.json", {catalog_content_checksum_sha256: checksum, candidate_count: candidates.length, candidate_columns: cols, pages});
  put("alias-index.json", {catalog_content_checksum_sha256: checksum,
    columns: ["event_id", "source_key", "designation", "ambiguous"], rows: [
      [ids[2], "tns", "AT2025archive", false], [ids[2], "tns", "ambiguous", true], [ids[1], "ztf", "ambiguous", true]
    ]});
  return documents;
}
const releases = {a: fixture("a"), b: fixture("b")};
const server = http.createServer((req, res) => {
  const file = path.resolve(root, "." + decodeURIComponent(new URL(req.url, "http://localhost").pathname));
  if (!file.startsWith(root + path.sep)) { res.writeHead(403); return res.end(); }
  fs.readFile(file, (error, bytes) => {
    if (error) { res.writeHead(404); return res.end(); }
    const types = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".json": "application/json"};
    res.setHeader("content-type", types[path.extname(file)] || "application/octet-stream"); res.end(bytes);
  });
});
let browser, base;
async function session(query = "") {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}});
  const control = {version: "a", statusVersion: "a", requests: [], errors: [], hold: null, held: null};
  page.on("pageerror", e => control.errors.push(e.message));
  await page.clock.setFixedTime(now);
  await page.addInitScript(() => {
    const original = window.setInterval;
    window.setInterval = function (callback, delay, ...args) {
      if (delay === 120000) window.__ctasRegressionPoll = () => callback(...args);
      return original.call(window, callback, delay, ...args);
    };
  });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith("/ctas/data/")) return route.continue();
    const name = url.pathname.slice("/ctas/data/".length);
    control.requests.push(name);
    const body = releases[name === "status.json" ? control.statusVersion : control.version][name];
    if (name === control.hold) {
      control.hold = null;
      await new Promise(resolve => { control.held = resolve; });
    }
    if (body === undefined) {
      // Secondary educational/reference artifacts are real local public files.
      return route.continue();
    }
    return route.fulfill({status: 200, contentType: "application/json", body});
  });
  await page.goto(base + "/ctas.html" + query);
  await page.waitForFunction(() => window.CTASApp && CTASApp.getCandidates().length === 1);
  return {page, control};
}
async function poll(page) { await page.evaluate(() => window.__ctasRegressionPoll()); }
async function loaded(page, count) {
  await page.waitForFunction(n => CTASApp.getCandidates().length === n, count);
}
async function test(name, run) { await run(); console.log("PASS " + name); }
async function main() {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  base = "http://127.0.0.1:" + server.address().port;
  browser = await chromium.launch({channel: process.env.CTAS_BROWSER_CHANNEL || "chrome", headless: true});
  await test("complete sky rows and on-demand off-summary dossier", async () => {
    const {page, control} = await session();
    assert.equal(await page.locator("#ctas-sky-accessible option").count(), 3);
    for (const days of [1, 7, 30, 90]) {
      await page.evaluate(d => document.querySelector('[data-sky-days="' + d + '"]').click(), days);
      assert.equal(await page.locator("#ctas-sky-accessible option").count(), days === 1 ? 2 : 3);
    }
    await page.selectOption("#ctas-sky-accessible", ids[1]);
    await page.waitForSelector("#ctas-dossier-title");
    assert.match(await page.locator("#ctas-dossier-title").innerText(), /AT2026sky/);
    assert(!control.requests.some(n => n.startsWith("catalog-pages/")));
    assert.equal(await page.evaluate(() => CTASApp.getCandidates().length), 1);
    assert.deepEqual(control.errors, []); await page.close();
  });
  await test("fresh archive UUID, scoped alias, teaching alias, and ambiguity", async () => {
    for (const query of ["?event=" + ids[2] + "#dossier", "?event=" + ids[2].toUpperCase() + "&at=2025-09-05T00%3A00%3A00Z#dossier", "?alias=AT2025archive&source=tns#dossier"]) {
      const {page, control} = await session(query);
      await page.waitForSelector("#ctas-dossier-title");
      assert.match(await page.locator("#ctas-dossier-title").innerText(), /AT2025archive/);
      if (query.includes("&at=")) assert(new URL(page.url()).searchParams.has("at"), "uppercase UUID normalization must preserve same-event replay");
      assert(!control.requests.some(n => n.startsWith("catalog-pages/")));
      await page.close();
    }
    const {page, control} = await session();
    await page.evaluate(() => CTASApp.openByName("AT2025archive"));
    await page.waitForSelector("#ctas-dossier-title");
    await page.evaluate(() => CTASApp.openByName("ambiguous"));
    assert.equal(await page.locator(".ctas-ambiguity [data-open-event]").count(), 2);
    await page.locator('.ctas-ambiguity [data-open-event="' + ids[2] + '"]').click();
    await page.waitForSelector("#ctas-dossier-title");
    assert.match(await page.locator("#ctas-dossier-title").innerText(), /AT2025archive/);
    await page.evaluate(() => CTASApp.openByName("does-not-exist"));
    assert.match(await page.locator(".ctas-ambiguity").innerText(), /No public candidate/);
    assert.deepEqual(control.errors, []); await page.close();
  });
  await test("staggered publication retains old release and retries successor", async () => {
    const {page, control} = await session(); control.statusVersion = "b";
    await poll(page);
    assert.equal(await page.evaluate(() => CTASApp.getStatus().catalog_content_checksum_sha256), "a".repeat(64));
    assert.equal(await page.evaluate(() => CTASApp.getSnapshot().catalog_content_checksum_sha256), "a".repeat(64));
    assert.match(await page.locator("#ctas-status").innerText(), /Refresh not applied/);
    control.version = "b"; await poll(page);
    assert.equal(await page.evaluate(() => CTASApp.getSnapshot().catalog_content_checksum_sha256), "b".repeat(64));
    assert.equal(await page.locator("#ctas-status .ctas-cache-warning").count(), 0);
    assert.deepEqual(control.errors, []); await page.close();
  });
  await test("complete catalog can reload after a new summary release", async () => {
    const {page, control} = await session();
    await page.locator("#ctas-load-complete").click(); await loaded(page, 3);
    control.version = control.statusVersion = "b"; await poll(page); await loaded(page, 1);
    assert(await page.locator("#ctas-load-complete").isEnabled());
    await page.locator("#ctas-load-complete").click(); await loaded(page, 4);
    assert((await page.evaluate(() => CTASApp.getCandidates().map(c => c.name))).every(n => n.endsWith("B")));
    assert.deepEqual(control.errors, []); await page.close();
  });
  await test("late old catalog pages cannot overwrite the successor", async () => {
    const {page, control} = await session(); control.hold = "catalog-pages/0001.json";
    await page.locator("#ctas-load-complete").click();
    for (let n = 0; n < 100 && !control.held; n++) await new Promise(resolve => setTimeout(resolve, 20));
    assert(control.held, "the old page was not held");
    control.version = control.statusVersion = "b"; await poll(page);
    control.held(); await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => CTASApp.getCandidates().length), 1);
    assert(await page.locator("#ctas-load-complete").isEnabled());
    await page.locator("#ctas-load-complete").click(); await loaded(page, 4);
    assert.deepEqual(control.errors, []); await page.close();
  });
  await test("closing replay clears the event-specific time", async () => {
    const {page} = await session("?event=" + ids[2] + "&at=2025-09-05T00%3A00%3A00Z#dossier");
    await page.waitForSelector("#ctas-dossier-title");
    await page.locator("[data-close-candidate]").first().click();
    assert(!new URL(page.url()).searchParams.has("at")); await page.close();
  });
  await test("competing dossier requests cannot replace the latest selection", async () => {
    for (const pair of [[ids[0], ids[2]], [ids[2], ids[0]]]) {
      const {page, control} = await session(); control.hold = chunkPath(pair[0]);
      await page.evaluate(id => { void CTASApp.openById(id); }, pair[0]);
      for (let n = 0; n < 100 && !control.held; n++) await new Promise(resolve => setTimeout(resolve, 20));
      assert(control.held);
      await page.evaluate(id => CTASApp.openById(id), pair[1]);
      await page.waitForSelector("#ctas-dossier-title");
      const winner = await page.locator("#ctas-dossier-title").innerText();
      control.held(); await page.waitForTimeout(200);
      assert.equal(await page.locator("#ctas-dossier-title").innerText(), winner);
      assert.deepEqual(control.errors, []); await page.close();
    }
  });
  await test("refresh preserves external keyboard focus and status disclosure", async () => {
    const {page, control} = await session("?event=" + ids[2] + "#dossier");
    await page.waitForSelector("#ctas-dossier-title");
    await page.locator("#ctas-status details").evaluate(el => { el.open = true; });
    await page.focus("#ctas-q"); control.version = control.statusVersion = "b"; await poll(page);
    await page.waitForFunction(() => document.querySelector("#ctas-dossier-title")?.textContent.includes("archiveB"));
    assert.equal(await page.evaluate(() => document.activeElement.id), "ctas-q");
    assert(await page.locator("#ctas-status details").evaluate(el => el.open));
    assert.deepEqual(control.errors, []); await page.close();
  });
  await test("archive dossiers support compare and local watch actions", async () => {
    const {page, control} = await session("?event=" + ids[2] + "#dossier");
    await page.waitForSelector("#ctas-dossier-title");
    await page.locator("#candidate-workspace .ctas-more-actions summary").click();
    await page.locator('#candidate-workspace [data-compare-event="' + ids[2] + '"]').click();
    assert(new URL(page.url()).searchParams.get("compare").includes(ids[2]));
    await page.locator('#candidate-workspace [data-watch-event="' + ids[2] + '"]').click();
    assert((await page.evaluate(() => JSON.parse(localStorage.getItem("ctas-browser-watchlist-v1")))).includes(ids[2]));
    assert.deepEqual(control.errors, []); await page.close();
  });
  if (process.env.CTAS_REAL_BROWSER_SMOKE === "1") await test("real local release: first screen, archive teaching examples and five viewports", async () => {
    const page = await browser.newPage(), errors = [], requests = [];
    page.on("pageerror", e => errors.push(e.message));
    page.on("request", r => { if (r.url().includes("/ctas/data/")) requests.push(r.url()); });
    await page.route("**/*", route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    await page.goto(base + "/ctas.html");
    await page.waitForFunction(() => window.CTASApp && CTASApp.getCandidates().length > 0);
    assert(!requests.some(p => /catalog-pages\/|candidate-chunks\/manifest/.test(p)));
    const output = process.env.CTAS_BROWSER_OUTPUT;
    if (output) fs.mkdirSync(output, {recursive: true});
    for (const [width, height] of [[320, 568], [390, 844], [768, 1024], [1280, 720], [1440, 900]]) {
      await page.setViewportSize({width, height}); await page.waitForTimeout(150);
      assert(await page.locator("#ctas-sky-canvas").isVisible());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      if (output) await page.screenshot({path: path.join(output, "ctas-" + width + ".png")});
    }
    for (const name of ["SN2026xby", "AT2026zwn", "IceCube-250416A", "NuEm-220601A-118386"]) {
      const result = await page.evaluate(name_ => CTASApp.openByName(name_), name);
      assert(result, name + " failed resolution");
      await page.waitForSelector("#ctas-dossier-title");
    }
    assert.deepEqual(errors, []); await page.close();
  });
  console.log("9 release browser regressions passed in installed Chrome; real-release smoke " + (process.env.CTAS_REAL_BROWSER_SMOKE === "1" ? "passed" : "not requested") + ".");
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close(); server.close();
});
