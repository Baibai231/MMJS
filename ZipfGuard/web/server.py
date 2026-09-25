"""Zero-dependency local demo server.

Run from the ZipfGuard directory::

    python web/server.py --port 8765

It serves an interactive dashboard and JSON endpoints. The browser never sees
raw Rockyou strings; the server returns only counts, model metrics and policy
aggregates.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.passllm_adapter import PassLLMConfig, runtime_status
from core.data import load_count_json, write_count_json
from core.rockyou import aggregate_rockyou
from experiments.pipeline import run_pipeline

DEFAULT_ROCKYOU = ROOT.parent / "lab_basic_50_dicts" / "Rockyou.txt"
CACHE = ROOT / "demo_data" / "rockyou_top_2000.json"
_cache_lock = threading.Lock()


def _json_safe(result):
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def rockyou_result(max_lines=1_000_000, top_k=2_000, bootstrap=40):
    payload = aggregate_rockyou(DEFAULT_ROCKYOU, max_lines=max_lines, top_k=top_k)
    return run_pipeline(payload, bootstrap_repetitions=bootstrap)



from web.presentation import report_html, STYLE
from experiments.config import load_config, validate_config
from core.data import validate_count_payload
from hashlib import sha256


SETTINGS_STYLE = """
.settings-panel>summary{display:flex;align-items:center;gap:12px;flex-wrap:wrap;list-style:none;margin:0;padding:4px 0;cursor:pointer}
.settings-panel>summary::-webkit-details-marker{display:none}
.settings-panel>summary::before{content:"›";font-size:24px;line-height:1;transition:transform .15s}
.settings-panel[open]>summary::before{transform:rotate(90deg)}
.settings-panel>summary:focus-visible{outline:2px solid #2463d9;outline-offset:6px;border-radius:4px}
.settings-title{font-size:19px;font-weight:600}.settings-summary{flex:1;min-width:160px}
.settings-toggle{font-size:13px;color:#2463d9}.settings-toggle::after{content:"展开参数"}
.settings-panel[open] .settings-toggle::after{content:"收起参数"}
.settings-content{border-top:1px solid #e4eaf2;margin-top:18px;padding-top:10px}
.settings-content h3{font-size:14px;color:#62728a;margin:18px 8px 4px}
.settings-content .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:10px}
.settings-content label{min-width:0;margin:8px 0}.settings-content input,.settings-content select{box-sizing:border-box;width:100%;min-width:0}
.settings-content textarea{box-sizing:border-box;width:100%}
.experiment-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:20px 0 0}
button:disabled{opacity:.55;cursor:default}
@media(max-width:600px){main{padding:16px}section{padding:18px}.settings-summary{flex-basis:100%;order:3}.settings-toggle{margin-left:auto}}
"""

INDEX = "<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>ZipfGuard 实验台</title><style>" + STYLE + SETTINGS_STYLE + "</style>" + r'''<body><main><h1>ZipfGuard · 离线实验台</h1><p>统一 Python 实验核心。下面四步是现场要看的链路，不需要先读源码。</p>
<ol><li>数据来源：合成演示和真实聚合用不同标签。RockYou 页面只做分布，不跑策略。</li><li>论文复现：Zipf 曲率、九特征 IGR、只对头部给出的建议。</li><li>新方法建议：同一批候选上，用验证集的预算和修改率排序。</li><li>同预算对照：无策略、传统字符类别、长度加黑名单、论文 IGR、预算/成本排序。</ol>
<p class="muted">合成演示会标成合成。聚合分析会标成聚合，并写明截断。模型失败会显示原因，不会换成另一个模型。预算是离线猜测次数，不是在线登录次数。</p>
<section aria-label="实验设置">
<details id="experiment-settings" class="settings-panel">
<summary><span class="settings-title">实验参数</span><span id="settings-summary" class="settings-summary muted">正在加载配置…</span><span class="settings-toggle" aria-hidden="true"></span></summary>
<div class="settings-content"><h3>数据与基础参数</h3><div class="grid">
<label>预设<select id="preset"><option value="quick">快速演示 · 1,000 用户</option><option value="full">完整实验 · 20,000 用户 + 可选 PCFG</option></select></label>
<label>数据来源<select id="source"><option value="synthetic">合成用户</option><option value="rockyou">RockYou 聚合（仅分布分析）</option><option value="upload">上传聚合 JSON（仅分布分析）</option></select></label>
<label>随机种子<input id="seed" type="number" min="0"></label><label>样本规模<input id="size" type="number" min="100"></label>
<label>Zipf 指数<input id="exponent" type="number" step="0.01"></label><label>攻击预算（逗号分隔）<input id="budgets"></label>
<label>Bootstrap 次数<input id="bootstrap" type="number" min="20"></label><label>风险阈值 q<input id="q" type="number" step="0.01"></label>
</div><h3>攻击器与策略</h3><div class="grid" id="attackers"></div><div class="grid">
<label>PCFG 生成上限<input id="limit" type="number"></label><label>PCFG 超时秒数<input id="timeout" type="number"></label>
<label>用户响应<select id="response"><option value="repair">修补优先，再尝试短语</option><option value="phrase">短语优先，再尝试修补</option></select></label>
<label>响应成本权重（其余为规则成本）<input id="weight" type="number" min="0" max="1" step="0.05"></label><label>策略选择预算<input id="riskbudget" type="number"></label>
</div><h3>聚合数据选项</h3><div class="grid">
<label>来源类型<select id="semantics"><option value="unknown">未确认</option><option value="frequency">原始重复行频次</option><option value="unique_dictionary">去重字典</option></select></label>
<label>RockYou 读取行数<input id="maxlines" type="number" value="1000000"></label><label>保留 top-k<input id="topk" type="number" value="2000"></label>
<label>聚合文件<input id="upload" type="file" accept=".json"></label></div>
<details><summary>待比较策略（可编辑 JSON；保留 baseline）</summary><textarea id="policies"></textarea></details>
<details><summary>完整配置（可修改词表、搜索动作、响应顺序和约束；点击应用后再运行）</summary><textarea id="config"></textarea><button id="apply">应用完整配置</button><button id="export">下载当前配置</button></details>
</div></details>
<p class="experiment-actions"><button id="run">运行历史 576 项演示</button> <button id="open-compare">同预算对照（300 用户）</button> <button id="download" disabled>下载结果与 SHA-256</button> <span id="status" role="status"></span></p>
<p class="muted">“运行历史 576 项演示”会在高预算饱和，只作旧基线。“同预算对照”使用更大的支持集和原始生成序号，不读取 RockYou。</p>
<p class="muted">PassLLM 尚未接入主评估。可选模型失败会明确排除；不会冒充其他模型。</p></section></main><div id="result"></div>
<script>
const $=id=>document.getElementById(id);let cfg=null,last=null;
function updateSettingsSummary(){const source=$('source').selectedOptions[0].textContent;const detail=$('source').value==='synthetic'?$('size').value+' 用户 · 种子 '+$('seed').value:'保留 top-'+$('topk').value;$('settings-summary').textContent=source+' · '+detail}
$('experiment-settings').addEventListener('input',updateSettingsSummary);
$('experiment-settings').addEventListener('change',updateSettingsSummary);
function put(c){cfg=c;$('seed').value=c.seed;$('size').value=c.synthetic.size;$('exponent').value=c.synthetic.exponent;$('budgets').value=c.budgets.join(',');$('bootstrap').value=c.bootstrap_repetitions;$('q').value=c.q;$('limit').value=c.pcfg.generation_limit;$('timeout').value=c.pcfg.timeout_seconds;$('weight').value=c.search.response_cost_weight;$('riskbudget').value=c.search.risk_budget;$('response').value=c.response.order[0]==='random-phrase'?'phrase':'repair';$('policies').value=JSON.stringify(c.comparison_policies,null,2);$('config').value=JSON.stringify(c,null,2);$('attackers').replaceChildren();for(const [id,label] of [['frequency','频次'],['synthetic-dictionary','合成字典'],['character-ngram','字符 n-gram'],['pcfg','PCFG']]){const l=document.createElement('label');l.textContent=label;const s=document.createElement('select');s.id='att-'+id;for(const [v,t] of [['off','不参与'],['required','必选'],['optional','可选']]){const o=new Option(t,v);s.add(o)}s.value=c.attackers[id]||'off';l.append(s);$('attackers').append(l)}updateSettingsSummary()}
function get(){const c=structuredClone(cfg);c.seed=+$('seed').value;c.synthetic.size=+$('size').value;c.synthetic.exponent=+$('exponent').value;c.budgets=$('budgets').value.split(',').map(Number);c.bootstrap_repetitions=+$('bootstrap').value;c.q=+$('q').value;c.pcfg.generation_limit=+$('limit').value;c.pcfg.timeout_seconds=+$('timeout').value;c.search.response_cost_weight=+$('weight').value;c.search.rule_cost_weight=1-c.search.response_cost_weight;c.search.risk_budget=+$('riskbudget').value;const expected=$('response').value==='phrase'?'random-phrase':'append-symbol';if(c.response.order[0]!==expected)c.response.order=$('response').value==='phrase'?['random-phrase','append-symbol','append-symbol-digit']:['append-symbol','append-symbol-digit','random-phrase'];c.comparison_policies=JSON.parse($('policies').value);c.attackers={};for(const id of ['frequency','synthetic-dictionary','character-ngram','pcfg']){const mode=$('att-'+id).value;if(mode!=='off')c.attackers[id]=mode}return c}
async function preset(){try{const r=await fetch('/api/config/'+$('preset').value);put(await r.json())}catch(e){$('status').textContent=e.message}}
function download(name,text){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type:'application/octet-stream'}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
$('preset').onchange=preset;$('apply').onclick=()=>{try{put(JSON.parse($('config').value));$('status').textContent='配置已应用'}catch(e){$('status').textContent=e.message}};$('export').onclick=()=>{try{download('experiment.json',JSON.stringify(get(),null,2))}catch(e){$('status').textContent=e.message}};
$('run').onclick=async()=>{$('run').disabled=true;$('download').disabled=true;$('status').textContent='正在计算…';$('result').replaceChildren();last=null;try{const c=get();$('config').value=JSON.stringify(c,null,2);let payload=null;if($('source').value==='upload'){const f=$('upload').files[0];if(!f)throw Error('请选择聚合 JSON 文件');payload=JSON.parse(await f.text())}const res=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:c,source:$('source').value,payload,max_lines:+$('maxlines').value,top_k:+$('topk').value,source_semantics:$('semantics').value})});const data=await res.json();if(!res.ok)throw Error(data.error);last=data;$('result').innerHTML=data.html;$('download').disabled=false;$('status').textContent='实验完成'}catch(e){$('status').textContent='未完成：'+e.message}finally{$('run').disabled=false}};
$('download').onclick=()=>{download('zipfguard_report.json',last.json);download('zipfguard_report.json.sha256',last.sha256+'  zipfguard_report.json\n')};
$('open-compare').onclick=async()=>{$('open-compare').disabled=true;$('status').textContent='正在计算同预算对照…';$('result').replaceChildren();last=null;try{const res=await fetch('/api/open-compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seed:+$('seed').value||1})});const data=await res.json();if(!res.ok)throw Error(data.error);last=data;$('result').innerHTML=data.html;$('download').disabled=false;$('status').textContent='同预算对照完成'}catch(e){$('status').textContent='未完成：'+e.message}finally{$('open-compare').disabled=false}};
preset();
</script></body></html>'''



class Handler(BaseHTTPRequestHandler):
    def _send(self, body, content_type="application/json; charset=utf-8", status=200):
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/": return self._send(INDEX.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/status":
                config = PassLLMConfig.workspace_default(ROOT.parent)
                return self._send(_json_safe({"rockyou_present": DEFAULT_ROCKYOU.is_file(), "rockyou_bytes": DEFAULT_ROCKYOU.stat().st_size if DEFAULT_ROCKYOU.exists() else 0, "passllm": runtime_status(config)}))
            if path.startswith("/api/config/"):
                preset = path.rsplit("/", 1)[-1]
                if preset not in ("quick", "full"): raise ValueError("未知预设")
                return self._send(_json_safe(load_config(preset=preset)))
            if path == "/api/demo": return self._send(_json_safe(run_pipeline(bootstrap_repetitions=40)))
            if path == "/api/rockyou": return self._send(_json_safe(rockyou_result()))
            return self._send(b"not found", "text/plain; charset=utf-8", 404)
        except Exception as exc:
            return self._send(_json_safe({"error": str(exc)}), status=500)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/run", "/api/open-compare"):
            return self._send(b"not found", "text/plain", 404)
        # Local UI accepts JSON only; reject cross-site browser writes.
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self._send(b"forbidden", "text/plain", 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8_000_000:
                raise ValueError("请求大小无效")
            request = json.loads(self.rfile.read(length))
            with _cache_lock:
                if path == "/api/open-compare":
                    result = execute_open_compare(request)
                    content = json.dumps(result["report"], ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                    return self._send(_json_safe({"html": result["html"], "json": content, "sha256": sha256(content.encode("utf-8")).hexdigest()}))
                result = execute_request(request)
            content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            return self._send(_json_safe({"result": result, "html": report_html(result, document=False),
                "json": content, "sha256": sha256(content.encode("utf-8")).hexdigest()}))
        except (ValueError, TypeError, KeyError) as exc:
            return self._send(_json_safe({"error": str(exc)}), status=400)
        except Exception as exc:
            return self._send(_json_safe({"error": str(exc)}), status=500)

    def log_message(self, format, *args):
        return


def execute_open_compare(request):
    from experiments.provenance import robustness_manifest
    from experiments.robustness_protocol import evaluate_scenario, public_comparison_html, sample_mechanism
    seed = request.get("seed", 1)
    if type(seed) is not int or seed < 0:
        raise ValueError("种子必须是非负整数")
    report = evaluate_scenario(sample_mechanism("zipf", size=300, seed=seed), budget=40, with_markov=True)
    if "stem00" in json.dumps(report):
        raise RuntimeError("对照结果含有口令字符串")
    public = {
        "support_size": report["support_size"],
        "candidate_count": report["candidate_count"],
        "budget": report["budget"],
        "test_users": report["test_users"],
        "main_budget_saturated": report["main_budget_saturated"],
        "main_budget_decided_by_576": report["main_budget_decided_by_576"],
        "arms": {
            name: {
                "closed_absolute_point_change": arm["absolute_point_change_vs_none"],
                "open_absolute_point_change": arm["open_absolute_point_change_vs_none"],
                "head_frequency_absolute_point_change": arm["head_users"]["frequency_absolute_point_change"],
                "headline_published": arm.get("headline_published"),
                "withheld_reason": arm.get("withheld_reason"),
                "head_users": arm["head_users"]["users"],
                "all_users": arm["all_users"]["users"],
                "test_modification_rate": arm["test_modification_rate"],
                "closed_protocol": arm["adaptive_worst_attacker_protocol"],
            }
            for name, arm in report["arms"].items()
        },
        "selection_bias_note": "多条策略放在同一张表里时，挑最高的一行会夸大收益。预先指定的比较是预算/成本排序对论文 IGR 和长度加黑名单。",
        "plaintext_retained": False,
        "historical_576_grammar": False,
        "provenance": robustness_manifest(),
    }
    return {"report": public, "html": public_comparison_html(report)}


def execute_request(request):
    config = validate_config(request["config"])
    source = request.get("source", "synthetic")
    payload = None
    if source == "rockyou":
        max_lines, top_k = request.get("max_lines", 1_000_000), request.get("top_k", 2000)
        if type(max_lines) is not int or not 20 <= max_lines <= 10_000_000:
            raise ValueError("读取行数须在 20 到 10000000 之间")
        payload = aggregate_rockyou(DEFAULT_ROCKYOU, max_lines=max_lines, top_k=top_k,
                                  source_semantics=request.get("source_semantics", "unknown"))
    elif source == "upload":
        payload = validate_count_payload(request.get("payload"))
    elif source != "synthetic":
        raise ValueError("未知数据来源")
    if payload is not None:
        config["attackers"].pop("pcfg", None)
        if not config["attackers"]:
            config["attackers"] = {"frequency": "required"}
    return run_pipeline(payload, config=config)


def main():
    parser = argparse.ArgumentParser(description="ZipfGuard local demo UI")
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(); server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ZipfGuard demo: http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
