// SQL over the catalogs (Framework v2 Track 5): DuckDB-WASM in the visitor's browser over the
// VOParquet copies the deploy writes to tools/data/sql/ (scripts/build_visitor_products.py).
// The engine is loaded from jsDelivr at a pinned exact version when the first query runs.
// A page may set window.DUCKDB_MODULE / window.DUCKDB_BASE first (the browser test points them
// at a local copy of the same release).
import { loadProducts, siteRoot, download, esc } from "../lib/visitor-data.js?v=20261029";

export const DUCKDB_VERSION = "1.32.0";
const MODULE_URL = window.DUCKDB_MODULE || `https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@${DUCKDB_VERSION}/+esm`;
const DIST = window.DUCKDB_BASE || `https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@${DUCKDB_VERSION}/dist/`;
const SHOW_ROWS = 500, CSV_ROWS = 200000;
const $ = (id) => document.getElementById(id);

// Example queries live in examples.json (each is run against the real tables by scripts/test_visitor_products.py).
let EXAMPLES = [];

let engine = null, lastResult = null, manifest = null;

async function startEngine() {
  if (engine) return engine;
  $("sql-status").textContent = "Loading DuckDB-WASM…";
  const duckdb = await import(MODULE_URL);
  const bundles = {
    mvp: { mainModule: `${DIST}duckdb-mvp.wasm`, mainWorker: `${DIST}duckdb-browser-mvp.worker.js` },
    eh: { mainModule: `${DIST}duckdb-eh.wasm`, mainWorker: `${DIST}duckdb-browser-eh.worker.js` },
  };
  const bundle = await duckdb.selectBundle(bundles);
  const workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }));
  const worker = new Worker(workerUrl);
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  const conn = await db.connect();
  const problems = [];
  for (const table of manifest.sql.tables) {
    const url = new URL(table.path, siteRoot()).href;
    try {
      await db.registerFileURL(`${table.name}.parquet`, url, duckdb.DuckDBDataProtocol.HTTP, false);
      // DuckDB-WASM loads its Parquet reader from extensions.duckdb.org on first use.
      await conn.query(`CREATE OR REPLACE VIEW ${table.name} AS SELECT * FROM read_parquet('${table.name}.parquet')`);
    } catch (error) {
      problems.push(`${table.name}: ${error && error.message ? error.message : error}`);
    }
  }
  engine = { db, conn, problems };
  if (problems.length) {
    $("tables-line").textContent = `The engine started, but ${problems.length} table(s) could not be opened (DuckDB fetches its Parquet reader from extensions.duckdb.org; a blocker or a network filter can stop it): ${problems[0]}`;
  }
  return engine;
}

function cellText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Math.round(value * 1e8) / 1e8);
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "object" && typeof value.toJSON === "function") return JSON.stringify(value.toJSON());
  return String(value);
}

function render(result) {
  const columns = result.schema.fields.map((f) => f.name);
  const rows = [];
  const n = Math.min(result.numRows, SHOW_ROWS);
  for (let i = 0; i < n; i += 1) {
    const row = result.get(i);
    rows.push(`<tr>${columns.map((c) => `<td>${esc(cellText(row[c]))}</td>`).join("")}</tr>`);
  }
  $("result-meta").textContent = `${result.numRows.toLocaleString("en-US")} row${result.numRows === 1 ? "" : "s"}, ${columns.length} column${columns.length === 1 ? "" : "s"}`
    + (result.numRows > SHOW_ROWS ? ` (first ${SHOW_ROWS} shown; the CSV holds up to ${CSV_ROWS.toLocaleString("en-US")})` : "") + ".";
  $("result").innerHTML = columns.length ? `<div class="table-wrap"><table><caption class="visually-hidden">Query result</caption><thead><tr>${columns.map((c) => `<th scope="col">${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : "";
}

function toCsv(result) {
  const columns = result.schema.fields.map((f) => f.name);
  const quote = (v) => { const s = cellText(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const lines = [columns.map(quote).join(",")];
  for (let i = 0; i < Math.min(result.numRows, CSV_ROWS); i += 1) { const row = result.get(i); lines.push(columns.map((c) => quote(row[c])).join(",")); }
  return lines.join("\n") + "\n";
}

async function run() {
  const sql = $("sql-text").value.trim();
  if (!sql) { $("sql-status").textContent = "Write a query first."; return; }
  if (!manifest || !manifest.sql || !manifest.sql.tables.length) { $("sql-status").textContent = "No tables are available in this deployment."; return; }
  $("sql-run").disabled = true;
  try {
    const { conn } = await startEngine();
    $("sql-status").textContent = "Running…";
    const started = performance.now();
    lastResult = await conn.query(sql);
    render(lastResult);
    $("sql-csv").disabled = false;
    $("sql-status").textContent = `Done in ${((performance.now() - started) / 1000).toFixed(2)} s.`;
    $("sql-status").classList.add("ok");
  } catch (error) {
    $("sql-status").classList.remove("ok");
    $("sql-status").textContent = `The query failed: ${error && error.message ? error.message : error}`;
  } finally {
    $("sql-run").disabled = false;
  }
}

async function start() {
  $("duckdb-version").textContent = DUCKDB_VERSION;
  try { EXAMPLES = ((await (await fetch("examples.json", { cache: "no-cache" })).json()).examples || []).map((e) => [e.title, e.sql]); } catch { EXAMPLES = []; }
  $("sql-examples").innerHTML = EXAMPLES.map(([title], i) => `<button type="button" class="button button-quiet" data-example="${i}">${esc(title)}</button>`).join("");
  $("sql-examples").querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => { $("sql-text").value = EXAMPLES[Number(b.dataset.example)][1]; $("sql-text").focus(); }));
  $("sql-run").addEventListener("click", run);
  $("sql-text").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") run(); });
  $("sql-csv").addEventListener("click", () => { if (lastResult) download("query-result.csv", toCsv(lastResult), "text/csv"); });
  manifest = await loadProducts();
  if (!manifest || !manifest.sql || !manifest.sql.tables.length) {
    $("tables-line").textContent = "The SQL tables are not available in this deployment.";
    $("tables").innerHTML = '<p class="muted">The deploy builds them from the published releases; when that step does not run (a local preview, or a deployment where it failed) there is nothing to query here.</p>';
    $("sql-run").disabled = true;
    return;
  }
  const inputs = manifest.inputs || {};
  $("tables-line").textContent = `${manifest.sql.tables.length} tables from the CTAS release of ${(inputs.ctas && inputs.ctas.release_time) || "—"} and the WorldsIndex release of ${(inputs.worldsindex && inputs.worldsindex.release_time) || "—"}.`;
  $("tables").innerHTML = manifest.sql.tables.map((t) => `<h3><code>${esc(t.name)}</code> <small>${t.rows.toLocaleString("en-US")} rows · ${(t.bytes / 1e6).toFixed(1)} MB · <a href="../../${esc(t.path)}" download>Parquet</a></small></h3>`
    + `<p class="muted">From <code>${esc(t.source)}</code> (SHA-256 ${esc(t.sha256.slice(0, 12))}…).</p><ul class="schema-list">${t.columns.map((c) => `<li><code>${esc(c.name)}</code> ${esc(c.type)}${c.unit ? ` [${esc(c.unit)}]` : ""}${c.description ? ` — ${esc(c.description)}` : ""}</li>`).join("")}</ul>`).join("");
}
start();
