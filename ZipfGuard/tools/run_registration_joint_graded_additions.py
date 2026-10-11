"""Append user-requested comparisons without altering the running frozen study."""
from pathlib import Path
from collections import Counter
from dataclasses import fields
import argparse, hashlib, json, sys, time, traceback, subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import experiments.registration_joint_pipeline as joint
from experiments.registration_joint_data import load_private, developer_stream, save_private
from experiments.registration_joint_pilot import ReusableAttackCache
from experiments.registration_comparison import (base_rule,response_for,simulate_arm,developer_counts,score_counts)
from experiments.registration_full_additions import segreti_rule,simulate_static,website_candidates
from policy.registration_comparators import CompositionRule
from core.corpus import counts_hash

OLD=ROOT / 'reports/registration_comparison_final_20261011'


def stamp():return time.strftime('%Y-%m-%d %H:%M:%S')


def run_one(root,cfg,extension,seed,arm):
    path=root / 'runs' / str(seed) / arm;path.mkdir(parents=True,exist_ok=True)
    progress=path / 'task_status.json';final=path / 'result.json'
    if final.exists():return
    def event(message):
        line=stamp()+f' [{seed}/{arm}] '+message
        print(line,flush=True)
        with (path / 'events.log').open('a',encoding='utf-8') as f:f.write(line+'\n')
        joint.write_json(progress,{'stage':'running','updated_at':stamp(),'last_event':message})
    data=load_private(root / 'private/partition.pkl.gz')
    records=data['roles'][f'test_{seed}'];n=cfg['users']//cfg['batches']
    cohorts=[records[t*n:(t+1)*n] for t in range(cfg['batches'])]
    dev=developer_stream(data['roles']['train'],'raw',seed,cfg['batches'],data['median_length'])
    website=arm.startswith('github_')
    local_cfg={**cfg,**(extension['website_incremental_limits'] if website else {})}
    cache=ReusableAttackCache(joint.AttackCache(root / 'private/attacks' / str(seed) / arm,local_cfg,event),
                             [OLD / ('private/attacks/website' if website else 'private/attacks/literature') / str(seed)])
    prediction=load_private(OLD / f'private/predictor-{"website" if website else "literature"}-{seed}.pkl.gz')
    metadata={}
    if arm=='github_graded':
        raw_train=Counter(w for _,w in data['roles']['train'])
        frozen=cache.raw(raw_train,cfg['frozen_budget'],'pcfg')
        gate=cache.index(raw_train,cfg['final_samples'],'frozen')
        prefixes={m:cache.raw(prediction,cfg['risk_membership_budget'],m) for m in ('pcfg','omen')}
        assert all(p['complete'] for p in prefixes.values())
        guessed=frozenset(w for p in prefixes.values() for w,r in p['ranks'].items() if r<=cfg['risk_membership_budget']//2)
        original_class,original_candidates=joint.JointRule,joint.candidates
        joint.JointRule=lambda minimum,common:CompositionRule(minimum=minimum,common=common,github=True)
        joint.candidates=lambda base,recent,hold,ks,js:website_candidates(base,recent,hold,ks,js,guessed)
        try:
            result=joint.trajectory('joint',cohorts,dev,data,{**local_cfg,'actual_attacks':False},seed,
                                    {'fixed_pair':[1000,0]},path,cache,frozen,gate,event)
        finally:joint.JointRule, joint.candidates=original_class,original_candidates
        state=load_private(path / 'joint/state.pkl.gz');training=state['dev_history']
        if result['status']!='complete':raise RuntimeError('Website dynamic developer response unavailable')
    elif arm=='malone':
        prefixes={m:cache.raw(prediction,cfg['risk_membership_budget'],m) for m in ('pcfg','omen')}
        grammar,_=cache.grammar(prediction)
        state=simulate_arm('malone',cohorts,data,local_cfg,'literature',seed,{},path,prefixes,grammar,event)
        training=developer_counts(state,'malone',data,local_cfg,'literature',seed,{})
        result={}
        metadata={'source':'Malone and Maher 2012 section 6','counts_all_proposals':True,
                  'reference_fixed_per_user':True,'bootstrap':'Shared first registration batch; all its proposals initialize F'}
    else:
        response=response_for(data,local_cfg)
        base=base_rule(data,'website' if website else 'literature')
        if arm=='github_core':rule=CompositionRule(minimum=8,github=True)
        elif arm=='github_base':rule=base
        else:
            prefix=cache.raw(prediction,extension['segreti_candidate_prefix_budget'],'pcfg')
            assert prefix['complete']
            rule,metadata=segreti_rule(base,prefix,extension['segreti_compliant_guesses'],path / 'blacklist.pkl.gz')
            joint.write_json(path / 'mechanism.json',metadata)
        state=simulate_static(cohorts,dev,response,base,rule,seed,path,event)
        training=state['dev_history'];result={}
    save_private(path / 'developer_counts.pkl.gz',training)
    prefixes={m:cache.raw(training,max(cfg['budgets']),m) for m in ('pcfg','omen')}
    if not all(p['complete'] for p in prefixes.values()):raise RuntimeError('Actual attack prefix incomplete')
    cost=joint.summed_cost(state['costs'])
    result={**result,'arm':arm,'seed':seed,'experiment':'website' if website else 'literature',
            'status':'complete','cost':cost,'timeline':state['timeline'],'decisions':state['decisions'],
            'A1_training_sha256':counts_hash(training),'A1_training_occurrences':sum(training.values()),
            'eligibility_failures':joint.cost_failures(cost,local_cfg),'attack_failures':{},'mechanism':metadata,
            'actual_attacks':{m:joint.observed(p,state['history'],cfg['budgets'],cfg['users']) for m,p in prefixes.items()}}
    result['actual_attacks']['union_equal_total_B']={'estimated':False,'points':[
        {'budget':b,'per_model_budget':b//2,'max_total_attempts':b,'hits':round(r*cfg['users']),
         'initial_users':cfg['users'],'rate_per_initial_user':r} for b,r in
         zip(cfg['budgets'],score_counts(state['history'],prefixes,cfg['budgets'],cfg['users']))]}
    # Cost failures are results, not grounds to weaken or omit a literature rule.
    if cost['pending_users']:result['interpretation']='Pending targets count in denominator, not proved safe'
    joint.write_json(final,result)
    joint.write_json(progress,{'stage':'complete','completed_at':stamp(),'result':str(final)})
    event('Result saved; cost '+('eligible' if not result['eligibility_failures'] else 'ineligible'))
    joint.write_json(progress,{'stage':'complete','completed_at':stamp(),'result':str(final)})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args()
    root=Path(args.root).resolve()
    import msvcrt
    lock=(root / 'additions.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    base_manifest=json.loads((root / 'manifest.json').read_text('utf-8'));cfg=base_manifest['config']
    extension=json.loads((ROOT / 'configs/registration_joint_graded_additions.json').read_text('utf-8'))
    files=[Path(__file__),ROOT / 'experiments/registration_full_additions.py',ROOT / 'tools/report_registration_joint_graded_combined.py']
    identity={'version':'report-additions-v1','settings':extension,
              'parent_manifest_sha256':hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
              'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              'targets':'Reuse exact fresh test roles already frozen for current full study',
              'user_authorized':'Add Malone-Maher and Segreti alongside Schechter, plus one common website',
              'data_timing':'Scope extension after parent study began; no new tuning on observed target results',
              'paper_scope':'Mechanism reproduction with explicit shared-simulation adaptations; not original human study replication',
              'website':'GitHub public composition core, base with common blacklist, dynamic extension under same base',
              'website_limits':'Incremental to GitHub+common base, following existing website experiment',
              'sources':[
                 {'url':'https://www.maths.tcd.ie/~dwmalone/p/www2012.pdf','locator':'Section 6, PDF p.9','source_opened':True,'human_verification':'pending'},
                 {'url':'https://www.usenix.org/system/files/conference/soups2017/soups2017-segreti.pdf','locator':'Sections 3.3 and 3.4, StructMNoHint','source_opened':True,'human_verification':'pending'},
                 {'url':'https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-strong-password','locator':'Public length/category rules and weak-password check statement, accessed 2026-10-11','source_opened':True,'human_verification':'pending'}]}
    manifest=root / 'additions_manifest.json'
    if manifest.exists() and json.loads(manifest.read_text('utf-8'))!=identity:raise RuntimeError('Frozen additions changed; no silent resume')
    joint.write_json(manifest,identity)
    tasks=[(seed,arm) for arm in extension['literature_arms']+extension['website_arms'] for seed in cfg['seeds']]
    status={'stage':'running','started_at':stamp(),'total_trajectories':len(tasks),'completed':[],
            'combined_total_trajectories':len(tasks)+len(cfg['arms'])*len(cfg['seeds']),'workers':1,'failures':[]}
    joint.write_json(root / 'additions_status.json',status)
    for seed,arm in tasks:
        status.update(active=f'{seed}/{arm}',updated_at=stamp());joint.write_json(root / 'additions_status.json',status)
        try:
            run_one(root,cfg,extension,seed,arm);status['completed'].append(f'{seed}/{arm}')
        except Exception as exc:
            status['failures'].append({'task':f'{seed}/{arm}','error':str(exc),'traceback':traceback.format_exc()})
        status.update(updated_at=stamp());joint.write_json(root / 'additions_status.json',status)
    subprocess.run([sys.executable,str(ROOT / 'tools/report_registration_joint_graded_combined.py'),str(root),'--partial'],check=True)
    if status['failures']:
        status.update(stage='failed');joint.write_json(root / 'additions_status.json',status);raise RuntimeError('Addition jobs failed; checkpoints retained')
    status.update(stage='waiting_for_parent');joint.write_json(root / 'additions_status.json',status)
    while True:
        parent=json.loads((root / 'status.json').read_text('utf-8'))
        if parent['stage']=='complete':break
        if parent['stage']=='failed':
            status.update(stage='parent_failed');joint.write_json(root / 'additions_status.json',status);return
        time.sleep(30)
    subprocess.run([sys.executable,str(ROOT / 'tools/report_registration_joint_graded_combined.py'),str(root)],check=True)
    status.update(stage='complete',completed_at=stamp());joint.write_json(root / 'additions_status.json',status)


if __name__=='__main__':main()
