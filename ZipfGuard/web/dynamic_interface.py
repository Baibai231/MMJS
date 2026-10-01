"""A separate browser entry for the sequential policy study."""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from experiments.dynamic_config import load_dynamic_config, validate_dynamic_config
from experiments.dynamic_pipeline import run_dynamic_pipeline
from web.dynamic_presentation import render_dynamic_html
from web.presentation import STYLE
from policy.site_catalog import site_catalog

JOBS = {}
LOCK = threading.Lock()


def dynamic_report_directory(run_id):
    """Resolve a run ID even when a study chose a descriptive output folder."""
    directory = Path(__file__).resolve().parents[1] / 'reports' / 'dynamic'
    canonical = directory / run_id
    if (canonical / 'report.json').is_file():
        return canonical
    for report in directory.rglob('report.json'):
        try:
            value = json.loads(report.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if value.get('metadata', {}).get('run_id') == run_id:
            return report.parent
    raise FileNotFoundError(run_id)


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
        if 'monte_carlo' not in result.get('config', {}):
            continue
        return {'output': _published_output(result)}
    return {'output': None}


def _published_output(result):
    run_id = result['metadata']['run_id']
    return {'html': render_dynamic_html(result, document=False),
            'summary': {'run_id': run_id, 'config': result['config'],
                        'users': result['dataset']['registration_occurrences'],
                        'seed': result['config']['seed']},
            'report_url': f'/api/dynamic/report/{run_id}.json'}


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
            output = _published_output(result)
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


def sequence_search_snapshot():
    from web.sequence_rankings import EXHAUSTIVE
    try:
        exhaustive = json.loads((EXHAUSTIVE / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        exhaustive = None
    if exhaustive is not None and exhaustive.get('status') == 'complete':
        best = exhaustive['best_evaluated']
        return {'status': 'complete_feasible_space', 'path': best['path'],
                'screened_paths': exhaustive['feasible_distinct_rule_paths'],
                'a1_evaluated_paths': exhaustive['feasible_distinct_rule_paths'],
                'exact_outcome_groups': exhaustive['exact_outcome_groups'],
                'development_a1_rate': best['a1']['rate'],
                'global_optimum_proven': True,
                'scope': 'cost-feasible executable site-rule paths on development data'}
    path = Path(__file__).resolve().parents[1] / 'reports' / 'dynamic' / 'top15_sequence_search.json'
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'status': 'unavailable'}
    if result.get('status') != 'candidate_search_complete_global_A1_optimum_unproven':
        return {'status': result.get('status', 'unavailable'),
                'screened_paths': result.get('screened_paths', 0),
                'a1_evaluated_paths': result.get('a1_evaluated_paths', 0)}
    best = result['a1_rows'][0]
    return {'status': result['status'], 'path': best['path'],
            'screened_paths': result['screened_paths'],
            'a1_evaluated_paths': result['a1_evaluated_paths'],
            'development_a1_rate': best['a1']['rate'],
            'global_optimum_proven': False}


SITE_CHOICES = [{'id': 'site_' + row['id'], 'site': row['site'], 'basis': row['basis']}
                for row in site_catalog()['sites']
                if row['basis'] in ('partial_hard', 'recommended_scenario')]


DYNAMIC_INDEX = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 '<title>ZipfGuard · 分批注册</title><style>' + STYLE + '''
body{background:#f2f6f4;color:#173028}main{max-width:1200px}section{border-radius:12px}
.controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.controls label{display:flex;min-width:0}.controls input,.controls select{width:100%;box-sizing:border-box}
.wide-path{display:flex;width:100%;box-sizing:border-box}.wide-path input{width:100%;box-sizing:border-box}
.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}button{background:#13715d}
#status{font-size:13px;color:#456a61}#status.error{color:#b73535}
''' + '</style><body><main><p><a href="/open">历史静态实验</a></p>'
                 '<h1>分批注册的网站口令策略实验台</h1>'
                 '<p class="notice">工作台区分固定网站规则、十批完整路径的全空间筛选和重新训练 PCFG A1 的候选评价。请查看结果区的搜索数量与结论范围；未完成全空间 A1 评价时不宣称全局最优。</p>'
                 '<p>只使用 PCFG，训练后先建立蒙特卡洛排名索引。对比无策略、Top15 资料中可执行的网站规则子集与动态策略，保留 F / A0 / A1。</p>'
                 '<p class="actions"><a href="#final-distribution">口令分布</a><a href="#attack-success">攻击曲线</a><a href="#policy-rankings">三指标排序</a><a href="#site-catalog">Top15 策略来源</a></p>'
                 '<section><h2>实验设置</h2><div class="controls">'
                 '<label>预设<select id="preset"><option value="dynamic_smoke">快速验证 · 300 人</option>'
                 '<option value="dynamic_full">完整研究 · 10 万人</option></select></label>'
                 '<label>随机种子<input id="seed" type="number" min="0"></label>'
                 '<label>注册用户<input id="users" type="number" min="1"></label>'
                 '<label>开发参考样本<input id="development" type="number" min="3"></label>'
                 '<label>每批人数<input id="cohort" type="number" min="1"></label>'
                 '<label>攻击次数刻度<input id="budgets"></label>'
                 '<label>蒙特卡洛采样数<input id="samples" type="number" min="100"></label>'
                 '<label>建议场景<select id="recommendations"><option value="false">仅已知强制规则子集</option><option value="true">另纳入建议采纳场景</option></select></label>'
                 '</div><label class="wide-path">本地带频次语料路径<input id="path"></label>'
                 '<h3>逐批网站规则路径</h3><p>可为每批单独指定网站规则并评价这条路径。此功能不自动搜索全部路径。</p>'
                 '<p class="actions"><button id="load-sequence" type="button">载入开发集选出的十批候选路径</button></p>'
                 '<label>选择方式<select id="sequence-mode"><option value="online">逐批控制器</option><option value="explicit">手动指定每一批</option></select></label>'
                 '<div id="sequence-controls" class="controls"></div>'
                 '<details><summary>完整配置</summary><textarea id="config" style="width:100%;height:240px"></textarea>'
                 '<button id="apply">应用配置</button></details>'
                 '<p class="actions"><button id="run">运行当前配置</button>'
                 '<button id="recent">查看最近 PCFG 结果</button>'
                 '<button id="download" disabled>下载公开报告</button><span id="status">正在读取配置…</span></p>'
                 '<p class="muted">“300 人”是从整份语料中抽取的人数，首次运行仍需扫描全文件两遍；之后若文件未变，可复用统计缓存并扫描一遍。页面会显示扫描、注册和攻击阶段。逐用户结果只写入本机私有文件，网页不传送口令。</p>'
                 r'''</section><section><h2>查询一条口令</h2><p>先运行或加载结果。查询复用已保存索引，不重新训练；热门排名来自相应的用户口令集。</p>
<label>口令<input id="query-password" type="password" autocomplete="off"></label>
<label>攻击层次<select id="query-level"><option>F</option><option>A0</option><option>A1</option></select></label>
<label>A0 网站规则<select id="query-policy"><option value="site_google">Google</option><option value="site_wikipedia">Wikipedia</option><option value="site_netflix">Netflix</option><option value="site_github">GitHub</option><option value="site_amazon">Amazon</option></select></label>
<button id="query">查询</button><p id="query-result" role="status"></p></section></main><div id="result"></div><script>
const siteChoices=__SITE_CHOICES_JSON__;
const $=id=>document.getElementById(id);let cfg=null,last=null;
function currentSequence(){return [...document.querySelectorAll('#sequence-controls select')].map(x=>x.value)}
function buildSequence(values=[]){const box=$('sequence-controls');box.replaceChildren();box.hidden=$('sequence-mode').value!=='explicit';if(box.hidden)return;const count=Math.ceil(+$('users').value/Math.max(1,+$('cohort').value));if(!Number.isFinite(count)||count<1||count>50){box.textContent='批次数需为 1–50；请调整用户数或每批人数。';return}const allowed=siteChoices.filter(x=>x.basis==='partial_hard'||$('recommendations').value==='true');for(let i=0;i<count;i++){const label=document.createElement('label');label.textContent='第 '+(i+1)+' 批';const select=document.createElement('select');for(const site of allowed){const option=document.createElement('option');option.value=site.id;option.textContent=site.site+(site.basis==='recommended_scenario'?'（建议场景）':'（已知规则）');select.append(option)}select.value=allowed.some(x=>x.id===values[i])?values[i]:'site_google';label.append(select);box.append(label)}}

function setFigureLinks(summary){for(const a of document.querySelectorAll('#result a.figure-link'))a.href='/api/dynamic/figure/'+summary.run_id+'/'+a.getAttribute('href')}
function put(c){cfg=c;$('samples').value=c.monte_carlo.samples;$('recommendations').value=String(c.controller.include_recommendations);$('seed').value=c.seed;$('users').value=c.data.users;$('development').value=c.data.development;$('cohort').value=c.data.cohort_size;$('path').value=c.data.path;$('budgets').value=c.budgets.join(',');$('config').value=JSON.stringify(c,null,2);$('sequence-mode').value=c.controller.sequence?'explicit':'online';buildSequence(c.controller.sequence||[])}
function get(){let c=structuredClone(cfg);c.monte_carlo.samples=+$('samples').value;c.controller.include_recommendations=$('recommendations').value==='true';c.seed=+$('seed').value;c.data.users=+$('users').value;c.data.development=+$('development').value;c.data.cohort_size=+$('cohort').value;c.data.path=$('path').value;c.budgets=$('budgets').value.split(',').map(Number);if($('sequence-mode').value==='explicit'){c.controller.sequence=currentSequence();if(c.controller.sequence.length!==Math.ceil(c.data.users/c.data.cohort_size))throw Error('逐批策略数量与批次数不一致')}else c.controller.sequence=null;return c}
async function preset(){try{let r=await fetch('/api/dynamic/config/'+$('preset').value);let c=await r.json();if(!r.ok)throw Error(c.error);put(c);$('status').textContent='配置就绪'}catch(e){$('status').textContent=e.message;$('status').className='error'}}
async function recent(){if($('run').disabled)return;$('recent').disabled=true;try{let r=await fetch('/api/dynamic/latest');let state=await r.json();if(!r.ok)throw Error(state.error);if(!state.output){$('status').textContent='暂无已完成结果，可运行一次实验';return}last=state.output;const saved=last.summary;put(saved.config);$('preset').value=saved.users>=100000?'dynamic_full':'dynamic_smoke';$('result').innerHTML=last.html;setFigureLinks(saved);$('download').disabled=false;$('status').className='';$('status').textContent='已加载最近完成结果：'+saved.users.toLocaleString()+' 人 · 种子 '+saved.seed+'（无需重新计算）'}catch(e){$('status').textContent='读取结果失败：'+e.message;$('status').className='error'}finally{$('recent').disabled=false}}
$('recent').onclick=recent;
$('load-sequence').onclick=async()=>{try{let r=await fetch('/api/dynamic/sequence-search');let s=await r.json();if(!r.ok)throw Error(s.error);if(!s.path)throw Error('路径搜索尚未完成');$('recommendations').value='false';$('sequence-mode').value='explicit';buildSequence(s.path);$('status').className='';$('status').textContent='已载入候选路径：'+s.a1_evaluated_paths+' 条 A1 候选中最优；全局最优性未证明。'}catch(e){$('status').textContent=e.message;$('status').className='error'}};
$('query').onclick=async()=>{try{if(!last)throw Error('请先运行或加载结果');let saved=last.summary;let r=await fetch('/api/dynamic/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:saved.run_id,password:$('query-password').value,level:$('query-level').value,policy:$('query-policy').value})});let d=await r.json();if(!r.ok)throw Error(d.error);let g=d.guess,p=d.popularity;let labels={estimated:'蒙特卡洛估计',low_sample_support:'前驱样本不足，估计不稳定',outside_model_support:'模型未覆盖',policy_ineligible:'不符合所选规则'};$('query-result').textContent=d.population+'：热门排名 '+(p.rank??'未出现')+'，出现 '+p.count+' 次；猜测次数 '+(g.guess_count===null?'不可估计':g.guess_count.toLocaleString(undefined,{maximumFractionDigits:1}))+'；'+labels[g.status]+(g.standard_error===null?'':'；采样标准误 '+g.standard_error.toPrecision(3));}catch(e){$('query-result').textContent=e.message}};
$('preset').onchange=preset;$('sequence-mode').onchange=()=>buildSequence(currentSequence());$('users').onchange=()=>buildSequence(currentSequence());$('cohort').onchange=()=>buildSequence(currentSequence());$('recommendations').onchange=()=>buildSequence(currentSequence());$('apply').onclick=()=>{try{put(JSON.parse($('config').value));$('status').textContent='配置已应用'}catch(e){$('status').textContent=e.message}};
$('run').onclick=async()=>{last=null;$('result').replaceChildren();$('run').disabled=true;$('recent').disabled=true;$('download').disabled=true;$('status').className='';try{let c=get();$('config').value=JSON.stringify(c,null,2);let r=await fetch('/api/dynamic/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:c})});let j=await r.json();if(!r.ok)throw Error(j.error);while(true){await new Promise(done=>setTimeout(done,1300));let q=await fetch('/api/dynamic/jobs/'+j.job_id);let state=await q.json();if(!q.ok)throw Error(state.error);$('status').textContent=state.message;if(state.status==='failed')throw Error(state.message);if(state.status==='complete'){last=state.output;$('result').innerHTML=last.html;setFigureLinks(last.summary);$('download').disabled=false;break}}}catch(e){$('status').textContent='未完成：'+e.message;$('status').className='error'}finally{$('run').disabled=false;$('recent').disabled=false}};
$('download').onclick=()=>{if(!last)return;const a=document.createElement('a');a.href=last.report_url;a.download='zipfguard_dynamic.json';a.click()};preset().then(recent);
</script></body></html>''')

DYNAMIC_INDEX = DYNAMIC_INDEX.replace('__SITE_CHOICES_JSON__',
    json.dumps(SITE_CHOICES, ensure_ascii=False).replace('<', chr(92) + 'u003c'))
