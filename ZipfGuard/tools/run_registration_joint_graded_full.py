"""Frozen, resumable 100k paired study; only report-relevant trajectories.

Fresh targets exclude ALL earlier occurrence-role partitions. Prior development
models/parameters can be reused; old final outcomes never become new results.
"""
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse, bisect, csv, hashlib, json, random, sys, time, traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import experiments.registration_joint_pipeline as joint
from experiments.registration_joint_data import load_private, save_private, developer_stream
from experiments.registration_joint_pilot import graded_candidates, ReusableAttackCache
from experiments.registration_comparison import (simulate_arm, developer_counts, score_counts,
                                                  response_for, base_rule)
from core.corpus import parse_record, counts_hash

OLD = ROOT / 'reports/registration_comparison_final_20261011'
ORIGINAL = ROOT / 'reports/registration_joint_minimal_20261011'


def stamp():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def log(message):
    print(stamp()+' '+message, flush=True)


def prepare(root, cfg):
    path = root / 'private/partition.pkl.gz'
    if path.exists():
        return
    old = load_private(OLD / 'private/partition.pkl.gz')
    initial = load_private(ORIGINAL / 'private/partition.pkl.gz')
    excluded = {i for dataset in (old,initial) for records in dataset['roles'].values() for i,_ in records}
    total = initial['metadata']['valid_ASCII_occurrences']
    used = set(excluded)
    rng = random.Random(cfg['fresh_split_seed'])
    chosen = []
    required = cfg['users']*len(cfg['seeds'])
    while len(chosen) < required:
        i = rng.randrange(total)
        if i not in used:
            used.add(i); chosen.append(i)
    allocation = {i: f'test_{seed}' for s,seed in enumerate(cfg['seeds'])
                  for i in chosen[s*cfg['users']:(s+1)*cfg['users']]}
    ids = sorted(chosen)
    roles = {f'test_{seed}':[] for seed in cfg['seeds']}
    digest = hashlib.sha256()
    cursor = offset = rows = 0
    with (ROOT.parent / cfg['source']).open('rb') as f:
        for raw in f:
            digest.update(raw); rows += 1
            try: word,count = parse_record(raw,'password_with_count','latin-1')
            except ValueError: continue
            if any(not 32 <= ord(c) <= 126 for c in word): continue
            end = bisect.bisect_left(ids,offset+count,cursor)
            for i in ids[cursor:end]: roles[allocation[i]].append((i,word))
            cursor,offset = end,offset+count
            if rows%2000000 == 0: log(f'Fresh partition: scanned {rows:,} source rows')
    assert digest.hexdigest() == initial['metadata']['source_sha256'] == old['metadata']['source_sha256']
    assert cursor == len(ids) and offset == total
    for records in roles.values():
        assert len(records)==cfg['users']
        rng.shuffle(records)
    roles.update({r:old['roles'][r] for r in ('train','tuning','preview_select','preview_confirm')})
    metadata = {'created_at':stamp(),'source_sha256':digest.hexdigest(),
                'fresh_split_seed':cfg['fresh_split_seed'],'excluded_previous_occurrences':len(excluded),
                'all_new_target_occurrences_disjoint':True,'target_seeds_disjoint':True,
                'development_roles_reused':True,'pilot_occurrences_excluded':True,
                'role_sizes':{r:len(v) for r,v in roles.items()},
                'position_hashes':{r:hashlib.sha256(','.join(map(str,sorted(i for i,_ in v))).encode()).hexdigest()
                                   for r,v in roles.items()},
                'interpretation':'Frequency-declared occurrence users, not verified independent persons'}
    save_private(path,{'roles':roles,'median_length':old['median_length'],'metadata':metadata})
    joint.write_json(root / 'data_manifest.json',metadata)


def run_one(task):
    root,cfg,seed,arm = Path(task['root']),task['cfg'],task['seed'],task['arm']
    path = root / 'runs' / str(seed) / arm
    path.mkdir(parents=True,exist_ok=True)
    progress = path / 'task_status.json'
    def event(message):
        line = stamp()+f' [{seed}/{arm}] '+message
        print(line,flush=True)
        with (path / 'events.log').open('a',encoding='utf-8') as f: f.write(line+'\n')
        joint.write_json(progress,{'stage':'running','last_event':message,'updated_at':stamp()})
    final = path / 'result.json'
    if final.exists():
        result=json.loads(final.read_text('utf-8'))
        if result['status']=='complete' and not result.get('attack_failures'): return str(final)
    try:
        data=load_private(root / 'private/partition.pkl.gz')
        records=data['roles'][f'test_{seed}']
        size=cfg['users']//cfg['batches']
        cohorts=[records[t*size:(t+1)*size] for t in range(cfg['batches'])]
        cache=ReusableAttackCache(joint.AttackCache(root / 'private/attacks' / str(seed) / arm,cfg,event),
                                 [OLD / 'private/attacks/literature' / str(seed)])
        raw_train=Counter(w for _,w in data['roles']['train'])
        frozen=cache.raw(raw_train,cfg['frozen_budget'],'pcfg')
        assert frozen['complete']
        gate=cache.index(raw_train,cfg['final_samples'],'frozen')
        if arm in ('joint','graded'):
            dev=developer_stream(data['roles']['train'],'raw',seed,cfg['batches'],data['median_length'])
            original_candidates=joint.candidates
            if arm=='graded':
                # The risk set is frozen at the pilot's total 100k budget.
                prediction=load_private(OLD / f'private/predictor-literature-{seed}.pkl.gz')
                prefixes={m:cache.raw(prediction,cfg['risk_membership_budget'],m) for m in ('pcfg','omen')}
                assert all(p['complete'] for p in prefixes.values())
                guessed=frozenset(w for p in prefixes.values() for w,r in p['ranks'].items()
                                  if r <= cfg['risk_membership_budget']//2)
                joint.candidates=lambda base,recent,hold,ks,js:graded_candidates(base,recent,hold,ks,js,guessed)
            try:
                legacy=path / 'joint/result.json'
                if legacy.exists():
                    saved=json.loads(legacy.read_text('utf-8'))
                    if saved['status']!='complete' or saved.get('attack_failures'):
                        legacy.rename(legacy.with_name(f'result_incomplete_{time.time_ns()}.json'))
                # Skip redundant frozen-model final attacks and per-cohort fits;
                # execute only adapted final attacks used in the report below.
                result=joint.trajectory('joint',cohorts,dev,data,{**cfg,'actual_attacks':False},seed,{'fixed_pair':[1000,0]},
                                        path,cache,frozen,gate,event)
            finally: joint.candidates=original_candidates
            result={**result,'arm':arm,'source_result':str(legacy)}
            state=load_private(path / 'joint/state.pkl.gz')
            training=state['dev_history']
            prefixes={m:cache.raw(training,max(cfg['budgets']),m) for m in ('pcfg','omen')}
            assert all(p['complete'] for p in prefixes.values())
            result['actual_attacks']={m:joint.observed(p,state['history'],cfg['budgets'],cfg['users']) for m,p in prefixes.items()}
            result['actual_attacks']['union_equal_total_B']={'estimated':False,'points':[
                {'budget':b,'per_model_budget':b//2,'max_total_attempts':b,'hits':round(r*cfg['users']),
                 'initial_users':cfg['users'],'rate_per_initial_user':r} for b,r in
                 zip(cfg['budgets'],score_counts(state['history'],prefixes,cfg['budgets'],cfg['users']))]}
        else:
            rate=cfg['schechter_rates'][arm]
            prediction=load_private(OLD / f'private/predictor-literature-{seed}.pkl.gz')
            selection={m:cache.raw(prediction,cfg['risk_membership_budget'],m) for m in ('pcfg','omen')}
            grammar,_=cache.grammar(prediction)
            state=simulate_arm('schechter',cohorts,data,cfg,'literature',seed,{'rate':rate},path,
                               selection,grammar,event)
            training=developer_counts(state,'schechter',data,cfg,'literature',seed,{'rate':rate})
            save_private(path / 'developer_counts.pkl.gz',training)
            prefixes={m:cache.raw(training,max(cfg['budgets']),m) for m in ('pcfg','omen')}
            assert all(p['complete'] for p in prefixes.values())
            cost=joint.summed_cost(state['costs'])
            result={'arm':arm,'seed':seed,'status':'complete','parameters':{'rate':rate},
                    'cost':cost,'timeline':state['timeline'],'decisions':state['decisions'],
                    'A1_training_sha256':counts_hash(training),'A1_training_occurrences':sum(training.values()),
                    'eligibility_failures':joint.cost_failures(cost,cfg),'attack_failures':{},
                    'actual_attacks':{m:joint.observed(p,state['history'],cfg['budgets'],cfg['users']) for m,p in prefixes.items()}}
            result['actual_attacks']['union_equal_total_B']={'estimated':False,'points':[
                {'budget':b,'per_model_budget':b//2,'max_total_attempts':b,'hits':round(r*cfg['users']),
                 'initial_users':cfg['users'],'rate_per_initial_user':r} for b,r in
                 zip(cfg['budgets'],score_counts(state['history'],prefixes,cfg['budgets'],cfg['users']))]}
        if result['status']!='complete' or result.get('attack_failures'):
            raise RuntimeError(f'Incomplete observed attacks: {result.get("attack_failures")}')
        if result['cost']['completed_users']!=cfg['users'] or result['cost']['pending_users']:
            raise RuntimeError('Target users incomplete; do not present a full result')
        joint.write_json(final,result)
        joint.write_json(progress,{'stage':'complete','completed_at':stamp(),'result':str(final)})
        event('Result saved; costs '+('eligible' if not result['eligibility_failures'] else 'ineligible'))
        joint.write_json(progress,{'stage':'complete','completed_at':stamp(),'result':str(final)})
        return str(final)
    except Exception as exc:
        joint.write_json(progress,{'stage':'failed','updated_at':stamp(),'error':str(exc),'traceback':traceback.format_exc()})
        raise


def export(root,cfg,complete=False):
    rows=[]
    for seed in cfg['seeds']:
        for arm in cfg['arms']:
            p=root / 'runs' / str(seed) / arm / 'result.json'
            if not p.exists(): continue
            result=json.loads(p.read_text('utf-8'))
            for point in result['actual_attacks']['union_equal_total_B']['points']:
                rows.append({'seed':seed,'arm':arm,'users':cfg['users'],'budget':point['budget'],
                             'hits':point['hits'],'rate':point['rate_per_initial_user'],
                             'extra':result['cost']['mean_extra_submissions'],
                             'modification':result['cost']['modification_rate'],
                             'more_than_three':result['cost']['more_than_three_fraction'],
                             'eligible':not result['eligibility_failures'],
                             'whole_shape_batches':sum(t['shape_size']>0 for t in result['timeline']),
                             'soft_shape_batches':sum(t.get('soft_shape_size',0)>0 for t in result['timeline'])})
    if rows:
        with (root / 'results.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    if complete:
        import subprocess
        subprocess.run([sys.executable,str(ROOT / 'tools/report_registration_joint_graded_full.py'),str(root)],check=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args()
    root=Path(args.output).resolve();root.mkdir(parents=True,exist_ok=True)
    import msvcrt
    lock=(root / 'runner.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    cfg=json.loads(Path(args.config).read_text('utf-8'))
    assert cfg['users']%cfg['batches']==0
    files=[Path(__file__),ROOT / 'tools/report_registration_joint_graded_full.py',
           ROOT / 'experiments/registration_joint_pilot.py', ROOT / 'experiments/registration_joint_pipeline.py',
           ROOT / 'experiments/registration_comparison.py', ROOT / 'experiments/registration_joint_data.py',
           ROOT / 'policy/registration_joint.py',ROOT / 'policy/registration_comparators.py',
           ROOT / 'ai/dual_attack.py',ROOT / 'ai/pcfg_adapter.py',ROOT / 'ai/pcfg_monte_carlo.py']
    identity={'version':'graded-registration-fresh-full-v1','config':cfg,
              'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              'prior_development_parameter_file_sha256':hashlib.sha256((OLD / 'frozen_parameters/literature.json').read_bytes()).hexdigest(),
              'prior_outcomes_observed':'pilot and previous studies are development information; fresh target occurrences required',
              'scope':'25 report trajectories; three methods with three Schechter cost points; no sensitivity grid',
              'primary':'union total 100k actual attempts','secondary':'union total 1m actual attempts',
              'comparison':'shared cost limits; report actual costs and paired per-seed differences; no guaranteed superiority',
              'cost_curve':'three prespecified thresholds; no interpolation presented as a newly executed cost-matched method',
              'contribution_boundary':'graded arm changes risk ordering and structure-internal restrictions together; no isolated attribution'}
    manifest=root / 'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text('utf-8'))!=identity:
        raise RuntimeError('Frozen implementation/config changed; cannot silently resume')
    joint.write_json(manifest,identity)
    tasks=[{'root':str(root),'cfg':cfg,'seed':seed,'arm':arm} for seed in cfg['seeds'] for arm in cfg['arms']]
    completed=[f"{t['seed']}/{t['arm']}" for t in tasks if (root / 'runs' / str(t['seed']) / t['arm'] / 'result.json').exists()]
    status={'stage':'preparing','started_at':stamp(),'updated_at':stamp(),'completed':completed,
            'total_trajectories':len(tasks),'workers':args.workers,'users_per_trajectory':cfg['users'],'batches':cfg['batches']}
    joint.write_json(root / 'status.json',status)
    prepare(root,cfg)
    if not (root / 'test_opening.json').exists():
        joint.write_json(root / 'test_opening.json',{'opened_at':stamp(),'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
                          'parameters_frozen_before_fresh_target_access':True,'policy_tuned_on_this_test':False})
    todo=[t for t in tasks if f"{t['seed']}/{t['arm']}" not in completed]
    # Cheap baselines produce report evidence first. Separate worker processes
    # keep the graded candidate callback from affecting any other method.
    todo.sort(key=lambda t:(0 if t['arm'].startswith('schechter') else 1,cfg['seeds'].index(t['seed']),cfg['arms'].index(t['arm'])))
    status.update(stage='running',queued=[f"{t['seed']}/{t['arm']}" for t in todo],updated_at=stamp())
    joint.write_json(root / 'status.json',status)
    failures=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(run_one,t):t for t in todo}
        for future in as_completed(futures):
            task=futures[future];key=f"{task['seed']}/{task['arm']}"
            try:
                future.result();status['completed'].append(key);export(root,cfg)
            except Exception as exc:
                failures.append({'task':key,'error':str(exc)})
            status['queued'].remove(key);status.update(updated_at=stamp(),failures=failures)
            joint.write_json(root / 'status.json',status)
            log(f'Finished {key}; {len(status["completed"])}/{len(tasks)} results')
    if failures:
        status.update(stage='failed',updated_at=stamp());joint.write_json(root / 'status.json',status)
        raise RuntimeError(f'{len(failures)} jobs failed; checkpoints retained')
    status.update(stage='reporting',updated_at=stamp());joint.write_json(root / 'status.json',status)
    export(root,cfg,complete=True)
    status.update(stage='complete',completed_at=stamp());joint.write_json(root / 'status.json',status)
    log('All report data and figures complete')


if __name__=='__main__':
    main()
