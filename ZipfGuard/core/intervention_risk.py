"""Frequency-weighted estimates; unsupported passwords never create free gains."""
import math
from core.monte_carlo_attack import MCRun, evaluate_mc


class InterventionRisk:
    def __init__(self, index, budgets, risk_budget):
        self.index, self.budgets, self.budget = index, list(budgets), risk_budget
        self.run = None if getattr(index, 'dual_attack', False) else MCRun(index, max(budgets))

    def detail(self, word):
        return self.index.query(word) if self.run is None else self.run.ranks.detail(word)

    def passes_threshold(self, word, budget=None):
        if self.run is not None:
            raise ValueError('个体门槛不能由 PCFG 点估计或未覆盖状态认证')
        return self.index.passes_threshold(word, self.budget if budget is None else budget)

    def hit(self, word):
        rank = self.detail(word)['guess_count']
        return float(rank is not None and rank <= self.budget)

    def loss(self, word):
        # Decision proxy only: assign unresolved model-support mass full loss.
        # This is NOT an empirical cracking rate or a confidence bound.
        return 1. if self.detail(word)['status'] == 'outside_model_support' else self.hit(word)

    def evaluate(self, counts):
        n = sum(counts.values())
        result = (self.index.evaluate(counts, self.budgets) if self.run is None else
                  evaluate_mc([self.run], counts, self.budgets, n))
        result['guarded_risk'] = sum(c*self.loss(w) for w, c in counts.items())/n
        result['selection_metric'] = ('PCFG + OMEN observed union + outside-domain diagnostic' if self.run is None
                                      else 'PCFG point-estimated hits + outside-model-support mass')
        result['risk_budget'] = self.budget
        return result


class CombinedInterventionRisk:
    """Fixed-F primary endpoint with adaptive-model and concentration diagnostics."""
    def __init__(self, *models, baseline_counts=None, risk_weight=.8):
        self.models = tuple(models)
        if not self.models:
            raise ValueError('至少需要一个攻击模型')
        self.budgets = tuple(self.models[0].budgets)
        logs = [math.log10(b) for b in self.budgets]
        if len(logs) == 1:
            self.weights = (1.,)
        else:
            widths = [logs[i + 1] - logs[i] for i in range(len(logs) - 1)]
            total = sum(widths)
            self.weights = tuple((widths[0] if i == 0 else widths[-1]
                                  if i == len(logs) - 1 else widths[i - 1] + widths[i]) / (2 * total)
                                 for i in range(len(logs)))
        self.risk_weight = risk_weight
        self.baseline_counts = baseline_counts
        self.baseline_risk = self.raw_risk(baseline_counts) if baseline_counts else 1.
        self.baseline_hhi_excess = self.hhi_excess(baseline_counts) if baseline_counts else 1.

    def _curve_hit(self, model, word):
        rank = model.detail(word)['guess_count']
        return 0. if rank is None else sum(weight for budget, weight in zip(self.budgets, self.weights)
                                           if rank <= budget)

    def hit(self, word):
        return self.models[0].hit(word)

    def primary_risk(self, counts):
        total = sum(counts.values())
        return sum(count * self.hit(word) for word, count in counts.items()) / total

    def adaptive_hit(self, word):
        return self.models[1].hit(word) if len(self.models) > 1 else self.hit(word)

    def loss(self, word):
        # Unsupported F words retain full conservative loss; their migration
        # to another unsupported word cannot count as a security improvement.
        frozen = (1. if self.models[0].detail(word)['guess_count'] is None
                  else self._curve_hit(self.models[0], word))
        other = sum(self._curve_hit(model, word) for model in self.models[1:])
        return (frozen + other) / len(self.models)

    def raw_risk(self, counts):
        total = sum(counts.values())
        return sum(count * self.loss(word) for word, count in counts.items()) / total

    @staticmethod
    def hhi_excess(counts):
        total = sum(counts.values())
        return sum(count * (count - 1) for count in counts.values()) / (total * total)

    def objective(self, counts):
        return (self.risk_weight * self.raw_risk(counts) / max(self.baseline_risk, 1e-12)
                + (1 - self.risk_weight) * self.hhi_excess(counts)
                / max(self.baseline_hhi_excess, 1e-12))

    def gain_components(self, rows, counts):
        total = sum(counts.values())
        risk_gain = sum(self.loss(row['old']) - self.loss(row['new']) for row in rows) / total
        from collections import Counter
        delta = Counter()
        for row in rows:
            delta[row['old']] -= 1
            delta[row['new']] += 1
        hhi_delta = sum((counts.get(word, 0) + change) ** 2 - counts.get(word, 0) ** 2
                        for word, change in delta.items()) / (total * total)
        shape_gain = -hhi_delta / max(self.baseline_hhi_excess, 1e-12)
        combined = (self.risk_weight * risk_gain / max(self.baseline_risk, 1e-12)
                    + (1 - self.risk_weight) * shape_gain)
        return risk_gain, shape_gain, combined, hhi_delta

    def guarded_risk(self, counts):
        return self.raw_risk(counts)


def reference_mutation_ranks(reference, limit):
    """Independent reference words only; no target originals or rank-guided response."""
    ranks = {}
    for word in sorted(reference, key=lambda w: (-reference[w], w))[:limit]:
        for value in (word, word+'1', word+'!', word+'2026', word+'2026!',
                      word+'@7', word[:1].upper()+word[1:]+'1!'):
            if value not in ranks:
                ranks[value] = len(ranks)+1
    return ranks


def evaluate_mutations(counts, ranks, budgets):
    n = sum(counts.values())
    return {'method': 'independent-reference-mutations-v1',
            'interpretation': '有限参考词表变换攻击；不是全部规则感知攻击的上界',
            'unique_guesses': len(ranks),
            'points': [{'budget': b, 'effective_guesses': min(b, len(ranks)),
                        'exhausted': b >= len(ranks),
                        'rate': sum(c for w, c in counts.items() if ranks.get(w, float('inf')) <= b)/n}
                       for b in budgets]}
