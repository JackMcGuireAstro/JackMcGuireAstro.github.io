// HEALPix (NESTED) and Multi-Order Coverage maps for the visitor tools (Framework v2 Track 5).
// Pure functions: no network, no storage, no DOM. Angles in degrees. Orders up to 20 (the
// pixel numbers stay exact in a JavaScript number). The cell arithmetic follows healpix_base
// (Górski et al. 2005, ApJ 622, 759) and is checked against astropy-healpix in
// scripts/test_visitor_tools.mjs (reference values from scripts/test_visitor_products.py).
export const MAX_ORDER = 20;
const D2R = Math.PI / 180, R2D = 180 / Math.PI;
const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4], JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];

function checkOrder(order) {
  if (!Number.isInteger(order) || order < 0 || order > MAX_ORDER) throw new RangeError(`HEALPix order must be an integer 0–${MAX_ORDER}`);
}

// Interleave ix (even bits) and iy (odd bits) without 32-bit bitwise operators.
function interleave(ix, iy, order) {
  let out = 0, bit = 1;
  for (let b = 0; b < order; b += 1) {
    out += ((ix % 2) * bit) + ((iy % 2) * bit * 2);
    ix = Math.floor(ix / 2); iy = Math.floor(iy / 2); bit *= 4;
  }
  return out;
}

// NESTED pixel containing (ra, dec) at `order`.
export function ang2pixNest(order, raDeg, decDeg) {
  checkOrder(order);
  if (!Number.isFinite(raDeg) || !Number.isFinite(decDeg) || decDeg < -90 || decDeg > 90) throw new RangeError("position out of range");
  const nside = 2 ** order, npface = nside * nside;
  // z = cos(colatitude), as healpix_base and astropy-healpix compute it (same ties on cell edges).
  const z = Math.cos((90 - decDeg) * D2R), za = Math.abs(z);
  let tt = ((((raDeg % 360) + 360) % 360) * D2R) / (Math.PI / 2);
  if (tt >= 4) tt -= 4;
  let face, ix, iy;
  if (za <= 2 / 3) {
    const temp1 = nside * (0.5 + tt), temp2 = nside * z * 0.75;
    const jp = Math.floor(temp1 - temp2), jm = Math.floor(temp1 + temp2);
    const ifp = Math.floor(jp / nside), ifm = Math.floor(jm / nside);
    face = ifp === ifm ? (ifp % 4) + 4 : (ifp < ifm ? ifp % 4 : (ifm % 4) + 8);
    ix = ((jm % nside) + nside) % nside;
    iy = nside - (((jp % nside) + nside) % nside) - 1;
  } else {
    const ntt = Math.min(3, Math.floor(tt)), tp = tt - ntt;
    const tmp = nside * Math.sqrt(3 * (1 - za));
    const jp = Math.min(nside - 1, Math.floor(tp * tmp)), jm = Math.min(nside - 1, Math.floor((1 - tp) * tmp));
    if (z >= 0) { face = ntt; ix = nside - jm - 1; iy = nside - jp - 1; }
    else { face = ntt + 8; ix = jp; iy = jm; }
  }
  return face * npface + interleave(ix, iy, order);
}

// Centre of NESTED cell `ipix` at `order` as {ra, dec} in degrees.
export function pix2angNest(order, ipix) {
  checkOrder(order);
  const nside = 2 ** order, npface = nside * nside;
  if (!Number.isInteger(ipix) || ipix < 0 || ipix >= 12 * npface) throw new RangeError("pixel out of range");
  const face = Math.floor(ipix / npface);
  let rest = ipix - face * npface, ix = 0, iy = 0, bit = 1;
  for (let b = 0; b < order; b += 1) {
    const two = rest % 4; rest = Math.floor(rest / 4);
    if (two & 1) ix += bit;
    if (two & 2) iy += bit;
    bit *= 2;
  }
  const jr = JRLL[face] * nside - ix - iy - 1;
  let nr, z, kshift;
  if (jr < nside) { nr = jr; z = 1 - (nr * nr) / (3 * npface); kshift = 0; }
  else if (jr > 3 * nside) { nr = 4 * nside - jr; z = (nr * nr) / (3 * npface) - 1; kshift = 0; }
  else { nr = nside; z = ((2 * nside - jr) * 2) / (3 * nside); kshift = (jr - nside) & 1; }
  let jp = Math.floor((JPLL[face] * nr + ix - iy + 1 + kshift) / 2);
  if (jp > 4 * nside) jp -= 4 * nside;
  if (jp < 1) jp += 4 * nside;
  const phi = (jp - (kshift + 1) * 0.5) * ((Math.PI / 2) / nr);
  return { ra: ((phi * R2D) % 360 + 360) % 360, dec: 90 - Math.acos(Math.max(-1, Math.min(1, z))) * R2D };
}

export function separationDeg(ra1, dec1, ra2, dec2) {
  const a = dec1 * D2R, b = dec2 * D2R, dr = (ra2 - ra1) * D2R;
  const h = Math.sin((b - a) / 2) ** 2 + Math.cos(a) * Math.cos(b) * Math.sin(dr / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) * R2D;
}

// Largest centre-to-edge distance of any cell at the order, in degrees, with a 25 % margin
// over the largest value measured on 2 million random points (scripts/test_visitor_products.py).
const MAX_CELL_RADIUS_DEG = { 0: 60.5, 1: 34.5, 2: 18.2, 3: 9.3, 4: 4.75, 5: 2.4 };
export function maxCellRadiusDeg(order) {
  if (order in MAX_CELL_RADIUS_DEG) return MAX_CELL_RADIUS_DEG[order];
  return MAX_CELL_RADIUS_DEG[5] / 2 ** (order - 5);
}

// Every NESTED cell at `order` that can overlap the cone (a superset: cells whose centre is
// within radius + the largest cell radius). Used to choose which index shards to fetch.
export function cellsTouchingCone(order, raDeg, decDeg, radiusDeg) {
  checkOrder(order);
  if (order > 6) throw new RangeError("cellsTouchingCone enumerates every cell; use order ≤ 6");
  const reach = radiusDeg + maxCellRadiusDeg(order), out = [];
  for (let p = 0; p < 12 * 4 ** order; p += 1) {
    const c = pix2angNest(order, p);
    if (separationDeg(raDeg, decDeg, c.ra, c.dec) <= reach) out.push(p);
  }
  return out;
}

// ------------------------------------------------------------------ MOC
// A MOC as {order: Set(pixels)}. Accepts the MOC 2.0 JSON serialisation ({"3":[1,2],...},
// as mocpy's to_json and Aladin write) and the MOC 2.0 ASCII form ("3/1-3,5 4/10"; a
// leading "s" marks a spatial MOC). FITS MOCs are not read here (convert with mocpy).
export function parseMoc(text) {
  const source = String(text ?? "").trim();
  if (!source) throw new Error("The MOC is empty.");
  const moc = {};
  const add = (order, pixel) => {
    if (!Number.isInteger(order) || order < 0 || order > 29) throw new Error(`MOC order ${order} is not 0–29.`);
    if (order > MAX_ORDER) throw new Error(`MOC order ${order} is finer than this tool reads (≤ ${MAX_ORDER}).`);
    if (!Number.isInteger(pixel) || pixel < 0 || pixel >= 12 * 4 ** order) throw new Error(`MOC cell ${order}/${pixel} does not exist.`);
    (moc[order] ||= new Set()).add(pixel);
  };
  if (source.startsWith("{")) {
    let parsed;
    try { parsed = JSON.parse(source); } catch { throw new Error("The MOC is not valid JSON."); }
    for (const [key, cells] of Object.entries(parsed)) {
      if (!/^\d+$/.test(key)) continue; // e.g. a "t" time MOC entry or metadata
      if (!Array.isArray(cells)) throw new Error(`MOC order ${key} is not a list of cells.`);
      for (const cell of cells) add(Number(key), Number(cell));
    }
  } else {
    let order = null;
    for (const token of source.replace(/^s\s*/i, "").split(/[\s,]+/).filter(Boolean)) {
      let body = token;
      const slash = token.indexOf("/");
      if (slash >= 0) { order = Number(token.slice(0, slash)); body = token.slice(slash + 1); }
      if (order === null) throw new Error(`MOC text must start with an order, e.g. "3/12-15" (got "${token}").`);
      if (!body) continue;
      const range = body.match(/^(\d+)(?:-(\d+))?$/);
      if (!range) throw new Error(`"${token}" is not a MOC cell or range.`);
      const lo = Number(range[1]), hi = range[2] === undefined ? lo : Number(range[2]);
      if (hi < lo || hi - lo > 1_000_000) throw new Error(`"${token}" is not a usable range.`);
      for (let p = lo; p <= hi; p += 1) add(order, p);
    }
  }
  if (!Object.keys(moc).length) throw new Error("The MOC has no cells.");
  return moc;
}

export function mocOrders(moc) { return Object.keys(moc).map(Number).sort((a, b) => a - b); }

export function mocContains(moc, raDeg, decDeg) {
  const orders = mocOrders(moc);
  if (!orders.length) return false;
  const deepest = orders[orders.length - 1];
  const pixel = ang2pixNest(deepest, raDeg, decDeg);
  for (const order of orders) {
    if (moc[order].has(Math.floor(pixel / 4 ** (deepest - order)))) return true;
  }
  return false;
}

export function mocAreaSqDeg(moc) {
  const sky = 4 * Math.PI * R2D * R2D;
  // Overlapping cells at different orders are counted once (cells covered by a coarser one skipped).
  const orders = mocOrders(moc);
  let area = 0;
  for (const order of orders) {
    for (const pixel of moc[order]) {
      let covered = false;
      for (const coarser of orders) {
        if (coarser >= order) break;
        if (moc[coarser].has(Math.floor(pixel / 4 ** (order - coarser)))) { covered = true; break; }
      }
      if (!covered) area += sky / (12 * 4 ** order);
    }
  }
  return area;
}

// The MOC 2.0 ASCII text of a MOC (for export and shareable URLs).
export function mocToAscii(moc) {
  return mocOrders(moc).map((order) => {
    const cells = [...moc[order]].sort((a, b) => a - b), parts = [];
    for (let i = 0; i < cells.length;) {
      let j = i;
      while (j + 1 < cells.length && cells[j + 1] === cells[j] + 1) j += 1;
      parts.push(i === j ? String(cells[i]) : `${cells[i]}-${cells[j]}`);
      i = j + 1;
    }
    return `${order}/${parts.join(",")}`;
  }).join(" ");
}

// A cone as a MOC at `order` (cells whose centre lies inside; approximate at the edge).
export function coneMoc(raDeg, decDeg, radiusDeg, order = 8) {
  checkOrder(order);
  const moc = { [order]: new Set() };
  const coarse = Math.min(order, 5);
  for (const parent of cellsTouchingCone(coarse, raDeg, decDeg, radiusDeg)) {
    const span = 4 ** (order - coarse);
    for (let p = parent * span; p < (parent + 1) * span; p += 1) {
      const c = pix2angNest(order, p);
      if (separationDeg(raDeg, decDeg, c.ra, c.dec) <= radiusDeg) moc[order].add(p);
    }
  }
  if (!moc[order].size) moc[order].add(ang2pixNest(order, raDeg, decDeg));
  return moc;
}
