"""
ftp_web.py  -  Browser-based web UI for FTP sync.
Run:  python ftp_web.py
Open: http://localhost:8080
"""

# ---------------------------------------------------------------------------
# Self-update: runs FIRST, fixes PyInstaller frozen importer so updates/ wins
# ---------------------------------------------------------------------------
def _apply_updates():
    import os, sys, importlib
    if not getattr(sys, "frozen", False):
        return

    exe_dir     = os.path.dirname(sys.executable)
    updates_dir = os.path.join(exe_dir, "updates")
    if not os.path.isdir(updates_dir):
        return

    has_updates = any(
        f in os.listdir(updates_dir)
        for f in ("ftp_core.py", "ftp_gui.py", "ftp_web.py")
    )
    if not has_updates:
        return

    if updates_dir not in sys.path:
        sys.path.insert(0, updates_dir)

    try:
        frozen_idx = path_idx = None
        for i, finder in enumerate(sys.meta_path):
            n = type(finder).__name__
            if n == "FrozenImporter": frozen_idx = i
            elif n == "PathFinder":   path_idx   = i
        if frozen_idx is not None and path_idx is not None and path_idx > frozen_idx:
            sys.meta_path.insert(frozen_idx, sys.meta_path.pop(path_idx))
    except Exception:
        pass

    importlib.invalidate_caches()
    for mod in ("ftp_core", "ftp_gui", "ftp_web"):
        sys.modules.pop(mod, None)

    if os.environ.get("FTPSYNC_OVERRIDE_ACTIVE") == "1":
        return
    web_override = os.path.join(updates_dir, "ftp_web.py")
    if os.path.exists(web_override):
        os.environ["FTPSYNC_OVERRIDE_ACTIVE"] = "1"
        with open(web_override, "r", encoding="utf-8") as _f:
            _code = _f.read()
        exec(compile(_code, web_override, "exec"), {"__file__": web_override, "__name__": "__main__"})
        raise SystemExit(0)

_apply_updates()
# ---------------------------------------------------------------------------

import os
import socket
import threading
import webbrowser
from datetime import datetime

import io
import tempfile
from flask import Flask, jsonify, redirect, render_template_string, request, send_file

import ftp_core as core

app     = Flask(__name__)
history = core.DownloadHistory()
worker  = None
scanner = None

transfers      = {}
transfers_lock = threading.Lock()
log_lines      = []
log_lock       = threading.Lock()
MAX_LOG        = 400

# FTP error tracking: remote_path -> {error, count, last_seen}
ftp_errors      = {}
ftp_errors_lock = threading.Lock()

prescan_result = None
prescan_state  = {"running": False, "scanned": 0, "matched": 0, "total": 0}
verbose_flag   = False


def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with log_lock:
        log_lines.append(f"[{ts}] {msg}")
        if len(log_lines) > MAX_LOG:
            log_lines.pop(0)

def on_transfer_start(tid, filename):
    with transfers_lock:
        transfers[tid] = {"name": os.path.basename(filename),
                          "pct": 0, "done": 0, "total": 0, "status": "Downloading"}

def on_transfer_progress(tid, pct, done, total):
    with transfers_lock:
        if tid in transfers:
            transfers[tid].update(pct=pct, done=done, total=total)

def on_transfer_done(tid, status):
    with transfers_lock:
        if tid in transfers:
            transfers[tid]["status"] = status
            transfers[tid]["pct"]    = 100

def on_transfer_error(remote_path, error_msg, count):
    ts = datetime.now().strftime("%H:%M:%S")
    with ftp_errors_lock:
        ftp_errors[remote_path] = {"error": error_msg, "count": count, "last_seen": ts}


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FTP Remote Sync</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1e1e2e;color:#cdd6f4;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px}
a{color:#89b4fa;text-decoration:none}
h2{font-size:1.05rem;color:#89b4fa;margin-bottom:8px}
.topbar{background:#181825;padding:12px 20px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #313244}
.topbar h1{flex:1;font-size:1.3rem;color:#89b4fa}
.tabs{display:flex;gap:4px;padding:10px 20px 0;background:#1e1e2e;border-bottom:1px solid #313244;flex-wrap:wrap}
.tab{padding:8px 18px;border-radius:6px 6px 0 0;background:#313244;color:#a6adc8;font-size:13px}
.tab.active{background:#45475a;color:#cdd6f4;font-weight:600}
.content{padding:20px;max-width:1100px}
.section{background:#181825;border-radius:8px;padding:16px;margin-bottom:16px}
.field-row{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
.field-row label{color:#a6adc8;font-size:12px;white-space:nowrap}
input[type=text],input[type=password],input[type=number]{background:#313244;border:1px solid #45475a;color:#cdd6f4;padding:6px 10px;border-radius:5px;font-size:13px;width:200px}
input[type=number]{width:80px}
input:focus{outline:none;border-color:#89b4fa}
.btn{padding:7px 16px;border-radius:5px;border:none;cursor:pointer;font-size:13px;font-weight:600}
.btn-green{background:#40a02b;color:#fff}.btn-green:hover{background:#2d7c1e}
.btn-red{background:#d20f39;color:#fff}.btn-red:hover{background:#a00b2b}
.btn-gray{background:#45475a;color:#cdd6f4}.btn-gray:hover{background:#585b70}
.btn-blue{background:#1e66f5;color:#fff}.btn-blue:hover{background:#1a5cd4}
.btn-sm{padding:4px 10px;font-size:12px}
.pill{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700}
.pill-idle{background:#313244;color:#a6adc8}
.pill-running{background:#40a02b;color:#fff}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#313244;color:#89b4fa;padding:8px 10px;text-align:left;font-weight:600}
td{padding:7px 10px;border-bottom:1px solid #2a2a3e}
tr:hover td{background:#232334}
.pbar-wrap{background:#313244;border-radius:4px;height:12px;width:120px;display:inline-block;vertical-align:middle}
.pbar-fill{background:#89b4fa;height:12px;border-radius:4px;transition:width .3s}
.pbar-outer{background:#313244;border-radius:4px;height:16px;width:100%;margin:8px 0}
.pbar-inner{background:#89b4fa;height:16px;border-radius:4px;transition:width .5s}
#logbox{background:#0d0d17;color:#a6e3a1;font-family:Consolas,monospace;font-size:12px;padding:12px;border-radius:6px;height:240px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}
.stat-card{background:#313244;border-radius:7px;padding:12px 20px;text-align:center;min-width:130px}
.stat-val{font-size:1.5rem;font-weight:700;color:#89b4fa}
.stat-lbl{font-size:11px;color:#a6adc8;margin-top:2px}
.stats-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.alert{padding:10px 14px;border-radius:6px;margin-bottom:12px;font-size:13px}
.alert-ok{background:#1e3a1e;color:#a6e3a1;border:1px solid #40a02b}
.alert-err{background:#3a1e1e;color:#f38ba8;border:1px solid #d20f39}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700}
.badge-dl{background:#1e3a5e;color:#89b4fa}
.badge-ps{background:#2a2a1e;color:#f9e2af}
.badge-pm{background:#1e2a1e;color:#a6e3a1}
.warn-box{background:#2a1e00;border:1px solid #f9e2af;border-radius:6px;padding:10px 14px;margin-bottom:12px;color:#f9e2af;font-size:13px}
input[type=checkbox]{accent-color:#89b4fa;width:15px;height:15px}
code{background:#313244;padding:2px 6px;border-radius:4px;font-size:12px}
/* Browser tab */
.br-toolbar{display:flex;gap:6px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.br-path{flex:1;min-width:200px;font-family:Consolas,monospace;background:#313244;border:1px solid #45475a;color:#cdd6f4;padding:6px 10px;border-radius:5px;font-size:13px}
.br-path:focus{outline:none;border-color:#89b4fa}
.br-layout{display:grid;grid-template-columns:220px 1fr;gap:10px;min-height:360px}
.br-tree{background:#181825;border-radius:6px;padding:8px;overflow-y:auto;max-height:420px}
.br-tree ul{list-style:none;padding-left:14px}
.br-tree li{line-height:1.8;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.br-tree li:hover{color:#89b4fa}
.br-tree li.selected{color:#cba6f7;font-weight:700}
.br-files{background:#181825;border-radius:6px;overflow:hidden;display:flex;flex-direction:column}
.br-files table{flex:1}
.br-files th{position:sticky;top:0;z-index:1;cursor:pointer}
.br-files th:hover{background:#45475a}
.br-files td.name{font-family:Consolas,monospace;font-size:12px}
.br-files tr.dir td{color:#89b4fa}
.br-files tr.sel td{background:#1e3a5e!important}
.br-dest{background:#313244;border-radius:6px;padding:10px 14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
.br-dest label{color:#a6adc8;font-size:12px;white-space:nowrap}
.br-dest input{flex:1;min-width:160px;font-family:Consolas,monospace}
.br-queue{background:#181825;border-radius:6px;padding:10px;margin-top:8px;max-height:220px;overflow-y:auto}
.br-qrow{display:flex;align-items:center;gap:8px;padding:4px 6px;border-bottom:1px solid #2a2a3e;font-size:12px}
.br-qrow:last-child{border-bottom:none}
.br-qname{width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:Consolas,monospace}
.br-qbar{flex:1;background:#313244;border-radius:3px;height:8px}
.br-qfill{height:8px;border-radius:3px;background:#89b4fa;transition:width .3s}
.br-qfill.up{background:#cba6f7}
.br-qstatus{width:80px;text-align:right;color:#a6adc8}
.br-servers{background:#313244;border-radius:6px;padding:10px 14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.br-servers label{color:#a6adc8;font-size:12px}
.br-servers select{background:#181825;border:1px solid #45475a;color:#cdd6f4;padding:5px 8px;border-radius:5px;font-size:13px}
</style>
</head>
<body>
<div class="topbar">
  <h1>FTP Remote Sync</h1>
  <span id="status-pill" class="pill pill-idle">Idle</span>
  <form method="post" action="/sync_now" style="display:inline">
    <button class="btn btn-blue btn-sm">Sync Now</button>
  </form>
  {% if running %}
  <form method="post" action="/stop" style="display:inline">
    <button class="btn btn-red btn-sm">Stop</button>
  </form>
  {% else %}
  <form method="post" action="/start" style="display:inline">
    <button class="btn btn-green btn-sm">Start Sync</button>
  </form>
  {% endif %}
</div>

<div class="tabs">
  <div class="tab {% if tab=='dashboard' %}active{% endif %}"><a href="/?tab=dashboard" style="color:inherit">Dashboard</a></div>
  <div class="tab {% if tab=='folders'   %}active{% endif %}"><a href="/?tab=folders"   style="color:inherit">Folder Pairs</a></div>
  <div class="tab {% if tab=='prescan'   %}active{% endif %}"><a href="/?tab=prescan"   style="color:inherit">Pre-Scan</a></div>
  <div class="tab {% if tab=='ignore'    %}active{% endif %}"><a href="/?tab=ignore"    style="color:inherit">Ignore List</a></div>
  <div class="tab {% if tab=='history'   %}active{% endif %}"><a href="/?tab=history"   style="color:inherit">History</a></div>
  <div class="tab {% if tab=='errors'    %}active{% endif %}"><a href="/?tab=errors"    style="color:inherit">
    FTP Errors{% if error_count %} <span style="background:#d20f39;color:#fff;border-radius:10px;padding:1px 7px;font-size:11px">{{ error_count }}</span>{% endif %}
  </a></div>
  <div class="tab {% if tab=='browser'   %}active{% endif %}"><a href="/?tab=browser"   style="color:inherit">Browser</a></div>
  <div class="tab {% if tab=='settings'  %}active{% endif %}"><a href="/?tab=settings"  style="color:inherit">Settings</a></div>
</div>

<div class="content">
{% if flash %}
<div class="alert {% if flash_type=='ok' %}alert-ok{% else %}alert-err{% endif %}">{{ flash }}</div>
{% endif %}

{% if warnings %}
<div class="warn-box">
  <strong>Configuration warnings:</strong><br>
  {% for w in warnings %}• {{ w }}<br>{% endfor %}
</div>
{% endif %}

<!-- ===== DASHBOARD ===== -->
{% if tab=='dashboard' %}
<div class="section">
  <h2>Credentials</h2>
  <p style="color:#a6adc8;font-size:12px;margin-bottom:10px">
    Password is stored encrypted in <code>settings.json</code> — not in plaintext.
    Uncheck "Save" to remove credentials from disk entirely.
  </p>
  <form method="post" action="/save_credentials">
  <div class="field-row">
    <label>Host</label>
    <input type="text" name="host" value="{{ cfg.get('host','') }}" style="width:220px">
    <label>Port</label>
    <input type="number" name="port" value="{{ cfg.get('port',21) }}">
    <label>User</label>
    <input type="text" name="user" value="{{ cfg.get('user','') }}" style="width:140px">
    <label>Password</label>
    <input type="password" name="password" value="" placeholder="(leave blank to keep saved)" style="width:160px">
    <label><input type="checkbox" name="save_credentials" {% if cfg.get('save_credentials') %}checked{% endif %}> Save encrypted</label>
    <button class="btn btn-gray btn-sm">Save</button>
  </div>
  </form>
  <div style="margin-top:10px;display:flex;align-items:center;gap:12px">
    <button class="btn btn-blue btn-sm" onclick="testConn(event)">Test Connection</button>
    <span id="test-result" style="font-size:13px"></span>
  </div>
</div>

<div class="section">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
    <h2 style="margin:0">Status</h2>
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#a6adc8">
      <input type="checkbox" id="verbose_chk" onchange="setVerbose(this.checked)" {% if verbose %}checked{% endif %}> Verbose log
    </label>
  </div>
  <div class="stats-row">
    {% set stats = get_stats() %}
    <div class="stat-card"><div class="stat-val">{{ stats.total_files }}</div><div class="stat-lbl">Total Files</div></div>
    <div class="stat-card"><div class="stat-val">{{ '%.1f'|format(stats.total_bytes/1048576) }} MB</div><div class="stat-lbl">Total Size</div></div>
    <div class="stat-card"><div class="stat-val">{{ stats.today }}</div><div class="stat-lbl">Today</div></div>
    <div class="stat-card"><div class="stat-val">{{ stats.prescans }}</div><div class="stat-lbl">Pre-Scanned</div></div>
    <div class="stat-card"><div class="stat-val">{{ cfg.get('parallel_downloads',3) }}</div><div class="stat-lbl">Parallel DLs</div></div>
  </div>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
    <h2 style="margin:0">Active Transfers</h2>
    <button class="btn btn-gray btn-sm" onclick="clearCompleted()">Clear Completed</button>
  </div>
  <table><tr><th>File</th><th>Progress</th><th>Transferred</th><th>Status</th></tr>
  <tbody id="transfers-body"><tr><td colspan="4" style="color:#585b70;text-align:center">No active transfers</td></tr></tbody></table>
</div>

<div class="section">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
    <h2 style="margin:0">Log</h2>
    <button class="btn btn-gray btn-sm" onclick="clearLog()">Clear</button>
  </div>
  <div id="logbox">Loading...</div>
</div>

<script>
let lastLogLen=0,localLog=[];
function setVerbose(v){fetch('/set_verbose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({verbose:v})});}
function testConn(e){
  e.preventDefault();
  const btn=e.target;
  const host=document.querySelector('input[name=host]').value.trim();
  const port=document.querySelector('input[name=port]').value.trim()||'21';
  const user=document.querySelector('input[name=user]').value.trim();
  const pwd=document.querySelector('input[name=password]').value;
  const res=document.getElementById('test-result');
  if(!host){res.style.color='#f38ba8';res.textContent='Enter a host first.';return;}
  btn.disabled=true;btn.textContent='Testing...';
  res.style.color='#a6adc8';res.textContent='Connecting...';
  fetch('/api/test_connection',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({host,port:parseInt(port),user,password:pwd})})
  .then(r=>r.json()).then(d=>{
    btn.disabled=false;btn.textContent='Test Connection';
    if(d.ok){res.style.color='#a6e3a1';res.textContent='Connected! '+d.msg.split('\n')[0];}
    else{res.style.color='#f38ba8';res.textContent='Failed: '+d.msg;}
  }).catch(err=>{btn.disabled=false;btn.textContent='Test Connection';res.style.color='#f38ba8';res.textContent='Request error: '+err;});
}
function clearLog(){localLog=[];lastLogLen=0;document.getElementById('logbox').textContent='';}
function clearCompleted(){fetch('/clear_completed_transfers',{method:'POST'}).then(()=>{updateStatus();});}
function fmtBytes(b){if(b>=1073741824)return(b/1073741824).toFixed(1)+' GB';if(b>=1048576)return(b/1048576).toFixed(1)+' MB';if(b>=1024)return(b/1024).toFixed(1)+' KB';return b+' B';}
async function poll(){
  try{
    const d=await(await fetch('/api/status')).json();
    document.getElementById('status-pill').className='pill '+(d.running?'pill-running':'pill-idle');
    document.getElementById('status-pill').textContent=d.running?'Syncing':'Idle';
    const tbody=document.getElementById('transfers-body');
    const tk=Object.keys(d.transfers);
    tbody.innerHTML=tk.length===0
      ?'<tr><td colspan="4" style="color:#585b70;text-align:center">No active transfers</td></tr>'
      :tk.map(tid=>{const t=d.transfers[tid];
        const clr=t.status==='Done'?'#a6e3a1':t.status.startsWith('Error')||t.status==='Stopped'?'#f38ba8':'#cdd6f4';
        return `<tr><td>${t.name}</td><td><div class="pbar-wrap"><div class="pbar-fill" style="width:${t.pct}%"></div></div> <span style="font-size:12px">${t.pct}%</span></td><td style="font-size:12px">${fmtBytes(t.done)} / ${fmtBytes(t.total)}</td><td style="color:${clr}">${t.status}</td></tr>`;
      }).join('');
    if(d.log.length>lastLogLen){
      const box=document.getElementById('logbox');
      const nl=d.log.slice(lastLogLen);
      if(lastLogLen===0)box.textContent='';
      box.textContent+=nl.join('\n')+'\n';
      box.scrollTop=box.scrollHeight;
      lastLogLen=d.log.length;
    }
  }catch(e){}
  setTimeout(poll,1500);
}
poll();
</script>

<!-- ===== FOLDERS ===== -->
{% elif tab=='folders' %}
<div class="section">
  <h2>Add Folder Pair</h2>
  <p style="color:#a6adc8;font-size:12px;margin-bottom:10px">Pairs are saved to settings.json immediately when added or removed.</p>
  <form method="post" action="/add_pair">
  <div class="field-row">
    <label>Remote Path</label>
    <input type="text" name="remote" placeholder="/home/user/shows" style="width:300px">
    <label>Local Path</label>
    <input type="text" name="local" placeholder="/mnt/media or C:/Downloads" style="width:300px">
    <button class="btn btn-green btn-sm">+ Add</button>
  </div>
  </form>
</div>
<div class="section">
  <h2>Current Pairs</h2>
  {% if folder_pairs %}
  <table><tr><th>Remote</th><th>Local</th><th></th></tr>
  {% for i,pair in enumerate(folder_pairs) %}
  <tr>
    <td style="font-family:Consolas,monospace;font-size:12px">{{ pair.remote }}</td>
    <td style="font-family:Consolas,monospace;font-size:12px">{{ pair.local }}</td>
    <td style="width:80px">
      <form method="post" action="/remove_pair">
        <input type="hidden" name="index" value="{{ i }}">
        <button class="btn btn-red btn-sm">Remove</button>
      </form>
    </td>
  </tr>
  {% endfor %}</table>
  {% else %}<p style="color:#585b70">No pairs yet. Add one above.</p>{% endif %}
</div>

<!-- ===== PRE-SCAN ===== -->
{% elif tab=='prescan' %}
<div class="section">
  <h2>Pre-Scan Existing Folders</h2>
  <p style="color:#a6adc8;font-size:13px;margin-bottom:12px">
    Already have files downloaded elsewhere? Pre-scan fingerprints them so they are never
    re-downloaded. Unambiguous matches are automatic — anything unclear appears in the review table.
  </p>

  <!-- Folder pair quick-pick -->
  {% if folder_pairs %}
  <div style="margin-bottom:12px">
    <label style="color:#a6adc8;font-size:12px;display:block;margin-bottom:4px">
      Quick-fill from a saved Folder Pair:
    </label>
    <select id="pair-picker" style="background:#313244;color:#cdd6f4;border:1px solid #45475a;
            border-radius:4px;padding:5px 10px;font-size:12px;cursor:pointer"
            onchange="
              var opt=this.options[this.selectedIndex];
              if(!opt.dataset.remote)return;
              document.getElementById('ps-remote').value=opt.dataset.remote;
              document.getElementById('ps-local').value=opt.dataset.local;
              this.selectedIndex=0;
            ">
      <option value="">— pick a saved pair to fill the fields —</option>
      {% for pair in folder_pairs %}
      <option data-remote="{{ pair.remote }}" data-local="{{ pair.local }}">
        {{ pair.remote }} → {{ pair.local }}
      </option>
      {% endfor %}
    </select>
  </div>
  {% endif %}

  <form method="post" action="/prescan_start" id="ps-form">
  <div style="display:grid;grid-template-columns:120px 1fr;gap:8px;align-items:center;max-width:700px">
    <label style="color:#a6adc8;font-size:12px">Remote Path</label>
    <input id="ps-remote" type="text" name="remote" value="{{ prescan_remote }}"
           placeholder="/home/user/shows" style="width:100%">
    <label style="color:#a6adc8;font-size:12px">Local Folder</label>
    <input id="ps-local" type="text" name="local" value="{{ prescan_local }}"
           placeholder="/mnt/media/shows or D:/Shows" style="width:100%">
  </div>
  <div style="display:flex;gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap">
    <button class="btn btn-green" id="ps-start-btn">Start Pre-Scan</button>
    <button type="button" class="btn btn-red" id="ps-cancel-btn" style="display:none"
            onclick="fetch('/prescan_stop').then(()=>{psReset();})">Cancel</button>
    <button type="button" class="btn btn-gray btn-sm" id="ps-clear-btn" style="display:none"
            onclick="fetch('/prescan_clear').then(()=>{psReset();})">Clear Results</button>
  </div>
  </form>
</div>

<!-- Progress bar — shown while scanning, updated via AJAX, no page reloads -->
<div class="section" id="ps-progress-section" style="display:none">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
    <h2 style="margin:0">Progress</h2>
  </div>
  <div style="color:#a6adc8;font-size:13px;margin-bottom:6px" id="ps-status-text">Starting...</div>
  <div class="pbar-outer"><div class="pbar-inner" id="ps-bar" style="width:0%"></div></div>
</div>

<!-- Results — injected by JS when scan finishes, no page reload -->
<div id="ps-results-container"></div>

<script>
(function(){
  // State
  var polling   = false;
  var pollTimer = null;

  function psReset(){
    polling = false;
    if(pollTimer) clearTimeout(pollTimer);
    document.getElementById('ps-progress-section').style.display='none';
    document.getElementById('ps-results-container').innerHTML='';
    document.getElementById('ps-start-btn').disabled=false;
    document.getElementById('ps-cancel-btn').style.display='none';
    document.getElementById('ps-clear-btn').style.display='none';
    document.getElementById('ps-status-text').textContent='Starting...';
    document.getElementById('ps-bar').style.width='0%';
  }
  window.psReset = psReset;

  function startPolling(){
    if(polling) return;
    polling = true;
    document.getElementById('ps-progress-section').style.display='';
    document.getElementById('ps-start-btn').disabled=true;
    document.getElementById('ps-cancel-btn').style.display='';
    document.getElementById('ps-clear-btn').style.display='none';
    doPoll();
  }

  function doPoll(){
    if(!polling) return;
    fetch('/api/prescan_status')
      .then(function(r){ return r.json(); })
      .then(function(d){
        var pct = d.total ? Math.round(d.scanned/d.total*100) : 0;
        document.getElementById('ps-status-text').textContent =
          'Scanning... ' + d.scanned + '/' + d.total +
          ' checked, ' + d.matched + ' matched (' + pct + '%)';
        document.getElementById('ps-bar').style.width = pct + '%';

        if(d.running){
          pollTimer = setTimeout(doPoll, 800);
        } else {
          // Done — fetch rendered results via AJAX, no page reload
          polling = false;
          document.getElementById('ps-cancel-btn').style.display='none';
          document.getElementById('ps-clear-btn').style.display='';
          document.getElementById('ps-start-btn').disabled=false;
          fetch('/api/prescan_results')
            .then(function(r){ return r.json(); })
            .then(function(res){
              if(res.html){
                document.getElementById('ps-status-text').innerHTML = res.summary;
                document.getElementById('ps-bar').style.width='100%';
                document.getElementById('ps-results-container').innerHTML = res.html;
              }
            });
        }
      })
      .catch(function(){
        if(polling) pollTimer = setTimeout(doPoll, 2000);
      });
  }

  // Hook form submit
  document.getElementById('ps-form').addEventListener('submit', function(e){
    e.preventDefault();
    psReset();
    var data = new FormData(this);
    fetch('/prescan_start', {method:'POST', body:data})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(d.error){
          alert(d.error);
        } else {
          startPolling();
        }
      });
  });

  // If a scan is already running when page loads, resume polling
  {% if ps_running %}
  startPolling();
  {% elif ps_result %}
  // Results exist from a previous scan — fetch and display them
  document.getElementById('ps-clear-btn').style.display='';
  fetch('/api/prescan_results')
    .then(function(r){ return r.json(); })
    .then(function(res){
      if(res.html){
        document.getElementById('ps-progress-section').style.display='';
        document.getElementById('ps-status-text').innerHTML = res.summary;
        document.getElementById('ps-bar').style.width='100%';
        document.getElementById('ps-results-container').innerHTML = res.html;
      }
    });
  {% endif %}
})();
</script>

<!-- ===== IGNORE LIST ===== -->
{% elif tab=='ignore' %}
<div class="section">
  <h2>Server Ignore List</h2>
  <p style="color:#a6adc8;font-size:13px;margin-bottom:12px">
    Remote paths listed here are completely skipped during sync and pre-scan.
    A prefix like <code>/home/user/trash</code> also skips everything inside it.
  </p>
  <form method="post" action="/ignore_add">
  <div class="field-row">
    <label>Remote path to ignore</label>
    <input type="text" name="path" placeholder="/home/user/unwanted" style="width:380px">
    <button class="btn btn-green btn-sm">+ Add</button>
  </div>
  </form>
</div>
<div class="section">
  <h2>Currently Ignored</h2>
  {% set ignored = cfg.get('ignored_paths', []) %}
  {% if ignored %}
  <table>
    <tr><th>Remote Path</th><th style="width:80px"></th></tr>
    {% for path in ignored %}
    <tr>
      <td style="font-family:Consolas,monospace;font-size:13px">{{ path }}</td>
      <td>
        <form method="post" action="/ignore_remove">
          <input type="hidden" name="path" value="{{ path }}">
          <button class="btn btn-red btn-sm">Remove</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  <br>
  <form method="post" action="/ignore_clear" onsubmit="return confirm('Remove all ignored paths?')">
    <button class="btn btn-red btn-sm">Clear All</button>
  </form>
  {% else %}
  <p style="color:#585b70">No paths ignored yet.</p>
  {% endif %}
</div>

<!-- ===== FTP ERRORS ===== -->
{% elif tab=='errors' %}
<div class="section">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
    <h2 style="margin:0;color:#f38ba8">FTP Errors</h2>
    <form method="post" action="/clear_errors" style="display:inline">
      <button class="btn btn-red btn-sm">Clear All</button>
    </form>
    <form method="post" action="/error_ignore_all"
          onsubmit="return confirm('Add all errored paths to the Ignore List?')" style="display:inline">
      <button class="btn btn-gray btn-sm">Ignore All</button>
    </form>
    <span style="color:#a6adc8;font-size:12px">
      Files that fail 2+ times are listed here instead of spamming the log.
      Right-click on a row in the desktop app for more options.
    </span>
  </div>
  {% if ftp_errors %}
  <table>
    <tr><th>Remote Path</th><th>Error</th><th>Fails</th><th>Last Seen</th><th></th></tr>
    {% for path, info in ftp_errors.items() %}
    <tr>
      <td style="font-family:Consolas,monospace;font-size:12px;word-break:break-all">{{ path }}</td>
      <td style="font-size:12px;color:#f38ba8">{{ info.error }}</td>
      <td style="text-align:center;font-weight:700;color:{% if info.count > 5 %}#f38ba8{% else %}#f9e2af{% endif %}">{{ info.count }}</td>
      <td style="font-size:12px;color:#a6adc8;white-space:nowrap">{{ info.last_seen }}</td>
      <td style="white-space:nowrap">
        <form method="post" action="/error_add_ignore" style="display:inline">
          <input type="hidden" name="path" value="{{ path }}">
          <button class="btn btn-gray btn-sm">+ Ignore</button>
        </form>
        <form method="post" action="/error_remove" style="display:inline;margin-left:4px">
          <input type="hidden" name="path" value="{{ path }}">
          <button class="btn btn-red btn-sm">&#x2715;</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p style="color:#585b70">No FTP errors recorded this session.</p>
  {% endif %}
</div>

<!-- ===== HISTORY ===== -->
{% elif tab=='history' %}
<div class="section">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">
    <h2 style="margin:0">Download History</h2>
    <a href="/export_history_csv" class="btn btn-gray btn-sm">Export CSV</a>
    <form method="post" action="/import_history_csv" enctype="multipart/form-data"
          style="display:inline;display:flex;align-items:center;gap:6px">
      <label class="btn btn-gray btn-sm" style="cursor:pointer;margin:0">
        Import CSV
        <input type="file" name="csvfile" accept=".csv" style="display:none"
               onchange="this.form.submit()">
      </label>
    </form>
    <form method="post" action="/clear_history"
          onsubmit="return confirm('Clear all history? Files will re-download on next sync.')">
      <button class="btn btn-red btn-sm">Clear All</button>
    </form>
  </div>
  {% set stats = get_stats() %}
  <div class="stats-row">
    <div class="stat-card"><div class="stat-val">{{ stats.total_files }}</div><div class="stat-lbl">Total Files</div></div>
    <div class="stat-card"><div class="stat-val">{{ '%.1f'|format(stats.total_bytes/1048576) }} MB</div><div class="stat-lbl">Total Size</div></div>
    <div class="stat-card"><div class="stat-val">{{ stats.today }}</div><div class="stat-lbl">Today</div></div>
    <div class="stat-card"><div class="stat-val">{{ stats.prescans }}</div><div class="stat-lbl">Pre-Scanned</div></div>
  </div>
  <div class="field-row" style="margin-bottom:10px">
    <label>Search:</label>
    <input type="text" id="search-box" placeholder="filter by path..." onkeyup="filterHistory()" style="width:300px">
  </div>
  <table id="hist-table">
    <tr><th>Date</th><th>Source</th><th>Remote Path</th><th>Local Path</th><th>Size</th></tr>
    {% for row in history_rows %}
    <tr data-remote="{{ row.remote_path }}" data-local="{{ row.local_path }}">
      <td style="font-size:12px;white-space:nowrap">{{ row.downloaded_at }}</td>
      <td>
        {% if row.source=='prescan' %}<span class="badge badge-ps">pre-scan</span>
        {% elif row.source=='prescan_manual' %}<span class="badge badge-pm">manual</span>
        {% else %}<span class="badge badge-dl">download</span>{% endif %}
      </td>
      <td style="font-size:12px;word-break:break-all">{{ row.remote_path }}</td>
      <td style="font-size:12px;word-break:break-all">{{ row.local_path }}</td>
      <td style="font-size:12px;white-space:nowrap">
        {% set sz=row.file_size %}
        {% if sz>=1073741824 %}{{ '%.1f'|format(sz/1073741824) }} GB
        {% elif sz>=1048576  %}{{ '%.1f'|format(sz/1048576)    }} MB
        {% elif sz>=1024     %}{{ '%.1f'|format(sz/1024)       }} KB
        {% else %}{{ sz }} B{% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</div>
<script>
function filterHistory(){
  const q=document.getElementById('search-box').value.toLowerCase();
  document.querySelectorAll('#hist-table tr[data-remote]').forEach(r=>{
    r.style.display=(r.dataset.remote.toLowerCase().includes(q)||
                     r.dataset.local.toLowerCase().includes(q))?'':'none';
  });
}
</script>

<!-- ===== BROWSER ===== -->
{% elif tab=='browser' %}

<!-- Server switcher -->
<div class="br-servers">
  <label>Server:</label>
  <select id="br-server-sel" onchange="brServerSwitch(this.value)">
    {% for s in servers %}
    <option value="{{ s.name }}" {% if s.name==active_server %}selected{% endif %}>{{ s.name }}</option>
    {% endfor %}
    {% if not servers %}<option value="">— no servers saved —</option>{% endif %}
  </select>
  <button class="btn btn-gray btn-sm" onclick="brShowServerModal()">+ Add Server</button>
  <button class="btn btn-gray btn-sm" onclick="brEditServer()">Edit</button>
  <button class="btn btn-red btn-sm" onclick="brRemoveServer()">Remove</button>
  <span style="color:#a6adc8;font-size:12px;margin-left:8px">Active: <b id="br-active-lbl" style="color:#cba6f7">{{ active_server or "none" }}</b></span>
  <span style="flex:1"></span>
  <button class="btn btn-blue btn-sm" onclick="brSaveSession()">💾 Save Session</button>
</div>

<!-- Nav toolbar -->
<div class="br-toolbar">
  <button class="btn btn-gray btn-sm" onclick="brBack()">◀</button>
  <button class="btn btn-gray btn-sm" onclick="brFwd()">▶</button>
  <button class="btn btn-gray btn-sm" onclick="brUp()">Up</button>
  <button class="btn btn-gray btn-sm" onclick="brRefresh()">Refresh</button>
  <input id="br-path" class="br-path" value="/" onkeydown="if(event.key==='Enter')brNavigate(this.value)">
  <button class="btn btn-green btn-sm" onclick="brConnect()">Connect</button>
  <button class="btn btn-gray btn-sm" onclick="brDisconnect()">Disconnect</button>
  <span id="br-conn-status" style="font-size:12px;color:#a6adc8">Not connected</span>
</div>

<!-- Main layout -->
<div class="br-layout">
  <!-- Left: dir tree -->
  <div>
    <div style="color:#89b4fa;font-size:12px;font-weight:700;margin-bottom:6px">Folders</div>
    <div class="br-tree" id="br-tree">
      <ul id="br-tree-ul"><li style="color:#45475a">Connect to browse</li></ul>
    </div>
  </div>

  <!-- Right: file list + controls -->
  <div style="display:flex;flex-direction:column;gap:0">
    <!-- File list header -->
    <div style="display:flex;align-items:center;background:#313244;padding:6px 10px;border-radius:6px 6px 0 0;gap:8px">
      <span style="color:#89b4fa;font-size:12px;font-weight:700">Remote Files</span>
      <span style="color:#6c7086;font-size:11px">Double-click folder=open &nbsp;|&nbsp; Click=select &nbsp;|&nbsp; Ctrl+click=multi</span>
      <span style="flex:1"></span>
      <button class="btn btn-gray btn-sm" onclick="brSelectAll()">Select All</button>
      <button class="btn btn-gray btn-sm" onclick="brNewFolder()">New Folder</button>
    </div>
    <div class="br-files" style="max-height:360px;overflow-y:auto">
      <table>
        <thead><tr>
          <th onclick="brSortBy('name')" style="width:55%">Name &#9660;</th>
          <th onclick="brSortBy('size')" style="width:15%;text-align:right">Size</th>
          <th onclick="brSortBy('modified')" style="width:30%">Modified</th>
        </tr></thead>
        <tbody id="br-filelist"><tr><td colspan="3" style="color:#45475a;text-align:center;padding:20px">Connect to browse</td></tr></tbody>
      </table>
    </div>

    <!-- Destination + transfer buttons -->
    <div class="br-dest">
      <label>Local folder:</label>
      <input type="text" id="br-local" class="br-path" placeholder="/path/to/local/folder" style="font-size:12px">
      <button class="btn btn-green btn-sm" onclick="brDownloadSelected()">↓ Download</button>
      <button class="btn btn-sm" style="background:#cba6f7;color:#1e1e2e;font-weight:700" onclick="brUploadFiles()">↑ Upload</button>
      <button class="btn btn-gray btn-sm" onclick="brMoveSelected()">Rename/Move</button>
      <button class="btn btn-red btn-sm" onclick="brDeleteSelected()">Delete</button>
    </div>

    <!-- Queue -->
    <div style="display:flex;align-items:center;background:#313244;padding:5px 10px;margin-top:8px;border-radius:6px 6px 0 0">
      <span style="color:#89b4fa;font-size:12px;font-weight:700">Queue</span>
      <span style="flex:1"></span>
      <button class="btn btn-gray btn-sm" onclick="brClearDone()">Clear Done</button>
    </div>
    <div class="br-queue" id="br-queue"><div style="color:#45475a;font-size:12px;text-align:center;padding:12px">No transfers queued</div></div>
  </div>
</div>

<!-- Add/Edit Server modal -->
<div id="br-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:999;align-items:center;justify-content:center">
  <div style="background:#1e1e2e;border:1px solid #45475a;border-radius:8px;padding:24px;width:400px;max-width:95vw">
    <h2 id="br-modal-title">Add Server</h2>
    <div style="display:grid;grid-template-columns:100px 1fr;gap:8px;align-items:center;margin-top:14px">
      <label style="color:#a6adc8;font-size:13px">Name</label>
      <input type="text" id="br-m-name" style="width:100%">
      <label style="color:#a6adc8;font-size:13px">Host</label>
      <input type="text" id="br-m-host" style="width:100%">
      <label style="color:#a6adc8;font-size:13px">Port</label>
      <input type="number" id="br-m-port" value="21" style="width:80px">
      <label style="color:#a6adc8;font-size:13px">Username</label>
      <input type="text" id="br-m-user" style="width:100%">
      <label style="color:#a6adc8;font-size:13px">Password</label>
      <input type="password" id="br-m-pass" style="width:100%">
    </div>
    <div id="br-m-status" style="color:#f38ba8;font-size:12px;min-height:16px;margin-top:8px"></div>
    <div style="display:flex;gap:8px;margin-top:14px">
      <button class="btn btn-gray btn-sm" onclick="brTestServer()">Test Connection</button>
      <span style="flex:1"></span>
      <button class="btn btn-green" onclick="brSaveServer()">Save</button>
      <button class="btn btn-gray" onclick="brCloseModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
// ── Browser state ──────────────────────────────────────────────────────────
const BR = {
  cwd:      "/",
  history:  ["/"],
  fwd:      [],
  entries:  [],         // [{name,is_dir,size,modify}, ...]
  selected: new Set(),  // indices into entries
  sortCol:  "name",
  sortRev:  false,
  queue:    [],         // [{id,name,dir,pct,status}]
  qNextId:  1,
  editingServer: null,
};

// ── Formatting ─────────────────────────────────────────────────────────────
function fmtSize(n){
  if(!n && n!==0) return "";
  const u=["B","KB","MB","GB","TB"];
  let i=0; while(n>=1024&&i<4){n/=1024;i++;}
  return i===0?n+" B":n.toFixed(1)+" "+u[i];
}
function fmtMod(raw){
  if(!raw) return "";
  if(raw.length>=14&&/^\d+$/.test(raw.slice(0,14))){
    const s=raw;
    return s.slice(0,4)+"-"+s.slice(4,6)+"-"+s.slice(6,8)+" "+s.slice(8,10)+":"+s.slice(10,12);
  }
  return raw.slice(0,16);
}

// ── Connection ─────────────────────────────────────────────────────────────
function brSetStatus(msg,ok){
  const el=document.getElementById("br-conn-status");
  el.textContent=msg;
  el.style.color=ok===true?"#a6e3a1":ok===false?"#f38ba8":"#a6adc8";
}

function brConnect(){
  brSetStatus("Connecting…");
  fetch("/api/br/connect",{method:"POST"})
    .then(r=>r.json()).then(d=>{
      if(d.ok){ brSetStatus("Connected to "+d.host,true); brNavigate("/"); brLoadTree("/"); }
      else     { brSetStatus("Failed: "+d.error,false); }
    }).catch(e=>brSetStatus("Error: "+e,false));
}
function brDisconnect(){
  fetch("/api/br/disconnect",{method:"POST"});
  brSetStatus("Disconnected");
  document.getElementById("br-filelist").innerHTML=
    '<tr><td colspan="3" style="color:#45475a;text-align:center;padding:20px">Disconnected</td></tr>';
  document.getElementById("br-tree-ul").innerHTML="<li style='color:#45475a'>Connect to browse</li>";
  BR.entries=[];BR.selected.clear();
}

// ── Navigation ─────────────────────────────────────────────────────────────
function brNavigate(path, push=true){
  if(!path) path="/";
  if(!path.startsWith("/")) path="/"+path;
  if(push && path!==BR.cwd){ BR.history.push(BR.cwd); BR.fwd=[]; }
  BR.cwd=path;
  document.getElementById("br-path").value=path;
  document.getElementById("br-filelist").innerHTML=
    '<tr><td colspan="3" style="color:#a6adc8;text-align:center;padding:12px">Loading…</td></tr>';
  BR.entries=[]; BR.selected.clear();
  fetch("/api/br/list?path="+encodeURIComponent(path))
    .then(r=>r.json()).then(d=>{
      if(d.error){ brSetStatus("Error: "+d.error,false); return; }
      BR.entries=d.entries;
      brRenderFiles();
    }).catch(e=>brSetStatus("List error: "+e,false));
}
function brRefresh(){ brNavigate(BR.cwd,false); }
function brUp(){
  if(BR.cwd==="/") return;
  const p=BR.cwd.replace(/\/+$/,"").split("/").slice(0,-1).join("/")||"/";
  brNavigate(p);
}
function brBack(){ if(BR.history.length){ BR.fwd.push(BR.cwd); brNavigate(BR.history.pop(),false); } }
function brFwd(){  if(BR.fwd.length){     BR.history.push(BR.cwd); brNavigate(BR.fwd.pop(),false);  } }

// ── Dir tree ───────────────────────────────────────────────────────────────
function brLoadTree(path){
  fetch("/api/br/list?path="+encodeURIComponent(path)+"&dirsonly=1")
    .then(r=>r.json()).then(d=>{
      if(d.error) return;
      const ul=document.getElementById("br-tree-ul");
      ul.innerHTML="";
      d.entries.filter(e=>e.is_dir).forEach(e=>{
        const li=document.createElement("li");
        li.textContent="📁 "+e.name;
        li.title=path.replace(/\/+$/,"")+"/"+e.name;
        li.onclick=()=>brNavigate(path.replace(/\/+$/,"")+"/"+e.name);
        ul.appendChild(li);
      });
    });
}

// ── File list rendering ────────────────────────────────────────────────────
function brRenderFiles(){
  const tb=document.getElementById("br-filelist");
  let rows="";
  // ".." back entry
  if(BR.cwd!=="/")
    rows+='<tr class="dir" style="cursor:pointer" ondblclick="brUp()"><td class="name" colspan="3">  ↑ ..</td></tr>';
  BR.entries.forEach((e,i)=>{
    const sel=BR.selected.has(i)?"sel":"";
    const cls=(e.is_dir?"dir ":"")+sel;
    const icon=e.is_dir?"📁 ":"📄 ";
    rows+=`<tr class="${cls}" style="cursor:pointer"
      onclick="brFileClick(event,${i})"
      ondblclick="brFileDbl(${i})">
      <td class="name">${icon}${escHtml(e.name)}</td>
      <td style="text-align:right">${e.is_dir?"":fmtSize(e.size)}</td>
      <td>${fmtMod(e.modify||"")}</td>
    </tr>`;
  });
  tb.innerHTML=rows||'<tr><td colspan="3" style="color:#45475a;text-align:center;padding:16px">Empty folder</td></tr>';
}
function escHtml(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function brFileClick(ev,i){
  if(ev.ctrlKey||ev.metaKey){
    if(BR.selected.has(i)) BR.selected.delete(i); else BR.selected.add(i);
  } else if(ev.shiftKey && BR.selected.size){
    const last=[...BR.selected].pop();
    const lo=Math.min(last,i), hi=Math.max(last,i);
    for(let j=lo;j<=hi;j++) BR.selected.add(j);
  } else {
    BR.selected.clear(); BR.selected.add(i);
  }
  brRenderFiles();
}
function brFileDbl(i){
  const e=BR.entries[i];
  if(e.is_dir) brNavigate(BR.cwd.replace(/\/+$/,"")+"/"+e.name);
  else brQueueDownload([e]);
}
function brSelectAll(){
  BR.entries.forEach((_,i)=>BR.selected.add(i));
  brRenderFiles();
}
function brSortBy(col){
  if(BR.sortCol===col) BR.sortRev=!BR.sortRev; else{BR.sortCol=col;BR.sortRev=false;}
  const key={name:e=>e.name.toLowerCase(),size:e=>e.size||0,modified:e=>e.modify||""};
  BR.entries.sort((a,b)=>{const k=key[col]; return BR.sortRev?k(b)>k(a)?1:-1:k(a)>k(b)?1:-1;});
  BR.selected.clear();
  brRenderFiles();
}

// ── Transfers ──────────────────────────────────────────────────────────────
function brGetSelectedEntries(){
  return [...BR.selected].map(i=>BR.entries[i]).filter(Boolean);
}

function brDownloadSelected(){
  const entries=brGetSelectedEntries();
  const files=entries.filter(e=>!e.is_dir);
  const folders=entries.filter(e=>e.is_dir);
  const local=document.getElementById("br-local").value.trim();
  if(!entries.length){alert("Select files or folders first.");return;}
  if(!local){alert("Set a local folder first.");return;}
  if(files.length) brQueueDownload(files,local);
  if(folders.length) brQueueFolders(folders,local);
}

function brQueueDownload(entries,local){
  local=local||document.getElementById("br-local").value.trim();
  if(!local){alert("Set a local folder.");return;}
  entries.forEach(e=>{
    if(e.is_dir) return;
    const remote=BR.cwd.replace(/\/+$/,"")+"/"+e.name;
    const id=BR.qNextId++;
    brAddQueueRow(id,"↓",e.name);
    fetch("/api/br/download",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({remote_path:remote,local_dir:local,size:e.size,qid:id})
    }).then(r=>r.json()).then(d=>{if(!d.ok) brUpdateQueue(id,"ERR:"+d.error,0,false);});
  });
}

function brQueueFolders(folders,local){
  local=local||document.getElementById("br-local").value.trim();
  folders.forEach(folder=>{
    const id=BR.qNextId++;
    brAddQueueRow(id,"↓",folder.name+"/");
    brUpdateQueue(id,"Scanning…",0,null);
    fetch("/api/br/download_folder",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({remote_folder:BR.cwd.replace(/\/+$/,"")+"/"+folder.name,local_dir:local,qid:id})
    }).then(r=>r.json()).then(d=>{
      if(!d.ok) brUpdateQueue(id,"ERR:"+d.error,0,false);
      else brUpdateQueue(id,`Queued ${d.count} files`,100,true);
    });
  });
}

function brUploadFiles(){
  // Use a hidden file input
  let inp=document.getElementById("br-upload-inp");
  if(!inp){ inp=document.createElement("input"); inp.type="file"; inp.id="br-upload-inp"; inp.multiple=true; inp.style.display="none"; document.body.appendChild(inp); }
  inp.onchange=()=>{
    const local=document.getElementById("br-local").value.trim();
    [...inp.files].forEach(file=>{
      const id=BR.qNextId++;
      brAddQueueRow(id,"↑",file.name);
      const remote=BR.cwd.replace(/\/+$/,"")+"/"+file.name;
      const fd=new FormData(); fd.append("file",file); fd.append("remote_path",remote); fd.append("qid",id);
      fetch("/api/br/upload",{method:"POST",body:fd})
        .then(r=>r.json()).then(d=>{
          if(d.ok){ brUpdateQueue(id,"Done ✓",100,true); setTimeout(brRefresh,400); }
          else     { brUpdateQueue(id,"ERR:"+d.error,0,false); }
        });
    });
    inp.value="";
  };
  inp.click();
}

function brMoveSelected(){
  const entries=brGetSelectedEntries();
  if(entries.length!==1){alert("Select exactly one item to rename/move.");return;}
  const e=entries[0];
  const newName=prompt("New name or full path for:\n"+e.name, e.name);
  if(!newName||newName===e.name) return;
  const oldPath=BR.cwd.replace(/\/+$/,"")+"/"+e.name;
  const newPath=newName.includes("/")?newName:BR.cwd.replace(/\/+$/,"")+"/"+newName;
  fetch("/api/br/rename",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({old_path:oldPath,new_path:newPath})
  }).then(r=>r.json()).then(d=>{
    if(d.ok) brRefresh(); else alert("Rename failed: "+d.error);
  });
}

function brDeleteSelected(){
  const entries=brGetSelectedEntries();
  if(!entries.length){alert("Select items to delete.");return;}
  const names=entries.map(e=>e.name).join(", ");
  if(!confirm("Permanently delete from server:\n"+names+"\n\nThis cannot be undone.")) return;
  fetch("/api/br/delete",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({items:entries.map(e=>({name:e.name,is_dir:e.is_dir,path:BR.cwd.replace(/\/+$/,"")+"/"+e.name}))})
  }).then(r=>r.json()).then(d=>{
    if(d.errors&&d.errors.length) alert("Some deletes failed:\n"+d.errors.join("\n"));
    brRefresh();
  });
}

function brNewFolder(){
  const name=prompt("New folder name:");
  if(!name) return;
  fetch("/api/br/mkdir",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({path:BR.cwd.replace(/\/+$/,"")+"/"+name})
  }).then(r=>r.json()).then(d=>{
    if(d.ok) brRefresh(); else alert("Failed: "+d.error);
  });
}

// ── Queue UI ───────────────────────────────────────────────────────────────
function brAddQueueRow(id,dir,name){
  const q=document.getElementById("br-queue");
  if(q.querySelector(".no-items")) q.innerHTML="";
  const dirColor=dir==="↓"?"#a6e3a1":"#cba6f7";
  const row=document.createElement("div");
  row.className="br-qrow"; row.id="br-qrow-"+id;
  row.innerHTML=`
    <span style="color:${dirColor};font-weight:700;width:14px">${dir}</span>
    <span class="br-qname" title="${escHtml(name)}">${escHtml(name)}</span>
    <div class="br-qbar"><div class="br-qfill${dir==="↑"?" up":""}" id="br-qfill-${id}" style="width:0%"></div></div>
    <span class="br-qstatus" id="br-qst-${id}">Queued</span>
    <button class="btn btn-gray btn-sm" style="padding:2px 6px" onclick="brCancelItem(${id})">✕</button>`;
  q.appendChild(row);
  q.scrollTop=q.scrollHeight;
}
function brUpdateQueue(id,status,pct,done){
  const fill=document.getElementById("br-qfill-"+id);
  const st=document.getElementById("br-qst-"+id);
  if(fill) fill.style.width=pct+"%";
  if(st){
    st.textContent=status;
    st.style.color=done===true?"#a6e3a1":done===false?"#f38ba8":"#a6adc8";
  }
}
function brCancelItem(id){
  fetch("/api/br/cancel",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({qid:id})});
  brUpdateQueue(id,"Cancelled",0,false);
}
function brClearDone(){
  document.querySelectorAll(".br-qrow").forEach(row=>{
    const st=row.querySelector(".br-qstatus");
    if(st&&(st.textContent.includes("Done")||st.textContent.includes("ERR")||st.textContent.includes("Cancelled")))
      row.remove();
  });
  if(!document.querySelector(".br-qrow"))
    document.getElementById("br-queue").innerHTML='<div class="no-items" style="color:#45475a;font-size:12px;text-align:center;padding:12px">No transfers queued</div>';
}

// Poll for queue progress
setInterval(()=>{
  fetch("/api/br/queue_status").then(r=>r.json()).then(d=>{
    if(!d.items) return;
    d.items.forEach(item=>{
      brUpdateQueue(item.qid, item.status, item.pct, item.done===true?true:item.done===false?false:null);
    });
  }).catch(()=>{});
},800);

// ── Server management ──────────────────────────────────────────────────────
function brServerSwitch(name){
  fetch("/api/br/server_switch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})})
    .then(r=>r.json()).then(d=>{
      if(d.ok){
        document.getElementById("br-active-lbl").textContent=name;
        brDisconnect();
        brSetStatus("Switched to "+name+" — click Connect.");
      }
    });
}
function brShowServerModal(profile){
  BR.editingServer=profile||null;
  document.getElementById("br-modal-title").textContent=profile?"Edit Server":"Add Server";
  document.getElementById("br-m-name").value=profile?profile.name:"";
  document.getElementById("br-m-host").value=profile?profile.host:"";
  document.getElementById("br-m-port").value=profile?profile.port:21;
  document.getElementById("br-m-user").value=profile?profile.user:"";
  document.getElementById("br-m-pass").value="";
  document.getElementById("br-m-status").textContent="";
  document.getElementById("br-modal").style.display="flex";
}
function brEditServer(){
  const name=document.getElementById("br-server-sel").value;
  if(!name) return;
  fetch("/api/br/get_server?name="+encodeURIComponent(name))
    .then(r=>r.json()).then(d=>{ if(d.profile) brShowServerModal(d.profile); });
}
function brCloseModal(){ document.getElementById("br-modal").style.display="none"; }
function brRemoveServer(){
  const name=document.getElementById("br-server-sel").value;
  if(!name||!confirm("Remove server '"+name+"'?")) return;
  fetch("/api/br/remove_server",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})})
    .then(r=>r.json()).then(d=>{ if(d.ok) location.reload(); });
}
function brTestServer(){
  const h=document.getElementById("br-m-host").value.trim();
  const p=parseInt(document.getElementById("br-m-port").value)||21;
  const u=document.getElementById("br-m-user").value.trim();
  const pw=document.getElementById("br-m-pass").value;
  document.getElementById("br-m-status").textContent="Testing…";
  fetch("/api/br/test_server",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({host:h,port:p,user:u,password:pw})})
    .then(r=>r.json()).then(d=>{
      const el=document.getElementById("br-m-status");
      el.textContent=d.ok?"✓ Connected!":"✗ "+d.error;
      el.style.color=d.ok?"#a6e3a1":"#f38ba8";
    });
}
function brSaveServer(){
  const name=document.getElementById("br-m-name").value.trim();
  const host=document.getElementById("br-m-host").value.trim();
  if(!name||!host){alert("Name and Host are required.");return;}
  fetch("/api/br/save_server",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({
      old_name:BR.editingServer?BR.editingServer.name:null,
      name,host,
      port:parseInt(document.getElementById("br-m-port").value)||21,
      user:document.getElementById("br-m-user").value.trim(),
      password:document.getElementById("br-m-pass").value,
    })
  }).then(r=>r.json()).then(d=>{ if(d.ok) location.reload(); else alert("Save failed: "+d.error); });
}
function brSaveSession(){
  const name=prompt("Session name:","My Session");
  if(!name) return;
  fetch("/api/br/save_session",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name,cwd:BR.cwd,local_dir:document.getElementById("br-local").value.trim()})
  }).then(r=>r.json()).then(d=>{
    if(d.ok) alert("Session saved to:\n"+d.path);
    else alert("Failed: "+d.error);
  });
}
</script>

<!-- ===== SETTINGS ===== -->
{% elif tab=='settings' %}
<div class="section">
  <h2>Sync Settings</h2>
  <form method="post" action="/save_settings">
  <div class="field-row">
    <label>Check interval (minutes)</label>
    <input type="number" name="interval" value="{{ cfg.get('interval',5) }}" min="1" max="1440">
  </div>
  <div class="field-row">
    <label>Parallel downloads (1-10)</label>
    <input type="number" name="parallel_downloads" value="{{ cfg.get('parallel_downloads',3) }}" min="1" max="10">
    <span style="color:#a6adc8;font-size:12px">Each download opens its own FTP connection</span>
  </div>
  <button class="btn btn-green" style="margin-top:8px">Save Settings</button>
  </form>
</div>
<div class="section">
  <h2>File Locations</h2>
  <p style="color:#a6adc8;font-size:13px;line-height:2">
    Settings: <code>{{ config_file }}</code><br>
    History:  <code>{{ db_file }}</code>
  </p>
</div>
<div class="section">
  <h2>Folder Mounts</h2>

  <!-- ── What's currently mounted (live, from the running container) ── -->
  <p style="color:#a6adc8;font-size:12px;margin-bottom:6px">
    <strong style="color:#cdd6f4">Currently available in this container:</strong>
    these paths can be used as Local Path in Folder Pairs right now.
  </p>
  {% if docker_mounts %}
  <table style="margin-bottom:18px">
    <tr><th>Local Path (use this in Folder Pairs)</th><th>Type</th><th></th></tr>
    {% for m in docker_mounts %}
    <tr>
      <td style="font-family:Consolas,monospace;font-size:13px">{{ m.path }}</td>
      <td style="font-size:12px;color:#a6adc8">{{ m.fstype }}</td>
      <td>
        {% if m.path != '/data' %}
        <button class="btn btn-gray btn-sm"
                onclick="navigator.clipboard.writeText('{{ m.path }}');this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1500)">
          Copy
        </button>
        {% else %}
        <span style="color:#585b70;font-size:11px">config/history only</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p style="color:#585b70;font-size:12px;margin-bottom:18px">No extra folders mounted yet. Add one below.</p>
  {% endif %}

  <!-- ── Compose file editor ── -->
  {% if compose_path %}
  <div style="border-top:1px solid #313244;padding-top:16px;margin-top:4px">
    <p style="color:#cdd6f4;font-size:13px;font-weight:600;margin-bottom:4px">
      Edit docker-compose.yml volume mounts
    </p>
    <p style="color:#a6adc8;font-size:12px;margin-bottom:12px">
      Add or remove folders here. Changes are saved to
      <code style="background:#313244;padding:1px 5px;border-radius:3px">{{ compose_path }}</code>
      and the container is restarted automatically to apply them.<br><br>
      <strong style="color:#f9e2af">Left side</strong> = the real path on your Unraid server
      (e.g. <code style="background:#313244;padding:1px 4px;border-radius:3px">/mnt/user/Media/TV</code>)<br>
      <strong style="color:#a6e3a1">Right side</strong> = the path the app uses — what you type in Folder Pairs
      (e.g. <code style="background:#313244;padding:1px 4px;border-radius:3px">/mnt/tv</code>)
    </p>

    <!-- existing mounts editable list -->
    <form method="post" action="/compose_save_volumes" id="compose-form">
    <table id="mount-table" style="margin-bottom:10px">
      <tr>
        <th style="color:#f9e2af">Server Path (left side)</th>
        <th style="color:#a6e3a1">Container Path (right side)</th>
        <th></th>
      </tr>
      {% for v in compose_volumes %}
      <tr class="mount-row">
        <td>
          <input type="text" name="host[]" value="{{ v.host }}"
                 style="width:100%;font-family:Consolas,monospace;font-size:12px"
                 {% if not v.exists %}
                 title="Warning: this path does not exist on the server"
                 {% endif %}>
          {% if not v.exists %}
          <div style="color:#f38ba8;font-size:10px;margin-top:2px">
            ⚠ Path not found on server
          </div>
          {% endif %}
        </td>
        <td>
          <input type="text" name="container[]" value="{{ v.container }}"
                 style="width:100%;font-family:Consolas,monospace;font-size:12px">
        </td>
        <td style="white-space:nowrap;padding-left:8px">
          <button type="button" class="btn btn-red btn-sm"
                  onclick="this.closest('tr').remove()">Remove</button>
        </td>
      </tr>
      {% endfor %}
    </table>

    <!-- add new row button -->
    <button type="button" class="btn btn-gray btn-sm" style="margin-bottom:12px"
            onclick="addMountRow()">+ Add Folder</button>

    <br>
    <button type="submit" class="btn btn-green">Save &amp; Restart Container</button>
    <span style="color:#585b70;font-size:11px;margin-left:10px">
      Container will restart — the web UI will be back in ~10 seconds
    </span>
    </form>
  </div>

  <script>
  function addMountRow() {
    var tbody = document.getElementById('mount-table');
    var row = document.createElement('tr');
    row.className = 'mount-row';
    row.innerHTML =
      '<td><input type="text" name="host[]" placeholder="/mnt/user/Media/TV" ' +
      'style="width:100%;font-family:Consolas,monospace;font-size:12px"></td>' +
      '<td><input type="text" name="container[]" placeholder="/mnt/tv" ' +
      'style="width:100%;font-family:Consolas,monospace;font-size:12px"></td>' +
      '<td style="white-space:nowrap;padding-left:8px">' +
      '<button type="button" class="btn btn-red btn-sm" ' +
      'onclick="this.closest('tr').remove()">Remove</button></td>';
    tbody.appendChild(row);
    row.querySelector('input').focus();
  }
  </script>

  {% else %}
  <!-- compose file not found — show instructions + manual path entry -->
  <div style="border-top:1px solid #313244;padding-top:16px;margin-top:4px">
    <div style="background:#313244;border-radius:6px;padding:14px 16px;color:#a6adc8;font-size:12px">
      <strong style="color:#cdd6f4">docker-compose.yml not found in the expected locations.</strong><br><br>
      To edit volume mounts from this UI, place <strong>docker-compose.yml</strong>
      in the same folder as the app files (e.g. <code>/app/docker-compose.yml</code>)
      and restart the container.<br><br>
      Or edit it manually on your server and run:
      <code style="background:#1e1e2e;padding:2px 6px;border-radius:3px">docker compose up -d</code>
      <br><br>
      Example volume line to add under <code>volumes:</code>:<br>
      <code style="background:#1e1e2e;padding:4px 8px;border-radius:3px;display:inline-block;margin:4px 0">
        - "/mnt/user/Media/TV:/mnt/tv"
      </code><br>
      <strong style="color:#f9e2af">Left side</strong> = real path on your server &nbsp;|&nbsp;
      <strong style="color:#a6e3a1">Right side</strong> = path to use in Folder Pairs
    </div>
  </div>
  {% endif %}
</div>

<div class="section">
  <h2>Backup &amp; Migration</h2>
  <p style="color:#a6adc8;font-size:13px;margin-bottom:12px">
    Export your settings and history to move to a new machine or back things up.
    Passwords are stripped from exported settings — re-enter them after importing.
  </p>
  <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
    <a href="/export_settings" class="btn btn-gray btn-sm">Export Settings</a>
    <form method="post" action="/import_settings" enctype="multipart/form-data"
          style="display:inline;display:flex;align-items:center">
      <label class="btn btn-gray btn-sm" style="cursor:pointer;margin:0">
        Import Settings
        <input type="file" name="jsonfile" accept=".json" style="display:none"
               onchange="this.form.submit()">
      </label>
    </form>
    <span style="color:#585b70;font-size:12px">|</span>
    <a href="/export_history_csv" class="btn btn-gray btn-sm">Export History CSV</a>
    <form method="post" action="/import_history_csv" enctype="multipart/form-data"
          style="display:inline;display:flex;align-items:center">
      <label class="btn btn-gray btn-sm" style="cursor:pointer;margin:0">
        Import History CSV
        <input type="file" name="csvfile" accept=".csv" style="display:none"
               onchange="this.form.submit()">
      </label>
    </form>
  </div>
</div>
<div class="section">
  <h2>Updates</h2>
  <p style="color:#a6adc8;font-size:13px;margin-bottom:12px">
    Install updated <code>.py</code> files without rebuilding the EXE.
    Files are syntax-checked before installing. <strong>Restart the app</strong> after installing to apply.
  </p>
  {% set upd = get_override_status() %}
  {% set active = get_active_overrides() %}
  <table style="margin-bottom:12px">
    <tr><th>File</th><th>Status</th><th>Installed</th><th></th></tr>
    {% for fname, info in upd.items() %}
    <tr>
      <td style="font-family:Consolas,monospace;font-size:13px">{{ fname }}</td>
      <td>
        {% if info.installed %}
          <span style="color:#a6e3a1;font-weight:700">&#10003; Override installed</span>
          {% if fname in active %}<span style="color:#f9e2af;font-size:11px"> (active)</span>
          {% else %}<span style="color:#a6adc8;font-size:11px"> (restart to activate)</span>{% endif %}
        {% else %}
          <span style="color:#585b70">&#8211; Built-in</span>
        {% endif %}
      </td>
      <td style="font-size:12px;color:#a6adc8">{{ info.mtime or '&mdash;' }}</td>
      <td>
        {% if info.installed %}
        <form method="post" action="/remove_override"
              onsubmit="return confirm('Remove override for {{ fname }}? Built-in version will be used after restart.')">
          <input type="hidden" name="filename" value="{{ fname }}">
          <button class="btn btn-red btn-sm">Remove</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  <form method="post" action="/install_update" enctype="multipart/form-data"
        style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
    <label class="btn btn-blue btn-sm" style="cursor:pointer;margin:0">
      &#8593; Install Update (.py)
      <input type="file" name="pyfile" accept=".py" style="display:none"
             onchange="this.form.submit()">
    </label>
    <span style="color:#585b70;font-size:12px">
      Updates folder: <code>{{ updates_dir }}</code>
    </span>
  </form>
</div>
<div class="section">
  <h2>Network Access</h2>
  <p style="color:#a6adc8;font-size:13px;line-height:2">
    Local:   <code>http://localhost:8080</code><br>
    Network: <code>http://{{ lan_ip }}:8080</code>
  </p>
</div>
<div style="text-align:center;padding:20px 0 8px">
  <div style="color:#89b4fa;font-size:15px;font-weight:700;font-style:italic">Vibe Coded by Itsuko</div>
  <div style="color:#585b70;font-size:11px;margin-top:4px">Built with Python, Flask &amp; a lot of vibes</div>
  <div style="margin-top:6px">
    <a href="https://twitter.com/Itsukos" target="_blank"
       style="color:#89dceb;font-size:11px;text-decoration:underline">
      DM bugs @ twitter.com/Itsukos
    </a>
  </div>
  <div style="color:#45475a;font-size:10px;margin-top:4px">v{{ version }}</div>
</div>
{% endif %}

</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_running():
    return worker is not None and worker.is_alive()

# ---------------------------------------------------------------------------
# Docker compose file helpers
# ---------------------------------------------------------------------------

def _find_compose_file():
    """Search common locations for docker-compose.yml."""
    candidates = [
        "/app/docker-compose.yml",
        "/docker-compose.yml",
        os.path.join(os.path.dirname(__file__), "docker-compose.yml"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "docker-compose.yml"),
        "/mnt/user/appdata/ftpsync-build/docker-compose.yml",
    ]
    # Also check the directory that owns /data (one level up from appdata)
    data_dir = os.environ.get("FTP_CONFIG_FILE", "/data/config.json")
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(data_dir)), "ftpsync-build", "docker-compose.yml"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _parse_compose_volumes(compose_path):
    """
    Parse the volumes: section of docker-compose.yml and return a list of
    dicts: [{host, container, is_named, raw}, ...]
    Only bind mounts (host:container pairs) are returned, not named volumes.
    """
    volumes = []
    try:
        with open(compose_path) as f:
            lines = f.readlines()
        in_volumes = False
        in_service_volumes = False
        service_indent = None
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            # Detect we're inside the service volumes block
            if stripped == "volumes:" and indent > 0:
                in_service_volumes = True
                service_indent = indent
                continue
            # Top-level volumes: block (named volumes) — skip
            if stripped == "volumes:" and indent == 0:
                in_service_volumes = False
                in_volumes = True
                continue
            if in_volumes and indent == 0 and stripped != "volumes:":
                in_volumes = False
            if in_service_volumes:
                if indent <= service_indent and stripped not in ("", ):
                    # Back out of the service volumes block
                    if not stripped.startswith("-"):
                        in_service_volumes = False
                        continue
                if stripped.startswith("- "):
                    raw = stripped[2:].strip().strip('"').strip("'")
                    if ":" in raw:
                        parts = raw.split(":")
                        host      = parts[0].strip()
                        container = parts[1].strip()
                        # Skip named volumes (no / in host path)
                        if "/" in host or host.startswith("."):
                            volumes.append({
                                "host": host,
                                "container": container,
                                "raw": raw,
                                "exists": os.path.exists(host),
                            })
    except Exception:
        pass
    return volumes


def get_docker_mounts():
    """
    Return a list of dicts describing folder mounts available to the app.
    Works by reading /proc/mounts (Linux/Docker) and filtering for bind mounts.
    Falls back gracefully on Windows.
    """
    mounts = []
    skip_prefixes = ("/proc", "/sys", "/dev", "/run", "/etc", "/usr", "/var",
                     "/lib", "/bin", "/sbin", "/tmp", "/app")
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mountpoint = parts[1]
                fstype     = parts[2]
                if any(mountpoint.startswith(p) for p in skip_prefixes):
                    continue
                if mountpoint in ("/", "/data"):
                    continue
                if fstype in ("proc", "sysfs", "devtmpfs", "devpts", "tmpfs",
                               "cgroup", "cgroup2", "mqueue", "hugetlbfs",
                               "nsfs", "overlay", "shm"):
                    continue
                mounts.append({"path": mountpoint, "fstype": fstype})
    except Exception:
        pass
    paths = [m["path"] for m in mounts]
    if "/data" not in paths:
        mounts.insert(0, {"path": "/data", "fstype": "config/history"})
    return sorted(mounts, key=lambda m: m["path"])


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def render(tab="dashboard", flash=None, flash_type="ok",
           prescan_remote="", prescan_local=""):
    cfg          = core.load_config()
    folder_pairs = cfg.get("folder_pairs", [])
    history_rows = history.get_all(limit=500)
    warnings     = core.validate_config(cfg) if cfg.get("host") else []
    return render_template_string(
        PAGE,
        tab=tab, cfg=cfg,
        folder_pairs=folder_pairs,
        history_rows=history_rows,
        running=get_running(),
        verbose=verbose_flag,
        flash=flash, flash_type=flash_type,
        warnings=warnings,
        get_stats=history.get_stats,
        lan_ip=get_lan_ip(),
        enumerate=enumerate,
        config_file=core.CONFIG_FILE,
        db_file=core.DB_FILE,
        ps_running=prescan_state["running"],
        ps_state=prescan_state,
        ps_result=prescan_result,
        prescan_remote=prescan_remote,
        prescan_local=prescan_local,
        get_override_status=core.get_override_status,
        get_active_overrides=core.get_active_overrides,
        updates_dir=core.get_updates_dir(),
        ftp_errors=dict(ftp_errors),
        error_count=len(ftp_errors),
        version=core.VERSION,
        docker_mounts=get_docker_mounts(),
        compose_path=_find_compose_file(),
        compose_volumes=_parse_compose_volumes(_find_compose_file()) if _find_compose_file() else [],
        servers=core.list_servers(cfg),
        active_server=cfg.get("active_server", "Default" if cfg.get("host") else ""),
    )


# ---------------------------------------------------------------------------
# Routes - credentials & sync control
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render(tab=request.args.get("tab", "dashboard"))

@app.route("/save_credentials", methods=["POST"])
def save_credentials():
    cfg  = core.load_config()
    save = "save_credentials" in request.form
    cfg["save_credentials"] = save
    if save:
        cfg["host"] = request.form.get("host", "").strip()
        cfg["port"] = int(request.form.get("port", 21) or 21)
        cfg["user"] = request.form.get("user", "").strip()
        pwd = request.form.get("password", "")
        if pwd:  # only update if a new password was typed
            core.set_password(cfg, pwd)
    else:
        # Wipe credentials from disk
        cfg["host"] = ""; cfg["port"] = 21; cfg["user"] = ""; cfg["password_enc"] = ""
    core.save_config(cfg)
    msg = "Credentials saved (password encrypted)." if save else "Credentials removed from disk."
    return render(tab="dashboard", flash=msg, flash_type="ok")

@app.route("/start", methods=["POST"])
def start():
    global worker
    if get_running():
        return render(tab="dashboard", flash="Already running.", flash_type="err")
    cfg    = core.load_config()
    warns  = core.validate_config(cfg)
    if warns:
        return render(tab="dashboard", flash=" | ".join(warns), flash_type="err")
    worker = core.SyncWorker(
        config=cfg, history=history,
        on_log=add_log,
        on_transfer_start=on_transfer_start,
        on_transfer_progress=on_transfer_progress,
        on_transfer_done=on_transfer_done,
        on_transfer_error=on_transfer_error,
        debug=verbose_flag,
    )
    worker.start()
    add_log("[Sync] Started.")
    return redirect("/?tab=dashboard")

@app.route("/stop", methods=["POST"])
def stop():
    global worker
    if worker:
        worker.stop()   # closes active FTP sockets immediately
        worker = None
        add_log("[Sync] Stopped by user.")
    return redirect("/?tab=dashboard")

@app.route("/sync_now", methods=["POST"])
def sync_now():
    cfg   = core.load_config()
    warns = core.validate_config(cfg)
    if warns:
        return render(tab="dashboard", flash=" | ".join(warns), flash_type="err")
    def run():
        w = core.SyncWorker(
            config=cfg, history=history,
            on_log=add_log,
            on_transfer_start=on_transfer_start,
            on_transfer_progress=on_transfer_progress,
            on_transfer_done=on_transfer_done,
            on_transfer_error=on_transfer_error,
            debug=verbose_flag,
        )
        w._sync_all()
    threading.Thread(target=run, daemon=True).start()
    return redirect("/?tab=dashboard")

@app.route("/set_verbose", methods=["POST"])
def set_verbose():
    global verbose_flag
    verbose_flag = request.json.get("verbose", False)
    return jsonify(ok=True)

@app.route("/clear_completed_transfers", methods=["POST"])
def clear_completed_transfers():
    with transfers_lock:
        done_keys = [tid for tid, t in transfers.items()
                     if t.get("status", "").lower() != "downloading"]
        for k in done_keys:
            del transfers[k]
    return "", 204


@app.route("/api/test_connection", methods=["POST"])
def api_test_connection():
    data     = request.json or {}
    host     = data.get("host", "").strip()
    port     = data.get("port", 21)
    user     = data.get("user", "").strip()
    password = data.get("password", "")
    # If no password was typed in the form, use the saved encrypted one
    if not password:
        cfg      = core.load_config()
        password = core.get_password(cfg)
    ok, msg = core.test_connection(host, port, user, password)
    return jsonify(ok=ok, msg=msg)

@app.route("/api/status")
def api_status():
    with transfers_lock: t = dict(transfers)
    with log_lock:       lines = list(log_lines)
    return jsonify(running=get_running(), transfers=t, log=lines)


# ---------------------------------------------------------------------------
# Routes - folder pairs  (auto-save to settings.json)
# ---------------------------------------------------------------------------

@app.route("/add_pair", methods=["POST"])
def add_pair():
    remote = request.form.get("remote", "").strip()
    local  = request.form.get("local", "").strip()
    if not remote or not local:
        return render(tab="folders", flash="Both paths required.", flash_type="err")
    cfg = core.load_config()
    cfg.setdefault("folder_pairs", []).append({"remote": remote, "local": local})
    core.save_config(cfg)
    add_log(f"[Pairs] Added: {remote} -> {local}")
    return render(tab="folders", flash="Pair added and saved.", flash_type="ok")

@app.route("/remove_pair", methods=["POST"])
def remove_pair():
    idx = int(request.form.get("index", -1))
    cfg = core.load_config()
    pairs = cfg.get("folder_pairs", [])
    if 0 <= idx < len(pairs):
        removed = pairs.pop(idx)
        core.save_config(cfg)
        add_log(f"[Pairs] Removed: {removed['remote']}")
    return render(tab="folders", flash="Pair removed.", flash_type="ok")


# ---------------------------------------------------------------------------
# Routes - settings
# ---------------------------------------------------------------------------

@app.route("/save_settings", methods=["POST"])
def save_settings():
    cfg = core.load_config()
    try: cfg["interval"] = int(request.form.get("interval", 5))
    except: cfg["interval"] = 5
    try: cfg["parallel_downloads"] = max(1, min(10, int(request.form.get("parallel_downloads", 3))))
    except: cfg["parallel_downloads"] = 3
    core.save_config(cfg)
    return render(tab="settings", flash="Settings saved.", flash_type="ok")

@app.route("/clear_history", methods=["POST"])
def clear_history():
    history.clear_all()
    return render(tab="history", flash="History cleared.", flash_type="ok")


# ---------------------------------------------------------------------------
# Routes - pre-scan
# ---------------------------------------------------------------------------

@app.route("/prescan_start", methods=["POST"])
def prescan_start():
    global scanner, prescan_result
    remote = request.form.get("remote", "").strip()
    local  = request.form.get("local", "").strip()
    if not remote or not local:
        return jsonify(error="Both paths are required.")
    cfg = core.load_config()
    if not cfg.get("host"):
        return jsonify(error="No FTP credentials configured on Dashboard.")

    prescan_result = None
    prescan_state.update(running=True, scanned=0, matched=0, total=0)

    def on_progress(scanned, matched, total):
        prescan_state.update(scanned=scanned, matched=matched, total=total)

    def on_done(result):
        global prescan_result
        prescan_result = result
        prescan_state["running"] = False
        add_log(f"[PreScan] Done: {len(result.auto_matched)} matched, "
                f"{len(result.needs_review)} review, "
                f"{len(result.remote_only)} remote-only")

    scanner = core.PreScanner(
        config=cfg, history=history,
        remote_path=remote, local_path=local,
        on_log=add_log,
        on_progress=on_progress,
        on_done=on_done,
    )
    scanner.start()
    return jsonify(ok=True)

@app.route("/prescan_stop")
def prescan_stop():
    global scanner
    if scanner:
        scanner.stop()
        scanner = None
    prescan_state["running"] = False
    add_log("[PreScan] Cancelled by user.")
    return redirect("/?tab=prescan")

@app.route("/prescan_resolve", methods=["POST"])
def prescan_resolve():
    if not prescan_result:
        return redirect("/?tab=prescan")
    count    = int(request.form.get("count", 0))
    resolved = 0
    for i in range(count):
        if request.form.get(f"resolve_{i}"):
            item  = prescan_result.needs_review[i]
            local = item["candidates"][0] if item.get("candidates") else ""
            history.record(
                fingerprint=item["fingerprint"],
                remote_path=item["remote_path"],
                local_path=local,
                size=item["size"],
                modify=item["modify"],
                source="prescan_manual",
            )
            resolved += 1
    return render(tab="prescan",
                  flash=f"{resolved} file(s) marked as known.",
                  flash_type="ok")

@app.route("/api/prescan_status")
def api_prescan_status():
    return jsonify(**prescan_state)


@app.route("/prescan_clear")
def prescan_clear():
    """Reset prescan state so results don't persist across page loads."""
    global prescan_result
    prescan_result = None
    prescan_state.update(running=False, scanned=0, matched=0, total=0)
    return "", 204


@app.route("/api/prescan_results")
def api_prescan_results():
    """Return rendered HTML fragments for prescan results via AJAX."""
    if not prescan_result:
        return jsonify(html="", summary="No results.")

    r = prescan_result
    auto    = r.auto_matched    if r.auto_matched    else []
    review  = r.needs_review    if r.needs_review    else []
    ro      = r.remote_only     if r.remote_only     else []
    known   = r.already_known   if hasattr(r, "already_known") else 0

    summary = (
        f"Done. &nbsp;"
        f"<strong style='color:#a6e3a1'>Auto-matched: {len(auto)}</strong> &nbsp;|&nbsp; "
        f"<strong style='color:#f9e2af'>Needs review: {len(review)}</strong> &nbsp;|&nbsp; "
        f"<strong style='color:#89b4fa'>Will download: {len(ro)}</strong> &nbsp;|&nbsp; "
        f"Already known: <strong>{known}</strong>"
    )

    # Build review form HTML
    review_html = ""
    if review:
        rows = ""
        for i, item in enumerate(review):
            cands = ", ".join(item.get("candidates", []))
            rows += (
                f"<tr>"
                f"<td style='text-align:center'>"
                f"<input type='checkbox' name='resolve_{i}' value='1'></td>"
                f"<td style='font-size:12px;word-break:break-all'>"
                f"{item['remote_path'].split('/')[-1]}</td>"
                f"<td style='font-size:12px;color:#f9e2af'>{item.get('reason','')}</td>"
                f"<td style='font-size:12px;color:#a6adc8;word-break:break-all'>{cands}</td>"
                f"</tr>"
            )
        review_html = f"""
<div class='section'>
  <h2>Review — Ambiguous Files</h2>
  <p style='color:#a6adc8;font-size:13px;margin-bottom:10px'>
    Check files you want to mark as <strong>already have it</strong> — fingerprinted and never downloaded again.
    Leave unchecked to let normal sync handle them.
  </p>
  <form method='post' action='/prescan_resolve'>
  <table>
    <tr><th style='width:50px'>Known</th><th>Remote File</th><th>Reason</th><th>Local Candidates</th></tr>
    {rows}
  </table>
  <input type='hidden' name='count' value='{len(review)}'>
  <div style='margin-top:10px;display:flex;gap:8px;flex-wrap:wrap'>
    <button class='btn btn-green'>Apply Decisions</button>
    <button type='button' class='btn btn-gray btn-sm'
            onclick="document.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=true)">
      Check All</button>
    <button type='button' class='btn btn-gray btn-sm'
            onclick="document.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=false)">
      Uncheck All</button>
  </div>
  </form>
</div>"""
    else:
        review_html = "<div class='section'><p style='color:#a6e3a1'>No ambiguous files — everything matched automatically!</p></div>"

    # Build all-results table
    result_rows = ""
    for rp, lp, sz, _ in auto:
        result_rows += (f"<tr><td><span class='badge badge-pm'>Matched</span></td>"
                        f"<td style='font-size:12px'>{rp.split('/')[-1]}</td>"
                        f"<td style='font-size:12px;color:#a6adc8'>{lp}</td></tr>")
    for item in review:
        result_rows += (f"<tr><td><span class='badge badge-ps'>Review</span></td>"
                        f"<td style='font-size:12px'>{item['remote_path'].split('/')[-1]}</td>"
                        f"<td style='font-size:12px;color:#f9e2af'>{item.get('reason','')}</td></tr>")
    for rp, sz in ro:
        result_rows += (f"<tr><td><span class='badge badge-dl'>Will DL</span></td>"
                        f"<td style='font-size:12px'>{rp.split('/')[-1]}</td>"
                        f"<td style='font-size:12px;color:#a6adc8'>Not found locally</td></tr>")

    all_results_html = f"""
<div class='section'>
  <h2>All Results</h2>
  <table>
    <tr><th>Result</th><th>File</th><th>Detail</th></tr>
    {result_rows}
  </table>
</div>""" if result_rows else ""

    html = review_html + all_results_html
    return jsonify(html=html, summary=summary)


# ---------------------------------------------------------------------------
# Routes - export / import
# ---------------------------------------------------------------------------

@app.route("/export_history_csv")
def export_history_csv():
    """Stream the full history as a CSV download."""
    import csv, io
    rows = history.get_all(limit=999_999)
    buf  = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "id", "downloaded_at", "source", "remote_path", "local_path",
        "file_size", "modify_time", "pair_remote",
    ])
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    fname = f"ftpsync_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=fname,
    )

@app.route("/import_history_csv", methods=["POST"])
def import_history_csv():
    f = request.files.get("csvfile")
    if not f or not f.filename:
        return render(tab="history", flash="No file selected.", flash_type="err")
    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            f.save(tmp.name)
            imported, skipped = core.import_history_csv(history, tmp.name)
        os.unlink(tmp.name)
        add_log(f"[History] Imported {imported}, skipped {skipped}")
        return render(tab="history",
                      flash=f"Imported {imported} record(s), skipped {skipped} (already known).",
                      flash_type="ok")
    except Exception as e:
        return render(tab="history", flash=f"Import failed: {e}", flash_type="err")

@app.route("/export_settings")
def export_settings_route():
    """Download settings.json with password stripped."""
    import io
    buf = io.StringIO()
    cfg = core.load_config()
    cfg.pop("password_enc", None)
    cfg["save_credentials"] = False
    cfg["_comment"] = "Exported settings (password stripped). Re-enter after importing."
    import json
    json.dump(cfg, buf, indent=2)
    buf.seek(0)
    fname = f"ftpsync_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="application/json",
        as_attachment=True,
        download_name=fname,
    )

@app.route("/import_settings", methods=["POST"])
def import_settings_route():
    f = request.files.get("jsonfile")
    if not f or not f.filename:
        return render(tab="settings", flash="No file selected.", flash_type="err")
    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            f.save(tmp.name)
            warns = core.import_settings(tmp.name)
        os.unlink(tmp.name)
        add_log("[Settings] Imported from uploaded file")
        msg = "Settings imported."
        if warns:
            msg += " Warnings: " + " | ".join(warns)
        return render(tab="settings", flash=msg, flash_type="ok")
    except Exception as e:
        return render(tab="settings", flash=f"Import failed: {e}", flash_type="err")


# ---------------------------------------------------------------------------
# Routes - ignore list
# ---------------------------------------------------------------------------

@app.route("/ignore_add", methods=["POST"])
def ignore_add():
    path = request.form.get("path", "").strip()
    if not path:
        return render(tab="ignore", flash="Enter a path.", flash_type="err")
    cfg = core.load_config()
    ignored = cfg.get("ignored_paths", [])
    if path not in ignored:
        ignored.append(path)
        cfg["ignored_paths"] = ignored
        core.save_config(cfg)
        add_log(f"[Ignore] Added: {path}")
    return render(tab="ignore", flash=f"Added: {path}", flash_type="ok")

@app.route("/ignore_remove", methods=["POST"])
def ignore_remove():
    path = request.form.get("path", "").strip()
    cfg  = core.load_config()
    ignored = cfg.get("ignored_paths", [])
    if path in ignored:
        ignored.remove(path)
        cfg["ignored_paths"] = ignored
        core.save_config(cfg)
        add_log(f"[Ignore] Removed: {path}")
    return render(tab="ignore", flash=f"Removed: {path}", flash_type="ok")

@app.route("/ignore_clear", methods=["POST"])
def ignore_clear():
    cfg = core.load_config()
    cfg["ignored_paths"] = []
    core.save_config(cfg)
    add_log("[Ignore] Cleared all.")
    return render(tab="ignore", flash="All ignored paths cleared.", flash_type="ok")


# ---------------------------------------------------------------------------
# Routes - compose volume editor
# ---------------------------------------------------------------------------

@app.route("/compose_save_volumes", methods=["POST"])
def compose_save_volumes():
    """
    Write the new volumes list back into docker-compose.yml, preserving
    everything else in the file, then call `docker compose up -d` to
    apply the changes.
    """
    import subprocess, re

    compose_path = _find_compose_file()
    if not compose_path:
        return render(tab="settings", flash="docker-compose.yml not found.", flash_type="err")

    hosts      = request.form.getlist("host[]")
    containers = request.form.getlist("container[]")

    # Build clean volume pair list, skip empty rows
    new_pairs = []
    for h, c in zip(hosts, containers):
        h = h.strip()
        c = c.strip()
        if h and c:
            new_pairs.append((h, c))

    # Read the current compose file
    try:
        with open(compose_path) as f:
            content = f.read()
    except Exception as e:
        return render(tab="settings", flash=f"Could not read compose file: {e}", flash_type="err")

    # Rebuild the volumes block under the service, preserving the
    # required named-volume entry (ftp-sync-data:/data) and replacing
    # all bind-mount lines with the new set.
    #
    # Strategy: find the service volumes: block, extract indentation,
    # keep named-volume lines, replace bind-mount lines.
    lines = content.splitlines(keepends=True)
    new_lines = []
    in_service_vols = False
    service_vol_indent = None
    named_kept = set()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        # Detect entering the service-level volumes: block
        if stripped == "volumes:" and indent > 0 and not in_service_vols:
            in_service_vols = True
            service_vol_indent = indent
            new_lines.append(line)
            i += 1

            # Collect existing named volumes and write new bind mounts
            vol_indent = None
            named_lines = []

            # Scan ahead to collect/replace volume entries
            while i < len(lines):
                vline = lines[i]
                vs = vline.strip()
                vi = len(vline) - len(vline.lstrip())

                # End of this volumes block
                if vs and not vs.startswith("-") and not vs.startswith("#") and vi <= service_vol_indent:
                    break

                if vs.startswith("- "):
                    if vol_indent is None:
                        vol_indent = vi
                    raw = vs[2:].strip().strip('"').strip("'")
                    # Keep named volumes (no / in host part)
                    if ":" in raw:
                        parts = raw.split(":")
                        if "/" not in parts[0] and not parts[0].startswith("."):
                            named_lines.append(vline)
                    # Skip old bind mounts — we'll rewrite them
                elif vs.startswith("#") or not vs:
                    pass  # drop old comments in this block
                i += 1

            pad = " " * (vol_indent if vol_indent else service_vol_indent + 2)

            # Write named volumes first
            for nl in named_lines:
                new_lines.append(nl)

            # Write new bind mounts
            for h, c in new_pairs:
                # Quote paths with spaces
                h_str = f'"{h}"' if " " in h else h
                c_str = f'"{c}"' if " " in c else c
                new_lines.append(f"{pad}- {h_str}:{c_str}\n")

            continue  # already advanced i

        new_lines.append(line)
        i += 1

    new_content = "".join(new_lines)

    try:
        with open(compose_path, "w") as f:
            f.write(new_content)
    except Exception as e:
        return render(tab="settings", flash=f"Could not write compose file: {e}", flash_type="err")

    add_log(f"[Mounts] docker-compose.yml updated with {len(new_pairs)} bind mount(s).")

    # Try to apply changes by running docker compose up -d
    compose_dir = os.path.dirname(os.path.abspath(compose_path))
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=compose_dir,
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            msg = f"Saved and applied. Container restarting — refresh in ~10 seconds."
            add_log("[Mounts] docker compose up -d succeeded.")
        else:
            msg = (f"Saved to file but docker compose failed: {result.stderr.strip()[:200]} "
                   f"— run 'docker compose up -d' manually.")
            add_log(f"[Mounts] docker compose up -d failed: {result.stderr.strip()[:300]}")
    except FileNotFoundError:
        msg = "Saved to file. docker CLI not found in container — run 'docker compose up -d' on your server to apply."
        add_log("[Mounts] docker CLI not available inside container.")
    except subprocess.TimeoutExpired:
        msg = "Saved to file. docker compose timed out — the container may still be restarting."
    except Exception as e:
        msg = f"Saved to file. Could not restart automatically: {e}"

    return render(tab="settings", flash=msg, flash_type="ok")


# ---------------------------------------------------------------------------
# Routes - FTP errors tab
# ---------------------------------------------------------------------------

@app.route("/clear_errors", methods=["POST"])
def clear_errors():
    global ftp_errors
    with ftp_errors_lock:
        ftp_errors.clear()
    if worker:
        with worker._error_counts_lock:
            worker._error_counts.clear()
    add_log("[Errors] Error list cleared.")
    return render(tab="errors", flash="Error list cleared.", flash_type="ok")

@app.route("/error_ignore_all", methods=["POST"])
def error_ignore_all():
    with ftp_errors_lock:
        paths = list(ftp_errors.keys())
    if not paths:
        return render(tab="errors", flash="Error list is empty.", flash_type="err")
    cfg     = core.load_config()
    ignored = cfg.get("ignored_paths", [])
    added   = [p for p in paths if p not in ignored]
    if added:
        ignored.extend(added)
        cfg["ignored_paths"] = ignored
        core.save_config(cfg)
        add_log(f"[Ignore] Added {len(added)} path(s) from error list.")
    msg = f"Added {len(added)} path(s) to Ignore List." if added else "All paths already ignored."
    return render(tab="errors", flash=msg, flash_type="ok")


@app.route("/error_add_ignore", methods=["POST"])
def error_add_ignore():
    path = request.form.get("path", "").strip()
    if path:
        cfg = core.load_config()
        ignored = cfg.get("ignored_paths", [])
        if path not in ignored:
            ignored.append(path)
            cfg["ignored_paths"] = ignored
            core.save_config(cfg)
            add_log(f"[Ignore] Added from error list: {path}")
    return render(tab="errors", flash=f"Added to ignore list: {path}", flash_type="ok")

@app.route("/error_remove", methods=["POST"])
def error_remove():
    path = request.form.get("path", "").strip()
    with ftp_errors_lock:
        ftp_errors.pop(path, None)
    if worker:
        with worker._error_counts_lock:
            worker._error_counts.pop(path, None)
    return render(tab="errors", flash=f"Removed: {path}", flash_type="ok")


# ---------------------------------------------------------------------------
# Routes - updates
# ---------------------------------------------------------------------------

@app.route("/install_update", methods=["POST"])
def install_update():
    f = request.files.get("pyfile")
    if not f or not f.filename:
        return render(tab="settings", flash="No file selected.", flash_type="err")
    import tempfile, os
    # Save to a temp location with the original filename so install_update() can check it
    tmp_dir  = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, f.filename)
    try:
        f.save(tmp_path)
        ok, msg = core.install_update(tmp_path)
    finally:
        try: os.remove(tmp_path)
        except: pass
        try: os.rmdir(tmp_dir)
        except: pass
    if ok:
        add_log(f"[Update] Installed: {msg}")
        return render(tab="settings",
                      flash=f"Installed {msg}. Restart the app to apply.",
                      flash_type="ok")
    else:
        return render(tab="settings", flash=f"Install failed: {msg}", flash_type="err")


@app.route("/remove_override", methods=["POST"])
def remove_override():
    filename = request.form.get("filename", "").strip()
    ok, msg  = core.remove_override(filename)
    if ok:
        add_log(f"[Update] Removed override: {filename}")
        return render(tab="settings", flash=f"{msg}", flash_type="ok")
    else:
        return render(tab="settings", flash=f"Remove failed: {msg}", flash_type="err")


# ---------------------------------------------------------------------------
# Browser API routes
# ---------------------------------------------------------------------------
#
# State: one FTP connection per web session (keyed by Flask session id).
# We use a simple global dict since FTP sync typically runs single-user.
# Each download runs in its own thread; progress is polled via /api/br/queue_status.

import uuid as _uuid
from flask import session

app.secret_key = app.secret_key or _uuid.uuid4().hex  # needed for session

_br_connections = {}   # session_id -> ftplib.FTP instance
_br_queue       = {}   # qid (int) -> {name, dir, pct, status, done, stop_ev, part_path}
_br_queue_lock  = threading.Lock()


def _br_sid():
    if "br_sid" not in session:
        session["br_sid"] = _uuid.uuid4().hex
    return session["br_sid"]


def _br_get_ftp():
    sid = _br_sid()
    return _br_connections.get(sid)


def _br_set_ftp(ftp):
    _br_connections[_br_sid()] = ftp


def _br_make_ftp(cfg=None):
    cfg = cfg or core.load_config()
    return core.ftp_connect(
        cfg["host"], cfg.get("port", 21),
        cfg.get("user", ""), core.get_password(cfg))


@app.route("/api/br/connect", methods=["POST"])
def api_br_connect():
    try:
        cfg = core.load_config()
        if not cfg.get("host"):
            return jsonify(ok=False, error="No server configured. Add a server profile first.")
        ftp = _br_make_ftp(cfg)
        _br_set_ftp(ftp)
        return jsonify(ok=True, host=cfg["host"])
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/api/br/disconnect", methods=["POST"])
def api_br_disconnect():
    ftp = _br_get_ftp()
    if ftp:
        try: ftp.quit()
        except: pass
        _br_connections.pop(_br_sid(), None)
    return jsonify(ok=True)


@app.route("/api/br/list")
def api_br_list():
    path     = request.args.get("path", "/")
    dirs_only = request.args.get("dirsonly") == "1"
    ftp = _br_get_ftp()
    if not ftp:
        return jsonify(error="Not connected")
    try:
        # Liveness check
        try: ftp.voidcmd("NOOP")
        except Exception:
            cfg = core.load_config()
            ftp = _br_make_ftp(cfg)
            _br_set_ftp(ftp)
        entries = core.ftp_list_dir_full(ftp, path)
        if dirs_only:
            entries = [e for e in entries if e["is_dir"]]
        return jsonify(entries=entries)
    except Exception as e:
        return jsonify(error=str(e))


@app.route("/api/br/download", methods=["POST"])
def api_br_download():
    data        = request.get_json()
    remote_path = data.get("remote_path", "")
    local_dir   = data.get("local_dir", "")
    size        = int(data.get("size", 0))
    qid         = int(data.get("qid", 0))
    name        = remote_path.split("/")[-1]
    local_path  = os.path.join(local_dir, name)
    part_path   = local_path + ".part"
    stop_ev     = threading.Event()

    with _br_queue_lock:
        _br_queue[qid] = {"name": name, "dir": "down", "pct": 0,
                          "status": "Queued", "done": None,
                          "stop_ev": stop_ev, "part_path": part_path}

    def _run():
        try:
            cfg = core.load_config()
            ftp = _br_make_ftp(cfg)
            done_b = [0]
            with _br_queue_lock:
                _br_queue[qid]["status"] = "Downloading…"
            os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
            if os.path.exists(part_path): os.remove(part_path)
            if os.path.exists(local_path): os.remove(local_path)
            with open(part_path, "wb") as f:
                def _cb(chunk):
                    if stop_ev.is_set(): raise Exception("Cancelled")
                    f.write(chunk)
                    done_b[0] += len(chunk)
                    pct = int(done_b[0] / size * 100) if size else 0
                    with _br_queue_lock:
                        _br_queue[qid]["pct"] = pct
                        _br_queue[qid]["status"] = core._fmt_size_core(done_b[0]) if hasattr(core, "_fmt_size_core") else f"{done_b[0]//1024}KB"
                ftp.retrbinary(f"RETR {remote_path}", _cb, blocksize=65536)
            # Success — rename .part to final
            os.rename(part_path, local_path)
            try: ftp.quit()
            except: pass
            with _br_queue_lock:
                _br_queue[qid]["pct"] = 100
                _br_queue[qid]["status"] = "Done ✓"
                _br_queue[qid]["done"] = True
        except Exception as e:
            try:
                if os.path.exists(part_path): os.remove(part_path)
            except: pass
            try: ftp.quit()
            except: pass
            err = "Cancelled" if stop_ev.is_set() else str(e)[:24]
            with _br_queue_lock:
                _br_queue[qid]["status"] = err
                _br_queue[qid]["done"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/br/download_folder", methods=["POST"])
def api_br_download_folder():
    """Recursively list a remote folder and queue all files inside it."""
    data          = request.get_json()
    remote_folder = data.get("remote_folder", "")
    local_dir     = data.get("local_dir", "")
    qid           = int(data.get("qid", 0))

    with _br_queue_lock:
        _br_queue[qid] = {"name": remote_folder.split("/")[-1]+"/",
                          "dir": "down", "pct": 0,
                          "status": "Scanning…", "done": None,
                          "stop_ev": threading.Event(), "part_path": None}
    try:
        cfg      = core.load_config()
        walk_ftp = _br_make_ftp(cfg)
        file_map = core.ftp_list_recursive(walk_ftp, remote_folder)
        try: walk_ftp.quit()
        except: pass
    except Exception as e:
        with _br_queue_lock:
            _br_queue[qid]["status"] = f"Scan failed: {e}"
            _br_queue[qid]["done"] = False
        return jsonify(ok=False, error=str(e))

    count = len(file_map)
    # Queue each file as a separate download in its own thread
    for rel_path, (size, _) in file_map.items():
        remote_file = remote_folder.rstrip("/") + "/" + rel_path
        local_file  = os.path.join(local_dir,
                                   remote_folder.split("/")[-1],
                                   rel_path.replace("/", os.sep))
        new_qid = max(_br_queue.keys(), default=0) + 1
        # Reuse the download endpoint logic inline
        part   = local_file + ".part"
        st_ev  = threading.Event()
        with _br_queue_lock:
            _br_queue[new_qid] = {"name": rel_path.split("/")[-1],
                                  "dir": "down", "pct": 0,
                                  "status": "Queued", "done": None,
                                  "stop_ev": st_ev, "part_path": part}
        def _dl(rp=remote_file, lp=local_file, pp=part, sz=size, q=new_qid, ev=st_ev):
            try:
                cfg2 = core.load_config()
                ftp2 = _br_make_ftp(cfg2)
                os.makedirs(os.path.dirname(os.path.abspath(lp)), exist_ok=True)
                if os.path.exists(pp): os.remove(pp)
                done_b2 = [0]
                with open(pp, "wb") as f2:
                    def _cb2(chunk):
                        if ev.is_set(): raise Exception("Cancelled")
                        f2.write(chunk)
                        done_b2[0] += len(chunk)
                        pct2 = int(done_b2[0]/sz*100) if sz else 0
                        with _br_queue_lock:
                            _br_queue[q]["pct"] = pct2
                    ftp2.retrbinary(f"RETR {rp}", _cb2, blocksize=65536)
                os.rename(pp, lp)
                try: ftp2.quit()
                except: pass
                with _br_queue_lock:
                    _br_queue[q].update(pct=100, status="Done ✓", done=True)
            except Exception as ex:
                try:
                    if os.path.exists(pp): os.remove(pp)
                except: pass
                err2 = "Cancelled" if ev.is_set() else str(ex)[:24]
                with _br_queue_lock:
                    _br_queue[q].update(status=err2, done=False)
        threading.Thread(target=_dl, daemon=True).start()

    with _br_queue_lock:
        _br_queue[qid].update(pct=100, status=f"Queued {count} files", done=True)
    return jsonify(ok=True, count=count)


@app.route("/api/br/upload", methods=["POST"])
def api_br_upload():
    f           = request.files.get("file")
    remote_path = request.form.get("remote_path", "")
    qid         = int(request.form.get("qid", 0))
    if not f:
        return jsonify(ok=False, error="No file")
    name    = f.filename
    stop_ev = threading.Event()
    with _br_queue_lock:
        _br_queue[qid] = {"name": name, "dir": "up", "pct": 0,
                          "status": "Uploading…", "done": None,
                          "stop_ev": stop_ev, "part_path": None}
    # Read file into memory (suitable for typical web uploads)
    import io as _io
    data = f.read()
    def _run():
        upload_ok = [False]
        try:
            cfg = core.load_config()
            ftp = _br_make_ftp(cfg)
            total   = len(data)
            sent    = [0]
            buf     = _io.BytesIO(data)
            def _cb(chunk_size=65536):
                # storbinary calls with block; we track progress manually
                pass
            # Use storbinary with a callback-aware wrapper
            class _ProgressIO(_io.BytesIO):
                def read(self, size=-1):
                    chunk = super().read(size)
                    if stop_ev.is_set():
                        raise Exception("Cancelled")
                    sent[0] += len(chunk)
                    pct = int(sent[0] / total * 100) if total else 0
                    with _br_queue_lock:
                        _br_queue[qid]["pct"] = pct
                    return chunk
            remote_dir = remote_path.rsplit("/", 1)[0] or "/"
            core._ftp_mkdirs(ftp, remote_dir)
            ftp.storbinary(f"STOR {remote_path}", _ProgressIO(data), blocksize=65536)
            upload_ok[0] = True
            try: ftp.quit()
            except: pass
            with _br_queue_lock:
                _br_queue[qid].update(pct=100, status="Done ✓", done=True)
        except Exception as e:
            if not upload_ok[0]:
                # Delete incomplete remote file
                try:
                    cfg2 = core.load_config()
                    ftp2 = _br_make_ftp(cfg2)
                    core.ftp_delete_remote(ftp2, remote_path, is_dir=False)
                    ftp2.quit()
                except: pass
            err = "Cancelled" if stop_ev.is_set() else str(e)[:24]
            with _br_queue_lock:
                _br_queue[qid].update(status=err, done=False)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/br/queue_status")
def api_br_queue_status():
    with _br_queue_lock:
        items = [{"qid": k, "status": v["status"],
                  "pct": v["pct"], "done": v["done"]}
                 for k, v in _br_queue.items()]
    return jsonify(items=items)


@app.route("/api/br/cancel", methods=["POST"])
def api_br_cancel():
    qid = int(request.get_json().get("qid", 0))
    with _br_queue_lock:
        item = _br_queue.get(qid)
    if item:
        item["stop_ev"].set()
    return jsonify(ok=True)


@app.route("/api/br/rename", methods=["POST"])
def api_br_rename():
    data = request.get_json()
    ftp  = _br_get_ftp()
    if not ftp:
        return jsonify(ok=False, error="Not connected")
    try:
        core.ftp_rename_remote(ftp, data["old_path"], data["new_path"])
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/api/br/delete", methods=["POST"])
def api_br_delete():
    data   = request.get_json()
    items  = data.get("items", [])
    ftp    = _br_get_ftp()
    if not ftp:
        return jsonify(ok=False, error="Not connected")
    errors = []
    for item in items:
        try:
            core.ftp_delete_remote(ftp, item["path"], item["is_dir"])
        except Exception as e:
            errors.append(f"{item['name']}: {e}")
    return jsonify(ok=True, errors=errors)


@app.route("/api/br/mkdir", methods=["POST"])
def api_br_mkdir():
    data = request.get_json()
    ftp  = _br_get_ftp()
    if not ftp:
        return jsonify(ok=False, error="Not connected")
    try:
        ftp.mkd(data["path"])
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ── Server management API ──────────────────────────────────────────────────────

@app.route("/api/br/server_switch", methods=["POST"])
def api_br_server_switch():
    name = request.get_json().get("name", "")
    cfg  = core.load_config()
    cfg  = core.activate_server(cfg, name)
    core.save_config(cfg)
    # Drop existing browser connection so next Connect uses new creds
    old_ftp = _br_connections.pop(_br_sid(), None)
    if old_ftp:
        try: old_ftp.quit()
        except: pass
    return jsonify(ok=True)


@app.route("/api/br/get_server")
def api_br_get_server():
    name    = request.args.get("name", "")
    cfg     = core.load_config()
    profile = core.get_server(cfg, name)
    # Never send password_enc to the browser
    safe = {k: v for k, v in profile.items() if k != "password_enc"}
    return jsonify(profile=safe)


@app.route("/api/br/save_server", methods=["POST"])
def api_br_save_server():
    data     = request.get_json()
    old_name = data.get("old_name")
    password = data.get("password", "")
    cfg      = core.load_config()
    # If editing, remove old entry first
    if old_name and old_name != data["name"]:
        cfg = core.delete_server(cfg, old_name)
    existing_enc = ""
    if old_name:
        old_profile  = core.get_server(cfg, old_name)
        existing_enc = old_profile.get("password_enc", "")
    enc = core.encrypt_password(password) if password else existing_enc
    profile = {
        "name":         data["name"],
        "host":         data["host"],
        "port":         int(data.get("port", 21)),
        "user":         data.get("user", ""),
        "password_enc": enc,
        "folder_pairs": [],
    }
    cfg = core.save_server(cfg, profile)
    core.save_config(cfg)
    return jsonify(ok=True)


@app.route("/api/br/remove_server", methods=["POST"])
def api_br_remove_server():
    name = request.get_json().get("name", "")
    if name == "Default":
        return jsonify(ok=False, error="Cannot remove Default server")
    cfg = core.load_config()
    cfg = core.delete_server(cfg, name)
    core.save_config(cfg)
    return jsonify(ok=True)


@app.route("/api/br/test_server", methods=["POST"])
def api_br_test_server():
    data = request.get_json()
    try:
        ftp = core.ftp_connect(
            data["host"], int(data.get("port", 21)),
            data.get("user", ""), data.get("password", ""))
        ftp.quit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/api/br/save_session", methods=["POST"])
def api_br_save_session():
    import json as _json
    data      = request.get_json()
    name      = data.get("name", "session")
    safe_name = "".join(c for c in name if c.isalnum() or c in (" -_")).strip() or "session"
    cfg       = core.load_config()
    session_data = {
        "session_name": name,
        "host":         cfg.get("host", ""),
        "port":         cfg.get("port", 21),
        "user":         cfg.get("user", ""),
        "start_path":   data.get("cwd", "/"),
        "local_dir":    data.get("local_dir", ""),
    }
    sessions_dir = os.path.join(os.path.dirname(core.CONFIG_FILE), "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    path = os.path.join(sessions_dir, f"{safe_name}.session.json")
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(session_data, f, indent=2)
    return jsonify(ok=True, path=path)


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port   = 8080
    lan_ip = get_lan_ip()

    # Ensure settings.json exists on first run
    core.load_config()

    print("=" * 52)
    print("  FTP Remote Sync - Web UI")
    print(f"  Local:    http://localhost:{port}")
    print(f"  Network:  http://{lan_ip}:{port}")
    print(f"  Settings: {core.CONFIG_FILE}")
    print("  Ctrl+C to stop")
    print("=" * 52)

    if os.environ.get("FTP_NO_BROWSER", "").lower() not in ("1", "true", "yes"):
        import threading
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
