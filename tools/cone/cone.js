// Cone search (Framework v2 Track 5) over the deploy-time HEALPix index in tools/data/cone/
// (scripts/build_visitor_products.py). Fetches only the order-2 cells that can overlap the cone
// and measures every separation exactly; nothing is sent anywhere.
import { cellsTouchingCone, separationDeg } from "../lib/healpix.js?v=20261029";
import { parseAngle } from "../lib/astrometry.js?v=20261003";
import { loadProducts, loadConeCell, ctasLink, worldsLink, download, esc } from "../lib/visitor-data.js?v=20261029";

export const CONE_ORDER = 2, MAX_RADIUS_DEG = 10;
const PRODUCTS = { ctas: "CTAS", worldsindex: "WorldsIndex" };

// Rows of the index cells within the cone, nearest first: {product, id, name, ra, dec, distDeg, a, b, c}.
export function selectInCone(cells, ra, dec, radiusDeg) {
  const out = [];
  for (const cell of cells) {
    const at = Object.fromEntries(cell.columns.map((c, i) => [c, i]));
    for (const row of cell.rows) {
      const d = separationDeg(ra, dec, row[at.ra], row[at.dec]);
      if (d <= radiusDeg) {
        out.push({ product: cell.product, id: row[at.id], name: row[at.name], ra: row[at.ra], dec: row[at.dec], distDeg: d,
                   kind: row[at.event_type ?? at.status], label: row[at.classification ?? at.method], when: row[at.discovery_time ?? at.discovery_year] });
      }
    }
  }
  return out.sort((x, y) => x.distDeg - y.distDeg || String(x.id).localeCompare(String(y.id)));
}

const xml = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// The result as a VOTable 1.4 document in the IVOA Simple Cone Search 1.03 response form
// (a single RESOURCE/TABLE with ID, RA and Dec marked by meta.id;meta.main, pos.eq.ra;meta.main
// and pos.eq.dec;meta.main).
export function toVOTable(rows, query) {
  const fields = [
    ["id", "char", "meta.id;meta.main", null, "Stable public identifier (CTAS event UUID or WorldsIndex object id)"],
    ["catalog", "char", "meta.dataset", null, "CTAS or WorldsIndex"],
    ["name", "char", "meta.id", null, "Preferred public name"],
    ["ra", "double", "pos.eq.ra;meta.main", "deg", "ICRS right ascension as published"],
    ["dec", "double", "pos.eq.dec;meta.main", "deg", "ICRS declination as published"],
    ["dist", "double", "pos.angDistance", "deg", "Distance from the cone centre"],
    ["kind", "char", "src.class", null, "CTAS event type or WorldsIndex status"],
    ["label", "char", "src.class", null, "CTAS reported classification or WorldsIndex discovery method"],
    ["discovered", "char", "time.epoch", null, "CTAS discovery time (ISO 8601) or WorldsIndex discovery year"],
  ];
  const head = fields.map(([n, t, ucd, unit, d]) => `      <FIELD name="${n}" ID="${n}" datatype="${t}"${t === "char" ? ' arraysize="*"' : ""} ucd="${ucd}"${unit ? ` unit="${unit}"` : ""}><DESCRIPTION>${xml(d)}</DESCRIPTION></FIELD>`).join("\n");
  const body = rows.map((r) => `        <TR><TD>${xml(r.id)}</TD><TD>${xml(PRODUCTS[r.product])}</TD><TD>${xml(r.name)}</TD><TD>${r.ra}</TD><TD>${r.dec}</TD><TD>${r.distDeg.toFixed(7)}</TD><TD>${xml(r.kind)}</TD><TD>${xml(r.label)}</TD><TD>${xml(r.when)}</TD></TR>`).join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE type="results">
    <DESCRIPTION>Cone search of the public CTAS and WorldsIndex releases (jackmcguireastro.github.io). Positions are those the sources published; inclusion is not confirmation.</DESCRIPTION>
    <INFO name="QUERY_STATUS" value="OK"/>
    <PARAM name="RA" datatype="double" unit="deg" ucd="pos.eq.ra" value="${query.ra}"/>
    <PARAM name="DEC" datatype="double" unit="deg" ucd="pos.eq.dec" value="${query.dec}"/>
    <PARAM name="SR" datatype="double" unit="deg" ucd="pos.angDistance" value="${query.radiusDeg}"/>
    <INFO name="releases" value="${xml(query.releases || "")}"/>
    <TABLE name="results">
${head}
      <DATA><TABLEDATA>
${body}
      </TABLEDATA></DATA>
    </TABLE>
  </RESOURCE>
</VOTABLE>
`;
}

export function toCsv(rows) {
  const q = (v) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return ["id,catalog,name,ra_deg,dec_deg,dist_deg,kind,label,discovered",
    ...rows.map((r) => [r.id, PRODUCTS[r.product], r.name, r.ra, r.dec, r.distDeg.toFixed(7), r.kind, r.label, r.when].map(q).join(","))].join("\n") + "\n";
}

// ------------------------------------------------------------------ page
function startPage() {
  const $ = (id) => document.getElementById(id);
  let last = null, manifest = null;
  const status = (t, ok = false) => { $("cone-status").textContent = t; $("cone-status").classList.toggle("ok", ok); };
  async function search(event) {
    if (event) event.preventDefault();
    const ra = parseAngle($("cone-ra").value, "ra"), dec = parseAngle($("cone-dec").value, "dec");
    const radiusRaw = Number($("cone-radius").value), unit = $("cone-unit").value;
    const radiusDeg = unit === "deg" ? radiusRaw : unit === "arcmin" ? radiusRaw / 60 : radiusRaw / 3600;
    $("cone-ra").toggleAttribute("aria-invalid", !!ra.error); $("cone-dec").toggleAttribute("aria-invalid", !!dec.error);
    if (ra.error || dec.error) { status(ra.error || dec.error); return; }
    if (!(radiusDeg > 0 && radiusDeg <= MAX_RADIUS_DEG)) { status(`Give a radius above 0 and up to ${MAX_RADIUS_DEG} degrees.`); return; }
    const products = [["ctas", $("cone-ctas").checked], ["worldsindex", $("cone-worlds").checked]].filter(([, on]) => on).map(([p]) => p);
    if (!products.length) { status("Choose at least one catalog."); return; }
    manifest = manifest || await loadProducts();
    if (!manifest || !manifest.cone || !Object.keys(manifest.cone.products || {}).length) { status("The sky index is not available in this deployment."); return; }
    status("Searching…");
    const cellIds = cellsTouchingCone(CONE_ORDER, ra.deg, dec.deg, radiusDeg);
    try {
      const cells = await Promise.all(products.filter((p) => manifest.cone.products[p]).flatMap((p) => cellIds.map((c) => loadConeCell(p, c))));
      const rows = selectInCone(cells, ra.deg, dec.deg, radiusDeg);
      const releases = Object.entries(manifest.inputs || {}).map(([p, v]) => `${PRODUCTS[p]} ${v.release_time}`).join("; ");
      last = { rows, query: { ra: ra.deg, dec: dec.deg, radiusDeg, releases } };
      const url = new URL(window.location.href);
      url.search = `?ra=${ra.deg.toFixed(6)}&dec=${dec.deg.toFixed(6)}&r=${radiusDeg.toFixed(6)}`;
      window.history.replaceState(null, "", url);
      $("cone-meta").textContent = `${rows.length.toLocaleString("en-US")} record${rows.length === 1 ? "" : "s"} within ${(radiusDeg * 60).toFixed(radiusDeg < 1 / 60 ? 3 : 1)}′ of RA ${ra.deg.toFixed(5)}°, Dec ${dec.deg.toFixed(5)}° (${cellIds.length} index cell${cellIds.length === 1 ? "" : "s"} read; ${releases}).`;
      $("cone-results").innerHTML = rows.length ? `<div class="table-wrap"><table><caption class="visually-hidden">Cone search results</caption><thead><tr><th scope="col">Name</th><th scope="col">Catalog</th><th scope="col" class="num">Distance (′)</th><th scope="col">Type / status</th><th scope="col">Class / method</th><th scope="col">Discovered</th><th scope="col" class="num">RA (deg)</th><th scope="col" class="num">Dec (deg)</th></tr></thead><tbody>`
        + rows.slice(0, 1000).map((r) => `<tr><th scope="row"><a href="${esc(r.product === "ctas" ? ctasLink(r.id) : worldsLink(r.id))}">${esc(r.name || r.id)}</a></th><td>${PRODUCTS[r.product]}</td><td class="num">${(r.distDeg * 60).toFixed(3)}</td><td>${esc(r.kind)}</td><td>${esc(r.label)}</td><td>${esc(String(r.when ?? "").slice(0, 10))}</td><td class="num">${Number(r.ra).toFixed(5)}</td><td class="num">${Number(r.dec).toFixed(5)}</td></tr>`).join("")
        + `</tbody></table></div>${rows.length > 1000 ? '<p class="muted">The first 1,000 are shown; the downloads hold every row.</p>' : ""}` : "";
      $("cone-votable").disabled = $("cone-csv").disabled = false;
      status("Done.", true);
    } catch (error) { status(`The index could not be read: ${error.message}`); }
  }
  $("cone-form").addEventListener("submit", search);
  $("cone-votable").addEventListener("click", () => last && download("cone-search.vot", toVOTable(last.rows, last.query), "application/x-votable+xml"));
  $("cone-csv").addEventListener("click", () => last && download("cone-search.csv", toCsv(last.rows), "text/csv"));
  const params = new URL(window.location.href).searchParams;
  if (params.get("ra") && params.get("dec")) {
    $("cone-ra").value = params.get("ra"); $("cone-dec").value = params.get("dec");
    if (params.get("r")) { $("cone-radius").value = params.get("r"); $("cone-unit").value = "deg"; }
    search();
  }
}
if (typeof document !== "undefined") startPage();
