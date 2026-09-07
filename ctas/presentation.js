/* Archival positional context, never a claimed transient detection. */
(function(root){
  "use strict";
  function esc(s){return String(s == null ? "" : s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
  function thumbnail(c){
    if(!c || c.ra_deg == null || c.dec_deg == null || c.ra_deg === "" || c.dec_deg === "" || !Number.isFinite(Number(c.ra_deg)) || !Number.isFinite(Number(c.dec_deg)) || Number(c.ra_deg)<0 || Number(c.ra_deg)>=360 || Math.abs(Number(c.dec_deg))>90) return '<span class="ctas-thumb ctas-thumb--missing" title="No valid archival image position">No position</span>';
    var q=new URLSearchParams({hips:"CDS/P/DSS2/color",width:"96",height:"96",projection:"TAN",fov:"0.05",ra:String(Number(c.ra_deg)),dec:String(Number(c.dec_deg)),coordsys:"icrs",format:"jpg"});
    return '<span class="ctas-thumb" title="DSS2 archival field, 3 arcmin; not a detection of the candidate"><img data-ctas-thumbnail src="https://alasky.cds.unistra.fr/hips-image-services/hips2fits?'+esc(q.toString())+'" alt="Archival DSS2 field at '+esc(c.name)+'" loading="lazy" decoding="async" referrerpolicy="no-referrer"><span aria-hidden="true">DSS2</span></span>';
  }
  function weight(c){var n=c.follow_up_counts||{};return ['observations','spectra','publications','classifications'].reduce(function(sum,k){return sum+Math.max(0,Number(n[k])||0);},0);}
  function examples(rows,key){return rows.filter(function(c){var n=c.follow_up_counts||{};return Number(n.observations)>0||Number(n.spectra)>0;}).slice().sort(function(a,b){return (key==='total'?weight(b)-weight(a):(Number((b.follow_up_counts||{})[key])||0)-(Number((a.follow_up_counts||{})[key])||0))||weight(b)-weight(a)||String(a.event_id).localeCompare(String(b.event_id));});}
  if(typeof document!=="undefined")document.querySelectorAll('[data-open-name][data-preview-ra]').forEach(function(button){button.insertAdjacentHTML('afterbegin',thumbnail({name:button.getAttribute('data-open-name'),ra_deg:button.getAttribute('data-preview-ra'),dec_deg:button.getAttribute('data-preview-dec')}));});
  root.CTASPresentation={thumbnail:thumbnail,examples:examples,weight:weight};
  if(typeof document!=="undefined")document.addEventListener('error',function(e){if(e.target.matches&&e.target.matches('[data-ctas-thumbnail]')){var p=e.target.parentElement;p.classList.add('ctas-thumb--missing');p.textContent='No image';p.title='Archival provider image unavailable';}},true);
  if(typeof module!=="undefined")module.exports=root.CTASPresentation;
}(typeof window!=="undefined"?window:globalThis));
