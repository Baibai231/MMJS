"""Freeze and execute a multi-seed open-protocol study; no plaintext exports."""
from __future__ import annotations
import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.corpus import load_corpus
from experiments.open_config import load_open_config,validate_open_config
from experiments.config import fingerprint
from experiments.open_pipeline import run_open_pipeline
from experiments.pipeline import write_json,write_report
from web.presentation import report_html


def build_plan(config,seeds,responses,budgets,ablations):
    jobs=[]
    for seed in seeds:
        for response in responses:
            for budget in budgets:
                for mode in ablations:
                    c=copy.deepcopy(config);c['seed']=seed
                    c['response'].update(primary=response,scenarios=[response])
                    c['search']['risk_budget']=budget;c['discovery']['mode']=mode
                    c=validate_open_config(c)
                    jobs.append({'id':f's{seed}_{response}_k{budget}_{mode}',
                                 'config_sha256':fingerprint(c),'config':c})
    if len({j['id'] for j in jobs})!=len(jobs):raise ValueError('实验条件不能重复')
    return {'schema':'zipfguard-study-v1','protocol_id':'open-minauto-v1',
            'frozen_before_data_access':True,'jobs':jobs,
            'inference':'descriptive repeated splits, not independent data sources; no pooled significance claim'}


def summary_for(results):
    rows=[];groups=defaultdict(set)
    for job,result in results:
        cfg=job['config'];models=tuple(result['metadata']['participating_attackers'])
        key=(cfg['seed'],cfg['response']['primary'],cfg['search']['risk_budget'])
        groups[key].add(models)
        for rec in result['recommendations']:
            test=rec.get('test',{});response=test.get('response',{})
            rows.append({'run':job['id'],'seed':cfg['seed'],'response':cfg['response']['primary'],
                         'budget':cfg['search']['risk_budget'],'discovery':cfg['discovery']['mode'],
                         'requirement':rec['requirement']['name'],'policy':rec['policy_name'],
                         'status':rec['status'],'risk':test.get('risk'),'cost':response.get('cost'),
                         'completion':response.get('completion_rate'),'extra_attempts':response.get('mean_extra_attempts'),
                         'models':list(models),'split_hashes':result['dataset']['split_hashes'],
                         'candidate_count':result['discovery']['candidate_count']})
    return {'schema':'zipfguard-study-summary-v1','runs':len(results),'recommendations':rows,
            'within_condition_model_sets_consistent':all(len(v)==1 for v in groups.values()),
            'interpretation':'Compare only matched conditions and participating models. Individual intervals are exploratory; no automatic pooled efficacy conclusion.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path);parser.add_argument('--preset',default='open_quick',choices=['open_quick','open_full'])
    parser.add_argument('--seeds',default='11,23,42,67,101');parser.add_argument('--responses',default='R0,R1,R2')
    parser.add_argument('--risk-budgets',help='Comma-separated selection budgets, each included in configuration budgets')
    parser.add_argument('--ablations',default='distribution,none');parser.add_argument('--out',type=Path,default=ROOT/'reports/open_study')
    parser.add_argument('--dry-run',action='store_true',help='Write the frozen experiment plan without reading corpus or evaluating test')
    args=parser.parse_args();cfg=load_open_config(args.preset,args.config)
    plan=build_plan(cfg,[int(x) for x in args.seeds.split(',')],args.responses.split(','),
                    [int(x) for x in args.risk_budgets.split(',')] if args.risk_budgets else [cfg['search']['risk_budget']],
                    args.ablations.split(','))
    args.out.mkdir(parents=True,exist_ok=True);planfile=args.out/'plan.json'
    if planfile.exists():
        if json.loads(planfile.read_text('utf-8'))!=plan:raise ValueError('输出目录已有不同计划，请使用新的目录')
        if (args.out/'summary.json').exists() and not args.dry_run:raise ValueError('已有结果，请使用新的输出目录保留原实验')
    write_json(plan,planfile)
    if args.dry_run:
        print(f'Frozen {len(plan["jobs"])} runs: {planfile}');return 0
    results=[];cached_seed=None;dataset=None
    for index,job in enumerate(plan['jobs'],1):
        c=job['config'];print(f'[{index}/{len(plan["jobs"])}] {job["id"]}',flush=True)
        if cached_seed!=c['seed']:
            d=c['data'];source=Path(d['path']);source=source if source.is_absolute() else ROOT/source
            dataset=load_corpus(source,source_format=d['format'],encoding=d['encoding'],sample_size=d['sample_size'],
                                seed=c['seed'],fractions=d['split_fractions'],progress=print)
            cached_seed=c['seed']
        result=run_open_pipeline(c,dataset=dataset,progress=print);stem=args.out/job['id']
        write_json(result,stem.with_suffix('.json'));write_report(result,stem.with_suffix('.md'))
        stem.with_suffix('.html').write_text(report_html(result),encoding='utf-8')
        results.append((job,result));write_json(summary_for(results),args.out/'summary.json')
    return 0

if __name__=='__main__':raise SystemExit(main())
