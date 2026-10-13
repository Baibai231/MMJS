"""Frozen v2 plus the four published comparators, on one fresh paired stream."""
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import argparse
import bisect
import hashlib
import json
import random
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.corpus import counts_hash, parse_record
from policy.registration_joint import JointRule
from policy.registration_comparators import PopularityOracle, MaloneMaher, CompositionRule, simulate_online
from policy.open_policy import edit_distance
from experiments.registration_joint_data import load_private, save_private, developer_stream
from experiments.registration_joint_pipeline import write_json, summed_cost, attach_base_cost
from experiments.registration_comparison import base_rule, StaticMechanism
from experiments.registration_full_additions import segreti_rule
from experiments.registration_variable_scope import UnlimitedPreferences, ReusingDualAttackCache
from experiments.registration_variable_pipeline import frozen_union, risk
from tools.run_registration_exchange_pareto import select, rule_summary
from tools.run_registration_variable_scope import attack_results

VERSION = 'registration-five-pareto-fresh-v1'
ARMS = ['bidirectional_exchange', 'schechter_strong', 'malone', 'segreti_struct_m_nohint', 'github_core']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(root, cfg):
    path = root/'private/partition.pkl.gz'
    if path.exists():
        return load_private(path)
    original = load_private(Path(cfg['comparison_source'])/'private/partition.pkl.gz')
    initial = load_private(ROOT/'reports/registration_joint_minimal_20261011/private/partition.pkl.gz')
    sources = list((ROOT/'reports').glob('registration*/private/partition.pkl.gz'))
    sources += [Path(p)/'private/partition.pkl.gz' for p in cfg['exclude_experiment_roots']]
    excluded, evidence = set(), []
    for source in sorted(set(p.resolve() for p in sources)):
        old = load_private(source)
        assert old['metadata']['source_sha256'] == initial['metadata']['source_sha256']
        excluded.update(i for records in old['roles'].values() for i, _ in records)
        evidence.append({'path': str(source), 'sha256': digest(source)})
    total = initial['metadata']['valid_ASCII_occurrences']
    rng = random.Random(cfg['fresh_split_seed'])
    chosen = set()
    while len(chosen) < cfg['users']:
        identifier = rng.randrange(total)
        if identifier not in excluded:
            chosen.add(identifier)
    ids = sorted(chosen)
    records, cursor, offset = [], 0, 0
    source_hash = hashlib.sha256()
    with (ROOT.parent/cfg['source']).open('rb') as handle:
        for raw in handle:
            source_hash.update(raw)
            try:
                word, count = parse_record(raw, 'password_with_count', 'latin-1')
            except ValueError:
                continue
            if any(not 32 <= ord(c) <= 126 for c in word):
                continue
            end = bisect.bisect_left(ids, offset+count, cursor)
            records.extend((i, word) for i in ids[cursor:end])
            cursor, offset = end, offset+count
    assert cursor == cfg['users'] and offset == total
    assert source_hash.hexdigest() == initial['metadata']['source_sha256']
    rng.shuffle(records)
    roles = {r: original['roles'][r] for r in ('train', 'tuning', 'preview_select', 'preview_confirm')}
    assert not chosen.intersection(i for rows in roles.values() for i, _ in rows)
    roles[f"test_{cfg['seeds'][0]}"] = records
    metadata = {'source_sha256': source_hash.hexdigest(), 'fresh_split_seed': cfg['fresh_split_seed'],
                'valid_ASCII_occurrences': total, 'new_target_occurrences': True,
                'excluded_previous_occurrences': len(excluded), 'excluded_partition_sources': evidence,
                'development_roles_reused': True, 'role_sizes': {r: len(v) for r,v in roles.items()},
                'position_hashes': {r: hashlib.sha256(','.join(map(str, sorted(i for i,_ in v))).encode()).hexdigest()
                                    for r,v in roles.items()},
                'interpretation': 'frequency-declared occurrence users; identical strings may recur across roles'}
    data = {'roles': roles, 'median_length': original['median_length'], 'metadata': metadata}
    save_private(path, data)
    write_json(root/'data_manifest.json', metadata)
    return data


def online_with_edits(records, mechanism, response, seed, capture_proposals=False):
    actual = simulate_online(records, mechanism, response, seed, capture_proposals=capture_proposals)
    actual['summary']['mean_edit_distance'] = sum(edit_distance(original, target)
        for (_, original), target in zip(records, actual['users']) if target is not None)/len(records)
    actual['summary']['terminal_state'] = 'continue_until_accepted_no_retry_cap'
    return actual


def make_mechanism(arm, base, bootstrap, cfg):
    if arm == 'schechter_strong':
        return PopularityOracle(base, cfg['schechter_rate'], bootstrap['counts'])
    return MaloneMaher(base, bootstrap['proposed'])


def run_arm(task):
    root, cfg, arm = Path(task['root']), task['cfg'], task['arm']
    seed = cfg['seeds'][0]
    path = root/'runs'/str(seed)/arm
    path.mkdir(parents=True, exist_ok=True)
    result_path = path/'result.json'
    if result_path.exists():
        result = json.loads(result_path.read_text('utf-8'))
        assert result['status'] == 'complete' and not result['attack_failures']
        return str(result_path)
    def log(message):
        line = time.strftime('%Y-%m-%d %H:%M:%S')+f' [{arm}] '+message
        print(line, flush=True)
        with (path/'events.log').open('a', encoding='utf-8') as handle:
            handle.write(line+'\n')
        write_json(path/'progress.json', {'stage': 'running', 'event': message, 'updated': time.time()})
    try:
        data = load_private(root/'private/partition.pkl.gz')
        response = UnlimitedPreferences([w for _,w in data['roles']['train']], cfg['minimum'],
                                        cfg['weights'], cfg['response_mode'], None)
        records = data['roles'][f'test_{seed}']
        assert len(records) == cfg['users']
        size = cfg['users']//cfg['batches']
        cohorts = [records[t*size:(t+1)*size] for t in range(cfg['batches'])]
        developer = developer_stream(data['roles']['train'], 'raw', seed, cfg['batches'], data['median_length'])
        literature_base = base_rule(data, 'literature')
        base = CompositionRule(minimum=8, github=True) if arm == 'github_core' else literature_base
        cache = ReusingDualAttackCache(root/'private/attacks'/str(seed)/arm, cfg, log)
        baseline, frozen = None, None
        mechanism_info = {'response': 'shared policy-independent unlimited preference sequence'}
        if arm == 'segreti_struct_m_nohint':
            training = response.simulate(data['roles']['train'], literature_base, seed)['counts']
            prefix = cache.raw(training, cfg['segreti_candidate_prefix_budget'], 'pcfg')
            assert prefix['complete']
            rule, info = segreti_rule(literature_base, prefix, cfg['segreti_compliant_guesses'], path/'blacklist.pkl.gz')
            mechanism_info.update(info)
        elif arm == 'bidirectional_exchange':
            saved = load_private(root/'runs'/str(seed)/'segreti_struct_m_nohint/blacklist.pkl.gz')
            r = saved['rule']
            baseline = JointRule(r.minimum, r.common, r.exact, r.shapes, r.k, len(r.shapes))
            rule = baseline
            training = response.simulate(data['roles']['train'], JointRule(r.minimum, r.common), seed)['counts']
            frozen = frozen_union(cache.index(training, purpose='frozen'))
            mechanism_info.update({'algorithm': 'edit-aware bidirectional Pareto v2',
                                  'initial_blacklist_sha256': digest(root/'runs'/str(seed)/'segreti_struct_m_nohint/blacklist.pkl.gz'),
                                  'cost_constraints_scope': 'selection/confirmation previews, not retrospective rollback'})
        else:
            rule = base
            if arm == 'github_core':
                mechanism_info['full_site_policy_reproduced'] = False
            elif arm == 'schechter_strong':
                mechanism_info['threshold_rate'] = cfg['schechter_rate']
        checkpoint = path/'state.pkl.gz'
        state = load_private(checkpoint) if checkpoint.exists() else {
            't': 0, 'rule': rule, 'history': Counter(), 'dev_history': Counter(),
            'final_batches': [], 'dev_batches': [], 'costs': [], 'timeline': [],
            'attempts': [], 'decisions': [], 'mechanism': None, 'dev_mechanism': None}
        online = arm in ('schechter_strong', 'malone')
        for t in range(state['t'], cfg['batches']):
            rule = state['rule']
            if baseline is not None and t:
                rule, decision = select(baseline, rule, state, cohorts, data, response, cache, frozen,
                                        cfg, seed, t, path, log)
                state['decisions'].append(decision)
            if online:
                if t == 0:
                    actual = online_with_edits(cohorts[t], StaticMechanism(base), response, seed, True)
                    dev = simulate_online(developer[t], StaticMechanism(base), response, seed, capture_proposals=True)
                    state['mechanism'] = make_mechanism(arm, base, actual, cfg)
                    state['dev_mechanism'] = make_mechanism(arm, base, dev, cfg)
                else:
                    actual = online_with_edits(cohorts[t], state['mechanism'], response, seed)
                    dev = simulate_online(developer[t], state['dev_mechanism'], response, seed)
            else:
                actual = response.simulate(cohorts[t], rule, seed, distances=True)
                dev = response.simulate(developer[t], rule, seed)
            common = response.simulate(cohorts[t], base, seed)
            attach_base_cost(actual, common)
            assert not actual['summary']['pending_users'] and not dev['summary']['pending_users']
            assert actual['summary']['mean_edit_distance'] is not None
            if baseline is not None and t == 0:
                other = load_private(root/'runs'/str(seed)/'segreti_struct_m_nohint/state.pkl.gz')
                assert actual['counts'] == other['final_batches'][0]
                assert actual['attempts'] == other['attempts'][:size]
                assert dev['counts'] == other['dev_batches'][0]
                log('First paired batch exactly matches fixed Segreti')
            state['history'].update(actual['counts']); state['dev_history'].update(dev['counts'])
            state['final_batches'].append(actual['counts']); state['dev_batches'].append(dev['counts'])
            state['costs'].append(actual['summary']); state['attempts'].extend(actual['attempts'])
            state['timeline'].append(rule_summary(rule))
            state['rule'], state['t'] = rule, t+1
            save_private(checkpoint, state)
            log(f'Completed batch {t+1}/{cfg["batches"]}: {sum(state["history"].values()):,} users')
        save_private(path/'developer_counts.pkl.gz', state['dev_history'])
        log('Computing final policy-conditioned PCFG + OMEN attacks')
        index = cache.index(state['dev_history'], purpose='final')
        result = {'version': VERSION, 'arm': arm, 'seed': seed, 'status': 'complete',
                  'cost': summed_cost(state['costs']), 'timeline': state['timeline'], 'decisions': state['decisions'],
                  'A1_training_sha256': counts_hash(state['dev_history']),
                  'A1_training_occurrences': sum(state['dev_history'].values()), 'attack_failures': {},
                  'actual_attacks': attack_results(index.prefixes, state['history'], cfg),
                  'final_dual_risk': risk(index, state['history'], cfg['budgets'], cfg['users']),
                  'partition_sha256': digest(root/'private/partition.pkl.gz'), 'mechanism': mechanism_info,
                  'analysis_type': 'frozen controller evaluated on fresh occurrence stream, reused development data'}
        assert result['cost']['completed_users'] == cfg['users'] and not result['cost']['pending_users']
        assert result['A1_training_occurrences'] == len(data['roles']['train'])
        write_json(result_path, result)
        write_json(path/'progress.json', {'stage': 'complete', 'users': cfg['users']})
        log('Complete result saved')
        return str(result_path)
    except Exception as exc:
        write_json(path/'progress.json', {'stage': 'failed', 'error': str(exc), 'traceback': traceback.format_exc()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text('utf-8'))
    assert cfg['users'] == 100000 and cfg['batches'] == 10 and cfg['seeds'] == [11]
    assert cfg['arms'] == ARMS
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=True)
    import msvcrt
    lock = (root/'runner.lock').open('a+b'); lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    files = sorted(set(['tools/run_registration_five_pareto.py', 'tools/run_registration_exchange_pareto.py',
                       'tools/run_registration_variable_scope.py', 'policy/registration_joint.py',
                       'policy/registration_comparators.py', 'policy/open_policy.py',
                       'experiments/registration_comparison.py', 'experiments/registration_full_additions.py'] +
                      [str(p.relative_to(ROOT)).replace('\\', '/') for p in (ROOT/'experiments').glob('registration_*.py')] +
                      ['ai/pcfg_adapter.py', 'ai/dual_attack.py', 'ai/pcfg_monte_carlo.py']))
    manifest = {'version': VERSION, 'config': cfg, 'source_hashes': {f: digest(ROOT/f) for f in files},
                'scope': 'five methods each 100000 users, one common fresh stream', 'human_verification': 'pending'}
    manifest_path = root/'manifest.json'
    if manifest_path.exists():
        assert json.loads(manifest_path.read_text('utf-8')) == manifest, 'Frozen code/config changed'
    else:
        write_json(manifest_path, manifest)
    try:
        write_json(root/'status.json', {'stage': 'preparing'})
        prepare(root, cfg)
        run_arm({'root': str(root), 'cfg': cfg, 'arm': 'segreti_struct_m_nohint'})
        write_json(root/'status.json', {'stage': 'baselines'})
        with ProcessPoolExecutor(max_workers=2) as pool:
            tasks = [{'root': str(root), 'cfg': cfg, 'arm': arm}
                     for arm in ('schechter_strong', 'malone', 'github_core')]
            list(pool.map(run_arm, tasks))
        write_json(root/'status.json', {'stage': 'new_algorithm'})
        run_arm({'root': str(root), 'cfg': cfg, 'arm': 'bidirectional_exchange'})
        write_json(root/'status.json', {'stage': 'complete', 'arms': ARMS, 'users_per_method': cfg['users'], 'audit': 'pending'})
    except Exception as exc:
        write_json(root/'status.json', {'stage': 'failed', 'error': str(exc), 'traceback': traceback.format_exc()})
        raise


if __name__ == '__main__':
    main()
