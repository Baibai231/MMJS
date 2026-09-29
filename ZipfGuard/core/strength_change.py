"""Per-user rank changes with explicit finite-budget censoring."""
from __future__ import annotations

import math
from collections import Counter


def observed_rank(word, runs, budget):
    positions = [run.ranks[word] for run in runs
                 if word in run.ranks and run.ranks[word] <= budget]
    if positions:
        return {'status': 'hit', 'rank': min(positions)}
    if runs and all(run.complete_at(budget) for run in runs):
        return {'status': 'beyond_budget', 'rank': None}
    return {'status': 'unresolved', 'rank': None}


def rank_change(before, after, *, same_password=False, budget):
    if same_password:
        return {'status': 'unchanged', 'percent': 0.0,
                'log2_gain': 0.0, 'bound_percent': None}
    a, b = before['status'], after['status']
    if a == b == 'hit':
        ratio = after['rank'] / before['rank']
        return {'status': 'exact', 'percent': 100 * (ratio - 1),
                'log2_gain': math.log2(ratio), 'bound_percent': None}
    if a == 'hit' and b == 'beyond_budget':
        return {'status': 'lower_bound', 'percent': None,
                'log2_gain': None,
                'bound_percent': 100 * (budget / before['rank'] - 1)}
    if a == 'beyond_budget' and b == 'hit':
        return {'status': 'upper_bound_regression', 'percent': None,
                'log2_gain': None,
                'bound_percent': 100 * (after['rank'] / budget - 1)}
    return {'status': 'unresolved' if 'unresolved' in (a, b) else 'both_beyond_budget',
            'percent': None, 'log2_gain': None, 'bound_percent': None}


def evaluate_users(records, runs, budget):
    summary = Counter()
    for record in records:
        if record['final'] is None:
            detail = {'status': 'not_applicable', 'percent': None,
                      'log2_gain': None, 'bound_percent': None}
            before = after = None
        else:
            before = observed_rank(record['original'], runs, budget)
            after = observed_rank(record['final'], runs, budget)
            detail = rank_change(before, after,
                                 same_password=record['original'] == record['final'],
                                 budget=budget)
        summary[detail['status']] += 1
        yield {'user_id': record['user_id'], 'cohort_id': record['cohort_id'],
               'policy_id': record['policy_id'], 'modified': record['modified'],
               'attempts': record['attempts'], 'edit_distance': record['edit_distance'],
               'response_kind': record['response_kind'],
               'original_rank': before, 'final_rank': after,
               'strength_change': detail}
