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
export function relativeFlux(points) {
  const sorted=points.map(p=>p[1]).sort((a,b)=>a-b),n=sorted.length;
  if(!n)return [];
  const median=n%2?sorted[(n-1)/2]:(sorted[n/2-1]+sorted[n/2])/2;
  return points.map(p=>{const flux=10**(-.4*(p[1]-median));return [p[0],flux,Math.log(10)*.4*flux*p[2],p[3],p[4]];});
}
