"""Budgeted contextual candidate search, with independent empirical-W1 validation.

Learning ranks simulation opportunities; it does not bypass individual gates.
No classical LinUCB regret or real-user confidence guarantee is claimed.
"""
from copy import deepcopy
import hashlib
import math
import random
import numpy as np
from core.intervention_distribution import log_cdf_area, moved_counts
from core.ideal_distribution import distance_from_area
from policy.local_actions import generate_actions
from policy.intervention_response import IndividualThresholdUnavailable


def distance(counts):
    return distance_from_area(log_cdf_area(counts), sum(counts.values()))


class MeteredResponder:
    """Counts all calls, including failed calls, without modifying the responder."""
    def __init__(self, inner):
        self.inner = inner
        self.calls = self.preview_calls = self.account_attempts = 0

    def __getattr__(self, key):
        return getattr(self.inner, key)

    def respond(self, population, action, seed, stream):
        self.calls += 1
        self.account_attempts += len(action.indices)
        return self.inner.respond(population, action, seed, stream)

    def preview(self, population, action, seed, stream):
        self.calls += 1
        self.preview_calls += 1
        self.account_attempts += len(action.indices)
        return self.inner.preview(population, action, seed, stream)


class LinearModel:
    def __init__(self, dimension=35, ridge=1.):
        self.V = np.eye(dimension)*ridge
        self.z = np.zeros(dimension)
        self.observations = 0

    def clone(self):
        return deepcopy(self)

    def update(self, x, reward):
        if not np.isfinite(reward) or not np.isfinite(x).all():
            raise ValueError('Nonfinite learning feedback')
        self.V += np.outer(x, x)
        self.z += x*reward
        self.observations += 1

    def scores(self, X, beta):
        # Solve once for the entire candidate matrix.
        solved = np.linalg.solve(self.V, X.T).T
        mean = X @ np.linalg.solve(self.V, self.z)
        radius = np.sqrt(np.maximum(0., np.sum(X*solved, axis=1)))
        return mean+beta*radius


def features(pop, actions, cfg, round_id):
    counts = pop.counts()
    n = pop.total
    values = sorted(counts.values(), reverse=True)
    c = cfg['controller']
    remaining = max(0., c['total_fraction']-pop.ledger()['adaptive_affected']/n)
    context = [1., distance(counts), sum((v/n)**2 for v in values),
               sum(values[:10])/n, sum(values[:100])/n, remaining,
               round_id/c['max_rounds']]
    rows = []
    for a in actions:
        m = len(a.indices)/n
        repetition = sum(counts[pop.accounts[i].password]-1 for i in a.indices)/max(1, len(a.indices))
        repeat_scaled = math.log1p(repetition)/math.log1p(n)
        # Fixed scaling, no test-fitted standardization; 7+6+4+18 = 35.
        scalars = [m, a.eligible_count/n, repeat_scaled,
                   a.rule.base.min_length/20, m*repeat_scaled, m*remaining]
        group = [float(a.group.kind == k) for k in ('hot', 'structure', 'all', 'random')]
        number = a.rule.fragment.number if a.rule.fragment else 0
        rule = [float(number == k) for k in range(1,19)]
        rows.append(context+scalars+group+rule)
    return np.asarray(rows, dtype=float)


def candidates(pop, risk, responder, cfg, round_id):
    generated = generate_actions(pop, risk, cfg, minimum_length=8,
                                 catalog_reference=responder.reference, round_id=round_id)
    result, seen = [], set()
    for a in generated:
        # Exact same rule and selected identities is a redundant evaluation.
        key = (a.rule, a.indices)
        if key not in seen:
            seen.add(key)
            result.append(a)
    return result


def choose(pop, risk, responder, cfg, opt, model, method, round_id, stream='target'):
    actions = candidates(pop, risk, responder, cfg, round_id)
    audit = {'candidate_count': len(actions), 'mode': opt['mode'], 'simulation_calls': 0,
             'screen_stream': f'{stream}:screen:{round_id}',
             'validation_stream': f'{stream}:validate:{round_id}'}
    if not actions:
        return None, audit
    X = features(pop, actions, cfg, round_id)
    counts = pop.counts()
    before = distance(counts)
    heuristic = sorted(range(len(actions)), key=lambda j: (
        -sum(counts[pop.accounts[i].password]-1 for i in actions[j].indices), j))
    limit = min(opt['shortlist'], len(actions))
    if method == 'heuristic':
        selected = heuristic[:limit]
    elif method == 'random':
        seed = int.from_bytes(hashlib.sha256(f'{cfg["seed"]}:{stream}:{round_id}'.encode()).digest()[:8], 'big')
        selected = random.Random(seed).sample(range(len(actions)), limit)
    else:
        scores = model.scores(X, opt['beta'] if method == 'bandit' else 0.)
        ordered = sorted(range(len(actions)), key=lambda j: (-scores[j], j))
        selected = heuristic[:min(opt['reserved'],limit)]
        selected += [j for j in ordered if j not in selected][:limit-len(selected)]
    audit['selected_candidate_indices'] = selected
    failures = []

    def simulate(j, phase, repeats):
        gains = []
        for rep in range(repeats):
            audit['simulation_calls'] += 1
            try:
                rows = responder.preview(pop, actions[j], cfg['seed'],
                                         f'{stream}:{phase}:{round_id}:{rep}')
            except IndividualThresholdUnavailable:
                failures.append({'candidate': j, 'phase': phase})
                return None
            # Defense in depth, including rule validity and supported-domain gate.
            if (len(rows) != len(actions[j].indices) or
                {row['index'] for row in rows} != set(actions[j].indices) or
                any(row['status'] != 'changed' or row['new'] == row['old'] or
                    len(row['new']) < 8 or not actions[j].rule.accepts(row['new']) or
                    not risk.passes_threshold(row['new']) for row in rows)):
                failures.append({'candidate': j, 'phase': phase})
                return None
            gains.append(before-distance(moved_counts(counts, rows)))
        return gains

    screened = []
    for j in selected:
        values = simulate(j, 'screen', opt['screening_repeats'])
        if values is not None:
            screened.append((float(np.mean(values)), j))
    finalists = [j for _, j in sorted(screened, key=lambda item: (-item[0],item[1]))[:opt['finalists']]]
    delta = 6*opt['delta']/(math.pi**2*(round_id+1)**2)
    width = math.sqrt(2*math.log(2*max(1,len(finalists))/delta)/opt['validation_repeats'])
    checked = []
    for j in finalists:
        values = simulate(j, 'validate', opt['validation_repeats'])
        if values is not None:
            avg = float(np.mean(values))
            checked.append({'candidate': j, 'mean_gain': avg, 'strict_lower': avg-width,
                            'std_gain': float(np.std(values)), 'action': actions[j].public()})
    audit.update(validation=checked, failures=failures, strict_radius=width)
    score_key = 'strict_lower' if opt['mode'] == 'strict' else 'mean_gain'
    eligible = [r for r in checked if r[score_key] > cfg['controller']['min_gain']]
    if not eligible:
        return None, audit
    best = min(eligible, key=lambda r: (-r[score_key],len(actions[r['candidate']].indices),r['candidate']))
    j = best['candidate']
    return (actions[j], X[j], best), audit
