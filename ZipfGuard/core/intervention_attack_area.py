"""Fixed-F population guard: normalized trapezoid area on the plotted log axis."""
from collections import Counter
from functools import lru_cache
import math

TOLERANCE = 1e-12
GUARD_VERSION = 'fixed-F-log-budget-trapezoid-area-v1'


@lru_cache(maxsize=32)
def log_budget_weights(budgets):
    if len(budgets) < 2 or any(b <= 0 for b in budgets) or tuple(sorted(set(budgets))) != budgets:
        raise ValueError('总体猜测面积需要至少两个递增的正猜测预算')
    logs = [math.log10(b) for b in budgets]
    span = logs[-1]-logs[0]
    weights = [0.] * len(budgets)
    for i in range(len(budgets)-1):
        half = (logs[i+1]-logs[i])/(2*span)
        weights[i] += half
        weights[i+1] += half
    return tuple(weights)


def curve_area(evaluation):
    points = evaluation['minauto']
    weights = log_budget_weights(tuple(p['budget'] for p in points))
    return sum(w*p['rate'] for w, p in zip(weights, points))


def fixed_model(risk):
    return risk.models[0] if getattr(risk, 'models', None) else risk


def area_change(risk, rows, total, budgets):
    """Exact change of the displayed polygon area; unknown mass is separate."""
    model = fixed_model(risk)
    weights = log_budget_weights(tuple(budgets))
    delta = Counter()
    for row in rows:
        if row.get('status', 'changed') == 'changed':
            delta[row['old']] -= 1
            delta[row['new']] += 1
    area_delta, unknown_delta = 0., 0
    for word, count in delta.items():
        if not count:
            continue
        rank = model.detail(word)['guess_count']
        if rank is None and model.detail(word)['status'] == 'outside_model_support':
            unknown_delta += count
        elif rank is not None:
            area_delta += count*sum(w for w, b in zip(weights, budgets) if rank <= b)
    return {'gain': -area_delta/total, 'uncovered_rate_change': unknown_delta/total}


def guard_reasons(change):
    reasons = []
    if change['gain'] <= TOLERANCE:
        reasons.append('固定 F 猜测成功曲线的对数面积未下降')
    if change['uncovered_rate_change'] > TOLERANCE:
        reasons.append('固定 F 模型未覆盖账户比例增加，无法确认总体改善')
    return reasons


def guard_batch(population, outcomes, risk, budgets):
    """Accept or reject all proposed edits; a notified rejected batch still costs."""
    change = area_change(risk, outcomes, population.total, budgets)
    reasons = guard_reasons(change)
    accepted = not reasons
    rows = [dict(row) for row in outcomes]
    if not accepted:
        for row in rows:
            if row['status'] == 'changed':
                row.update(new=row['old'], status='failed_to_comply', edit_cost=0.,
                           aggregate_guard_rejected=True)
    return rows, {'protocol': GUARD_VERSION, 'accepted': accepted,
                  'proposed_area_gain': change['gain'],
                  'proposed_uncovered_rate_change': change['uncovered_rate_change'],
                  'realized_area_gain': change['gain'] if accepted else 0.,
                  'rejection_reasons': reasons,
                  'rejected_modifications': sum(bool(r.get('aggregate_guard_rejected')) for r in rows)}


def strength_diagnostics(rows, risk):
    model = fixed_model(risk)
    counts = dict(improved=0, weakened=0, equal=0, uncomparable=0)
    for row in rows:
        if row['status'] != 'changed':
            continue
        old_detail, new_detail = model.detail(row['old']), model.detail(row['new'])
        before = old_detail['guess_count']
        after = new_detail['guess_count']
        if (before is not None and after is None and
                new_detail.get('status') == 'beyond_tested_budget' and
                new_detail.get('rank_lower_bound', 0) > before):
            counts['improved'] += 1
            continue
        if (after is not None and before is None and
                old_detail.get('status') == 'beyond_tested_budget' and
                old_detail.get('rank_lower_bound', 0) > after):
            counts['weakened'] += 1
            continue
        key = ('uncomparable' if before is None or after is None else
               'improved' if after > before else 'weakened' if after < before else 'equal')
        counts[key] += 1
    return counts
