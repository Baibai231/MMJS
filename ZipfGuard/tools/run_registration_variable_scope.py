"""Fresh paired users, actual dual-model selection, literature/site comparators."""
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import bisect
import csv
import hashlib
import json
import random
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import experiments.registration_variable_pipeline as dual
from experiments.registration_joint_data import load_private, save_private, developer_stream
from experiments.registration_variable_scope import scoped_candidates, ReusingDualAttackCache, UnlimitedPreferences
from experiments.registration_comparison import simulate_arm, developer_counts, response_for, base_rule, predictor
from experiments.registration_full_additions import segreti_rule, simulate_static
from policy.registration_comparators import CompositionRule
from core.corpus import counts_hash, parse_record

OLD = ROOT/'reports/registration_comparison_final_20261011'
INITIAL = ROOT/'reports/registration_joint_minimal_20261011'


def stamp():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def prepare(root, cfg):
    path = root/'private/partition.pkl.gz'
    if path.exists():
        return
    source_root = Path(cfg['comparison_source'])
    source = source_root/'private/partition.pkl.gz'
    source_manifest = json.loads((source_root/'manifest.json').read_text('utf-8'))
    if hashlib.sha256(source.read_bytes()).hexdigest() != cfg['paired_partition_sha256']:
        raise ValueError('Paired target/development partition changed')
    for relative, expected in source_manifest['source_hashes'].items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen original source changed: '+relative)
    import shutil
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, path)
    data = load_private(path)
    dual.write_json(root/'data_manifest.json', {
        'paired_to': str(source_root), 'partition_sha256': cfg['paired_partition_sha256'],
        'same_target_and_development_roles': True, 'original_metadata': data['metadata'],
        'analysis_type': 'post-hoc strategy revision; paired descriptive comparison',
        'previous_target_results_informed_revision': True})


def predictor(data, cfg, experiment, seed, cache, root, log):
    path = root/'private'/f'predictor-{experiment}-{seed}.pkl.gz'
    if path.exists():
        counts = load_private(path)
    else:
        response = UnlimitedPreferences([w for _, w in data['roles']['train']], cfg['minimum'],
                                        cfg['weights'], cfg['response_mode'], None)
        output = response.simulate(data['roles']['train'], base_rule(data, experiment), seed)
        if output['summary']['pending_users']:
            raise RuntimeError('Incomplete base predictor users')
        counts = output['counts']
        save_private(path, counts)
    prefixes = {m: cache.raw(counts, max(cfg['budgets']), m) for m in dual.MODELS}
    if not all(p['complete'] for p in prefixes.values()):
        raise RuntimeError('Incomplete predictor prefix')
    grammar, _ = cache.grammar(counts)
    return prefixes, grammar


def attack_results(prefixes, counts, cfg):
    total = cfg['users']
    results = {m: dual.observed(p, counts, cfg['budgets'], total) for m, p in prefixes.items()}
    for name, divisor in [('union_each_model_B', 1), ('union_equal_total_B', 2)]:
        points = []
        for b in cfg['budgets']:
            per = b//divisor
            hits = sum(n for w, n in counts.items() if any(p['ranks'].get(w, per+1) <= per for p in prefixes.values()))
            points.append({'budget': b, 'per_model_budget': per, 'max_total_attempts': 2*per,
                           'hits': hits, 'initial_users': total, 'rate_per_initial_user': hits/total})
        results[name] = {'estimated': False, 'points': points}
    return results


def run_one(task):
    root, cfg, seed, arm = Path(task['root']), task['cfg'], task['seed'], task['arm']
    path = root/'runs'/str(seed)/arm
    path.mkdir(parents=True, exist_ok=True)
    final, progress = path/'result.json', path/'task_status.json'
    if final.exists():
        saved = json.loads(final.read_text('utf-8'))
        if saved['status'] == 'complete' and not saved['attack_failures']:
            return str(final)
        raise RuntimeError('Existing incomplete result requires investigation')
    def event(message):
        line = stamp()+f' [{seed}/{arm}] '+message
        print(line, flush=True)
        with (path/'events.log').open('a', encoding='utf-8') as f:
            f.write(line+'\n')
        dual.write_json(progress, {'stage': 'running', 'updated_at': stamp(), 'last_event': message})
    try:
        data = load_private(root/'private/partition.pkl.gz')
        records = data['roles'][f'test_{seed}']
        size = cfg['users']//cfg['batches']
        cohorts = [records[t*size:(t+1)*size] for t in range(cfg['batches'])]
        dev = developer_stream(data['roles']['train'], 'raw', seed, cfg['batches'], data['median_length'])
        cache = ReusingDualAttackCache(root/'private/attacks'/str(seed)/arm, cfg, event)
        prediction_prefixes, grammar = predictor(data, cfg, 'literature', seed, cache, path, event)
        result, metadata = {}, {}
        if arm == 'graded':
            raw_train = Counter(w for _, w in data['roles']['train'])
            gate = cache.index(raw_train, purpose='frozen')
            frozen = dual.frozen_union(gate)
            membership = {m: cache.raw(load_private(path/f'private/predictor-literature-{seed}.pkl.gz'),
                                      cfg['risk_membership_budget'], m) for m in dual.MODELS}
            if not all(p['complete'] for p in membership.values()):
                raise RuntimeError('Dual risk membership prefix incomplete')
            memberships = {cutoff: frozenset(w for p in membership.values() for w, rank in p['ranks'].items()
                                              if rank <= cutoff) for cutoff in cfg['risk_membership_budgets']}
            original = dual.candidates
            dual.candidates = lambda base, recent, hold, ks, js: scoped_candidates(base, recent, hold, ks, js, memberships)
            try:
                result = dual.trajectory('joint', cohorts, dev, data, {**cfg, 'actual_attacks': False}, seed,
                                         {'fixed_pair': [1000, 0]}, path, cache, frozen, gate, event)
            finally:
                dual.candidates = original
            if result['status'] != 'complete':
                raise RuntimeError('Dynamic response incomplete; no final attack')
            state = load_private(path/'joint/state.pkl.gz')
            training = state['dev_history']
            metadata = {'candidate_selection': 'actual PCFG + OMEN union, B EACH',
                        'confirmation': 'same trained pair, separate response preview',
                        'fixed_checks': 'PCFG + OMEN union', 'risk_membership_budgets_per_model': cfg['risk_membership_budgets'],
                        'user_cost_caps': 'none; burden reported descriptively', 'changes': ['variable scope', 'remove all user-cost caps']}
        elif arm in ('schechter_strong', 'malone'):
            kind = 'schechter' if arm.startswith('schechter') else 'malone'
            parameters = {'rate': cfg['schechter_rates'][arm]} if kind == 'schechter' else {}
            state = simulate_arm(kind, cohorts, data, cfg, 'literature', seed, parameters, path,
                                 prediction_prefixes, grammar, event)
            training = developer_counts(state, kind, data, cfg, 'literature', seed, parameters)
            metadata = {'parameters': parameters, 'source': 'Schechter 2010' if kind == 'schechter' else 'Malone-Maher 2012 section 6'}
        else:
            response, base = response_for(data, cfg), base_rule(data, 'literature')
            if arm == 'github_core':
                rule = CompositionRule(minimum=8, github=True)
                metadata = {'source': 'GitHub public composition core', 'full_site_policy_reproduced': False}
            elif arm == 'segreti_struct_m_nohint':
                prediction = load_private(path/f'private/predictor-literature-{seed}.pkl.gz')
                prefix = cache.raw(prediction, cfg['segreti_candidate_prefix_budget'], 'pcfg')
                if not prefix['complete']:
                    raise RuntimeError('Segreti construction prefix incomplete')
                rule, metadata = segreti_rule(base, prefix, cfg['segreti_compliant_guesses'], path/'blacklist.pkl.gz')
            else:
                raise ValueError(arm)
            state = simulate_static(cohorts, dev, response, base, rule, seed, path, event)
            training = state['dev_history']
        save_private(path/'developer_counts.pkl.gz', training)
        index = cache.index(training, purpose='final')
        cost = dual.summed_cost(state['costs'])
        result = {**result, 'version': dual.VERSION, 'arm': arm, 'seed': seed, 'status': 'complete',
                  'cost': cost, 'timeline': state['timeline'], 'decisions': state['decisions'],
                  'A1_training_sha256': counts_hash(training), 'A1_training_occurrences': sum(training.values()),
                  'eligibility_failures': dual.cost_failures(cost, cfg), 'attack_failures': {}, 'mechanism': metadata,
                  'actual_attacks': attack_results(index.prefixes, state['history'], cfg),
                  'final_dual_risk': dual.risk(index, state['history'], cfg['budgets'], cfg['users'])}
        if cost['completed_users'] != cfg['users'] or cost['pending_users']:
            raise RuntimeError('Incomplete target registration; no complete-result label')
        dual.write_json(final, result)
        event('Result saved; cost '+('eligible' if not result['eligibility_failures'] else 'ineligible'))
        dual.write_json(progress, {'stage': 'complete', 'completed_at': stamp(), 'result': str(final)})
        return str(final)
    except Exception as exc:
        dual.write_json(progress, {'stage': 'failed', 'updated_at': stamp(), 'error': str(exc), 'traceback': traceback.format_exc()})
        raise


def export(root, cfg):
    rows = []
    for arm in cfg['arms']:
        p = root/'runs'/str(cfg['seeds'][0])/arm/'result.json'
        if not p.exists():
            continue
        r = json.loads(p.read_text('utf-8'))
        for model, curve in r['actual_attacks'].items():
            for point in curve['points']:
                rows.append({'arm': arm, 'model': model, 'budget': point['budget'], 'hits': point['hits'],
                             'users': cfg['users'], 'rate': point['rate_per_initial_user'],
                             'extra': r['cost']['mean_extra_submissions'], 'modification': r['cost']['modification_rate'],
                             'more_than_three': r['cost']['more_than_three_fraction'], 'eligible': not r['eligibility_failures'],
                             'outside_both': r['final_dual_risk']['outside_model_support_fraction']})
    if rows:
        with (root/'results.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    import msvcrt
    lock = (root/'runner.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    cfg = json.loads(Path(args.config).read_text('utf-8'))
    assert len(cfg['seeds']) == 1 and cfg['users'] % cfg['batches'] == 0
    assert cfg['arms'] == ['graded'] and cfg['user_cost_caps_disabled']
    assert all(cfg[k] is None for k in ('max_modification', 'max_extra', 'max_more_than_three'))
    files = [Path(__file__), ROOT/'experiments/registration_variable_pipeline.py']
    files += sorted((ROOT/'experiments').glob('registration*.py'))
    files += sorted((ROOT/'policy').glob('registration*.py'))
    files += [ROOT/'ai'/f for f in ('dual_attack.py', 'pcfg_adapter.py', 'pcfg_monte_carlo.py')]
    identity = {'version': dual.VERSION, 'config': cfg,
                'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                'primary': {'per_model_attempts': max(cfg['budgets']), 'max_union_attempts': 2*max(cfg['budgets'])},
                'policy_tuned_on_this_test': True, 'analysis_type': 'post-hoc paired revision, not independent validation',
                'paired_source': cfg['comparison_source'], 'paired_partition_sha256': cfg['paired_partition_sha256'], 'human_verification': 'pending',
                'comparators': 'Published mechanisms adapted to common simulated users; GitHub public core',
                'contribution_boundary': 'variable membership scope and removal of all user-cost caps change jointly; other control logic unchanged'}
    manifest = root/'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text('utf-8')) != identity:
        raise RuntimeError('Frozen code/config changed; use a new experiment directory')
    dual.write_json(manifest, identity)
    status = {'stage': 'preparing', 'started_at': stamp(), 'total_trajectories': len(cfg['arms']),
              'completed': [], 'failures': [], 'users_per_trajectory': cfg['users'], 'workers': args.workers}
    dual.write_json(root/'status.json', status)
    prepare(root, cfg)
    tasks = [{'root': str(root), 'cfg': cfg, 'seed': cfg['seeds'][0], 'arm': arm} for arm in cfg['arms']]
    status.update(stage='running', updated_at=stamp())
    dual.write_json(root/'status.json', status)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, task): task['arm'] for task in tasks}
        for future in as_completed(futures):
            arm = futures[future]
            try:
                future.result()
                status['completed'].append(arm)
                export(root, cfg)
            except Exception as exc:
                status['failures'].append({'arm': arm, 'error': str(exc), 'traceback': traceback.format_exc()})
            status.update(updated_at=stamp())
            dual.write_json(root/'status.json', status)
    status.update(stage='failed' if status['failures'] else 'complete', completed_at=stamp())
    dual.write_json(root/'status.json', status)
    if status['failures']:
        raise RuntimeError('Jobs failed; checkpoints retained')


if __name__ == '__main__':
    main()
