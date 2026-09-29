"""A separate browser entry for the sequential policy study."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path

from experiments.dynamic_config import load_dynamic_config, validate_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline
from web.dynamic_presentation import render_dynamic_html
from web.presentation import STYLE

JOBS = {}
LOCK = threading.Lock()


def latest_dynamic_result(report_dir=None):
    directory = (Path(report_dir) if report_dir is not None else
                 Path(__file__).resolve().parents[1] / 'reports' / 'dynamic')
    reports = sorted(directory.rglob('report.json'),
                     key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for report in reports:
        try:
            result = json.loads(report.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if result.get('schema_version') != 'zipfguard-dynamic-v1-result':
            continue
        if result.get('dataset', {}).get('registration_occurrences', 0) < 100_000:
            continue
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        return {'output': {'html': render_dynamic_html(result, document=False),
                           'json': content,
                           'sha256': hashlib.sha256(content.encode()).hexdigest()}}
    return {'output': None}


def start_dynamic_job(request):
    cfg = validate_dynamic_config(request['config'])
    with LOCK:
        if any(job['status'] == 'running' for job in JOBS.values()):
            raise ValueError('已有动态实验在运行')
        JOBS.clear()
        key = uuid.uuid4().hex
        JOBS[key] = {'status': 'running', 'message': '准备实验', 'output': None}

    def update(message):
        with LOCK:
            JOBS[key]['message'] = message

    def worker():
        try:
            result = run_dynamic_pipeline(cfg, progress=update)
            content = json.dumps(result, ensure_ascii=False, indent=2,
                                 allow_nan=False) + '\n'
            output = {'html': render_dynamic_html(result, document=False),
                      'json': content,
                      'sha256': hashlib.sha256(content.encode()).hexdigest()}
            with LOCK:
                JOBS[key].update(status='complete', message='动态实验完成',
                                 output=output)
        except Exception as exc:
            with LOCK:
                JOBS[key].update(status='failed', message=str(exc))
    threading.Thread(target=worker, daemon=True).start()
    return {'job_id': key}


def dynamic_job_snapshot(key):
    with LOCK:
        if key not in JOBS:
            raise ValueError('动态实验任务不存在')
        return dict(JOBS[key])


DYNAMIC_INDEX = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 '<title>ZipfGuard · 分批注册</title><style>' + STYLE + '''
body{background:#f2f6f4;color:#173028}main{max-width:1200px}section{border-radius:12px}
.controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.controls label{display:flex;min-width:0}.controls input,.controls select{width:100%;box-sizing:border-box}
.wide-path{display:flex;width:100%;box-sizing:border-box}.wide-path input{width:100%;box-sizing:border-box}
.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}button{background:#13715d}
#status{font-size:13px;color:#456a61}#status.error{color:#b73535}
''' + '</style><body><main><p><a href="/">返回静态实验</a></p>'
                 '<h1>分批注册的动态口令策略</h1>'
                 '<p>先看原始口令分布，再让每一批策略学习此前已注册用户的分布。每次增加规则都会保存修改前后的分布图。</p>'
                 '<section><h2>实验设置</h2><div class="controls">'
                 '<label>预设<select id="preset"><option value="dynamic_smoke">快速验证 · 300 人</option>'
                 '<option value="dynamic_full">完整研究 · 10 万人</option></select></label>'
                 '<label>随机种子<input id="seed" type="number" min="0"></label>'
                 '<label>注册用户<input id="users" type="number" min="1"></label>'
                 '<label>开发参考样本<input id="development" type="number" min="3"></label>'
                 '<label>每批人数<input id="cohort" type="number" min="1"></label>'
                 '<label>每模型攻击预算<input id="budgets"></label>'
                 '</div><label class="wide-path">本地带频次语料路径<input id="path"></label>'
                 '<details><summary>完整配置</summary><textarea id="config" style="width:100%;height:240px"></textarea>'
                 '<button id="apply">应用配置</button></details>'
                 '<p class="actions"><button id="run">运行分批实验</button>'
                 '<button id="recent">查看最近 10 万人结果</button>'
                 '<button id="download" disabled>下载公开报告</button><span id="status">正在读取配置…</span></p>'
                 '<p class="muted">“300 人”是从整份语料中抽取的人数，首次运行仍需扫描全文件两遍；之后若文件未变，可复用统计缓存并扫描一遍。页面会显示扫描、注册和攻击阶段。逐用户结果只写入本机私有文件，网页不传送口令。</p>'
                 r'''</section></main><div id="result"></div><script>
const $=id=>document.getElementById(id);let cfg=null,last=null;
function put(c){cfg=c;$('seed').value=c.seed;$('users').value=c.data.users;$('development').value=c.data.development;$('cohort').value=c.data.cohort_size;$('path').value=c.data.path;$('budgets').value=c.budgets.join(',');$('config').value=JSON.stringify(c,null,2)}
function get(){let c=structuredClone(cfg);c.seed=+$('seed').value;c.data.users=+$('users').value;c.data.development=+$('development').value;c.data.cohort_size=+$('cohort').value;c.data.path=$('path').value;c.budgets=$('budgets').value.split(',').map(Number);return c}
async function preset(){try{let r=await fetch('/api/dynamic/config/'+$('preset').value);let c=await r.json();if(!r.ok)throw Error(c.error);put(c);$('status').textContent='配置就绪'}catch(e){$('status').textContent=e.message;$('status').className='error'}}
async function recent(){if($('run').disabled)return;$('recent').disabled=true;try{let r=await fetch('/api/dynamic/latest');let state=await r.json();if(!r.ok)throw Error(state.error);if(!state.output){$('status').textContent='暂无已完成结果，可运行一次实验';return}last=state.output;const saved=JSON.parse(last.json);put(saved.config);$('preset').value=saved.config.data.users>=100000?'dynamic_full':'dynamic_smoke';$('result').innerHTML=last.html;$('download').disabled=false;$('status').className='';$('status').textContent='已加载最近完成结果：'+saved.dataset.registration_occurrences.toLocaleString()+' 人 · 种子 '+saved.config.seed+'（无需重新计算）'}catch(e){$('status').textContent='读取结果失败：'+e.message;$('status').className='error'}finally{$('recent').disabled=false}}
$('recent').onclick=recent;
$('preset').onchange=preset;$('apply').onclick=()=>{try{put(JSON.parse($('config').value));$('status').textContent='配置已应用'}catch(e){$('status').textContent=e.message}};
$('run').onclick=async()=>{last=null;$('result').replaceChildren();$('run').disabled=true;$('recent').disabled=true;$('download').disabled=true;$('status').className='';try{let c=get();$('config').value=JSON.stringify(c,null,2);let r=await fetch('/api/dynamic/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:c})});let j=await r.json();if(!r.ok)throw Error(j.error);while(true){await new Promise(done=>setTimeout(done,1300));let q=await fetch('/api/dynamic/jobs/'+j.job_id);let state=await q.json();if(!q.ok)throw Error(state.error);$('status').textContent=state.message;if(state.status==='failed')throw Error(state.message);if(state.status==='complete'){last=state.output;$('result').innerHTML=last.html;$('download').disabled=false;break}}}catch(e){$('status').textContent='未完成：'+e.message;$('status').className='error'}finally{$('run').disabled=false;$('recent').disabled=false}};
$('download').onclick=()=>{let blob=new Blob([last.json],{type:'application/json'}),a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download='zipfguard_dynamic.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};preset().then(recent);
</script></body></html>''')
