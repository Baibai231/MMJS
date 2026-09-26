"""Real-corpus -> response -> independent attacks -> Min_auto -> recommendations."""
from __future__ import annotations
import dataclasses
import json
import math
import random
import tempfile
import time
from collections import Counter
from pathlib import Path
import numpy as np
from ai.pcfg_adapter import PCFGAttacker, PCFGConfig, EXPECTED_COMMIT
from core.corpus import load_corpus, counts_hash
from core.open_attack import consume, frequency_stream, dictionary_stream, select_ngram, evaluate_runs, BUDGET_UNIT
from core.distributions import analyze_counts
from experiments.config import fingerprint
from experiments.open_config import validate_open_config, load_open_config, ROOT, PROTOCOL
from experiments.provenance import manifest
from policy.open_policy import discover_rules, response_for


class ModelFailure(RuntimeError):
    def __init__(self, model, reason): self.model=model;self.reason=reason;super().__init__(reason)


class AttackEngine:
    def __init__(self,cfg,temporary,excluded=()):
        self.cfg=cfg;self.models=[m for m in cfg['attackers'] if m not in excluded]
        self.cache={};self.fits={};self.raw_cache={};self.run_count=0
        p=cfg['pcfg']
        self.pcfg=PCFGAttacker(dataclasses.replace(PCFGConfig.workspace_default(
            generation_limit=p['raw_limit'],timeout_seconds=p['timeout_seconds']),runtime_root=Path(temporary)/'pcfg'))

    def attack(self,train,tuning,rule,knowledge):
        if not train: return []
        effective_rule=None if knowledge=='F' else rule
        key=(counts_hash(train),counts_hash(tuning),fingerprint(effective_rule.summary()) if effective_rule else 'unfiltered')
        if key in self.cache:return self.cache[key]
        runs=[];g=self.cfg['generation'];budget=max(self.cfg['budgets'])
        for model in self.models:
            fitkey=(model,key[0],key[1]);params={'training_sha256':key[0],'tuning_sha256':key[1]}
            source_stop='exhausted'; prep_start=time.perf_counter()
            try:
                if model=='frequency': stream=frequency_stream(train)
                elif model=='dictionary-rules':
                    stream=dictionary_stream(train);params['dictionary_source']='train frequency; fixed generic rules'
                elif model=='character-ngram':
                    if fitkey not in self.fits:
                        self.fits[fitkey]=select_ngram(train,tuning or train,self.cfg['ngram'])
                        if not tuning:
                            # Use the first pre-registered configuration, not train NLL tuning.
                            single={**self.cfg['ngram'],'orders':self.cfg['ngram']['orders'][:1],
                                    'smoothing':self.cfg['ngram']['smoothing'][:1]}
                            obj,meta=select_ngram(train,train,single)
                            meta.update(selection_split=None,selection='fixed first configuration; empty tuning',tuning_nll=None)
                            self.fits[fitkey]=(obj,meta)
                    obj,meta=self.fits[fitkey];params.update(meta)
                    generation_cfg={**self.cfg['ngram']}
                    if effective_rule:
                        generation_cfg['min_length']=max(generation_cfg['min_length'],effective_rule.min_length)
                        generation_cfg['required_classes']=effective_rule.classes
                    params['policy_conditioning']={'min_length':generation_cfg['min_length'],'required_classes':generation_cfg.get('required_classes',0)}
                    stream=obj.generate(generation_cfg,g)
                elif model=='pcfg':
                    rawkey=counts_hash(train)
                    if rawkey not in self.raw_cache:
                        words=sorted(train); weights=[train[w] for w in words]
                        total=int(sum(weights));limit=self.cfg['pcfg']['training_limit']
                        if total<=limit:
                            samples=[w for w in words for _ in range(int(train[w]))]
                        else:
                            samples=random.Random(self.cfg['seed']).choices(words,weights=weights,k=limit)
                        raw,meta=self.pcfg.generate_open(samples)
                        self.raw_cache[rawkey]=(raw,{**meta,'training_records_used':len(samples),
                                                   'training_resampled':total>limit})
                    raw,meta=self.raw_cache[rawkey];params.update(meta)
                    source_stop=meta['source_stop'];stream=iter(raw)
                else:raise ValueError('unsupported model')
                prep=time.perf_counter()-prep_start
                result=consume(model,stream,budget=budget,
                               raw_limit=self.cfg['pcfg']['raw_limit'] if model=='pcfg' else g['raw_limit'],
                               timeout_seconds=g['timeout_seconds'],accepts=effective_rule.accepts if effective_rule else lambda _:True,
                               parameters=params,source_stop=source_stop)
                result.stats['preparation_seconds']=round(prep,4)
                result.stats['upstream_raw_count']=params.get('upstream_raw_count')
                self.run_count+=1;runs.append(result)
            except Exception as exc:
                # External stderr may contain passwords. Only safe categories enter reports.
                raise ModelFailure(model,f'{model}: {type(exc).__name__}; 模型运行失败，未用其它模型替代') from exc
        self.cache[key]=runs
        return runs


def point(evaluation,k):
    return next(p for p in evaluation['minauto'] if p['budget']==k)


def dominates(a,b):
    keys=('risk','cost','attempts')
    return (all(a[k]<=b[k]+1e-12 for k in keys) and a['completion']>=b['completion']-1e-12
            and (any(a[k]<b[k]-1e-12 for k in keys) or a['completion']>b['completion']+1e-12))


def pareto(rows):
    valid=[r for r in rows if r['evaluable']]
    return [r['policy']['name'] for r in valid if not any(dominates(o,r) for o in valid if o is not r)]


def choose(rows,requirements,max_rules):
    selections=[]
    for req in requirements:
        feasible=[r for r in rows if r['evaluable'] and r['cost']<=req['max_cost']+1e-12
                  and r['risk']<=req['max_risk']+1e-12 and r['completion']>=req['min_completion']-1e-12
                  and r['attempts']<=req['max_extra_attempts']+1e-12 and r['policy']['rule_count']<=max_rules]
        if not feasible:
            selections.append({'requirement':req,'status':'no_feasible_policy','policy_name':None});continue
        if req['mode']=='risk_target': key=lambda r:(r['cost'],r['attempts'],r['risk'],r['policy']['rule_count'],r['policy']['name'])
        else:key=lambda r:(r['risk'],r['cost'],r['attempts'],r['policy']['rule_count'],r['policy']['name'])
        winner=min(feasible,key=key)
        commons=[r for r in feasible if r['policy']['origin']=='common']
        comparator=min(commons,key=key) if commons else None
        selections.append({'requirement':req,'status':'selected','policy_name':winner['policy']['name'],
                           'validation_risk':winner['risk'],'validation_cost':winner['cost'],
                           'comparator_name':comparator['policy']['name'] if comparator else None})
    return selections


def _conditional_metrics(weights,accepts,hits,attempts,completed,scenario,response_cfg):
    total=float(weights.sum())
    accepted=float(weights@accepts)/total
    cost=1-accepted
    if scenario=='R0':
        denominator=float(weights@accepts)
        risk=float(weights@hits)/denominator if denominator else None
        limit=response_cfg['r0_max_total_attempts']
        completion=(1-(1-accepted)**limit if limit else 1.) if accepted else 0.
        extra=((1-(1-accepted)**limit)/accepted-1 if limit else (1-accepted)/accepted) if accepted else None
    else:
        denominator=float(weights@completed)
        risk=float(weights@hits)/denominator if denominator else None
        completion=denominator/total;extra=float(weights@attempts)/total
    return {'risk':risk,'cost':cost,'completion':completion,'attempts':extra}


def response_vectors(response,runs,k):
    def hit(w):return bool(w is not None and any(r.ranks.get(w,math.inf)<=k for r in runs))
    if response.scenario=='R0':
        words=sorted(response.initial)
        return (np.array([response.initial[w] for w in words],dtype=int),
                np.array([response.rule.accepts(w) for w in words],dtype=float),
                np.array([response.rule.accepts(w) and hit(w) for w in words],dtype=float),
                np.zeros(len(words)),np.ones(len(words)))
    records=response.records
    return (np.array([r[4] for r in records],dtype=int),
            np.array([response.rule.accepts(r[0]) for r in records],dtype=float),
            np.array([hit(r[1]) for r in records],dtype=float),
            np.array([r[2] for r in records],dtype=float),
            np.array([r[1] is not None for r in records],dtype=float))


def paired_comparison(reference,candidate,reference_runs,candidate_runs,k,repetitions,seed):
    av=response_vectors(reference,reference_runs,k);bv=response_vectors(candidate,candidate_runs,k)
    if not np.array_equal(av[0],bv[0]):raise ValueError('配对比较初始队列不一致')
    def metrics(v,weights):return _conditional_metrics(weights,*v[1:],reference.scenario,reference.config)
    obs_a=metrics(av,av[0]);obs_b=metrics(bv,bv[0])
    rng=np.random.default_rng(seed);n=int(av[0].sum());p=av[0]/n;deltas={key:[] for key in obs_a}
    for _ in range(repetitions):
        weights=rng.multinomial(n,p)
        a=metrics(av,weights);b=metrics(bv,weights)
        for key in deltas:
            if a[key] is not None and b[key] is not None:deltas[key].append(a[key]-b[key])
    return {'budget':k,'direction':'common minus selected; positive risk/cost difference favors selected',
            'method':'paired initial-cohort multinomial bootstrap; attacks and selection held fixed',
            'repetitions':repetitions,'confidence':.95,'interval_scope':'pointwise exploratory; no simultaneous/multiple-comparison guarantee',
            'differences':{key:{'estimate':obs_a[key]-obs_b[key] if obs_a[key] is not None and obs_b[key] is not None else None,
                               'lower':float(np.quantile(values,.025)) if values else None,
                               'upper':float(np.quantile(values,.975)) if values else None,
                               'valid_repetitions':len(values)} for key,values in deltas.items()}}


def _run(cfg,dataset,engine,progress):
    splits=dataset['splits'];rules,discovery=discover_rules(splits['train'],cfg)
    response_cache={};evaluations={};internal={};validation=[]
    def respond(rule,scenario,split):
        key=(rule.name,scenario,split)
        if key not in response_cache:
            response_cache[key]=response_for(splits[split],rule,scenario,cfg['response'],
                                            pool=splits['train'],seed=cfg['seed'],split=split)
        return response_cache[key]
    def evaluate(rule,scenario,split,knowledge):
        key=(rule.name,scenario,split,knowledge)
        if key in evaluations:return evaluations[key]
        target=respond(rule,scenario,split)
        if knowledge=='A1':train=respond(rule,scenario,'train').final;tuning=respond(rule,scenario,'tuning').final
        else:train=splits['train'];tuning=splits['tuning']
        runs=engine.attack(train,tuning,rule,knowledge) if target.final and train else []
        ev=evaluate_runs(runs,target.final,cfg['budgets'])
        ev['response']=target.summary;ev['knowledge']=knowledge;ev['scenario']=scenario
        for pt in ev['minauto']:
            pt['completed_and_hit_fraction']=(pt['rate']*target.summary['completion_rate'] if pt['rate'] is not None else None)
        evaluations[key]=ev;internal[key]=(target,runs)
        return ev
    # Validation search precedes EVERY test access.
    for index,rule in enumerate(rules):
        if progress:progress(f'验证策略 {index+1}/{len(rules)}：{rule.name}')
        for scenario in cfg['response']['scenarios']:
            ev=evaluate(rule,scenario,'validation','A1');p=point(ev,cfg['search']['risk_budget']);s=ev['response']
            validation.append({'policy':rule.summary(),'scenario':scenario,'risk':p['rate'],'cost':s['cost'],
                               'completion':s['completion_rate'],'attempts':s['mean_extra_attempts'],
                               'evaluable':p['complete'] and s['feasible_response'] and s['mean_extra_attempts'] is not None,
                               'evaluation':ev})
    primary=cfg['response']['primary'];main=[r for r in validation if r['scenario']==primary]
    selected=choose(main,cfg['search']['requirements'],cfg['search']['max_rules'])
    frozen_selection=fingerprint(selected)
    test_names={r.name for r in rules if r.origin=='common'}|{r['policy_name'] for r in selected if r['policy_name']}
    final=[]
    for rule in rules:
        if rule.name not in test_names:continue
        if progress:progress('最终测试：'+rule.name)
        for scenario in cfg['response']['scenarios']:
            modes={h:evaluate(rule,scenario,'test',h) for h in cfg['knowledge']}
            final.append({'policy':rule.summary(),'scenario':scenario,'evaluations':modes})
    bykey={(r['policy']['name'],r['scenario']):r for r in final}
    for selection in selected:
        name=selection['policy_name'];common=selection.get('comparator_name')
        if not name:continue
        row=bykey[name,primary];ev=row['evaluations']['A1'];p=point(ev,cfg['search']['risk_budget'])
        selection['test']={'risk':p['rate'],'response':ev['response'],'complete':p['complete']}
        if common:
            ref=bykey[common,primary]['evaluations']['A1'];rp=point(ref,cfg['search']['risk_budget'])
            if p['complete'] and rp['complete']:
                a,ar=internal[(common,primary,'test','A1')];b,br=internal[(name,primary,'test','A1')]
                comp=paired_comparison(a,b,ar,br,cfg['search']['risk_budget'],cfg['bootstrap_repetitions'],cfg['seed'])
                selection['comparison']=comp
                gain=comp['differences']['risk'];cost=comp['differences']['cost']
                supported=(name!=common and gain['lower'] is not None and cost['lower'] is not None
                           and gain['valid_repetitions']>=.9*cfg['bootstrap_repetitions']
                           and gain['lower']>=-1e-12 and cost['lower']>=-1e-12
                           and max(gain['lower'],cost['lower'])>cfg['search']['min_gain'])
                selection['status']='supported_improvement' if supported else 'no_supported_improvement'
            else: selection['status']='incomplete_test_budget'
        else:selection['status']='no_feasible_common_comparator'
        req=selection['requirement'];costs=ev['response']
        test_constraints=(p['complete'] and p['rate'] is not None and p['rate']<=req['max_risk']
                          and costs['cost']<=req['max_cost'] and costs['completion_rate']>=req['min_completion']
                          and costs['mean_extra_attempts'] is not None and costs['mean_extra_attempts']<=req['max_extra_attempts'])
        selection['test_requirements_met']=bool(test_constraints)
        if p['complete'] and not test_constraints:selection['status']='test_constraints_not_met'

    # Discovery/fitting consumes train only; held-out fitting internal to train.
    counts=sorted(splits['train'].values(),reverse=True)
    analysis=analyze_counts(counts,q=cfg['discovery']['head_mass'],budget=cfg['search']['risk_budget'],
                            bootstrap_repetitions=cfg['bootstrap_repetitions'],seed=cfg['seed']) if len(counts)>=2 else None
    return {'schema_version':'zipfguard-open-result-v2','protocol_id':PROTOCOL,'budget_unit':BUDGET_UNIT,
            'dataset':{**dataset['metadata'],'sample_occurrences':dataset['sample_occurrences'],
                       'sample_unique':dataset['sample_unique'],'split_hashes':dataset['split_hashes'],
                       'split_sizes':{s:sum(c.values()) for s,c in splits.items()}},
            'analysis':analysis,'discovery':discovery,'validation_candidates':validation,
            'pareto_front':pareto(main),'common_front':pareto([r for r in main if r['policy']['origin']=='common']),
            'recommendations':selected,'test_policies':final,'selection_sha256':frozen_selection,
            'protocol':{'candidate_allowlist':False,'target_used_for_generation':False,'fit_split':'train',
                        'attack_selection_split':'tuning','policy_selection_split':'validation','final_evaluation_split':'test',
                        'primary_response':primary,'primary_knowledge':'A1','primary_budget':cfg['search']['risk_budget'],
                        'budget_interpretation':'per-model distinct eligible checks; not combined total budget',
                        'incomplete_budget':'unresolved target mass; excluded from complete-risk optimization'},
            'metadata':{'participating_attackers':engine.models,'open_runs':engine.run_count,
                        'passllm':'not integrated','knowledge_levels':cfg['knowledge'],
                        'response_scenarios':cfg['response']['scenarios'],
                        'limitations':['同源出现记录划分不能证明跨人群泛化。','用户响应为显式模型，成本是代理指标。',
                                        'Min_auto 只覆盖当前模型族；有限预算未命中不表示永远不可猜中。',
                                        'Bootstrap 条件于固定训练/选择结果，逐点区间未覆盖完整训练搜索不确定性。']}}


def run_open_pipeline(config=None,*,dataset=None,progress=None):
    cfg=validate_open_config(config) if config is not None else load_open_config()
    start=time.perf_counter()
    if dataset is None:
        d=cfg['data'];source=Path(d['path']);source=source if source.is_absolute() else ROOT/source
        if progress:progress('扫描完整真实语料并建立独立划分')
        dataset=load_corpus(source,source_format=d['format'],encoding=d['encoding'],sample_size=d['sample_size'],
                            seed=cfg['seed'],fractions=d['split_fractions'],progress=progress)
    if dataset['metadata'].get('source_format')=='unique_dictionary':
        raise ValueError('去重字典无法提供主实验用户频次；请选择真实出现记录或带频次语料')
    failures=[]
    with tempfile.TemporaryDirectory(prefix='zipfguard_open_') as temporary:
        while True:
            engine=AttackEngine(cfg,temporary,excluded=[f['model'] for f in failures])
            try:result=_run(cfg,dataset,engine,progress);break
            except ModelFailure as exc:
                if cfg['attackers'][exc.model]=='required':raise RuntimeError('必选攻击器失败：'+exc.reason) from exc
                if any(f['model']==exc.model for f in failures):raise
                failures.append({'model':exc.model,'reason':exc.reason,'excluded_from_entire_comparison':True})
                if progress:progress(exc.reason+'；移除该可选模型后重算全部比较')
    # Use the shared manifest without handing it any plaintext or legacy space.
    public_dataset=result['dataset']
    compat={**cfg,'synthetic':{}}
    evidence=manifest(compat,public_dataset,())
    evidence.update(config=cfg,config_sha256=fingerprint(cfg),dataset_version='weighted-corpus-v1',
                    dataset_sha256=fingerprint(dataset['split_hashes']),candidate_space_version='open; no common allowlist',
                    candidate_space_sha256=None,attacker_versions={m:('pcfg-'+EXPECTED_COMMIT if m=='pcfg' else 'open-v1') for m in engine.models})
    result['reproducibility']=evidence
    result['metadata'].update(runtime_seconds=round(time.perf_counter()-start,3),attacker_failures=failures,
                              requested_model_set_complete=not failures,seed=cfg['seed'],budgets=cfg['budgets'])
    json.dumps(result,ensure_ascii=False,allow_nan=False)
    return result
