"""Small offline privacy/profile ablation. Never runs attack training.

Passwords are available ONLY to this research evaluator/ideal functionality.
The numeric profile algorithm never reads strings. This is not an MPC protocol.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
from itertools import islice, product
import json
from pathlib import Path
import pickle
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from core.corpus import counts_hash
from policy.registration_joint import JointRule, Preferences, structure, stable_seed
from policy.privacy_joint_profile import (private_conditional, private_marginal, choose_shapes,
    choose_positions, predict_conditional, predict_marginal)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load_pickle(path):
    with gzip.open(path, 'rb') as handle:
        return pickle.load(handle)


def write_csv(path, rows):
    with Path(path).open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def verify_theory():
    def profile(c):
        return np.array([sum(c[:2]), sum(c[2:]), *sorted(c[:2], reverse=True), *sorted(c[2:], reverse=True)])
    checked = neighbor_pairs = 0
    maximum = 0
    for c in product(range(7), repeat=4):
        if sum(c) > 6:
            continue
        for old in range(4):
            if not c[old]:
                continue
            for new in range(4):
                d = list(c)
                d[old] -= 1
                d[new] += 1
                maximum = max(maximum, int(np.abs(profile(c) - profile(d)).sum()))
                neighbor_pairs += 1
        for shapes in ((), (0,), (1,)):
            for k in range(4):
                ranked = sorted(range(4), key=lambda i: (-c[i], i))
                ordinary = ranked[:k]
                residual = [i for i in ranked if i // 2 not in shapes][:k]
                union = lambda chosen: sum(c[i] for i in range(4) if i in chosen or i // 2 in shapes)
                assert union(residual) >= union(ordinary)
                predicted = sum(c[i] for i in range(4) if i // 2 in shapes) + sum(c[i] for i in ordinary if i // 2 not in shapes)
                assert predicted == union(ordinary)
                checked += 1
    return {'synthetic_policy_conditions': checked, 'neighbor_pairs': neighbor_pairs,
            'max_observed_L1_sensitivity': maximum, 'scope': 'finite synthetic verification, not a privacy proof'}


class IdealHiddenState:
    """Plaintext research oracle, deliberately excluded from admin output."""
    def __init__(self, counts, shapes, length):
        self.counts = counts
        self.shapes = tuple(shapes) + ('OTHER',)
        self.other = len(shapes)
        lookup = {s: i for i, s in enumerate(shapes)}
        self.word_shape = {w: lookup.get(structure(w), self.other) for w in counts}
        rows = [[] for _ in self.shapes]
        for word in counts:
            rows[self.word_shape[word]].append(word)
        for row in rows:
            row.sort(key=lambda w: (-counts[w], w))
        self.spectra = np.zeros((len(rows), length))
        self.owners = []
        self.shape_counts = np.zeros(len(rows))
        for s, row in enumerate(rows):
            self.shape_counts[s] = sum(counts[w] for w in row)
            owners = row[:length] + [None] * max(0, length - len(row))
            self.owners.append(owners)
            self.spectra[s] = [counts.get(w, 0) for w in owners]
        # Structure-first ties match conditional position ranking.
        self.global_owners = sorted(counts, key=lambda w: (-counts[w], self.word_shape[w], w))[:length]
        self.global_owners += [None] * (length - len(self.global_owners))
        self.global_frequencies = np.array([counts.get(w, 0) for w in self.global_owners], dtype=float)

    def rule_and_truth(self, base, k, j, method, profile):
        selected_shapes = choose_shapes(profile['counts'], j, self.other)
        if method == 'A':
            positions = tuple(i for i, v in enumerate(profile['frequencies']) if v >= 2)[:k]
            words = frozenset(self.global_owners[i] for i in positions if self.global_owners[i] is not None)
            prediction = predict_marginal(profile, positions, selected_shapes, sum(self.counts.values()))
        else:
            positions = choose_positions(profile, k, selected_shapes, residual=(method in ('C', 'O_residual')))
            words = frozenset(self.owners[s][r] for s, r in positions if self.owners[s][r] is not None)
            prediction = predict_conditional(profile, positions, selected_shapes)
        shape_names = frozenset(self.shapes[s] for s in selected_shapes)
        exact_mass = sum(self.counts[w] for w in words)
        overlap = sum(self.counts[w] for w in words if self.word_shape[w] in selected_shapes)
        truth = sum(v for w, v in self.counts.items() if w in words or self.word_shape[w] in selected_shapes)
        rule = JointRule(base.minimum, base.common, words, shape_names, k, j)
        metrics = {'predicted_users': min(float(sum(self.counts.values())), max(0.0, prediction)),
                   'true_users': truth, 'overlap_users': overlap,
                   'overlap_fraction_exact_mass': overlap / exact_mass if exact_mass else 0.0,
                   'redundant_exact_slots_fraction': sum(self.word_shape[w] in selected_shapes for w in words) / len(words) if words else 0.0,
                   'actual_exact_size': len(words), 'actual_shape_size': len(shape_names),
                   'padded_slot_fraction': (len(positions) - len(words)) / len(positions) if positions else 0.0}
        return rule, metrics


def simulate_cached(records, proposals, rule, attack, base_simulation=None):
    n = len(records)
    users, attempts = [], []
    modified = initial_rejected = 0
    for (_, original), sequence in zip(records, proposals):
        target = None
        last = 0
        for i, word in enumerate(sequence):
            last = i
            if rule.accepts(word):
                target = word
                break
        users.append(target)
        attempts.append(last)
        modified += target is not None and target != original
        initial_rejected += not rule.accepts(original)
    completed = sum(w is not None for w in users)
    hits = sum(w in attack for w in users if w is not None)
    result = {'initial_users': n, 'completed_users': completed, 'pending_users': n - completed,
              'completion_rate': completed / n, 'attack_hits': hits,
              'hit_rate_per_initial': hits / n,
              'hit_rate_per_completed': hits / completed if completed else 0.0,
              'modification_rate': modified / n, 'initial_rejection_rate': initial_rejected / n,
              'mean_extra_submissions': sum(attempts) / n,
              'users': users, 'attempts': attempts}
    if base_simulation is not None:
        result['incremental_output_change_rate'] = sum(a != b for a, b in zip(users, base_simulation['users'])) / n
        result['incremental_extra_submissions'] = sum(max(0, a - b) for a, b in zip(attempts, base_simulation['attempts'])) / n
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/privacy_joint_minimal.json')
    args = parser.parse_args()
    config_path = ROOT / args.config
    cfg = json.loads(config_path.read_text('utf-8'))
    output = ROOT / cfg['output']
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'status.json', {'state': 'running', 'started_at': time.strftime('%Y-%m-%d %H:%M:%S')})
    started = time.perf_counter()
    source = ROOT / cfg['source_study']
    source_manifest = json.loads((source / 'manifest.json').read_text('utf-8'))
    source_cfg = source_manifest['config']
    partition_path = source / 'private/partition.pkl.gz'
    data = load_pickle(partition_path)
    train = data['roles']['train']
    history_records = data['roles'][cfg['history_role']][:cfg['history_users']]
    registrations = data['roles'][cfg['registration_role']][:cfg['registration_users']]
    assert len(history_records) == cfg['history_users'] and len(registrations) == cfg['registration_users']
    assert not ({i for i, _ in train} & {i for i, _ in history_records})
    assert not ({i for i, _ in history_records} & {i for i, _ in registrations})
    assert not ({i for i, _ in train} & {i for i, _ in registrations})
    train_words = [w for _, w in train]
    train_counts = Counter(train_words)
    common = frozenset(w for w, _ in sorted(train_counts.items(), key=lambda item: (-item[1], item[0]))[:source_cfg['common_size']])
    base = JointRule(source_cfg['minimum'], common)
    preferences = Preferences(train_words, base.minimum, source_cfg['weights'], source_cfg['response_mode'], source_cfg['candidate_limit'])
    # Model completed historical registrations under the common base policy.
    history_result = preferences.simulate(history_records, base, cfg['response_seed'])
    if history_result['summary']['pending_users']:
        raise RuntimeError('Historical cohort did not complete: do not silently change fixed-N privacy adjacency')
    history = history_result['counts']
    training_shapes = Counter(structure(w) for _, w in train)
    shapes = [s for s, _ in sorted(training_shapes.items(), key=lambda item: (-item[1], item[0]))[:cfg['structure_limit']]]
    state = IdealHiddenState(history, shapes, cfg['spectrum_length'])
    assert all(k <= cfg['spectrum_length'] for k, _ in cfg['candidates'])
    train_hash = counts_hash(train_counts)
    prefix_path = source / 'private/attacks/raw/11/prefixes' / f"{train_hash}-{cfg['attack_model']}-{cfg['attack_budget']}.pkl.gz"
    if not prefix_path.exists():
        raise FileNotFoundError(f'Frozen attacker missing; no training fallback: {prefix_path}')
    prefix = load_pickle(prefix_path)
    assert prefix['raw_emissions'] >= cfg['attack_budget']
    attack = frozenset(w for w, rank in prefix['ranks'].items() if rank <= cfg['attack_budget'])
    proposals = [tuple(islice(preferences.proposals(w, identifier, cfg['response_seed']), cfg['patience'] + 1)) for identifier, w in registrations]
    base_simulation = simulate_cached(registrations, proposals, base, attack)
    # Check cached-response evaluation against the existing simulator, once.
    direct = preferences.simulate(registrations, base, cfg['response_seed'], patience=cfg['patience'])
    assert direct['users'] == base_simulation['users'] and direct['attempts'] == base_simulation['attempts']
    public_counts = state.shape_counts.copy()
    conditional_truth = {'counts': public_counts, 'spectra': state.spectra.copy()}
    marginal_truth = {'counts': public_counts, 'frequencies': state.global_frequencies.copy()}
    theoretical = verify_theory()
    manifest = {'version': 'privacy-joint-minimal-v1', 'config': cfg,
                'config_sha256': sha(config_path), 'source_partition_sha256': sha(partition_path),
                'source_corpus_sha256': data['metadata']['source_sha256'],
                'source_role_position_hashes': data['metadata']['role_position_hashes'],
                'attack_prefix_sha256': sha(prefix_path), 'attack_prefix_path': str(prefix_path),
                'attack_training_counts_sha256': train_hash, 'attack_raw_emissions': prefix['raw_emissions'],
                'attack_distinct_guesses': len(attack), 'attack_budget_unit': prefix['budget_unit'],
                'training_occurrences': len(train), 'historical_occurrences': sum(history.values()),
                'registration_occurrences': len(registrations),
                'structure_dimension_including_OTHER': len(state.shapes),
                'training_defined_structure_coverage': 1 - state.shape_counts[-1] / sum(history.values()),
                'history_preprocessing': history_result['summary'],
                'occurrence_ids_disjoint': True, 'individual_identities_verified': False,
                'base_policy': {'minimum': base.minimum, 'common_size': len(common)},
                'cost_limits': {'max_modification': source_cfg['max_modification'], 'max_extra': source_cfg['max_extra'],
                                'pending_limit': 0},
                'statistical_scope': '20 noise repetitions on one fixed cohort; error bars are empirical 10-90 percentiles',
                'privacy_scope': 'numeric profile only; ideal hidden mapping and policy checks are not DP outputs',
                'cryptographic_protocol_implemented': False, 'theory_checks': theoretical,
                'python': platform.python_version(), 'numpy': np.__version__,
                'implementation_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [Path(__file__), ROOT / 'policy/privacy_joint_profile.py', ROOT / 'policy/registration_joint.py']}}
    # Freeze settings/provenance before generating any noise outcomes.
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'theory_checks.json', theoretical)
    profile_rows, registration_rows, references = [], [], []
    print('Inputs frozen; running 60 numeric profile conditions and 20 paired registration conditions.', flush=True)
    for method in ['A', 'O_independent', 'O_residual']:
        true_profile = marginal_truth if method == 'A' else conditional_truth
        for k, j in cfg['candidates']:
            rule, metric = state.rule_and_truth(base, k, j, method, true_profile)
            metric.update(method=method, k=k, j=j, epsilon='infinite', repetition=-1,
                          absolute_error_users=abs(metric['predicted_users'] - metric['true_users']),
                          absolute_error_pp=100 * abs(metric['predicted_users'] - metric['true_users']) / sum(history.values()))
            references.append(metric)
    simulation_cache = {}
    for epsilon in cfg['epsilons']:
        for repetition in range(cfg['noise_repetitions']):
            t0 = time.perf_counter()
            marginal = private_marginal(public_counts, state.global_frequencies, epsilon,
                        np.random.default_rng(stable_seed(cfg['noise_seed'], 'marginal', epsilon, repetition)), cfg['baseline_budget_split'])
            marginal_ms = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            conditional = private_conditional(public_counts, state.spectra, epsilon,
                        np.random.default_rng(stable_seed(cfg['noise_seed'], 'conditional', epsilon, repetition)))
            conditional_ms = (time.perf_counter() - t0) * 1000
            for method in ['A', 'B', 'C']:
                profile = marginal if method == 'A' else conditional
                for k, j in cfg['candidates']:
                    t0 = time.perf_counter()
                    rule, metric = state.rule_and_truth(base, k, j, method, profile)
                    rule_ms = (time.perf_counter() - t0) * 1000
                    metric.update(method=method, k=k, j=j, epsilon=epsilon, repetition=repetition,
                                  absolute_error_users=abs(metric['predicted_users'] - metric['true_users']),
                                  absolute_error_pp=100 * abs(metric['predicted_users'] - metric['true_users']) / sum(history.values()),
                                  numeric_profile_ms=marginal_ms if method == 'A' else conditional_ms,
                                  ideal_rule_evaluation_ms=rule_ms)
                    profile_rows.append(metric)
                    if epsilon == cfg['registration_epsilon'] and [k, j] == cfg['registration_pair']:
                        signature = rule.signature()
                        if signature not in simulation_cache:
                            simulation_cache[signature] = simulate_cached(registrations, proposals, rule, attack, base_simulation)
                        result = {key: value for key, value in simulation_cache[signature].items() if key not in ('users', 'attempts')}
                        failures = []
                        if result['modification_rate'] > source_cfg['max_modification']:
                            failures.append('modification')
                        if result['mean_extra_submissions'] > source_cfg['max_extra']:
                            failures.append('extra_submissions')
                        if result['pending_users']:
                            failures.append('pending_users')
                        result.update(method=method, epsilon=epsilon, repetition=repetition, k=k, j=j,
                                      cost_eligible=not failures, cost_failures='|'.join(failures),
                                      paired_hit_difference_from_base=result['hit_rate_per_completed'] - base_simulation['hit_rate_per_completed'])
                        registration_rows.append(result)
        print(f'epsilon={epsilon}: complete', flush=True)
    for method in ['O_independent', 'O_residual']:
        k, j = cfg['registration_pair']
        rule, _ = state.rule_and_truth(base, k, j, method, conditional_truth)
        result = simulate_cached(registrations, proposals, rule, attack, base_simulation)
        clean = {key: value for key, value in result.items() if key not in ('users', 'attempts')}
        clean.update(method=method, epsilon='infinite', repetition=-1, k=k, j=j,
                     cost_eligible=(clean['modification_rate'] <= source_cfg['max_modification'] and clean['mean_extra_submissions'] <= source_cfg['max_extra'] and clean['pending_users'] == 0),
                     cost_failures='reference', paired_hit_difference_from_base=clean['hit_rate_per_completed'] - base_simulation['hit_rate_per_completed'])
        registration_rows.append(clean)
    clean = {key: value for key, value in base_simulation.items() if key not in ('users', 'attempts')}
    clean.update(incremental_output_change_rate=0.0, incremental_extra_submissions=0.0, method='base',
                 epsilon='none', repetition=-1, k=0, j=0,
                 cost_eligible=(clean['modification_rate'] <= source_cfg['max_modification'] and clean['mean_extra_submissions'] <= source_cfg['max_extra'] and clean['pending_users'] == 0),
                 cost_failures='reference', paired_hit_difference_from_base=0.0)
    registration_rows.append(clean)
    write_csv(output / 'profile_results.csv', profile_rows)
    write_csv(output / 'profile_references.csv', references)
    write_csv(output / 'registration_results.csv', registration_rows)
    summary = {'profile': {}, 'registration': {}, 'runtime_seconds': time.perf_counter() - started}
    for epsilon in cfg['epsilons']:
        summary['profile'][str(epsilon)] = {}
        for method in ['A', 'B', 'C']:
            rows = [r for r in profile_rows if r['epsilon'] == epsilon and r['method'] == method]
            summary['profile'][str(epsilon)][method] = {key: float(np.mean([r[key] for r in rows])) for key in
                ['absolute_error_users', 'absolute_error_pp', 'overlap_fraction_exact_mass', 'redundant_exact_slots_fraction', 'padded_slot_fraction', 'numeric_profile_ms', 'ideal_rule_evaluation_ms']}
    for method in ['base', 'O_independent', 'O_residual', 'A', 'B', 'C']:
        rows = [r for r in registration_rows if r['method'] == method]
        summary['registration'][method] = {key: float(np.mean([r[key] for r in rows])) for key in
            ['completed_users', 'pending_users', 'completion_rate', 'attack_hits', 'hit_rate_per_initial', 'hit_rate_per_completed',
             'modification_rate', 'initial_rejection_rate', 'mean_extra_submissions', 'incremental_output_change_rate', 'incremental_extra_submissions']}
        summary['registration'][method].update(runs=len(rows), cost_eligible_runs=sum(r['cost_eligible'] for r in rows))
    write_json(output / 'summary.json', summary)
    write_json(output / 'status.json', {'state': 'complete', 'completed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
               'profile_rows': len(profile_rows), 'registration_rows': len(registration_rows), 'runtime_seconds': summary['runtime_seconds']})
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
