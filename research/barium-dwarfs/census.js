// Barium dwarf census page: colour–magnitude diagram, distance histogram, candidate,
// catalog and separation tables from census-data.json. Nothing is stored or sent.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, digits) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(digits));
const SAMPLE_LABEL = { ba: "Barium dwarf", wd: "White-dwarf companion" };

export function rows(data) {
  return [
    ...data.bariumDwarfs.map((r) => ({ ...r, sample: "ba" })),
    ...data.localWdCompanionStars.map((r) => ({ ...r, sample: "wd" })),
  ];
}

export function median(values) {
  const v = values.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// Distance bins on a 1-2-5 scale; returns [{lo, hi, ba, wd}].
export const DISTANCE_EDGES = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
export function distanceBins(all) {
  const bins = DISTANCE_EDGES.slice(0, -1).map((lo, i) => ({ lo, hi: DISTANCE_EDGES[i + 1], ba: 0, wd: 0 }));
  for (const r of all) {
    if (!Number.isFinite(r.distancePc)) continue;
    const bin = bins.find((b) => r.distancePc >= b.lo && r.distancePc < b.hi);
    if (bin) bin[r.sample] += 1;
  }
  return bins;
}

export function toCsv(list) {
  const cols = ["name", "sample", "teffK", "distancePc", "bpRp", "gMag", "absG"];
  const cell = (v) => (v === null || v === undefined ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  return [cols.join(","), ...list.map((r) => cols.map((c) => cell(c === "sample" ? SAMPLE_LABEL[r.sample] : r[c])).join(","))].join("\n") + "\n";
}

export function filterRows(all, { q = "", sample = "all", maxDist = NaN } = {}) {
  const needle = q.trim().toLowerCase();
  return all.filter((r) => (sample === "all" || r.sample === sample)
    && (!needle || r.name.toLowerCase().includes(needle))
    && (!Number.isFinite(maxDist) || (Number.isFinite(r.distancePc) && r.distancePc <= maxDist)));
}

export function sortRows(list, key, dir) {
  const sign = dir === "descending" ? -1 : 1;
  return [...list].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sign;
  });
}

function cmdSvg(all, show) {
  const w = 720, h = 520, L = 52, R = 14, T = 14, B = 44;
  const x0 = -0.2, x1 = 4.5, y0 = -1, y1 = 16;
  const X = (v) => L + ((v - x0) / (x1 - x0)) * (w - L - R);
  const Y = (v) => T + ((v - y0) / (y1 - y0)) * (h - T - B);
  let out = `<svg viewBox="0 0 ${w} ${h}" role="group" aria-label="Colour–magnitude diagram: Gaia BP−RP against absolute G magnitude">`;
  for (let c = 0; c <= 4; c += 1) out += `<line class="grid" x1="${X(c)}" x2="${X(c)}" y1="${T}" y2="${h - B}"/><text x="${X(c)}" y="${h - B + 16}" text-anchor="middle">${c}</text>`;
  for (let m = 0; m <= 16; m += 2) out += `<line class="grid" x1="${L}" x2="${w - R}" y1="${Y(m)}" y2="${Y(m)}"/><text x="${L - 8}" y="${Y(m) + 4}" text-anchor="end">${m}</text>`;
  out += `<text class="axis-title" x="${(L + w - R) / 2}" y="${h - 8}" text-anchor="middle">Gaia BP−RP (bluer ← → redder)</text>`;
  out += `<text class="axis-title" transform="translate(14 ${(T + h - B) / 2}) rotate(-90)" text-anchor="middle">Absolute G magnitude (brighter ↑)</text>`;
  // Comparison stars first, so the barium dwarfs (the main sample) sit on top.
  const order = all.map((r, i) => [r, i]).sort((a, b) => (a[0].sample === "wd" ? 0 : 1) - (b[0].sample === "wd" ? 0 : 1));
  order.forEach(([r, i]) => {
    if (!show[r.sample] || !Number.isFinite(r.bpRp) || !Number.isFinite(r.absG)) return;
    const label = `${r.name}, ${SAMPLE_LABEL[r.sample].toLowerCase()}: BP−RP ${fmt(r.bpRp, 2)}, M_G ${fmt(r.absG, 2)}`;
    out += `<circle class="pt-${r.sample}" data-row="${i}" cx="${X(r.bpRp).toFixed(1)}" cy="${Y(r.absG).toFixed(1)}" r="${r.sample === "ba" ? 5 : 4.5}" tabindex="0" role="button" aria-label="${esc(label)}"><title>${esc(r.name)}</title></circle>`;
  });
  return out + "</svg>";
}

function histSvg(bins) {
  const w = 720, h = 260, L = 44, R = 12, T = 12, B = 40;
  const top = Math.max(1, ...bins.map((b) => Math.max(b.ba, b.wd)));
  const bw = (w - L - R) / bins.length;
  const Y = (v) => h - B - (v / top) * (h - T - B);
  let out = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Distance histogram: ${esc(bins.map((b) => `${b.lo}–${b.hi} pc: ${b.ba} barium dwarfs, ${b.wd} comparison stars`).join("; "))}">`;
  for (let k = 0; k <= top; k += Math.max(1, Math.ceil(top / 5))) out += `<line class="grid" x1="${L}" x2="${w - R}" y1="${Y(k)}" y2="${Y(k)}"/><text x="${L - 6}" y="${Y(k) + 4}" text-anchor="end">${k}</text>`;
  bins.forEach((b, i) => {
    const x = L + i * bw;
    out += `<rect class="bar-wd" x="${(x + bw * 0.12).toFixed(1)}" y="${Y(b.wd).toFixed(1)}" width="${(bw * 0.36).toFixed(1)}" height="${(h - B - Y(b.wd)).toFixed(1)}"/>`;
    out += `<rect class="bar-ba" x="${(x + bw * 0.52).toFixed(1)}" y="${Y(b.ba).toFixed(1)}" width="${(bw * 0.36).toFixed(1)}" height="${(h - B - Y(b.ba)).toFixed(1)}"/>`;
    out += `<text x="${(x + bw / 2).toFixed(1)}" y="${h - B + 15}" text-anchor="middle">${b.lo}–${b.hi}</text>`;
  });
  return out + `<text class="axis-title" x="${(L + w - R) / 2}" y="${h - 6}" text-anchor="middle">Distance (pc)</text></svg>`;
}

function links(name) {
  const q = encodeURIComponent(name);
  return `<a href="https://simbad.cds.unistra.fr/simbad/sim-id?Ident=${q}" target="_blank" rel="noopener">SIMBAD<span class="visually-hidden"> for ${esc(name)} (opens in a new tab)</span></a> · <a href="../../tools/finder/?name=${q}">Finder<span class="visually-hidden"> chart for ${esc(name)}</span></a>`;
}

async function init() {
  const $ = (s) => document.querySelector(s);
  const data = await fetch("census-data.json", { cache: "no-cache" }).then((r) => r.json());
  const all = rows(data);
  const show = { ba: true, wd: true };
  const state = { key: "distancePc", dir: "ascending" };
  $("#count-ba").textContent = data.bariumDwarfs.length;
  $("#count-wd").textContent = data.localWdCompanionStars.length;
  $("#median-ba").textContent = fmt(median(data.bariumDwarfs.map((r) => r.distancePc)), 0);
  $("#median-wd").textContent = fmt(median(data.localWdCompanionStars.map((r) => r.distancePc)), 0);

  const drawCmd = () => { $("#cmd").innerHTML = cmdSvg(all, show); };
  const pick = (index) => {
    const r = all[index];
    if (!r) return;
    document.querySelectorAll("#cmd .is-picked").forEach((n) => n.classList.remove("is-picked"));
    document.querySelector(`#cmd [data-row="${index}"]`)?.classList.add("is-picked");
    $("#picked").innerHTML = `<strong>${esc(r.name)}</strong> · ${SAMPLE_LABEL[r.sample]} · T<sub>eff</sub> ${fmt(r.teffK, 0)} K · ${fmt(r.distancePc, 1)} pc · BP−RP ${fmt(r.bpRp, 2)} · G ${fmt(r.gMag, 2)} · M<sub>G</sub> ${fmt(r.absG, 2)} · ${links(r.name)}`;
  };
  $("#cmd").addEventListener("click", (e) => { const n = e.target.closest("[data-row]"); if (n) pick(Number(n.dataset.row)); });
  $("#cmd").addEventListener("keydown", (e) => { const n = e.target.closest("[data-row]"); if (n && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(Number(n.dataset.row)); } });
  $("#show-ba").addEventListener("change", (e) => { show.ba = e.target.checked; drawCmd(); });
  $("#show-wd").addEventListener("change", (e) => { show.wd = e.target.checked; drawCmd(); });
  drawCmd();
  $("#hist").innerHTML = histSvg(distanceBins(all));

  $("#cand-table tbody").innerHTML = data.candidates.map((c) => `<tr><th scope="row">${esc(c.name)}</th><td>${esc(c.fuvSigma)}</td><td>${esc(c.nuvSigma)}</td><td>${esc(c.period)}</td><td>${esc(c.k)}</td><td>${esc(c.feH)}</td><td>${esc(c.baFe)}</td><td>${esc(c.source)}</td></tr>`).join("");
  $("#sep-table tbody").innerHTML = data.bariumDwarfSeparations.map((s) => `<tr><th scope="row">${esc(s.name)}</th><td>${esc(s.spectralType)}</td><td class="num">${fmt(s.parallaxMas, 3)}</td><td class="num">${fmt(s.separationAu, 2)}</td></tr>`).join("");

  const drawTable = () => {
    const list = sortRows(filterRows(all, { q: $("#q").value, sample: $("#sample").value, maxDist: $("#maxdist").value === "" ? NaN : Number($("#maxdist").value) }), state.key, state.dir);
    $("#cat-table tbody").innerHTML = list.map((r) => `<tr><th scope="row">${esc(r.name)}</th><td>${SAMPLE_LABEL[r.sample]}</td><td class="num">${fmt(r.teffK, 0)}</td><td class="num">${fmt(r.distancePc, 2)}</td><td class="num">${fmt(r.bpRp, 2)}</td><td class="num">${fmt(r.gMag, 2)}</td><td class="num">${fmt(r.absG, 2)}</td><td>${links(r.name)}</td></tr>`).join("");
    $("#rowcount").textContent = `${list.length} of ${all.length} stars`;
    $("#csv").href = `data:text/csv;charset=utf-8,${encodeURIComponent(toCsv(list))}`;
    document.querySelectorAll("#cat-table thead th").forEach((th) => {
      const b = th.querySelector("button[data-sort]");
      if (!b) return;
      if (b.dataset.sort === state.key) th.setAttribute("aria-sort", state.dir); else th.removeAttribute("aria-sort");
    });
  };
  $("#filters").addEventListener("input", drawTable);
  $("#filters").addEventListener("submit", (e) => e.preventDefault());
  document.querySelector("#cat-table thead").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sort]");
    if (!b) return;
    state.dir = state.key === b.dataset.sort && state.dir === "ascending" ? "descending" : "ascending";
    state.key = b.dataset.sort;
    drawTable();
  });
  drawTable();
}

if (typeof document !== "undefined" && document.getElementById("cmd")) init().catch((error) => {
  const p = document.getElementById("picked");
  if (p) p.textContent = `The census data could not be loaded (${error.message}).`;
});
