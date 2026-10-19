from pathlib import Path
import sys,json,gzip,time,traceback
B=Path(__file__).resolve().parent;R=B/'source/ZipfGuard';sys.path.insert(0,str(R));O=B/'runs/existing_80_15_5';P=O/'policy';D=O/'fixed_F_deliverable';D.mkdir(exist_ok=True)
def status(s,**kw):(D/'status.json').write_text(json.dumps({'stage':s,**kw},ensure_ascii=False,indent=2))
try:
 status('waiting_for_policy_endpoints')
 while len(list(P.glob('*.endpoint.json.gz')))<4:
  state=json.loads((O/'completion_status.json').read_text())
  if state['stage']=='failed':raise RuntimeError('Policy runner failed: '+state.get('error',''))
  time.sleep(15)
 from core.registration import load_registration
 from ai.dual_attack import build_dual_index
 cfg=json.loads((P/'config.json').read_text())
 status('evaluating_fixed_three_models')
 data=load_registration('/Users/cjx_main/Desktop/new/MMJS/rockyou-withcount.txt',users=100000,development=100000,seed=42,cohort_size=100000)
 idx=build_dual_index(data['development']['train'],cfg)
 llm={}
 for i,w in enumerate((O/'passllm_F_generation_fast/guesses.txt').read_text().splitlines(),1):llm.setdefault(w,i)
 rows=[]
 for path in sorted(P.glob('*.endpoint.json.gz')):
  with gzip.open(path,'rt') as f:e=json.load(f)
  points=[];total=sum(e['target'].values())
  for budget in [1000,10000,100000,1000000]:
   hits={k:0 for k in ['pcfg','markov','passllm','union']}
   for w,n in e['target'].items():
    yes={'pcfg':idx.ranks['pcfg'].get(w,budget+1)<=budget,'markov':idx.ranks['markov'].get(w,budget+1)<=budget,'passllm':llm.get(w,budget+1)<=budget}
    for k,v in yes.items():hits[k]+=n*bool(v)
    hits['union']+=n*any(yes.values())
   points.append({'per_model_budget':budget,'max_total_attempts':3*budget,'hits':hits,'rates':{k:n/total for k,n in hits.items()}})
  rows.append({'key':e['key'],'label':e['label'],'accounts':total,'points':points})
 raw=json.loads((O/'passllm_F_generation_fast/status.json').read_text())
 report={'scope':'fixed F only, 80/15/5, seed42, 100000 accounts','adaptive_A':'not performed; paused by user','PassLLM':raw,'budget_limitation':'1000000 raw draws produced 999549 retained strings. At sub-budgets retained-string positions do not restore missing-draw positions; label curves as retained-prefix diagnostics, not exact raw-budget curves. Million endpoint uses all 1000000 raw draws.','rows':rows}
 (D/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 text='# 已有账户：80/15/5场景，冻结三模型终态评价\n\n种子42，10万账户。冻结模型F，PCFG、OMEN各100万次；PassLLM共100万次实际抽样，保留999,549条字符串。仅报告冻结攻击，不能解释为适应性攻击效果。\n\n| 方法 | PCFG | OMEN | PassLLM | 三模型联合 |\n|---|---:|---:|---:|---:|\n'
 for row in rows:
  r=row['points'][-1]['rates'];text+='|'+row['label']+'|'+ '|'.join(f'{100*r[k]:.3f}%' for k in ['pcfg','markov','passllm','union'])+'|\n'
 text+='\n合计预算最多300万次，重复猜测占用次数，重复命中的账户只计一次。本阶段结果不代表两个板块或F/A全部实验已完成。中间预算曲线由于未保留无效抽样的原始位置，仅为保留前缀诊断，不作为精确原始次数曲线。\n'
 (D/'结果.md').write_text(text);status('completed',rows=4)
except BaseException as e:status('failed',error=str(e),traceback=traceback.format_exc());raise
