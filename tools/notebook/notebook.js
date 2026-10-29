// "Open in a notebook" (Framework v2 Track 5): a Jupyter notebook for one CTAS event or one
// WorldsIndex object, made in the browser from the public files, plus a link that opens the
// same code in the public JupyterLite demo (Pyodide; astropy installed with micropip).
// Nothing is stored or sent by this page; the JupyterLite demo is an outside site.
export const SITE = "https://jackmcguireastro.github.io";
export const JUPYTERLITE_REPL = "https://jupyterlite.github.io/demo/repl/index.html";

const SETUP = `# Works in Jupyter (CPython) and in JupyterLite (Pyodide).
import sys, io, json, gzip, urllib.request
if sys.platform == "emscripten":          # JupyterLite: install the packages, let urllib reach the web
    import micropip
    await micropip.install(["astropy", "matplotlib", "pyodide-http"])
    import pyodide_http
    pyodide_http.patch_all()
SITE = "${SITE}"

def fetch_bytes(path):
    with urllib.request.urlopen(f"{SITE}/{path}") as response:
        raw = response.read()
    return gzip.decompress(raw) if raw[:2] == b"\\x1f\\x8b" else raw`;

const CTAS_LOAD = (id, chunk) => `EVENT_ID = ${JSON.stringify(id)}
DETAIL_CHUNK = ${chunk ? JSON.stringify(chunk) : "None"}   # the record's dossier bucket (None: look it up)

def ctas_dossier(event_id, detail_chunk=None):
    """The complete public CTAS record: read its dossier chunk (gzip on GitHub Pages;
    multipart roots are reassembled and checked against their SHA-256)."""
    import hashlib
    if detail_chunk is None:   # the catalog index (about 14 MB) names every record's bucket
        index = json.loads(fetch_bytes("ctas/data/catalog-index.json"))
        cols = index["candidate_columns"]
        detail_chunk = next(r[cols.index("detail_chunk")] for r in index["candidate_rows"] if r[cols.index("event_id")] == event_id)
    row = {"detail_chunk": detail_chunk}
    path = "ctas/data/" + detail_chunk
    try:
        chunk = json.loads(fetch_bytes(path + ".gz"))
    except Exception:
        chunk = json.loads(fetch_bytes(path))
    if chunk.get("schema") == "ctas.candidate-chunk-parts@1.0.0":
        text = ""
        for part in chunk["parts"]:
            try:
                fragment = json.loads(fetch_bytes(part["path"] + ".gz"))
            except Exception:
                fragment = json.loads(fetch_bytes(part["path"]))
            text += fragment["json_fragment"]
        assert hashlib.sha256(text.encode()).hexdigest() == chunk["assembled_sha256"]
        chunk = json.loads(text)
    return row, next(c for c in chunk["candidates"] if c["event_id"] == event_id)

row, dossier = ctas_dossier(EVENT_ID, DETAIL_CHUNK)
print(dossier["name"], "|", dossier.get("classification"), "| RA", dossier.get("ra_deg"), "Dec", dossier.get("dec_deg"))`;

const CTAS_TABLE = `from astropy.table import Table
obs = dossier["follow_up"]["observations"]
keep = ["mjd", "band", "magnitude", "magnitude_error", "limiting_magnitude", "detection", "instrument", "provider"]
photometry = Table(rows=[[o.get(k) for k in keep] for o in obs], names=keep) if obs else Table(names=keep)
print(len(photometry), "photometry rows")
photometry[:10]`;

const CTAS_PLOT = `import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(8, 4.5))
for band in sorted(set(photometry["band"]) - {None}):
    sel = (photometry["band"] == band) & (photometry["detection"] == 1)
    if sel.any():
        ax.errorbar(photometry["mjd"][sel], photometry["magnitude"][sel], yerr=photometry["magnitude_error"][sel], fmt="o", ms=3, label=str(band))
ax.invert_yaxis(); ax.set_xlabel("MJD"); ax.set_ylabel("magnitude (as published)"); ax.legend(); ax.set_title(dossier["name"])
plt.show()`;

const CTAS_DATALINK = `# The DataLink 1.1 links document of the event's bucket (sky maps, circulars, TNS, brokers, archives).
from astropy.io.votable import parse_single_table
bucket = row["detail_chunk"].split("/")[-1].replace(".json", "")
links = parse_single_table(io.BytesIO(fetch_bytes(f"ctas/data/links/{bucket}.xml"))).to_table()
links = links[links["ID"] == EVENT_ID]
links["semantics", "description", "access_url"]`;

const WORLDS_LOAD = (id) => `OBJECT_ID = ${JSON.stringify(id)}

from astropy.table import Table
# Every retained catalog row of every planet (the WorldsIndex measurements export, ECSV 1.0).
measurements = Table.read(fetch_bytes("worldsindex/data/exports/measurements.ecsv.gz").decode(), format="ascii.ecsv")
mine = measurements[measurements["object_id"] == OBJECT_ID]
print(mine["name"][0] if len(mine) else OBJECT_ID, "|", len(mine), "catalog values")
mine["property_code", "value", "error_lower", "error_upper", "unit", "limit_type", "source_id", "reference"][:20]`;

const WORLDS_COMPARE = `# How the catalogs compare for each property (no averaging: every value is shown).
for prop in sorted(set(mine["property_code"])):
    rows = mine[mine["property_code"] == prop]
    values = ", ".join(f"{v:g} ({s})" for v, s in zip(rows["value"], rows["source_id"]) if v is not None)
    print(f"{prop}: {values}")`;

const WORLDS_PLOT = `import matplotlib.pyplot as plt
period = mine[mine["property_code"] == "orbital_period"]
fig, ax = plt.subplots(figsize=(7, 3.5))
ax.errorbar(range(len(period)), period["value"], yerr=[abs(period["error_lower"].filled(0)), period["error_upper"].filled(0)], fmt="o")
ax.set_xticks(range(len(period)), [str(s) for s in period["source_id"]], rotation=45, ha="right")
ax.set_ylabel("orbital period (days)"); ax.set_title("Orbital period, one point per catalog row")
plt.tight_layout(); plt.show()`;

function cell(kind, source) {
  const lines = source.split("\n").map((l, i, all) => (i < all.length - 1 ? `${l}\n` : l));
  return kind === "markdown" ? { cell_type: "markdown", metadata: {}, source: lines }
    : { cell_type: "code", execution_count: null, metadata: {}, outputs: [], source: lines };
}

// The notebook (nbformat 4.5) for one record. product: "ctas" | "worldsindex".
export function makeNotebook(product, id, name, release, detailChunk = null) {
  const title = name || id;
  const page = product === "ctas" ? `${SITE}/ctas.html?event=${encodeURIComponent(id)}` : `${SITE}/worldsindex/?object=${encodeURIComponent(id)}&section=object`;
  const intro = `# ${title}\n\n${product === "ctas" ? "CTAS public record" : "WorldsIndex object"} \`${id}\`, read from the public files of ${SITE} (release ${release || "current at run time"}).\n\n`
    + `Page: <${page}>. CTAS and WorldsIndex report what public sources published; a value here is not an independent measurement. Cite the original sources given in the records.`;
  const cells = product === "ctas"
    ? [cell("markdown", intro), cell("code", SETUP), cell("code", CTAS_LOAD(id, detailChunk)), cell("code", CTAS_TABLE), cell("code", CTAS_PLOT), cell("code", CTAS_DATALINK)]
    : [cell("markdown", intro), cell("code", SETUP), cell("code", WORLDS_LOAD(id)), cell("code", WORLDS_COMPARE), cell("code", WORLDS_PLOT)];
  return {
    nbformat: 4, nbformat_minor: 5,
    metadata: { kernelspec: { name: "python3", display_name: "Python 3", language: "python" }, language_info: { name: "python" },
                jm_visitor: { product, id, generated_from: SITE, release: release || null } },
    cells: cells.map((c, i) => ({ id: `cell-${i}`, ...c })),
  };
}

// The code of a notebook as one script (for the JupyterLite REPL link).
export function notebookCode(notebook) {
  return notebook.cells.filter((c) => c.cell_type === "code").map((c) => c.source.join("")).join("\n\n");
}
export function replLink(notebook) {
  const url = new URL(JUPYTERLITE_REPL);
  url.searchParams.set("kernel", "python");
  url.searchParams.set("toolbar", "1");
  url.searchParams.set("code", notebookCode(notebook));
  return url.href;
}

// ------------------------------------------------------------------ page
async function startPage() {
  const { loadCtas, loadWorlds, download, esc } = await import("../lib/visitor-data.js?v=20261029");
  const $ = (id) => document.getElementById(id);
  const params = new URL(window.location.href).searchParams;
  const product = params.has("ctas") ? "ctas" : params.has("worldsindex") ? "worldsindex" : null;
  const id = product ? params.get(product) : null;
  if (!product || !id) {
    $("nb-target").innerHTML = 'Open this page from a CTAS event or a WorldsIndex planet (“Open in a notebook”), or add <code>?ctas=&lt;event id&gt;</code> or <code>?worldsindex=&lt;object id&gt;</code> to the address.';
    return;
  }
  let record = null, release = null;
  try {
    const data = product === "ctas" ? await loadCtas() : await loadWorlds();
    release = data.now;
    record = data.records.find((r) => String(r[product === "ctas" ? "event_id" : "objectId"]) === id) || null;
  } catch (error) { $("nb-status").textContent = `The release could not be read: ${error.message}`; }
  if (!record) { $("nb-target").innerHTML = `No public record <code>${esc(id)}</code> in the current ${product === "ctas" ? "CTAS" : "WorldsIndex"} release.`; return; }
  const notebook = makeNotebook(product, id, record.name, release, product === "ctas" ? record.detail_chunk : null);
  const fileName = `${String(record.name || id).replace(/[^\w.-]+/g, "_")}.ipynb`;
  $("nb-target").innerHTML = `<strong>${esc(record.name || id)}</strong> · ${product === "ctas" ? "CTAS" : "WorldsIndex"} · <code>${esc(id)}</code> · release ${esc(release || "—")}`;
  $("nb-download").disabled = false;
  $("nb-download").addEventListener("click", () => download(fileName, JSON.stringify(notebook, null, 1) + "\n", "application/x-ipynb+json"));
  $("nb-repl").href = replLink(notebook);
  $("nb-repl").removeAttribute("aria-disabled");
  $("nb-code").textContent = notebookCode(notebook);
}
if (typeof document !== "undefined") startPage();
