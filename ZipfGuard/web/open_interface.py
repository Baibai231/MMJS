"""Browser controls and background job transport for the open experiment."""
import hashlib
import json
import threading
import uuid
from web.presentation import STYLE, report_html
from experiments.open_config import load_open_config, validate_open_config
from experiments.open_pipeline import run_open_pipeline
from experiments.config import load_config
from experiments.pipeline import run_pipeline
from core.data import validate_count_payload

JOBS={}
JOB_LOCK=threading.Lock()


def execute_open_request(request,progress=None):
    cfg=validate_open_config(request['config'])
    source=request.get('source','corpus')
    if source=='upload':
        # Aggregate-only input has no strings. Keep its separate analysis path.
        payload=validate_count_payload(request.get('payload'))
        legacy=load_config();legacy['seed']=cfg['seed'];legacy['bootstrap_repetitions']=cfg['bootstrap_repetitions']
        legacy['attackers']={'frequency':'required'}
        return run_pipeline(payload,config=legacy)
    if source!='corpus':raise ValueError('开放实验请选择真实语料；聚合上传只能做分布分析')
    return run_open_pipeline(cfg,progress=progress)


def package_result(result):
    content=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    return {'result':result,'html':report_html(result,document=False),'json':content,
            'sha256':hashlib.sha256(content.encode('utf-8')).hexdigest()}


def start_job(request):
    validate_open_config(request['config'])
    with JOB_LOCK:
        if any(j['status']=='running' for j in JOBS.values()):
            raise ValueError('已有实验在运行，请等待完成')
        # Keep only the current job; results can be downloaded by its client.
        JOBS.clear();key=uuid.uuid4().hex
        JOBS[key]={'status':'running','message':'准备运行','output':None}
    def update(message):
        with JOB_LOCK:JOBS[key]['message']=message
    def worker():
        try:
            result=execute_open_request(request,update)
            output=package_result(result)
            with JOB_LOCK:JOBS[key].update(status='complete',message='实验完成',output=output)
        except Exception as exc:
            with JOB_LOCK:JOBS[key].update(status='failed',message=str(exc))
    threading.Thread(target=worker,daemon=True).start()
    return {'job_id':key}


def job_snapshot(key):
    with JOB_LOCK:
        if key not in JOBS:raise ValueError('实验任务不存在')
        return dict(JOBS[key])


OPEN_STYLE='''
:root{color-scheme:light}body{background:#f2f5f8;color:#192d3e}main{max-width:1280px}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:6px 0 24px}.brand{font-weight:750;letter-spacing:-.5px;font-size:22px}.tag{color:#16725c;background:#dff2eb;border-radius:20px;padding:7px 12px;font-size:12px}
.hero{padding:12px 0 10px;max-width:800px}.eyebrow{color:#487b8a;font-size:12px;letter-spacing:2px;font-weight:650}.hero h1{font-size:38px;line-height:1.25;margin:12px 0}.hero p{color:#586d7d;line-height:1.8}
.workflow{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}.workflow span{font-size:12px;background:white;border:1px solid #dae4e9;padding:8px 13px;border-radius:6px}
.controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.controls label{display:flex;margin:0;min-width:0;font-size:13px;color:#536b7b}.controls input,.controls select{width:100%;box-sizing:border-box;color:#1d3444;background:#fff}.wide{grid-column:1/-1}
section{border-radius:12px;box-shadow:0 3px 16px #172f4004}h2{letter-spacing:-.3px}summary{font-weight:600}.hint{font-size:12px;color:#677f8e;line-height:1.8}.actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:24px}button{background:#126e62;border:0;padding:11px 18px;border-radius:8px}button.secondary{background:#e7eeef;color:#294858}button:disabled{opacity:.45;cursor:default}textarea{width:100%;box-sizing:border-box;font:12px ui-monospace,monospace}
#status{font-size:13px;color:#365f67}#status[data-error=true]{color:#b83b35}.progress-line{height:3px;background:#dbece7;overflow:hidden;border-radius:3px;margin-top:16px}.progress-line.active::before{content:'';display:block;background:#208676;height:100%;width:30%;animation:move 2s linear infinite}@keyframes move{from{transform:translateX(-100%)}to{transform:translateX(400%)}}
.empty{text-align:center;padding:36px 20px;color:#788b98;border:1px dashed #cbd8df;border-radius:12px}.empty strong{display:block;color:#445e6d;margin-bottom:8px}.open-report h1{font-size:26px}.open-report main{padding:0}
@media(max-width:800px){.controls{grid-template-columns:1fr 1fr}.hero h1{font-size:29px}main{padding:18px}.metrics{gap:18px}.metric strong{font-size:20px}}
@media(max-width:520px){.controls{grid-template-columns:1fr}.tag{font-size:10px}.hero h1{font-size:26px}section{padding:18px}}
'''

OPEN_INDEX='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ZipfGuard · 策略实验室</title><style>'+STYLE+OPEN_STYLE+'</style>'+r'''
<body><main><nav class="topbar"><div class="brand">ZipfGuard <span class="muted">/ 策略实验室</span></div><a href="/dynamic">分批注册动态策略 →</a><span class="tag">本地离线 · 开放候选</span></nav>
<div class="hero"><div class="eyebrow">PASSWORD POLICY RESEARCH</div><h1>为不同安全需求，<br>找到合适的口令规则。</h1><p>从真实口令分布出发，考虑用户如何重新选择、攻击者如何适应。在相同预算下，用 Min_auto 比较策略的安全收益与用户修改成本。</p></div>
<div class="workflow"><span>01 真实频次</span><span>02 规则与用户响应</span><span>03 自适应 Min_auto</span><span>04 风险与成本推荐</span></div>
<section><h2>设置这次实验</h2><div class="controls">
<label>运行预设<select id="preset"><option value="open_quick">快速验证 · 2,000 次出现</option><option value="open_full">研究预设 · 20,000 次出现 + PCFG</option></select></label>
<label>数据用途<select id="source"><option value="corpus">真实口令与频次 · 完整策略实验</option><option value="upload">聚合频次 JSON · 仅分布分析</option></select></label>
<label>随机种子<input type="number" id="seed" min="0"></label>
<label class="wide">本地语料路径<input id="path" spellcheck="false"></label>
<label>输入格式<select id="format"><option value="password_with_count">频次 + 一个分隔符 + 口令</option><option value="raw_occurrences">每行一次口令出现</option></select></label>
<label>字符编码<input id="encoding"></label><label>抽样出现次数<input type="number" id="size" min="40" max="200000"></label>
<label>攻击预算 K（逗号分隔）<input id="budgets"></label><label>推荐使用的预算<input type="number" id="riskbudget" min="1"></label>
<label>主要用户响应<select id="response"><option value="R0">R0 · 独立重新选择</option><option value="R1">R1 · 固定修补序列</option><option value="R2">R2 · 修改与重新选择混合</option></select></label>
<label>响应比较<select id="scenarios"><option value="primary">仅主要情景</option><option value="all">R0 / R1 / R2 敏感性比较</option></select></label>
<label>Bootstrap 次数<input type="number" id="bootstrap" min="20" max="2000"></label>
<label>聚合 JSON 文件<input id="upload" type="file" accept=".json"></label>
</div><p class="hint">抽样覆盖完整文件范围。预算按每个模型通过已知策略过滤、去重后实际校验的候选计数；不取模型候选集合交集。首次扫描大文件需要一些时间。</p>
<details id="advanced"><summary>攻击器、资源与安全需求</summary><div class="controls" id="attackers"></div><div class="controls">
<label>每模型原始输出上限<input type="number" id="rawlimit"></label><label>生成超时（秒）<input type="number" id="timeout"></label><label>PCFG 原始输出上限<input type="number" id="pcfglimit"></label></div>
<label>需求约束 JSON<textarea id="requirements" aria-label="需求约束 JSON"></textarea></label><p class="hint">cost_cap：成本上限内最小化风险；risk_target：风险要求内最小化成本。成本为初始拒绝比例，还同时约束完成率和额外尝试。无可行策略时不会自动放松要求。</p>
<details><summary>完整可复现配置</summary><textarea id="config" aria-label="完整配置"></textarea><button id="apply" class="secondary">应用配置</button> <button id="export" class="secondary">下载配置</button></details></details>
<div class="actions"><button id="run">运行实验</button><button id="download" class="secondary" disabled>下载结果与校验</button><button id="html-download" class="secondary" disabled>下载结果页面</button><span id="status" role="status">正在读取配置…</span></div><div id="progress" class="progress-line"></div>
</section><div id="empty" class="empty"><strong>实验结果将在这里展示</strong>推荐证据 · Min_auto 预算曲线 · 用户响应 · 风险—成本前沿</div></main><div id="result"></div>
<script>
const $=id=>document.getElementById(id);let cfg=null,last=null;const models=[['frequency','训练频次'],['dictionary-rules','字典变形'],['character-ngram','字符 n-gram'],['pcfg','PCFG']];
function put(c){cfg=c;$('seed').value=c.seed;$('path').value=c.data.path;$('format').value=c.data.format;$('encoding').value=c.data.encoding;$('size').value=c.data.sample_size;$('budgets').value=c.budgets.join(',');$('riskbudget').value=c.search.risk_budget;$('response').value=c.response.primary;$('scenarios').value=c.response.scenarios.length>1?'all':'primary';$('bootstrap').value=c.bootstrap_repetitions;$('rawlimit').value=c.generation.raw_limit;$('timeout').value=c.generation.timeout_seconds;$('pcfglimit').value=c.pcfg.raw_limit;$('requirements').value=JSON.stringify(c.search.requirements,null,2);$('config').value=JSON.stringify(c,null,2);$('attackers').replaceChildren();for(const [id,label] of models){const l=document.createElement('label');l.textContent=label;const s=document.createElement('select');s.id='att-'+id;for(const [v,t] of [['off','不参与'],['required','必选'],['optional','可选']])s.add(new Option(t,v));s.value=c.attackers[id]||'off';l.append(s);$('attackers').append(l)}}
function get(){if(!cfg)throw Error('配置尚未加载');const c=structuredClone(cfg);c.seed=+$('seed').value;c.data.path=$('path').value;c.data.format=$('format').value;c.data.encoding=$('encoding').value;c.data.sample_size=+$('size').value;c.budgets=$('budgets').value.split(',').map(Number);c.search.risk_budget=+$('riskbudget').value;c.response.primary=$('response').value;c.response.scenarios=$('scenarios').value==='all'?['R0','R1','R2']:[c.response.primary];c.bootstrap_repetitions=+$('bootstrap').value;c.generation.raw_limit=+$('rawlimit').value;c.generation.timeout_seconds=+$('timeout').value;c.pcfg.raw_limit=+$('pcfglimit').value;c.search.requirements=JSON.parse($('requirements').value);c.attackers={};for(const [id] of models){const mode=$('att-'+id).value;if(mode!=='off')c.attackers[id]=mode}return c}
function status(text,error=false){$('status').textContent=text;$('status').dataset.error=error}
function download(name,text,type='application/octet-stream'){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function preset(){try{const r=await fetch('/api/config/'+$('preset').value);const c=await r.json();if(!r.ok)throw Error(c.error);put(c);status('配置已就绪')}catch(e){status(e.message,true)}}
$('preset').onchange=preset;$('apply').onclick=()=>{try{put(JSON.parse($('config').value));status('配置已应用')}catch(e){status(e.message,true)}};$('export').onclick=()=>{try{download('open_experiment.json',JSON.stringify(get(),null,2))}catch(e){status(e.message,true)}};
$('run').onclick=async()=>{status('准备实验…');$('run').disabled=true;$('download').disabled=true;$('html-download').disabled=true;$('result').replaceChildren();$('empty').hidden=true;$('progress').classList.add('active');last=null;try{const c=get();$('config').value=JSON.stringify(c,null,2);let payload=null;if($('source').value==='upload'){const f=$('upload').files[0];if(!f)throw Error('请选择聚合 JSON 文件');payload=JSON.parse(await f.text())}status('开始实验…');const res=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:c,source:$('source').value,payload})});const start=await res.json();if(!res.ok)throw Error(start.error);while(true){await new Promise(r=>setTimeout(r,1200));const response=await fetch('/api/jobs/'+start.job_id);const state=await response.json();if(!response.ok)throw Error(state.error);status(state.message);if(state.status==='failed')throw Error(state.message);if(state.status==='complete'){last=state.output;$('result').innerHTML=last.html;$('download').disabled=false;$('html-download').disabled=false;break}}}catch(e){status('未完成：'+e.message,true);$('empty').hidden=false}finally{$('run').disabled=false;$('progress').classList.remove('active')}};
$('download').onclick=()=>{download('zipfguard_open.json',last.json);download('zipfguard_open.json.sha256',last.sha256+'  zipfguard_open.json\n')};
$('html-download').onclick=()=>download('zipfguard_open.html','<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+document.querySelector('style').textContent+'</style><body>'+last.html+'</body></html>','text/html');preset();
</script></body></html>
'''
