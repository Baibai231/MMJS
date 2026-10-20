from pathlib import Path
import json,csv,tarfile
B=Path(__file__).resolve().parent;R=B/'runs/registration_paired';rows=[]
assert json.loads((R/'status.json').read_text())['stage']=='completed'
for scenario in ['baseline_60_25_15','local_80_15_5']:
 for arm in ['github_core','schechter_strong','malone','segreti_struct_m_nohint','bidirectional_exchange']:
  r=json.loads((R/scenario/'runs/11'/arm/'result.json').read_text());assert r['status']=='complete' and not r['attack_failures']
  row={'scenario':scenario,'method':arm,'users':100000}
  for phase in ['F','A']:
   e=json.loads((R/scenario/'evaluation'/f'{arm}_{phase}.json').read_text());assert e['accounts']==100000 and e['budget_per_model']==1000000
   g=e['PassLLM_generation'];assert g['raw_draws']==1000000 and g['source_stop']=='raw_limit'
   for k,v in e['rates'].items():
    assert 0<=v<=1
    row[f'{phase}_{k}']=v
   row[f'{phase}_passllm_retained']=g['upstream_raw_count']
  row['cost']=r['cost'];rows.append(row)
(B/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
flat=[{k:v for k,v in r.items() if k!='cost'} for r in rows]
with (B/'results.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
lines=['# 注册阶段完整实验结果','', '10万模拟账户，种子11；两种比例共用同一新分区。单种子，不作统计显著性结论。训练FP32，生成BF16，批次4096。PCFG与OMEN各100万次；PassLLM各100万次原始抽样，保留字符串可能不足100万。联合预算最多300万。','', '|比例|策略|F PCFG|F OMEN|F PassLLM|F联合|A PCFG|A OMEN|A PassLLM|A联合|','|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
for r in rows:
 vals=[f"{r[f'{p}_{k}']*100:.3f}%" for p in ['F','A'] for k in ['pcfg','markov','passllm','union']]
 lines.append('|'+ '|'.join([r['scenario'],r['method']]+vals)+'|')
lines+=['','## 预算核验','', '已核验10组策略结果无攻击失败，20份终态评价均为10万账户、每模型百万预算；11套PassLLM均完成百万原始抽样。状态incomplete仅指保留字符串不足百万，不等于原始抽样未完成。','', '不能从这一个种子推出比例变化不影响结果；冻结与适应性结论分别报告。']
(B/'结果汇总.md').write_text('\n'.join(lines))
with tarfile.open(B/'registration_results_delivery.tar.gz','w:gz') as t:
 for p in B.glob('*.py'):t.add(p,arcname=p.name)
 for n in ['results.json','results.csv','结果汇总.md','gpu_run_manifest.json','generation_benchmark.json','batch_bf16_benchmark.jsonl','policy.log','gpu_pipeline.log']:
  if (B/n).exists():t.add(B/n,arcname=n)
 for p in R.rglob('*.json'):
  if p.name in ['manifest.json','partition_manifest.json','status.json','policy_status.json','result.json'] or 'evaluation' in p.parts:
   t.add(p,arcname=str(p.relative_to(B)))
print((B/'结果汇总.md').read_text())
