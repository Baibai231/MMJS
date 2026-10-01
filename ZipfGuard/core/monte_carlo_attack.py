"""Estimated guess-rank evaluation; all original users stay in denominators."""
import math
from ai.pcfg_monte_carlo import VERSION


class RankLookup:
    def __init__(self, index, rule=None):
        self.index, self.rule, self.cache = index, rule, {}

    def detail(self, word):
        if word not in self.cache:
            self.cache[word] = self.index.query(word, self.rule)
        return self.cache[word]

    def get(self, word, default=None):
        value = self.detail(word)['guess_count']
        return default if value is None else value

    def __contains__(self, word):
        return self.get(word) is not None

    def __getitem__(self, word):
        value = self.get(word)
        if value is None:
            raise KeyError(word)
        return value


class MCRun:
    model = 'pcfg'
    estimated = True

    def __init__(self, index, budget, rule=None):
        self.index, self.rule = index, rule
        self.ranks = RankLookup(index, rule)
        self.raw_ranks = {}
        self.stats = {'requested_budget': budget, 'completed_budget': None,
                      'charged_count': 0, 'raw_generated_count': index.n,
                      'stop_reason': 'monte_carlo_index_ready', 'exhausted': False}
        self.parameters = {**index.grammar.metadata, 'estimator': VERSION,
                           'samples': index.n, 'seed': index.seed,
                           'policy': rule.summary() if rule else None}

    def complete_at(self, k):
        # No finite candidate stream was exhaustively generated.
        return False

    def summary(self):
        return {'model': self.model, 'method': VERSION, 'estimated': True,
                'budget_unit': 'estimated_unique_pcfg_guesses', **self.stats,
                'parameters': self.parameters}


def evaluate_mc(runs, targets, budgets, total=None):
    total = sum(targets.values()) if total is None else total
    if total < sum(targets.values()):
        raise ValueError('Attack denominator cannot discard target users')
    run = runs[0]
    details = [(run.ranks.detail(w), n) for w, n in targets.items()]
    outside = sum(n for d, n in details if d['status'] == 'outside_model_support')
    low = sum(n for d, n in details if d['status'] == 'low_sample_support')
    points = []
    for budget in budgets:
        hits = sum(n for d, n in details if d['guess_count'] is not None and d['guess_count'] <= budget)
        points.append({'budget': budget, 'rate': hits / total if total else None,
                       'hits': hits, 'target_weight': total, 'complete': False,
                       'estimated': True, 'status': 'monte_carlo_estimate',
                       'lower_bound': None, 'upper_bound': None,
                       'unresolved_weight': low, 'low_sample_support_weight': low,
                       'outside_model_support_weight': outside,
                       'pending_weight': total - sum(targets.values())})
    return {'minauto': points, 'models': [{'run': run.summary(), 'points': points}],
            'method': VERSION, 'denominator': 'all original users; frequency weighted',
            'interpretation': 'point estimates, not observed cracking or exact ranks; low-support weights are disclosed'}


def mc_difficulty(runs, targets, budget, fractions=(.1, .25, .5)):
    total = sum(targets.values())
    ranks = sorted((runs[0].ranks.get(w, math.inf), n) for w, n in targets.items())
    result = {}
    for fraction in fractions:
        mass, value = 0, None
        for rank, n in ranks:
            mass += n
            if mass >= fraction * total:
                value = rank if math.isfinite(rank) else None
                break
        result[str(fraction)] = {'guess_count': value,
                                'status': 'estimated' if value is not None else 'outside_model_support'}
    return {'status': 'estimated', 'budget': budget, 'quantiles': result,
            'target_users': total, 'truncated_mean': None}
