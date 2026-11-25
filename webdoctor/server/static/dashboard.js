let MODEL_CAT = [];
// Defaults mirror webdoctor.config; the full list comes from /api/models so the
// dashboard never advertises stale or retired model IDs.
const DEFAULT_MODELS = {
  sandbox: "gpt-6-luna",
  openai: "gpt-6.2-sol",
  anthropic: "claude-sonnet-5-5",
  gemini: "gemini-4-hydrogen",
};
async function loadModels(){
  try{ const r=await fetch('/api/models'); const d=await r.json(); MODEL_CAT=d.models||[]; }catch(e){}
  onProvChange();
}
function onProvChange(){
  const p=document.getElementById('ai_p').value; const sel=document.getElementById('ai_m'); sel.innerHTML='';
  let ids=MODEL_CAT.filter(m=>m.provider===p).map(m=>m.id);
  if(!ids.length) ids=[DEFAULT_MODELS[p]||'gpt-6-luna'];
  if(DEFAULT_MODELS[p] && !ids.includes(DEFAULT_MODELS[p])) ids.unshift(DEFAULT_MODELS[p]);
  ids.forEach(id=>{ const m=MODEL_CAT.find(x=>x.id===id); const o=document.createElement('option');
    o.value=id; o.textContent=(m&&m.label)?m.label:id; sel.appendChild(o); });
  sel.value=DEFAULT_MODELS[p]||ids[0];
}
loadModels();
clearExports();

document.getElementById('f').addEventListener('submit', async e=>{
  e.preventDefault();
  const go=document.getElementById('go'); go.disabled=true;
  const prog=document.getElementById('prog'); prog.style.display='block';
  hideResults();
  const body={
    url: document.getElementById('u').value,
    browser: document.getElementById('cb_b').checked,
    links: document.getElementById('cb_l').checked,
    ai: document.getElementById('cb_ai').checked,
    ai_provider: document.getElementById('ai_p').value,
    ai_model: document.getElementById('ai_m').value,
    ai_key: document.getElementById('ai_k').value||null,
    ai_compare: document.getElementById('cb_cmp').checked,
    pdf: document.getElementById('cb_pdf').checked,
    login: document.getElementById('cb_login').checked,
    login_url: document.getElementById('login_url').value||null,
    username: document.getElementById('login_u').value||null,
    password: document.getElementById('login_p').value||null,
    session_json: document.getElementById('sess_json').value.trim()||null,
    monkey: document.getElementById('cb_monkey').checked,
    monkey_seed: parseInt(document.getElementById('monkey_seed').value,10)||20261124,
    monkey_actions: parseInt(document.getElementById('monkey_actions').value,10)||200,
    monkey_delay_ms: parseInt(document.getElementById('monkey_delay').value,10)||0,
    monkey_navigation: document.getElementById('monkey_nav').checked,
  };
  const res=await fetch('/api/diagnose',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  const {job_id}=await res.json();
  poll(job_id);
});

async function testLogin(){
  const msg=document.getElementById('login_msg'); msg.className='sub'; msg.textContent='Testing login...';
  try{
    const r=await fetch('/api/login-test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({
      login_url:document.getElementById('login_url').value||document.getElementById('u').value,
      username:document.getElementById('login_u').value, password:document.getElementById('login_p').value,
    })});
    const d=await r.json();
    if(d.ok){ msg.className='okmsg'; msg.textContent=d.message; }
    else{ msg.className='errmsg'; msg.textContent=d.error; }
  }catch(e){ msg.className='errmsg'; msg.textContent='Login test failed: '+e; }
}

async function poll(id){
  for(let i=0;i<180;i++){
    await new Promise(r=>setTimeout(r,1000));
    const r=await fetch('/api/progress/'+id); const d=await r.json();
    document.getElementById('pmsg').textContent=(d.message||'')+' ('+ (d.percent||0)+'%)';
    document.getElementById('pfill').style.width=(d.percent||0)+'%';
    if(d.status==='completed'){ render(id,d.report); break; }
    if(d.status==='failed'){ document.getElementById('pmsg').textContent='Failed: '+(d.message||''); break; }
  }
  document.getElementById('go').disabled=false;
}

function hideResults(){ document.getElementById('hero').style.display='none'; document.getElementById('diag').style.display='none'; document.getElementById('issuesbox').style.display='none'; clearExports(); }

function setExportMsg(text, cls){
  const el=document.getElementById('exportmsg');
  if(!el) return;
  el.textContent=text;
  el.className=cls||'modelnote';
}
function clearExports(){
  document.querySelectorAll('.export-btn').forEach(btn=>{
    btn.disabled=true; btn.onclick=null; btn.title='Run a diagnosis to enable exports.';
  });
  setExportMsg('Run a diagnosis to generate exportable reports.');
}
function setupExports(id, rep){
  document.querySelectorAll('.export-btn').forEach(btn=>{
    const fmt=btn.dataset.fmt;
    btn.disabled=false;
    btn.title='Download '+fmt.toUpperCase()+' report';
    btn.onclick=()=>downloadReport(id, fmt, btn);
  });
  const pdfBtn=document.getElementById('dl_pdf');
  if(!rep.pdf_available){
    pdfBtn.disabled=true;
    pdfBtn.title='PDF generation was unavailable for this run; HTML/Markdown/JSON are available.';
    setExportMsg('PDF unavailable for this run; HTML, Markdown and JSON are ready to download.', 'errmsg');
  }else{
    setExportMsg('Reports are saved as files when you click a format.');
  }
}
async function downloadReport(id, fmt, btn){
  const original=btn.textContent;
  btn.disabled=true; btn.textContent='Downloading...';
  setExportMsg('Preparing '+fmt.toUpperCase()+' download...');
  try{
    const r=await fetch('/api/reports/'+id+'/'+fmt, {cache:'no-store'});
    if(!r.ok) throw new Error('server returned HTTP '+r.status);
    const blob=await r.blob();
    if(!blob.size) throw new Error('report file is empty');
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url; a.download='webdoctor-'+id+'.'+fmt;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(url), 2000);
    setExportMsg('Saved webdoctor-'+id+'.'+fmt+' ('+Math.round(blob.size/1024)+' KB).', 'okmsg');
  }catch(e){
    setExportMsg('Download failed: '+e.message, 'errmsg');
  }finally{
    btn.textContent=original; btn.disabled=false;
  }
}

function render(id, rep){
  document.getElementById('hero').style.display='grid';
  document.getElementById('gbig').textContent=rep.overall_grade;
  document.getElementById('gsub').textContent=rep.score+'/100 * '+rep.overall_status.toUpperCase();
  const gmap={A:'#10b981',B:'#06b6d4',C:'#eab308',D:'#f97316',F:'#ef4444'};
  document.getElementById('gradecard').style.background='linear-gradient(135deg,'+(gmap[rep.overall_grade]||'#8b5cf6')+',#ec4899)';
  document.getElementById('rurl').textContent=rep.url+(rep.target_title?' -- '+rep.target_title:'');
  const ab=document.getElementById('authbadge'); ab.innerHTML=rep.logged_in?'<span class="badge-auth">LOGGED IN RUN</span>':'';
  const ms=rep.monkey_summary||{};
  if(ms.enabled){ ab.innerHTML+=' <span class="chip">MONKEY seed '+esc(ms.seed)+' · '+esc(ms.actions_executed)+'/'+esc(ms.planned_actions)+' actions</span>'; }
  const c=rep.issue_counts||{}; document.getElementById('scrit').textContent=c.critical||0;
  document.getElementById('serror').textContent=c.error||0; document.getElementById('swarn').textContent=c.warning||0;
  document.getElementById('sinfo').textContent=c.info||0;
  document.getElementById('sjs').textContent=(rep.metrics||{}).js_error_count||0;
  document.getElementById('smonkey').textContent=ms.actions_executed||0;
  document.getElementById('sdur').textContent=((rep.duration_ms||0)/1000).toFixed(1)+'s';
  setupExports(id, rep);

  if(rep.ai_summary){ const d=document.getElementById('diag'); d.style.display='block';
    document.getElementById('diagbody').textContent=rep.ai_summary; }

  const box=document.getElementById('issuesbox'); const list=document.getElementById('issuelist'); list.innerHTML='';
  const issues=rep.issues||[];
  const envCount=rep.environment_issue_count||0;
  document.getElementById('icnt').textContent=issues.length+(envCount?' +'+envCount+' runner note'+(envCount===1?'':'s')+' (not scored)':'');
  if(issues.length){ box.style.display='block'; }
  issues.forEach((it,idx)=>{
    const el=document.createElement('div');
    const sev=(it.severity||'info').toLowerCase();
    el.className='issue '+(sev==='critical'?'crit':sev==='error'?'error':sev==='warning'?'warn':'info');
    const head=document.createElement('div'); head.className='ihead';
    head.innerHTML='<div><span class="tag '+(it.severity||'').toUpperCase()+'">'+(it.severity||'').toUpperCase()+'</span>'
      +'<span class="chip">'+(it.category||'')+'</span><b>'+esc(it.title||'')+'</b>'
      +' <span class="sub" style="margin-left:8px">'+esc(it.summary||'')+'</span></div><span class="sub"># '+(idx+1)+' &#x25BC;</span>';
    const body=document.createElement('div'); body.className='ibody';
    let html='';
    if(it.how_it_happened) html+='<p><b>How it happened:</b> '+esc(it.how_it_happened)+'</p>';
    if(it.reproduction) html+='<p><b>Reproduce:</b> <code>'+esc(it.reproduction)+'</code></p>';
    if(it.fix) html+='<p><b>Fix:</b> '+esc(it.fix)+'</p>';
    if(it.detail) html+='<p class="sub" style="white-space:pre-wrap">'+esc(it.detail)+'</p>';
    body.innerHTML=html;
    head.onclick=()=>{ body.style.display = body.style.display==='block'?'none':'block'; };
    el.appendChild(head); el.appendChild(body); list.appendChild(el);
  });
}
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
