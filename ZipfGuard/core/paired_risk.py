"""Same-user risk differences conditional on fixed policy and attack streams."""
from __future__ import annotations

import math


def paired_attack_difference(pairs, left_runs, right_runs, budgets):
    """Positive difference means the left arm is easier to guess."""
    n = len(pairs)
    result = []
    for budget in budgets:
        complete = (n > 0 and left_runs and right_runs
                    and all(run.complete_at(budget)
                            for run in (*left_runs, *right_runs)))
        if not complete:
            result.append({'budget': budget, 'status': 'incomplete_budget',
                           'paired_users': n, 'difference': None,
                           'ci95_lower': None, 'ci95_upper': None})
            continue
        left_only = right_only = both = 0
        for left, right in pairs:
            lhit = any(run.ranks.get(left, math.inf) <= budget
                       for run in left_runs)
            rhit = any(run.ranks.get(right, math.inf) <= budget
                       for run in right_runs)
            left_only += lhit and not rhit
            right_only += rhit and not lhit
            both += lhit and rhit
        difference = (left_only - right_only) / n
        variance = max(0., ((left_only + right_only) / n - difference**2) / n)
        half = 1.96 * math.sqrt(variance)
        result.append({'budget': budget, 'status': 'complete',
                       'paired_users': n, 'left_only_hit': left_only,
                       'right_only_hit': right_only, 'both_hit': both,
                       'difference': difference,
                       'ci95_lower': max(-1., difference - half),
                       'ci95_upper': min(1., difference + half),
                       'interval_scope': 'normal approximation; fixed streams and selected policies'})
    return result
