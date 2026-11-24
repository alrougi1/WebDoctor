"""Web dashboard for WebDoctor: run tests, watch live progress, pick AI providers,
and run authenticated tests logged in as a real user."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from webdoctor.config import TOOL_VERSION, AI_MODEL_FAST
from webdoctor.models import TestReport, CheckStatus
from webdoctor.runner import run_diagnostics
from webdoctor.reporters.html_report import generate_html
from webdoctor.reporters.markdown_report import generate_markdown
from webdoctor.reporters.json_report import generate_json
from webdoctor.reporters.pdf_report import generate_pdf
from webdoctor.diagnostics.ai_adapters import list_available_models
from webdoctor.checks.auth import perform_form_login

REPORT_DIR = os.path.abspath(os.environ.get("WEBDOCTOR_REPORTS", "./reports"))
os.makedirs(REPORT_DIR, exist_ok=True)

app = FastAPI(title="WebDoctor Dashboard", version=TOOL_VERSION)
jobs: Dict[str, Dict[str, Any]] = {}


class DiagnoseRequest(BaseModel):
    url: str
    browser: bool = True
    links: bool = True
    ai: bool = True
    ai_provider: str = "sandbox"
    ai_model: str = AI_MODEL_FAST
    ai_key: Optional[str] = None
    ai_base: Optional[str] = None
    ai_compare: bool = False
    timeout: int = 20
    pdf: bool = True
    # Authenticated testing
    login: bool = False
    login_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    user_field: Optional[str] = None
    pass_field: Optional[str] = None
    submit_btn: Optional[str] = None
    session_json: Optional[str] = None


class LoginTestRequest(BaseModel):
    login_url: str
    username: str
    password: str
    user_field: Optional[str] = None
    pass_field: Optional[str] = None
    submit_btn: Optional[str] = None


def _background_run(job_id: str, req: DiagnoseRequest):
    job = jobs[job_id]

    def _cb(msg: str, pct: float):
        job["percent"] = int(pct * 100)
        job["message"] = msg

    try:
        report_out = os.path.join(REPORT_DIR, job_id)
        os.makedirs(report_out, exist_ok=True)
        shots = os.path.join(report_out, "screenshots")

        session_path = None
        if req.session_json:
            try:
                state = json.loads(req.session_json)
                session_path = os.path.join(report_out, "storage_state.json")
                with open(session_path, "w") as f:
                    json.dump(state, f)
            except Exception as e:
                job["message"] = f"Invalid session JSON: {e}"

        compare_list = None
        if req.ai_compare:
            compare_list = [
                {"provider": "sandbox", "model": "gpt-6-luna", "label": "GPT-6 Luna (OpenAI)"},
                {"provider": "sandbox", "model": "gemini-3.8-flash", "label": "Gemini 3.8 Flash (Google)"},
            ]

        report = run_diagnostics(
            req.url,
            browser_mode=req.browser,
            check_links=req.links,
            ai=req.ai,
            ai_provider=req.ai_provider,
            ai_model=req.ai_model,
            ai_key=req.ai_key,
            ai_base=req.ai_base,
            ai_compare=compare_list,
            timeout=req.timeout,
            screenshot_dir=shots,
            report_dir=report_out,
            login=req.login,
            login_url=req.login_url or req.url,
            username=req.username,
            password=req.password,
            user_field=req.user_field,
            pass_field=req.pass_field,
            submit_btn=req.submit_btn,
            session_path=session_path,
            progress=_cb,
        )

        base = os.path.join(report_out, "report")
        html_path = base + ".html"
        md_path = base + ".md"
        json_path = base + ".json"
        pdf_path = base + ".pdf"

        # PDF first so external tool can't wipe the html
        pdf_ok = False
        if req.pdf:
            pdf_ok = generate_pdf(report, pdf_path)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(generate_html(report))
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(generate_markdown(report))
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(generate_json(report))

        job["status"] = "completed"
        job["message"] = "Report ready"
        job["percent"] = 100
        job["report"] = {
            "overall_grade": report.overall_grade,
            "overall_status": report.overall_status.value,
            "score": report.score,
            "url": report.final_url or report.url,
            "target_title": report.target_title,
            "issue_counts": report.issue_counts(),
            "duration_ms": report.duration_ms,
            "issues": [i.__dict__ for i in report.issues],
            "ai_summary": report.ai_summary,
            "metrics": report.metrics,
            "pdf_available": pdf_ok,
            "logged_in": bool(req.login or req.session_json),
        }
    except Exception as e:
        job["status"] = "failed"
        job["message"] = str(e)


@app.get("/api/models")
def api_models():
    return {"models": list_available_models()}


@app.post("/api/diagnose")
async def api_diagnose(req: DiagnoseRequest):
    jid = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    jobs[jid] = {"status": "running", "percent": 0, "message": "Queued", "req": req.model_dump(), "created": loop.time()}
    loop.run_in_executor(None, _background_run, jid, req)
    return {"job_id": jid}


@app.get("/api/progress/{job_id}")
def api_progress(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.post("/api/login-test")
def api_login_test(req: LoginTestRequest):
    ok, res = perform_form_login(
        login_url=req.login_url,
        username=req.username,
        password=req.password,
        user_field=req.user_field,
        pass_field=req.pass_field,
        submit_btn=req.submit_btn,
    )
    if ok:
        cookies = res.get("cookies", [])
        return {"ok": True, "final_url": res.get("final_url"), "cookie_count": len(cookies),
                "message": f"Login succeeded -- redirected to {res.get('final_url')} ({len(cookies)} cookies stored)."}
    return {"ok": False, "error": res.get("error", "Login failed")}


@app.get("/api/reports/{job_id}/{fmt}")
def api_report_file(job_id: str, fmt: str):
    mapping = {"html": ("report.html", "text/html"), "md": ("report.md", "text/markdown"),
               "json": ("report.json", "application/json"), "pdf": ("report.pdf", "application/pdf")}
    if fmt not in mapping:
        raise HTTPException(400, "unknown format")
    fname, ctype = mapping[fmt]
    path = os.path.join(REPORT_DIR, job_id, fname)
    if not os.path.exists(path):
        raise HTTPException(404, "report file not found")
    return FileResponse(path, media_type=ctype, filename=f"webdoctor-{job_id}.{fmt}")


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WebDoctor -- Website Testing & Root-Cause Diagnostics</title>
<style>
:root{--bg:#0a0e1a;--card:#111827;--card2:#1a243b;--txt:#e2e8f0;--mut:#94a3b8;--border:#1f2f4f;--acc:#8b5cf6;--red:#ef4444;--green:#10b981}
*{box-sizing:border-box} body{margin:0;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--txt)}
a{color:#a78bfa} .wrap{max-width:1160px;margin:0 auto;padding:26px 18px 80px}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:14px}
.logo{font-size:24px;font-weight:800}.logo span{background:linear-gradient(90deg,#8b5cf6,#ec4899);-webkit-background-clip:text;color:transparent}
.sub{color:var(--mut);font-size:13px}
.box{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px;margin-top:18px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=text],input[type=password],select,textarea{background:#0b1220;border:1px solid var(--border);color:var(--txt);padding:11px 14px;border-radius:10px;font-size:14px}
input[type=text]:focus,input[type=password]:focus,select:focus{outline:none;border-color:var(--acc)}
input.url{flex:1;min-width:280px;font-size:16px}
button{background:linear-gradient(90deg,#8b5cf6,#6d28d9);color:#fff;border:none;padding:12px 22px;border-radius:10px;font-weight:700;font-size:14px;cursor:pointer}
button:hover{filter:brightness(1.1)} button:disabled{opacity:.4;cursor:not-allowed}
button.sec{background:var(--card2);border:1px solid var(--border)}
label.chk{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--mut);cursor:pointer}
.details{margin-top:14px;font-size:13px} details summary{cursor:pointer;color:#a78bfa}
.pbar{background:#0b1220;border:1px solid var(--border);border-radius:10px;height:12px;overflow:hidden;margin-top:14px}
.pfill{height:100%;width:0;background:linear-gradient(90deg,#8b5cf6,#10b981);transition:width .25s}
.pmsg{color:var(--mut);font-size:13px;margin-top:8px}
.hero{display:none;margin-top:22px;grid-template-columns:200px 1fr;gap:20px}
.grade{display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(135deg,#8b5cf6,#ec4899);border-radius:14px;padding:20px;text-align:center;box-shadow:0 12px 30px rgba(0,0,0,.35)}
.grade .big{font-size:74px;font-weight:900;line-height:1}.grade .sub{color:#fff;opacity:.9}
.badge-auth{display:inline-block;padding:2px 8px;border-radius:6px;background:#059669;color:#fff;font-size:11px;font-weight:700;margin-left:8px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-top:12px}
.stat{background:#0b1220;border:1px solid var(--border);border-radius:10px;padding:12px;text-align:center}
.stat .n{font-size:24px;font-weight:800}.stat .l{font-size:11px;color:var(--mut);text-transform:uppercase}
.diag{background:var(--card2);border:1px solid var(--border);border-radius:12px;padding:18px;margin-top:16px;font-size:13px}
.diag pre{white-space:pre-wrap;font-family:inherit;margin:0}
.issue{border:1px solid var(--border);border-radius:10px;margin-top:8px;overflow:hidden}
.ihead{display:flex;justify-content:space-between;padding:12px 14px;cursor:pointer;background:#0b1220;font-size:13px}
.ibody{display:none;padding:14px;background:var(--card);font-size:13px;border-top:1px solid var(--border)}
.crit{border-left:4px solid var(--red)} .error{border-left:4px solid #f97316} .warn{border-left:4px solid #eab308} .info{border-left:4px solid #64748b}
.tag{display:inline-block;padding:2px 7px;border-radius:5px;font-size:10px;font-weight:800;letter-spacing:.5px;margin-right:6px}
.tag.CRITICAL,.tag.ERROR{background:#ef444433;color:#fca5a5} .tag.WARNING{background:#eab30833;color:#fde047} .tag.INFO{background:#64748b33;color:#cbd5e1}
.toolbar{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap}
.modelnote{font-size:12px;color:var(--mut);margin-top:4px}
.okmsg{color:#10b981;font-size:12px} .errmsg{color:#ef4444;font-size:12px}
.chip{display:inline-block;padding:2px 7px;border-radius:5px;background:#0b1220;border:1px solid var(--border);font-size:11px;color:var(--mut);margin-right:4px}
footer{margin-top:60px;text-align:center;color:var(--mut);font-size:12px;border-top:1px solid var(--border);padding-top:16px}
@media(max-width:768px){.hero{grid-template-columns:1fr}}
</style></head><body>
<div class="wrap">
<header>
<div><div class="logo"><span>WebDoctor</span></div><div class="sub">Automated website testing + root-cause diagnostics with multiple AI providers</div></div>
<div class="sub">v__VER__</div>
</header>

<div class="box">
<form id="f">
<div class="row">
<input class="url" id="u" type="text" placeholder="https://your-site.com" required value="https://example.com">
<button id="go" type="submit">Run Diagnosis</button>
</div>
<div class="row" style="margin-top:12px">
<label class="chk"><input type="checkbox" id="cb_b" checked> Headless browser (JS errors & perf)</label>
<label class="chk"><input type="checkbox" id="cb_l" checked> Crawl broken links</label>
<label class="chk"><input type="checkbox" id="cb_ai" checked> AI root-cause diagnosis</label>
<label class="chk"><input type="checkbox" id="cb_pdf" checked> Generate PDF</label>
</div>

<div class="details">
<details>
<summary>AI provider & model (ChatGPT / Gemini / Claude / comparison)</summary>
<div style="margin-top:10px">
<div class="row">
<select id="ai_p" onchange="onProvChange()">
<option value="sandbox" selected>Sandbox built-in (OpenAI-compatible) -- free/cheap</option>
<option value="openai">OpenAI direct (api.openai.com) -- your key</option>
<option value="anthropic">Anthropic Claude -- your key</option>
<option value="gemini">Google Gemini direct -- your key</option>
</select>
<select id="ai_m" style="min-width:260px"></select>
<label class="chk"><input type="checkbox" id="cb_cmp"> Multi-AI compare (GPT-6 Luna + Gemini 3.8 Flash together)</label>
</div>
<div class="row" style="margin-top:8px">
<input type="password" id="ai_k" placeholder="API key (optional -- not needed for Sandbox models)" style="flex:1">
<input type="text" id="ai_base" placeholder="Custom base URL (optional)" style="flex:1">
</div>
<div class="modelnote">Tip: leave defaults for the fastest, cheapest run. Clean sites use 0 AI credits automatically.</div>
</div>
</details>

<details style="margin-top:10px">
<summary>Log in and test as a real user (authenticated testing)</summary>
<div style="margin-top:10px">
<label class="chk"><input type="checkbox" id="cb_login"> Enable login before tests (fills credentials, then runs all checks inside the protected area)</label>
<div class="row" style="margin-top:8px">
<input type="text" id="login_url" placeholder="Login page URL (leave blank = same as target)" style="flex:1">
<input type="text" id="login_u" placeholder="Username / email" style="width:230px">
<input type="password" id="login_p" placeholder="Password" style="width:190px">
<button type="button" class="sec" onclick="testLogin()">Verify login</button>
</div>
<div id="login_msg" style="margin-top:6px"></div>
<div style="margin-top:10px"><span class="sub">Or reuse a saved session (paste Playwright storageState JSON):</span></div>
<textarea id="sess_json" rows="3" style="width:100%;margin-top:6px;font-family:monospace;font-size:11px" placeholder="{"cookies": [...], "origins": [...]}"></textarea>
<div class="modelnote">Sessions are saved per-run at reports/<id>/storage_state.json for reuse.</div>
</div>
</details>
</div>
</form>

<div id="prog" style="display:none;margin-top:16px">
<div class="pmsg" id="pmsg">Starting...</div>
<div class="pbar"><div class="pfill" id="pfill"></div></div>
</div>
</div>

<div class="hero box" id="hero">
<div class="grade" id="gradecard"><div class="big" id="gbig">A</div><div class="sub" id="gsub">100/100</div></div>
<div>
<div class="row"><strong id="rurl" style="font-size:17px"></strong><span id="authbadge"></span></div>
<div class="stats">
<div class="stat"><div class="n" id="scrit" style="color:#ef4444">0</div><div class="l">Critical</div></div>
<div class="stat"><div class="n" id="serror" style="color:#f97316">0</div><div class="l">Errors</div></div>
<div class="stat"><div class="n" id="swarn" style="color:#eab308">0</div><div class="l">Warnings</div></div>
<div class="stat"><div class="n" id="sinfo" style="color:#64748b">0</div><div class="l">Info</div></div>
<div class="stat"><div class="n" id="sjs">0</div><div class="l">JS Errors</div></div>
<div class="stat"><div class="n" id="sdur">0s</div><div class="l">Duration</div></div>
</div>
<div class="toolbar">
<a class="btn" id="dl_html" href="#" target="_blank"><button class="sec">Open HTML report</button></a>
<a class="btn" id="dl_md" href="#" target="_blank"><button class="sec">Markdown</button></a>
<a class="btn" id="dl_json" href="#" target="_blank"><button class="sec">JSON</button></a>
<a class="btn" id="dl_pdf" href="#" target="_blank"><button class="sec">PDF</button></a>
</div>
</div>
</div>

<div class="diag box" id="diag" style="display:none">
<div style="display:flex;justify-content:space-between;align-items:center"><strong>Executive Diagnosis</strong><span class="sub" id="modelused"></span></div>
<pre id="diagbody"></pre>
</div>

<div class="box" id="issuesbox" style="display:none">
<div class="row" style="justify-content:space-between"><strong>Issues & Root Causes (<span id="icnt">0</span>)</strong><span class="sub">click an issue to expand root cause, reproduction, and fix</span></div>
<div id="issuelist" style="margin-top:10px"></div>
</div>

<footer>WebDoctor -- Developer-first website testing with ChatGPT, Gemini, and Claude * Open source (MIT)</footer>
</div>

<script>
let MODEL_CAT = [];
const PRESETS = {
  sandbox: ["gpt-6-luna","gpt-6.1-sol","gpt-6-sol","gemini-3.8-flash","gpt-5-mini"],
  openai: ["gpt-5.5","gpt-4.1","gpt-4o","gpt-4.1-mini","o3-mini"],
  anthropic: ["claude-sonnet-4","claude-haiku-4","claude-opus-4"],
  gemini: ["gemini-2.5-flash","gemini-2.5-pro","gemini-1.5-flash"],
};
async function loadModels(){
  try{ const r=await fetch('/api/models'); const d=await r.json(); MODEL_CAT=d.models; }catch(e){}
  onProvChange();
}
function onProvChange(){
  const p=document.getElementById('ai_p').value; const sel=document.getElementById('ai_m'); sel.innerHTML='';
  let ids=(PRESETS[p]||[]); const fromCat=MODEL_CAT.filter(m=>m.provider===p).map(m=>m.id);
  ids=ids.concat(fromCat.filter(x=>!ids.includes(x)));
  ids.forEach(id=>{ const m=MODEL_CAT.find(x=>x.id===id); const o=document.createElement('option');
    o.value=id; o.textContent=(m&&m.label)?m.label:id; sel.appendChild(o); });
  if(ids[0]) sel.value=ids[0];
}
loadModels();

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
    ai_base: document.getElementById('ai_base').value||null,
    ai_compare: document.getElementById('cb_cmp').checked,
    pdf: document.getElementById('cb_pdf').checked,
    login: document.getElementById('cb_login').checked,
    login_url: document.getElementById('login_url').value||null,
    username: document.getElementById('login_u').value||null,
    password: document.getElementById('login_p').value||null,
    session_json: document.getElementById('sess_json').value.trim()||null,
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

function hideResults(){ document.getElementById('hero').style.display='none'; document.getElementById('diag').style.display='none'; document.getElementById('issuesbox').style.display='none'; }

function render(id, rep){
  document.getElementById('hero').style.display='grid';
  document.getElementById('gbig').textContent=rep.overall_grade;
  document.getElementById('gsub').textContent=rep.score+'/100 * '+rep.overall_status.toUpperCase();
  const gmap={A:'#10b981',B:'#06b6d4',C:'#eab308',D:'#f97316',F:'#ef4444'};
  document.getElementById('gradecard').style.background='linear-gradient(135deg,'+(gmap[rep.overall_grade]||'#8b5cf6')+',#ec4899)';
  document.getElementById('rurl').textContent=rep.url+(rep.target_title?' -- '+rep.target_title:'');
  const ab=document.getElementById('authbadge'); ab.innerHTML=rep.logged_in?'<span class="badge-auth">LOGGED IN RUN</span>':'';
  const c=rep.issue_counts||{}; document.getElementById('scrit').textContent=c.critical||0;
  document.getElementById('serror').textContent=c.error||0; document.getElementById('swarn').textContent=c.warning||0;
  document.getElementById('sinfo').textContent=c.info||0;
  document.getElementById('sjs').textContent=(rep.metrics||{}).js_error_count||0;
  document.getElementById('sdur').textContent=((rep.duration_ms||0)/1000).toFixed(1)+'s';
  document.getElementById('dl_html').href='/api/reports/'+id+'/html';
  document.getElementById('dl_md').href='/api/reports/'+id+'/md';
  document.getElementById('dl_json').href='/api/reports/'+id+'/json';
  document.getElementById('dl_pdf').href='/api/reports/'+id+'/pdf';
  if(!rep.pdf_available) document.getElementById('dl_pdf').style.display='none';

  if(rep.ai_summary){ const d=document.getElementById('diag'); d.style.display='block';
    document.getElementById('diagbody').textContent=rep.ai_summary; }

  const box=document.getElementById('issuesbox'); const list=document.getElementById('issuelist'); list.innerHTML='';
  const issues=rep.issues||[]; document.getElementById('icnt').textContent=issues.length;
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
</script>
</body></html>"""

DASHBOARD_HTML = DASHBOARD_HTML.replace("__VER__", TOOL_VERSION)


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(DASHBOARD_HTML)
