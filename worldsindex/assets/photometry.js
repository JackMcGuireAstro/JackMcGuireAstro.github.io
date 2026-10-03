// Geometry and data transformations run entirely in the visitor's browser.
export function phase(time, period, epoch) {
  if (![time,period,epoch].every(Number.isFinite) || period<=0) return null;
  return ((time-epoch)/period+.5-Math.floor((time-epoch)/period+.5))-.5;
}
export function overlap(z,k) {
  if(!Number.isFinite(z)||!Number.isFinite(k)||z<0||k<=0) return 0;
  if(z>=1+k)return 0;
  if(z<=Math.abs(1-k))return Math.min(1,k*k);
  const clamp=x=>Math.max(-1,Math.min(1,x));
  const a=Math.acos(clamp((z*z+1-k*k)/(2*z)));
  const b=Math.acos(clamp((z*z+k*k-1)/(2*z*k)));
  return (a+k*k*b-.5*Math.sqrt(Math.max(0,(-z+1+k)*(z+1-k)*(z-1+k)*(z+1+k))))/Math.PI;
}
export function transitFlux(time,{period,epoch,ratio,scaledAxis,impact}) {
  if(![time,period,epoch,ratio,scaledAxis,impact].every(Number.isFinite)||period<=0||ratio<=0||scaledAxis<=1+ratio||impact<0||impact>scaledAxis)return null;
  const angle=2*Math.PI*((time-epoch)/period);
  if(Math.cos(angle)<0)return 1;
  return 1-overlap(Math.hypot(scaledAxis*Math.sin(angle),impact*Math.cos(angle)),ratio);
}
export function eccentricAnomaly(mean,e) {
  if(!Number.isFinite(mean)||!Number.isFinite(e)||e<0||e>=1)return null;
  let E=e<.8?mean:Math.PI;
  for(let i=0;i<30;i++){const step=(E-e*Math.sin(E)-mean)/(1-e*Math.cos(E));E-=step;if(Math.abs(step)<1e-12)break;}
  return E;
}
export function finite(value) { if(value===null||value===undefined||String(value).trim()==='')return null;const n=Number(value);return Number.isFinite(n)?n:null; }
export function relativeFlux(points, unit="mag") {
  const sorted=points.map(p=>p[1]).sort((a,b)=>a-b),n=sorted.length;
  if(!n)return [];
  const median=n%2?sorted[(n-1)/2]:(sorted[n/2-1]+sorted[n/2])/2;
  if(unit==='e-/s'){if(!(median>0))return [];return points.map(p=>[p[0],p[1]/median,p[2]/median,p[3],p[4]]);}
  if(unit!=='mag')return [];
  return points.map(p=>{const flux=10**(-.4*(p[1]-median));return [p[0],flux,Math.log(10)*.4*flux*p[2],p[3],p[4]];});
}

// Every value comes from one selected source row; missing values stay missing.
export function publishedTransitParameters(row) {
 const v=row?.values||{};
 const measured=(key)=>finite(v[key+'lim'])===null||finite(v[key+'lim'])===0?finite(v[key]):null;
 return {period:measured('pl_orbper'),epoch:measured('pl_tranmid'),ratio:measured('pl_ratror')??(measured('pl_rade')>0&&measured('st_rad')>0?measured('pl_rade')*.00916794/measured('st_rad'):null),axis:measured('pl_ratdor')??(measured('pl_orbsmax')>0&&measured('st_rad')>0?measured('pl_orbsmax')*215.032/measured('st_rad'):null),impact:measured('pl_imppar')};
}

// ------------------------------------------------------------------ folding on an ephemeris
// Points are [time BJD_TDB, relative flux, error, ...]. All helpers are pure.
export function median(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b), n = sorted.length;
  if (!n) return null;
  return n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
}
// Signed time from the nearest mid-transit, in days (-P/2 .. +P/2).
export function phaseDays(time, period, epoch) {
  const p = phase(time, period, epoch);
  return p === null ? null : p * period;
}
// [[days from mid-transit, flux, error], ...] sorted by phase.
export function foldPoints(points, period, epoch) {
  if (!(period > 0) || !Number.isFinite(epoch)) return [];
  return points.map((p) => [phaseDays(p[0], period, epoch), p[1], p[2]]).filter((p) => p[0] !== null).sort((a, b) => a[0] - b[0]);
}
// Median flux in bins of `binDays` across [-halfWindowDays, +halfWindowDays]: [{x, y, n}].
export function binFolded(folded, binDays, halfWindowDays) {
  if (!(binDays > 0) || !(halfWindowDays > 0)) return [];
  const count = Math.max(1, Math.round((2 * halfWindowDays) / binDays)), width = (2 * halfWindowDays) / count;
  const bins = Array.from({ length: count }, () => []);
  for (const [x, y] of folded) {
    if (x < -halfWindowDays || x >= halfWindowDays || !Number.isFinite(y)) continue;
    bins[Math.min(count - 1, Math.floor((x + halfWindowDays) / width))].push(y);
  }
  return bins.map((ys, i) => ({ x: -halfWindowDays + (i + 0.5) * width, y: median(ys), n: ys.length })).filter((b) => b.n > 0);
}
// True when `time` is within the transit window (duration × pad, centred on mid-transit).
export function inTransit(time, period, epoch, durationDays, pad = 1) {
  const d = phaseDays(time, period, epoch);
  return d !== null && durationDays > 0 && Math.abs(d) <= (durationDays * pad) / 2;
}
// Divide out slow trends: the median of the out-of-transit flux in consecutive time bins of
// `windowDays`, linearly interpolated between bin centres (held flat beyond the ends).
// `masked(time)` marks points left out of the trend (e.g. in transit). Returns new points.
export function flattenPoints(points, windowDays, masked = () => false) {
  if (!(windowDays > 0) || points.length < 3) return points.slice();
  const sorted = points.slice().sort((a, b) => a[0] - b[0]), start = sorted[0][0], groups = new Map();
  for (const p of sorted) {
    if (masked(p[0]) || !Number.isFinite(p[1])) continue;
    const key = Math.floor((p[0] - start) / windowDays);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  const knots = [...groups.values()].filter((g) => g.length >= 3)
    .map((g) => ({ t: median(g.map((p) => p[0])), f: median(g.map((p) => p[1])) })).filter((k) => k.f > 0)
    .sort((a, b) => a.t - b.t);
  if (!knots.length) return sorted;
  let k = 0;
  return sorted.map((p) => {
    while (k < knots.length - 2 && knots[k + 1].t < p[0]) k += 1;
    let trend;
    if (knots.length === 1 || p[0] <= knots[0].t) trend = knots[0].f;
    else if (p[0] >= knots[knots.length - 1].t) trend = knots[knots.length - 1].f;
    else { const a = knots[k], b = knots[k + 1]; trend = a.f + ((b.f - a.f) * (p[0] - a.t)) / (b.t - a.t); }
    return [p[0], p[1] / trend, p[2] / trend, ...p.slice(3)];
  });
}
// A rough observed depth in parts per thousand: median flux outside ±1 duration minus the
// median inside the central half of the transit. Null without enough points on either side.
export function foldedDepthPpt(folded, durationDays) {
  if (!(durationDays > 0)) return null;
  const inside = folded.filter((p) => Math.abs(p[0]) <= durationDays / 4).map((p) => p[1]);
  const outside = folded.filter((p) => Math.abs(p[0]) >= durationDays).map((p) => p[1]);
  if (inside.length < 3 || outside.length < 10) return null;
  return (median(outside) - median(inside)) * 1000;
}
