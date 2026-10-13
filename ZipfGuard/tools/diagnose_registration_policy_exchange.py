"""Local exploratory diagnosis; frozen baseline attacks, development users only.

Never retrains attackers or evaluates a new policy on the old target users.
Reports aggregate evidence only. This is not an adaptive-attack validation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.registration_joint_data import load_private
from experiments.registration_variable_scope import UnlimitedPreferences
from ai.pcfg_monte_carlo import Grammar


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(result, ranks, support):
    if result['summary']['pending_users']:
        raise ValueError('Incomplete users cannot be interpreted as lower risk')
    n = result['summary']['initial_users']
    return {
        'frozen_union_rate': sum(c for w, c in result['counts'].items() if w in ranks) / n,
        'mean_extra_submissions': result['summary']['mean_extra_submissions'],
        'modification_rate': result['summary']['modification_rate'],
        'more_than_three_fraction': result['summary']['more_than_three_fraction'],
        'pending_users': result['summary']['pending_users'],
        'pcfg_unsupported_fraction': sum(c for w, c in result['counts'].items()
                                         if not support(w)[0]) / n,
        'outside_both_fraction': sum(c for w, c in result['counts'].items()
                                     if not any(support(w))) / n,
    }


def paired(candidate, reference, ranks):
    n = len(reference['users'])
    if n < 2 or len(candidate['users']) != n or len(candidate['attempts']) != n:
        raise ValueError('Invalid paired response lengths')
    risk = Counter(int(a in ranks) - int(b in ranks)
                   for a, b in zip(candidate['users'], reference['users']))
    cost = Counter((a > b) - (a < b)
                   for a, b in zip(candidate['attempts'], reference['attempts']))
    mean = (risk[1] - risk[-1]) / n
    variance = ((risk[1] + risk[-1]) - n * mean * mean) / (n - 1)
    se = math.sqrt(variance / n)
    return {'risk_improved_users': risk[-1], 'risk_worsened_users': risk[1],
            'risk_unchanged_users': risk[0], 'cost_improved_users': cost[-1],
            'cost_worsened_users': cost[1], 'cost_unchanged_users': cost[0],
            'risk_delta_candidate_minus_baseline': mean,
            'conditional_paired_normal_interval95': [mean-1.96*se, mean+1.96*se],
            'interval_scope': 'Descriptive approximation under independent simulated confirmation users; not model-uncertainty or cross-run stability.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dynamic-root', type=Path, required=True)
    parser.add_argument('--baseline-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preview-users', type=int, default=3000)
    args = parser.parse_args()
    if args.preview_users < 1:
        parser.error('--preview-users must be positive')
    baseline_dir = args.baseline_root / 'runs/11/segreti_struct_m_nohint'
    dynamic_dir = args.dynamic_root / 'runs/11/segreti_dynamic'
    baseline_path = args.dynamic_root / 'private/segreti_baseline.pkl.gz'
    partition_path = args.baseline_root / 'private/partition.pkl.gz'
    if sha(partition_path) != sha(args.dynamic_root / 'private/partition.pkl.gz'):
        raise ValueError('Paired partitions differ')
    baseline = load_private(baseline_path)['rule']
    static_state = load_private(baseline_dir / 'state.pkl.gz')
    dynamic_state = load_private(dynamic_dir / 'state.pkl.gz')
    static_attempts, dynamic_attempts = static_state['attempts'], dynamic_state['attempts']
    if len(static_attempts) != len(dynamic_attempts):
        raise ValueError('Paired user counts differ')
    historical_pair = Counter((a > b) - (a < b)
                              for a, b in zip(dynamic_attempts, static_attempts))
    if historical_pair[-1]:
        raise AssertionError('Superset restriction unexpectedly reduced paired attempts')
    result_path = baseline_dir / 'result.json'
    result = json.loads(result_path.read_text('utf-8'))
    key = result['A1_training_sha256']
    cache = args.baseline_root / 'private/attacks/11/segreti_struct_m_nohint/prefixes'
    prefix_paths = [cache / f'{key}-{model}-1000000.pkl.gz' for model in ('pcfg', 'omen')]
    ranks = set()
    for p in prefix_paths:
        prefix = load_private(p)
        if not prefix['complete'] or prefix['raw_emissions'] != 1000000:
            raise ValueError('Incomplete actual attack evidence')
        ranks.update(w for w, rank in prefix['ranks'].items() if rank <= 1000000)
    grammar_path = cache.parent / 'grammars' / f'{key}.pkl.gz'
    grammar = Grammar(**load_private(grammar_path))
    omen = load_private(prefix_paths[1])['metadata']
    alphabet = frozenset(omen['alphabet'])

    @lru_cache(maxsize=200000)
    def support(word):
        return (grammar.probability(word) > 0,
                omen['min_length'] <= len(word) <= omen['max_length']
                and all(c in alphabet for c in word))
    partition = load_private(partition_path)
    if any(len(partition['roles'][role]) < args.preview_users
           for role in ('preview_select', 'preview_confirm')):
        raise ValueError('Requested preview exceeds available development roles')
    # Only named development roles are used for the new policy screening.
    select_records = partition['roles']['preview_select'][:args.preview_users]
    confirm_records = partition['roles']['preview_confirm'][:args.preview_users]
    response = UnlimitedPreferences([w for _, w in partition['roles']['train']])
    reference = response.simulate(select_records, baseline, 11)
    ref = metrics(reference, ranks, support)
    rows = []
    for i, shape in enumerate(sorted(baseline.shapes)):
        rule = replace(baseline, shapes=baseline.shapes - {shape}, j=len(baseline.shapes)-1)
        preview = response.simulate(select_records, rule, 11)
        row = {'shape': shape, **metrics(preview, ranks, support), **paired(preview, reference, ranks)}
        rows.append(row)
        if (i + 1) % 50 == 0:
            print(f'Development-only release screening: {i+1}/{len(baseline.shapes)}', flush=True)
    promising = [r for r in rows if r['frozen_union_rate'] <= ref['frozen_union_rate']
                 and r['mean_extra_submissions'] < ref['mean_extra_submissions']
                 and not r['pending_users']]
    promising.sort(key=lambda r: (r['frozen_union_rate'], r['mean_extra_submissions'], r['shape']))
    confirmation = None
    if promising:
        winner = promising[0]
        rule = replace(baseline, shapes=baseline.shapes - {winner['shape']}, j=len(baseline.shapes)-1)
        hold = response.simulate(confirm_records, baseline, 11)
        trial = response.simulate(confirm_records, rule, 11)
        confirmation = {'single_nominated_shape': winner['shape'],
                        'baseline': metrics(hold, ranks, support), 'candidate': metrics(trial, ranks, support),
                        'paired': paired(trial, hold, ranks)}
    output = {
        'status': 'exploratory_development_diagnosis_only',
        'human_verification': 'pending',
        'new_target_evaluation': False,
        'new_attacker_training': False,
        'interpretation': 'Frozen baseline-conditioned prefixes cannot certify adapted attack safety.',
        'historical_attempt_monotonicity': {
            'paired_users': len(static_attempts), 'lower_cost_users': historical_pair[-1],
            'higher_cost_users': historical_pair[1], 'equal_cost_users': historical_pair[0],
            'extra_attempts_total': sum(dynamic_attempts)-sum(static_attempts)},
        'screening': {'users_per_role': args.preview_users, 'seed': 11,
                      'per_model_raw_attempts': 1000000, 'max_total_raw_attempts': 2000000,
                      'baseline': ref, 'single_release_candidates': len(rows),
                      'weak_risk_and_strict_cost_improvement_candidates': len(promising),
                      'candidates': rows, 'confirmation': confirmation},
        'evidence': [{'path': str(p.resolve()), 'sha256': sha(p)}
                     for p in [baseline_path, partition_path, result_path,
                               baseline_dir / 'state.pkl.gz',
                               dynamic_dir / 'state.pkl.gz', *prefix_paths, grammar_path, Path(__file__)]],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps({k:v for k,v in output.items() if k in ('status', 'historical_attempt_monotonicity')},
                     ensure_ascii=False), flush=True)
    print(json.dumps({'baseline_preview': ref, 'promising_count': len(promising),
                      'confirmation': confirmation}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
