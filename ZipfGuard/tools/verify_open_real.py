"""Bounded real-data acceptance; scientific multi-seed study is a separate run."""
import copy
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.corpus import load_corpus
from experiments.open_config import load_open_config
from experiments.open_pipeline import run_open_pipeline
from experiments.pipeline import write_json,write_report
from web.presentation import report_html

cfg=load_open_config()
data=cfg['data']
dataset=load_corpus(ROOT/data['path'],source_format=data['format'],encoding=data['encoding'],
                    sample_size=data['sample_size'],seed=cfg['seed'],fractions=data['split_fractions'],progress=print)
result=run_open_pipeline(cfg,dataset=dataset,progress=print)
assert any(r['evaluable'] for r in result['validation_candidates']), 'No complete validation candidate'
assert result['validation_candidates'][0]['evaluable'], 'Baseline budget not completed'
write_json(result,ROOT/'reports/open_demo.json');write_report(result,ROOT/'reports/open_demo.md')
(ROOT/'reports/open_demo.html').write_text(report_html(result),encoding='utf-8')
# PCFG and all response paths, using the same sampled initial cohort.
check=copy.deepcopy(cfg);check['budgets']=[10,100];check['search']['risk_budget']=100
check['search']['lengths']=[8];check['discovery']['max_features']=1
check['response']['scenarios']=['R0','R1','R2'];check['attackers']['pcfg']='required'
check['pcfg']['raw_limit']=3000;check['generation']['raw_limit']=5000
second=run_open_pipeline(check,dataset=dataset,progress=print)
assert 'pcfg' in second['metadata']['participating_attackers']
assert not second['metadata']['attacker_failures']
assert {r['scenario'] for r in second['test_policies']}=={'R0','R1','R2'}
write_json(second,ROOT/'reports/open_pcfg_responses.json');write_report(second,ROOT/'reports/open_pcfg_responses.md')
(ROOT/'reports/open_pcfg_responses.html').write_text(report_html(second),encoding='utf-8')
summary={'real_data':result['dataset'],'quick_runtime':result['metadata']['runtime_seconds'],
         'quick_models':result['metadata']['participating_attackers'],
         'pcfg_response_models':second['metadata']['participating_attackers'],
         'pcfg_response_scenarios':second['metadata']['response_scenarios'],
         'pcfg_response_failures':second['metadata']['attacker_failures'],
         'quick_validation_complete':sum(r['evaluable'] for r in result['validation_candidates']),
         'quick_validation_count':len(result['validation_candidates'])}
(ROOT/'reports/open_real_checks.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='real_data'},ensure_ascii=False))
