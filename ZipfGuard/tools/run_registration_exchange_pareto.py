"""Run one paired 100k registration trajectory; historical arms are read only."""
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import hashlib
import json
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.corpus import counts_hash
from policy.registration_joint import JointRule
from experiments.registration_joint_data import load_private, save_private, developer_stream
from experiments.registration_joint_pipeline import write_json, preview_records, reweighted_train, summed_cost, attach_base_cost
from experiments.registration_variable_scope import UnlimitedPreferences, ReusingDualAttackCache, promote_group
from experiments.registration_variable_pipeline import frozen_union, risk
from experiments.registration_exchange_pareto import VERSION, generate_candidates, select_nominee, confirm_nominee, rule_summary
from tools.run_registration_variable_scope import attack_results


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def score_exchange_group(task):
    # Short worker paths avoid the Win32 process cwd limit. Full training hashes
    # remain in every evidence filename; the short directory has a collision guard.
    root = Path(task['root']).parents[3]/'j'/task['key'][:12]
    root.mkdir(parents=True, exist_ok=True)
    identity = root/'training_identity.json'
    if identity.exists():
        assert json.loads(identity.read_text('utf-8'))['sha256']==task['key']
    else:
        write_json(identity,{'sha256':task['key']})
    cache = ReusingDualAttackCache(root,task['cfg'],lambda _:None,[task['root']])
    index=cache.index(task['counts'],purpose='selection')
    cfg=task['cfg'];b=str(cfg['objective_budget'])
    h=risk(index,task['history'],[cfg['objective_budget']]);n=sum(task['history'].values())
    size=task['cohort_size'];items=[]
    for item in task['items']:
        p=risk(index,item['preview_counts'],[cfg['objective_budget']],cfg['preview_users'])
        items.append({'number':item['number'],'score':{
            'predicted_A1':(n*h['rates'][b]+size*p['rates'][b])/(n+size),
            'predicted_outside':(n*h['outside_model_support_fraction']+size*p['outside_model_support_fraction'])/(n+size),
            'frozen_rate':sum(v for w,v in item['preview_counts'].items() if w in task['frozen_ranks'])/cfg['preview_users'],
            'history_actual_dual':h,'preview_actual_dual':p,'training_sha256':task['key']}})
    return {'root':str(root),'key':task['key'],'items':items}


def select(baseline, hold, state, cohorts, data, response, cache, frozen, cfg, seed, t, path, log):
    recent_initial = cohorts[max(0,t-cfg['window']):t]
    recent = sum(state['final_batches'][-cfg['window']:], Counter())
    previews = {phase: preview_records(data['roles']['preview_'+phase], recent_initial,
                                      seed, t, phase, cfg['preview_users'], cfg['recent_weight'])
                for phase in ('select', 'confirm')}
    proposals, screening = generate_candidates(baseline, hold, recent, previews['select'], response,
                                               frozen['ranks'], cfg, seed, log)
    train = data['roles']['train']
    n = len(train)//cfg['batches']
    training = reweighted_train(train[t*n:(t+1)*n], train, recent_initial,
                                seed, t, cfg['recent_weight'])
    groups, rows = {}, []
    for number, item in enumerate(proposals):
        rule = item['rule']
        preview = response.simulate(previews['select'], rule, seed, distances=True)
        transformed = response.simulate(training, rule, seed)
        if transformed['summary']['pending_users'] or preview['summary']['pending_users']:
            raise RuntimeError('Incomplete preview/development training')
        counts = state['dev_history']+transformed['counts']
        item['train_counts'] = counts
        key = counts_hash(counts)
        groups.setdefault(key, {'counts': counts, 'items': []})['items'].append(
            {'number': number, 'preview_counts': preview['counts']})
        rows.append({'number': number, 'family': item['family'], 'roles': item['roles'],
                     'rule': rule_summary(rule), 'cost': preview['summary']})
    tasks = [{'key': key, **group, 'history': state['history'], 'cohort_size': len(cohorts[t]),
              'cfg': cfg, 'root': str(cache.root), 'frozen_ranks': frozen['ranks']}
             for key, group in groups.items()]
    with ProcessPoolExecutor(max_workers=cfg['candidate_workers']) as pool:
        futures = [pool.submit(score_exchange_group, task) for task in tasks]
        for future in as_completed(futures):
            scored = future.result()
            promote_group(scored['root'], cache.root, scored['key'], cfg['objective_budget'])
            for entry in scored['items']:
                rows[entry['number']].update(entry['score'])
            log(f'Batch {t+1}: conditioned dual fit completed for {len(scored["items"])} candidates')
    winner = select_nominee(rows, cfg)
    nominee = winner['number'] if winner else None
    numbers = [x['number'] for x in rows if 'baseline' in x['roles'] or 'hold' in x['roles']]
    if nominee is not None and nominee not in numbers:
        numbers.append(nominee)
    confirmed = []
    for number in numbers:
        item = proposals[number]
        preview = response.simulate(previews['confirm'], item['rule'], seed, distances=True)
        index = cache.index(item['train_counts'], purpose='confirmation')
        h = risk(index, state['history'], cfg['budgets'])
        p = risk(index, preview['counts'], cfg['budgets'], cfg['preview_users'])
        old, size = sum(state['history'].values()), len(cohorts[t])
        b = str(cfg['objective_budget'])
        confirmed.append({'number': number, 'roles': item['roles'], 'family': item['family'],
                          'rule': rule_summary(item['rule']), 'cost': preview['summary'],
                          'predicted_A1': (old*h['rates'][b]+size*p['rates'][b])/(old+size),
                          'predicted_outside': (old*h['outside_model_support_fraction']+size*p['outside_model_support_fraction'])/(old+size),
                          'frozen_rate': sum(v for w,v in preview['counts'].items() if w in frozen['ranks'])/cfg['preview_users'],
                          'history_actual_dual': h, 'preview_actual_dual': p,
                          'training_sha256': counts_hash(item['train_counts'])})
    hold_number = next(x['number'] for x in rows if 'hold' in x['roles'])
    chosen, status = (confirm_nominee(confirmed, nominee, cfg) if nominee is not None
                      else (hold_number, 'hold_no_selection_double_improvement'))
    rule = proposals[chosen]['rule']
    write_json(path/f'candidate_audit_{t+1:02}.json', {
        'status': status, 'screening': screening, 'candidates': rows,
        'nominee': nominee, 'confirmation': confirmed, 'chosen': rule_summary(rule),
        'single_nominee_only': True, 'all_screening_candidates_measure_edit_distance': True, 'baseline_retained_as_candidate_not_mandatory_subset': True,
        'selection_preview_size': cfg['preview_users'], 'confirmation_preview_size': cfg['preview_users']})
    log(f'Batch {t+1}: {status}; hard structures={len(rule.shapes)}, selective={len(getattr(rule,"soft_shapes",()))}')
    return rule, {'batch': t+1, 'status': status, 'updated': rule.signature()!=hold.signature(),
                  'candidate_count': len(rows), 'nominee': nominee}


def run(root, cfg):
    old = Path(cfg['comparison_source'])
    seed, arm = cfg['seeds'][0], cfg['arms'][0]
    saved = load_private(old/'runs/11/segreti_struct_m_nohint/blacklist.pkl.gz')
    r = saved['rule']
    baseline = JointRule(r.minimum, r.common, r.exact, r.shapes, r.k, len(r.shapes))
    assert len(baseline.shapes)==213 and baseline.minimum==8
    partition = old/'private/partition.pkl.gz'
    assert digest(partition)==cfg['paired_partition_sha256']
    target = root/'private/partition.pkl.gz'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(partition,target)
    assert digest(target)==digest(partition)
    data=load_private(target)
    response=UnlimitedPreferences([w for _,w in data['roles']['train']], baseline.minimum,
                                  cfg['weights'],cfg['response_mode'],None)
    records=data['roles'][f'test_{seed}']
    size=cfg['users']//cfg['batches']
    assert len(records)==cfg['users']
    cohorts=[records[t*size:(t+1)*size] for t in range(cfg['batches'])]
    dev=developer_stream(data['roles']['train'],'raw',seed,cfg['batches'],data['median_length'])
    path=root/'runs'/str(seed)/arm
    path.mkdir(parents=True,exist_ok=True)

    def log(message):
        line=time.strftime('%Y-%m-%d %H:%M:%S')+' '+message
        print(line,flush=True)
        with (path/'events.log').open('a',encoding='utf-8') as handle:
            handle.write(line+'\n')
        write_json(root/'status.json',{'stage':'running','last_event':message,
                                      'updated_at':time.strftime('%Y-%m-%d %H:%M:%S')})

    cache=ReusingDualAttackCache(root/'private/attacks'/str(seed)/arm,cfg,log)
    base=JointRule(baseline.minimum,baseline.common)
    # Frozen membership is trained only on public-base development responses.
    base_training=response.simulate(data['roles']['train'],base,seed)
    frozen=frozen_union(cache.index(base_training['counts'],purpose='frozen'))
    checkpoint=path/'state.pkl.gz'
    state=load_private(checkpoint) if checkpoint.exists() else {
        't':0,'rule':baseline,'history':Counter(),'dev_history':Counter(),
        'final_batches':[],'dev_batches':[],'costs':[],'timeline':[],'attempts':[],'decisions':[]}
    baseline_state=load_private(old/'runs/11/segreti_struct_m_nohint/state.pkl.gz')
    for t in range(state['t'],cfg['batches']):
        rule=state['rule']
        if t:
            rule,decision=select(baseline,rule,state,cohorts,data,response,cache,frozen,cfg,seed,t,path,log)
            state['decisions'].append(decision)
        assert rule.minimum==baseline.minimum and rule.common==baseline.common
        actual=response.simulate(cohorts[t],rule,seed,distances=True)
        common=response.simulate(cohorts[t],base,seed)
        attach_base_cost(actual,common)
        developer=response.simulate(dev[t],rule,seed)
        assert actual['summary']['pending_users']==developer['summary']['pending_users']==0
        if t==0:
            assert actual['counts']==baseline_state['batches'][0]
            assert actual['attempts']==baseline_state['attempts'][:size]
            assert developer['counts']==baseline_state['dev_batches'][0]
            log('First batch exactly matches historical Segreti target/development responses')
        state['history'].update(actual['counts']);state['dev_history'].update(developer['counts'])
        state['final_batches'].append(actual['counts']);state['dev_batches'].append(developer['counts'])
        state['costs'].append(actual['summary']);state['attempts'].extend(actual['attempts'])
        state['timeline'].append(rule_summary(rule));state['rule'],state['t']=rule,t+1
        save_private(checkpoint,state)
        log(f'Completed batch {t+1}/10: {sum(state["history"].values()):,} users')
    save_private(path/'developer_counts.pkl.gz',state['dev_history'])
    log('All 100,000 users registered; generating strategy-conditioned final actual attack prefixes')
    index=cache.index(state['dev_history'],purpose='final')
    result={'version':VERSION,'arm':arm,'seed':seed,'status':'complete',
            'cost':summed_cost(state['costs']),'timeline':state['timeline'],'decisions':state['decisions'],
            'A1_training_sha256':counts_hash(state['dev_history']),
            'A1_training_occurrences':sum(state['dev_history'].values()),
            'attack_failures':{},'actual_attacks':attack_results(index.prefixes,state['history'],cfg),
            'final_dual_risk':risk(index,state['history'],cfg['budgets'],cfg['users']),
            'analysis_type':'single paired post-hoc exploration; historical test informed design',
            'new_target_arms':1,'mechanism':{'baseline_first_batch':True,
            'edits':['release','refine','add','exchange'],'screening':'edit-aware Pareto plus sequential composite beam','cost_saving_fraction':cfg['cost_saving_fraction'],
            'costs_measured':['extra_submissions','edit_distance','modification_rate','tail_over_three'],
            'cost_constraints_scope':'selection and confirmation previews; actual costs reported without rollback'}}
    assert result['cost']['completed_users']==cfg['users'] and result['cost']['pending_users']==0
    assert result['A1_training_occurrences']==60000
    write_json(path/'result.json',result)
    write_json(root/'status.json',{'stage':'complete','users':cfg['users'],'audit':'pending'})
    log('New trajectory and final attack evidence complete')
    write_json(root/'status.json',{'stage':'complete','users':cfg['users'],'audit':'pending'})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    import msvcrt
    lock=(root/'runner.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    cfg=json.loads(args.config.read_text('utf-8'))
    assert cfg['users']==100000 and cfg['batches']==10 and cfg['seeds']==[11]
    assert cfg['arms']==['bidirectional_exchange'] and cfg['objective_budget']==1000000
    files=['experiments/registration_exchange_pareto.py','experiments/registration_exchange.py','tools/run_registration_exchange_pareto.py',
           'experiments/registration_variable_scope.py','experiments/registration_variable_pipeline.py',
           'experiments/registration_joint_pipeline.py','experiments/registration_joint_data.py',
           'policy/registration_joint.py','experiments/registration_joint_pilot.py',
           'ai/pcfg_adapter.py','ai/dual_attack.py','ai/pcfg_monte_carlo.py']
    baseline_path=Path(cfg['comparison_source'])/'runs/11/segreti_struct_m_nohint/blacklist.pkl.gz'
    manifest={'version':VERSION,'config':cfg,'source_hashes':{f:digest(ROOT/f) for f in files},
              'baseline_artifact_sha256':digest(baseline_path),'human_verification':'pending',
              'user_scope':'Only one new 100k arm; reuse all historical comparators read-only',
              'analysis_type':'paired post-hoc exploration, not independent validation'}
    p=root/'manifest.json'
    if p.exists():
        assert json.loads(p.read_text('utf-8'))==manifest,'Frozen code/config changed'
    else:
        write_json(p,manifest)
    try:
        run(root,cfg)
    except Exception as exc:
        write_json(root/'status.json',{'stage':'failed','error':str(exc),'traceback':traceback.format_exc()})
        raise


if __name__=='__main__':
    main()
