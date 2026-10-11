"""Isolated paired experiments with real-corpus starts and simulated responses."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from core.registration import load_registration
from core.intervention_state import Population
from core.intervention_risk import InterventionRisk
from core.intervention_acceptance import apply_response_policy, INDIVIDUAL_POLICY
from core.corpus import counts_hash
from experiments.contextual_config import validate_config
from experiments.intervention_config import ROOT
from experiments.intervention_pipeline import make_index, reference_replay, run_arm
from policy.intervention_response import InterventionResponder, IndividualThresholdUnavailable
from policy.local_actions import generate_actions, capacities
from policy.contextual_controller import LinearModel, MeteredResponder, choose, distance


def expand(counts):
    return [w for w, n in sorted(counts.items()) for _ in range(n)]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)


def common_start(words, prefix, train, risk, cfg, stream):
    pop = Population(words, prefix)
    bootstrap = deepcopy(cfg)
    bootstrap['controller'].update(round_fraction=1., total_fraction=1.)
    responder = InterventionResponder(train, cfg['response'])
    actions = generate_actions(pop, risk, bootstrap, fixed=True)
    if actions:
        pop.apply(responder.respond(pop, actions[0], cfg['seed'], stream), phase='google')
    assert all(len(a.password) >= 8 for a in pop.accounts)
    return pop


def run_new(method, initial, reference_initial, risk, inner, cfg, opt, model, *,
            stream='target', progress=print):
    pop = initial.clone()
    ref = reference_initial.clone() if reference_initial else None
    meter = MeteredResponder(inner)
    initial_d = distance(pop.counts())
    trajectory = [{'round': 0, 'distance': initial_d, 'notified': 0}]
    rounds = []
    started = time.perf_counter()
    stop = 'max_rounds'
    if initial_d <= 0:
        stop = 'zero_initial_distance'
    else:
        for t in range(1,cfg['controller']['max_rounds']+1):
            if not capacities(pop,cfg):
                stop='budget_exhausted'; break
            winner,audit=choose(pop,risk,meter,cfg,opt,model,method,t,stream)
            if winner is None:
                stop=('no_legal_candidate' if not audit['candidate_count'] else
                      'insufficient_evidence' if opt['mode']=='strict' else 'no_validated_positive_gain')
                rounds.append({'round':t,'issued':False,'audit':audit})
                break
            action,x,prediction=winner
            before=distance(pop.counts())
            assert len(action.indices)<=capacities(pop,cfg)
            try:
                rows=meter.respond(pop,action,cfg['seed'],f'{stream}:execute:{t}')
            except IndividualThresholdUnavailable:
                rounds.append({'round':t,'issued':False,'audit':audit,'execution_failure':True})
                stop='execution_construction_failed'; break
            if any(len(r['new'])<8 or not action.rule.accepts(r['new']) for r in rows):
                raise AssertionError('Invalid actual rule response')
            rows,gate=apply_response_policy(pop,rows,risk,cfg['budgets'],INDIVIDUAL_POLICY)
            if not gate['accepted']:
                stop='execution_gate_rejected'; break
            assert len(rows)==len(action.indices)
            hits_before=sum(risk.hit(a.password) for a in pop.accounts)
            removed=sum(risk.hit(r['old']) for r in rows)
            pop.apply(rows)
            assert hits_before-sum(risk.hit(a.password) for a in pop.accounts)==removed
            assert all(a.adaptive_notifications<=1 for a in pop.accounts)
            after=distance(pop.counts())
            if method in ('bandit','greedy'):
                model.update(x,(before-after)/initial_d)
            replay=reference_replay(ref,action,pop.total,inner,cfg,t) if ref else None
            row={'round':t,'issued':True,'action':action.public(),'audit':audit,
                 'predicted_gain':prediction['mean_gain'],'actual_gain':before-after,
                 'gate':gate,'reference_replay':replay,
                 'completion_count':sum(bool(r.get('explicit_completion')) for r in rows),
                 'edit_cost_sum':sum(r['edit_cost'] for r in rows)}
            rounds.append(row)
            trajectory.append({'round':t,'distance':after,'notified':pop.ledger()['adaptive_affected']})
            progress(f'{stream} {method} round={t} D={after:.8f} notified={trajectory[-1]["notified"]}')
    summary={'method':method,'initial_distance':initial_d,'final_distance':distance(pop.counts()),
             'ledger':pop.ledger(),'stop_reason':stop,'trajectory':trajectory,'rounds':rounds,
             'controller_seconds':time.perf_counter()-started,'response_calls':meter.calls,
             'preview_calls':meter.preview_calls,'response_account_attempts':meter.account_attempts,
             'simulation_calls':sum(r['audit']['simulation_calls'] for r in rounds),
             'learning_observations':model.observations,
             'reference_ledger':ref.ledger() if ref else None}
    return summary,pop.counts(),ref.counts() if ref else None


def run_contextual(config, output_dir, progress=print, dataset=None, index=None):
    validated=validate_config(config)
    cfg,opt=validated['intervention'],validated['learning']
    out=Path(output_dir)
    if (out/'report.json').exists():
        raise FileExistsError('Completed result exists; choose a new output directory')
    started=time.perf_counter()
    d=cfg['data']
    if dataset is None:
        source=Path(d['path'])
        dataset=load_registration(source if source.is_absolute() else ROOT/source,
                                  source_format=d['format'],encoding=d['encoding'],users=d['users'],
                                  development=d['development'],seed=cfg['seed'],cohort_size=d['users'],progress=progress)
    train=Counter(dataset['development']['train'])
    progress('Preparing frozen PCFG + author OMEN prefixes')
    frozen=index if index is not None else make_index(train,cfg)
    risk=InterventionRisk(frozen,cfg['budgets'],cfg['risk_budget'])
    if not getattr(frozen,'dual_attack',False):
        raise ValueError('Actual dual-prefix index required')
    inner=InterventionResponder(train,dict(cfg['response'],security_threshold=cfg['attack_models']['threshold']),risk)
    initial=common_start([w for batch in dataset['cohorts'] for w in batch],'account',train,risk,cfg,'google-baseline-target')
    if initial.total != d['users']:
        raise ValueError('Account count differs from configuration')
    reference=common_start(expand(train),'google-baseline-reference',train,risk,cfg,'google-baseline-reference')
    cfg['controller']['policy_floor_minimum_length']=8
    # Pretraining only on disjoint development tuning occurrences; never target outcomes.
    warm=common_start(expand(dataset['development']['tuning']),'warmup',train,risk,cfg,'warmup:baseline')
    model=LinearModel(ridge=opt['ridge'])
    warm_cfg=deepcopy(cfg)
    warm_cfg['controller']['max_rounds']=max(1,opt['warmup_rounds'])
    warm_cfg['controller']['round_fraction']=max(cfg['controller']['round_fraction'],1/warm.total)
    warm_summary=None
    if opt['warmup_rounds']:
        progress('Pretraining on disjoint development tuning population')
        warm_summary,_,_=run_new('bandit',warm,None,risk,inner,warm_cfg,opt,model,stream='warmup',progress=progress)
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
             for folder in ('core','policy','experiments','ai') for p in (ROOT/folder).glob('*.py')}
    report={'protocol':opt['protocol'],'configuration':validated,'dataset':dataset['metadata'],
            'source_hashes':sources,'start_account_hash':initial.account_fingerprint(),
            'start_distance':distance(initial.counts()),'baseline_ledger':initial.ledger(),
            'warmup':warm_summary,'frozen_attack_metadata':frozen.metadata,
            'budget_unit':'B raw attempts per model, union at most 2B',
            'fair_comparison':'Same per-round simulation caps for heuristic/random/greedy/bandit; legacy uncapped reference',
            'response_scope':'Real frequency initial sample; simulated all-notified-success responses',
            'arms':{},'status':'running'}
    write_json(out/'progress.json',report)
    adaptive_cache={}

    def attacks(summary,counts,refcounts):
        summary['F']=risk.evaluate(counts)
        summary['A1']=None
        if cfg['evaluation']['adaptive']:
            progress(summary['method']+' independent-reference A1 retraining')
            key=counts_hash(refcounts)
            if key not in adaptive_cache:
                adaptive_cache[key]=make_index(refcounts,cfg)
            adaptive=adaptive_cache[key]
            summary['A1']=InterventionRisk(adaptive,cfg['budgets'],cfg['risk_budget']).evaluate(counts)
            summary['A1_training_hash']=key
            summary['A1_metadata']=adaptive.metadata
        report['arms'][summary['method']]=summary
        write_json(out/'progress.json',report)

    for method in opt['methods']:
        progress('Starting paired arm '+method)
        summary,counts,refcounts=run_new(method,initial,reference,risk,inner,cfg,opt,model.clone(),progress=progress)
        attacks(summary,counts,refcounts)
    if opt['legacy']:
        progress('Original controller reference (uncapped search; unchanged algorithm)')
        metered=MeteredResponder(inner)
        legacy_cfg=deepcopy(cfg)
        # Disable the unrelated risk-target stopping branch; preserve selector.
        legacy_cfg['controller']['target_relative_reduction']=1.
        tick=time.perf_counter()
        result,counts,refcounts=run_arm('dynamic',initial,expand(train),risk,metered,legacy_cfg,
                                      progress=progress,reference_initial=reference)
        summary={'method':'legacy','initial_distance':distance(initial.counts()),
                 'final_distance':distance(counts),'ledger':result['final']['ledger'],
                 'stop_reason':result['stop_reason'],'controller_seconds':time.perf_counter()-tick,
                 'response_calls':metered.calls,'response_account_attempts':metered.account_attempts,
                 'preview_calls':metered.preview_calls,'simulation_calls':None,
                 'reference_ledger':result['adaptive_reference_ledger'],
                 'original_result':result,'comparison_scope':'uncapped-original-selector reference'}
        attacks(summary,counts,refcounts)
    report.update(status='complete',runtime_seconds=time.perf_counter()-started)
    write_json(out/'report.json',report)
    (out/'report.json.sha256').write_text(hashlib.sha256((out/'report.json').read_bytes()).hexdigest()+'\n',encoding='ascii')
    return report
