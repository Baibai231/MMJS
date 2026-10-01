"""Finite-budget guess counts with honest completion status."""
from __future__ import annotations

import math


def guess_difficulty(runs, targets, budget, fractions=(.10, .25, .50)):
    if runs and getattr(runs[0], 'estimated', False):
        from core.monte_carlo_attack import mc_difficulty
        return mc_difficulty(runs, targets, budget, fractions)
    total = sum(targets.values())
    if not runs or not total:
        return {'status': 'unavailable', 'quantiles': {},
                'truncated_mean': None}
    complete = all(run.complete_at(budget) for run in runs)
    ranks = []
    truncated_total = 0
    for word, weight in targets.items():
        rank = min((run.ranks.get(word, math.inf) for run in runs),
                   default=math.inf)
        rank = rank if rank <= budget else math.inf
        if math.isfinite(rank):
            ranks.append((rank, weight))
        truncated_total += weight * min(rank, budget + 1)
    ranks.sort()
    quantiles = {}
    for fraction in fractions:
        threshold = fraction * total
        observed = 0
        value = None
        for rank, weight in ranks:
            observed += weight
            if observed >= threshold:
                value = rank
                break
        quantiles[str(fraction)] = {
            'guess_count': value if complete else None,
            'status': ('exact' if value is not None else 'beyond_budget')
                      if complete else 'incomplete_budget'}
    return {'status': 'complete' if complete else 'incomplete_budget',
            'budget': budget, 'observed_hit_users': sum(weight for _, weight in ranks),
            'target_users': total, 'quantiles': quantiles,
            'truncated_mean': truncated_total / total if complete else None,
            'truncated_mean_definition': 'mean min(first guess rank, K+1)'}
