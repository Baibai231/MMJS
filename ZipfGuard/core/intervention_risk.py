"""Frequency-weighted estimates; unsupported passwords never create free gains."""
from core.monte_carlo_attack import MCRun, evaluate_mc


class InterventionRisk:
    def __init__(self, index, budgets, risk_budget):
        self.index, self.budgets, self.budget = index, list(budgets), risk_budget
        self.run = MCRun(index, max(budgets))

    def detail(self, word):
        return self.run.ranks.detail(word)

    def hit(self, word):
        rank = self.detail(word)['guess_count']
        return float(rank is not None and rank <= self.budget)

    def loss(self, word):
        # Decision proxy only: assign unresolved model-support mass full loss.
        # This is NOT an empirical cracking rate or a confidence bound.
        return 1. if self.detail(word)['guess_count'] is None else self.hit(word)

    def evaluate(self, counts):
        n = sum(counts.values())
        result = evaluate_mc([self.run], counts, self.budgets, n)
        result['guarded_risk'] = sum(c*self.loss(w) for w, c in counts.items())/n
        result['selection_metric'] = 'PCFG point-estimated hits + outside-model-support mass'
        result['risk_budget'] = self.budget
        return result


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
