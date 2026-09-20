const $=id=>document.getElementById(id);
const status=$('status'), canvas=$('canvas'), surface=$('surface'), pageInput=$('page');
const token=location.hash.slice(1);
let channel, pdf, task, renderTask, questionPage=1, pageNumber=1, renderRevision=0, received=false;
let attempts=0, retry;
function fail(message) { status.textContent=message; surface.setAttribute('aria-busy','false'); }
function request() {
  if(received)return;
  if(++attempts>12) { clearInterval(retry); fail('Your book is no longer connected. Return to the study tab, choose your PDFs, and click View question again.'); return; }
  channel.postMessage({type:'request',token});
}
async function render() {
  if(!pdf)return;
  const revision=++renderRevision;
  if(renderTask) { renderTask.cancel(); try { await renderTask.promise; } catch { /* A newer page replaced the cancelled render. */ } }
  if(revision!==renderRevision)return;
  surface.setAttribute('aria-busy','true');
  status.textContent='Opening page '+pageNumber+'…';
  canvas.hidden=true;
  $('text').textContent='';
  try {
    const page=await pdf.getPage(pageNumber);
    if(revision!==renderRevision)return;
    const original=page.getViewport({scale:1});
    const scale=$('zoom').value==='fit' ? Math.max(.15,(surface.clientWidth-2)/original.width) : Number($('zoom').value);
    const viewport=page.getViewport({scale});
    const ratio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(8000000/(viewport.width*viewport.height)));
    canvas.width=Math.floor(viewport.width*ratio);
    canvas.height=Math.floor(viewport.height*ratio);
    canvas.style.width=Math.floor(viewport.width)+'px';
    canvas.style.height=Math.floor(viewport.height)+'px';
    renderTask=page.render({canvas,viewport,transform:ratio===1?undefined:[ratio,0,0,ratio,0,0]});
    await renderTask.promise;
    if(revision!==renderRevision)return;
    canvas.hidden=false;
    canvas.setAttribute('aria-label','Textbook PDF page '+pageNumber+'. Page text is available below.');
    pageInput.value=String(pageNumber);
    $('previous').disabled=pageNumber<=1;
    $('next').disabled=pageNumber>=pdf.numPages;
    status.textContent='Page '+pageNumber+' of '+pdf.numPages+(pageNumber===questionPage?' · Question page':'');
    surface.setAttribute('aria-busy','false');
    const text=await page.getTextContent();
    if(revision===renderRevision) $('text').textContent=text.items.map(item=>item.str+(item.hasEOL?'\n':' ')).join('');
  } catch(error) {
    if(revision===renderRevision && error.name!=='RenderingCancelledException') fail('This page could not be displayed. Try another page or reconnect your book.');
  }
}
async function receive(event) {
  const value=event.data;
  if(received || value?.type!=='document' || value.token!==token || !(value.file instanceof Blob) || !Number.isInteger(value.page) || !Number.isInteger(value.pages) || value.page<1 || value.page>value.pages || typeof value.title!=='string')return;
  received=true;
  clearInterval(retry);
  channel.close();
  try {
    const renderer=await import('./reader-lib/display.mjs');
    renderer.GlobalWorkerOptions.workerSrc=new URL('./reader-lib/worker.mjs',location.href).href;
    task=renderer.getDocument({
      data:new Uint8Array(await value.file.arrayBuffer()),
      cMapUrl:new URL('./reader-lib/cmaps/',location.href).href,cMapPacked:true,
      standardFontDataUrl:new URL('./reader-lib/standard_fonts/',location.href).href,
      wasmUrl:new URL('./reader-lib/wasm/',location.href).href,
      iccUrl:new URL('./reader-lib/iccs/',location.href).href,
      enableXfa:false,
    });
    task.onPassword=()=>{ fail('Password-protected documents are not supported by this reader. Open your authorized copy in your usual PDF app.'); void task.destroy(); };
    pdf=await task.promise;
    if(pdf.numPages!==value.pages)throw new Error('Page count mismatch');
    questionPage=pageNumber=value.page;
    $('title').textContent=value.title;
    document.title=value.title+' · QualQuest';
    pageInput.max=String(pdf.numPages);
    $('total').textContent='of '+pdf.numPages;
    $('controls').hidden=false;
    $('accessible').hidden=false;
    await render();
  } catch {
    fail('The selected book could not be opened with its page map. Reconnect a supported, readable copy from the study tab.');
  }
}
function go(page) {
  if(!pdf || !Number.isInteger(page) || page<1 || page>pdf.numPages) { pageInput.value=String(pageNumber); return; }
  pageNumber=page;
  void render();
}
$('previous').onclick=()=>go(pageNumber-1);
$('next').onclick=()=>go(pageNumber+1);
$('question').onclick=()=>go(questionPage);
pageInput.onchange=()=>go(Number(pageInput.value));
$('zoom').onchange=()=>void render();
let resizeTimer;
new ResizeObserver(()=>{ clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(pdf && $('zoom').value==='fit')void render();},150); }).observe(surface);
window.addEventListener('pagehide',()=>{clearInterval(retry);channel?.close();void task?.destroy();});
if(!/^[a-f0-9-]{36}$/.test(token))fail('Open a question using View question in your QualQuest study tab.');
else if(!('BroadcastChannel' in window))fail('This reader needs a recent Safari, Firefox, or Chrome browser.');
else { channel=new BroadcastChannel('qualquest-local-reader-v1-'+token);channel.onmessage=receive;request();retry=setInterval(request,1000); }
