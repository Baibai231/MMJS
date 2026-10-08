"""Independent third workbench and background job lifecycle."""
import gzip
import json
import threading
import uuid
from pathlib import Path
from experiments.intervention_config import ROOT, PROTOCOL, load_intervention_config, validate_intervention_config
from experiments.intervention_pipeline import run_intervention_pipeline
from web.intervention_presentation import render_intervention_html
from web.presentation import STYLE
from web.study_dashboard import DASHBOARD_STYLE

SUPPORTED_RESULTS = {PROTOCOL+'-result', 'selective-intervention-v6-population-attack-area-result',
                     'selective-intervention-v7-ideal-wasserstein-result',
                     'selective-intervention-v5-full-cdf-area-result',
                     'selective-intervention-v4-result'}
JOBS, LOCK = {}, threading.Lock()
REPORT_ROOT = ROOT/'reports'/'intervention'
PUBLISHED_ROOT = ROOT/'published'/'intervention'
ACTIVE_FILE = ROOT/'reports'/'active_intervention.json'


def intervention_activity():
    try:
        state = json.loads(ACTIVE_FILE.read_text(encoding='utf-8'))
        return {key: state.get(key) for key in
                ('job_id', 'status', 'message', 'users', 'protocol', 'run_id', 'started_at', 'updated_at')}
    except (OSError, ValueError):
        return None


def published(result):
    return {'html': render_intervention_html(result, document=False),
            'run_id': result['metadata']['run_id'], 'config': result['config'],
            'report_url': f'/api/intervention/report/{result["metadata"]["run_id"]}.json'}


def latest_intervention_result():
    activity = intervention_activity()
    # The durable worker publishes its manifest only after all assets and checks
    # are complete. Do not expose an intermediate or failed local report.
    paths = ([] if activity and activity['status'] in ('running', 'failed', 'stopped') else
             sorted(REPORT_ROOT.glob('*/report.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True))
    for p in paths:
        try:
            result = json.loads(p.read_text(encoding='utf-8'))
            if result.get('schema_version') in SUPPORTED_RESULTS:
                return {'output': published(result)}
        except (OSError, ValueError, KeyError):
            continue
    manifest = PUBLISHED_ROOT / 'latest.json'
    try:
        run_id = json.loads(manifest.read_text(encoding='utf-8'))['run_id']
        if not isinstance(run_id, str) or len(run_id) != 16 or any(
                c not in '0123456789abcdef' for c in run_id):
            raise ValueError('发布快照编号无效')
        with gzip.open(PUBLISHED_ROOT/run_id/'report.json.gz', 'rt', encoding='utf-8') as handle:
            result = json.load(handle)
        if result.get('schema_version') in SUPPORTED_RESULTS:
            return {'output': published(result)}
    except (OSError, ValueError, KeyError):
        pass
    return {'output': None}


def intervention_index_with_latest():
    """Render the newest finished report in the initial HTML response."""
    output = latest_intervention_result()['output']
    if output is None:
        return INTERVENTION_INDEX
    marker = '<div id="result" aria-live="polite"></div>'
    replacement = (f'<div id="result" aria-live="polite" data-run-id="{output["run_id"]}">'
                   f'{output["html"]}</div>')
    return INTERVENTION_INDEX.replace(marker, replacement, 1)


def start_intervention_job(request):
    cfg = validate_intervention_config(request['config'])
    activity = intervention_activity()
    if activity and activity['status'] == 'running':
        raise ValueError('完整实验正在运行，请查看页面进度')
    if activity and activity['status'] in ('failed', 'stopped'):
        ACTIVE_FILE.unlink(missing_ok=True)
    with LOCK:
        if any(j['status'] == 'running' for j in JOBS.values()):
            raise ValueError('已有局部干预实验在运行')
        JOBS.clear()
        key = uuid.uuid4().hex
        JOBS[key] = {'status': 'running', 'message': '准备局部干预实验', 'output': None}

    def update(message):
        with LOCK:
            JOBS[key]['message'] = message

    def worker():
        try:
            result = run_intervention_pipeline(cfg, progress=update)
            with LOCK:
                JOBS[key].update(status='complete', message='局部干预实验完成', output=published(result))
        except Exception as exc:
            with LOCK:
                JOBS[key].update(status='failed', message=str(exc), output=None)
    threading.Thread(target=worker, daemon=True).start()
    return {'job_id': key}


def intervention_job_snapshot(key):
    with LOCK:
        if key not in JOBS:
            raise ValueError('局部干预任务不存在')
        return dict(JOBS[key])


INTERVENTION_INDEX = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
 '<meta name="viewport" content="width=device-width,initial-scale=1">'
 '<title>ZipfGuard · 第三展示台 · 局部账户干预</title><style>'+STYLE+DASHBOARD_STYLE+'''
 .controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
 .controls label{display:flex;margin:0;min-width:0}.controls input,.controls select{min-width:0;width:100%;box-sizing:border-box}
 .actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.wide{display:flex;margin:14px 0}.wide input{box-sizing:border-box;width:100%}
 #status.error{color:#b73535}button{background:#13715d}.plot-distinct{min-width:510px}
 </style><body><main class="workbench-header"><span class="brand">ZIPFGUARD / SELECTIVE INTERVENTION</span>
 <h1>少改一部分账户，口令分布能改善多少？</h1>
 <p>第三展示台 · 五组实验对照：原始分布、Google 基础策略，以及共同 Google 起点后的随机分批、初始排序分批和逐轮动态调整。</p>
 <nav class="study-nav" aria-label="实验结果导航"><a href="#intervention-overview">结果概览</a><a href="#ideal_distribution_reference">理想分布与距离</a><a href="#yahoo_control">Yahoo! 对照</a><a href="#distribution_cost">分布与通知成本</a><a href="#dynamic_distribution_detail">末轮放大</a><a href="#attack_area_cost">总体猜测面积</a><a href="#coverage_F">猜测成功率</a><a href="#attack_F">猜测曲线</a><a href="#google_round_zipf">逐轮 Zipf</a><a href="#round_parameters">逐轮参数</a><a href="#final_distribution">口令分布</a><a href="#distribution-fit">CDF 采样拟合</a><a href="#intervention-rounds">每轮任务</a><a href="#experiment-settings">实验设置</a></nav>
 <p class="muted">新协议：按距理想分布的 W1 距离选动作；所有被通知账户均成功修改。模拟方案的固定 F 攻击曲线面积下降才接受，模型未覆盖比例仅作诊断。Yahoo! JAPAN 作为额外全站强策略对照，另计修改成本。下方历史结果保留原协议标注。</p>
 <p class="actions"><button id="recent">加载最近完成结果</button><button id="download" disabled>下载公开报告</button><span id="status" role="status">正在读取结果…</span></p></main>
 <main id="full-study-progress" hidden><section><h2>完整实验进度</h2><p id="full-study-message" role="status"></p><p class="muted">下方保留最近完成结果。停止的实验不会自动重启。新协议全量结果完成并核验后才替换历史结果；最多 10 轮，也可能因预算或无有效动作提前停止。</p></section></main>
 <div id="result" aria-live="polite"></div>
 <main><section id="experiment-settings"><h2>实验设置</h2><p>首先使用快速验证检查流程。所有比例以全站初始账户数为分母；Google 起点通知与后续通知分别计数，后续阶段每账户最多通知一次。</p>
 <div class="controls">
 <label>预设<select id="preset"><option value="intervention_smoke">快速验证 · 1,000 人</option><option value="intervention_full">完整研究 · 10 万人</option></select></label>
 <label>模拟账户<input id="users" type="number" min="1"></label><label>开发参考样本<input id="development" type="number" min="10"></label>
 <label>每轮最多影响（%）<input id="round" type="number" min="0.01" max="100" step="0.1"></label>
 <label>累计最多影响（%）<input id="total" type="number" min="0.01" max="100" step="1"></label>
 <label>响应假设<input id="nonresponse" type="text" value="全部通知账户成功修改" disabled></label>
 <label>历史前段诊断范围（%）<input id="target" type="number" min="0.01" max="100" step="0.1"></label>
 <label>随机种子<input id="seed" type="number" min="0"></label><label>蒙特卡洛采样数<input id="samples" type="number" min="100"></label>
 </div><label class="wide">本地带频次语料路径<input id="path"></label>
 <details><summary>完整配置与评估选项</summary><textarea id="config" spellcheck="false"></textarea><button id="apply">应用完整配置</button></details>
 <p class="actions"><button id="run">运行局部干预实验</button></p>
 <p class="muted">真实语料首次仍需扫描完整文件。页面显示扫描、候选比较和 A1 训练进度；导出的公开报告不包含账户口令。</p>
 </section></main><script>
 const $=id=>document.getElementById(id);let cfg=null,last=null,busy=false,externalRunning=false,externalJob=null;
 function status(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error)}
 async function request(url,options){const res=await fetch(url,options);const data=await res.json();if(!res.ok)throw Error(data.error||'请求失败');return data}
 function fill(value){cfg=structuredClone(value);cfg.schema_version="__CURRENT_PROTOCOL__";cfg.controller.execution_policy='fixed-F-area-only-batch-v2';cfg.response.mode='all-notified-change-v1';cfg.response.nonresponse=0;$('preset').value=cfg.data.users===100000?'intervention_full':'intervention_smoke';$('users').value=cfg.data.users;$('development').value=cfg.data.development;$('round').value=cfg.controller.round_fraction*100;$('total').value=cfg.controller.total_fraction*100;$('nonresponse').value='全部通知账户成功修改';$('target').value=cfg.controller.distribution_top_fraction*100;$('seed').value=cfg.seed;$('samples').value=cfg.monte_carlo.samples;$('path').value=cfg.data.path;$('config').value=JSON.stringify(cfg,null,2)}
 function read(){const v=JSON.parse(JSON.stringify(cfg));v.data.users=+$('users').value;v.data.development=+$('development').value;v.data.path=$('path').value;v.controller.round_fraction=+$('round').value/100;v.controller.total_fraction=+$('total').value/100;v.response.nonresponse=0;v.controller.distribution_top_fraction=+$('target').value/100;v.seed=+$('seed').value;v.monte_carlo.samples=+$('samples').value;return v}
 function show(output){last=output;$('result').innerHTML=output?output.html:'<main><section><h2>还没有局部干预结果</h2><p>在下方选择配置，运行第一轮实验。</p></section></main>';if(output)$('result').dataset.runId=output.run_id;else delete $('result').dataset.runId;$('download').disabled=!output}
 async function recent(){try{const data=await request('/api/intervention/latest');show(data.output);if(data.output)fill(data.output.config);status(data.output?'已加载真实运行结果':'尚无结果，可运行快速验证');return data.output}catch(e){status(e.message,true);return null}}
 $('preset').onchange=async()=>{try{fill(await request('/api/intervention/config/'+$('preset').value))}catch(e){status(e.message,true)}};
 $('apply').onclick=()=>{try{fill(JSON.parse($('config').value));status('配置已应用；运行时会检查参数')}catch(e){status('配置无效：'+e.message,true)}};
 $('recent').onclick=recent;
 $('download').onclick=()=>{if(last){const a=document.createElement('a');a.href=last.report_url;a.download='intervention_report.json';a.click()}};
 async function activity(){try{const state=await request('/api/intervention/activity');const wasRunning=externalRunning;externalRunning=!!state&&state.status==='running';$('run').disabled=busy||externalRunning;$('full-study-progress').hidden=!state;if(state){$('full-study-message').textContent=Number(state.users).toLocaleString()+' 个账户 · '+state.message+' · 更新于 '+new Date(state.updated_at).toLocaleString();const changed=externalJob!==state.job_id;externalJob=state.job_id;if(state.status==='complete'&&(changed||wasRunning)&&!busy)await recent()}}catch(e){$('full-study-progress').hidden=false;$('full-study-message').textContent='暂时无法读取完整实验进度：'+e.message}}
 $('run').onclick=async()=>{if(busy||externalRunning)return;busy=true;$('run').disabled=true;$('recent').disabled=true;show(null);status('准备运行…');try{const current=read();fill(current);const job=await request('/api/intervention/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:current})});while(true){const state=await request('/api/intervention/jobs/'+job.job_id);status(state.message);if(state.status==='complete'){show(state.output);break}if(state.status==='failed')throw Error(state.message);await new Promise(resolve=>setTimeout(resolve,1500))}}catch(e){show(null);status('实验未完成：'+e.message,true)}finally{busy=false;$('run').disabled=externalRunning;$('recent').disabled=false}};
 (async()=>{try{if(!await recent())fill(await request('/api/intervention/config/intervention_smoke'));await activity();setInterval(activity,5000)}catch(e){status(e.message,true)}})();
 </script></body></html>''').replace("__CURRENT_PROTOCOL__", PROTOCOL)
