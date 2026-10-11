"""Exploratory paired pilot: unchanged joint controller and graded restrictions.

Only candidate generation changes in the proposed arm. No final-test claims.
"""
from collections import Counter
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path

from policy.registration_joint import JointRule, structure, candidates
from experiments.registration_joint_data import load_private, save_private


@dataclass(frozen=True)
class GradedRule(JointRule):
    soft_shapes: frozenset = frozenset()
    guessed: frozenset = frozenset()

    def accepts(self, word):
        return super().accepts(word) and not (
            structure(word) in self.soft_shapes and word in self.guessed)

    def signature(self):
        return super().signature() + (self.soft_shapes, self.guessed if self.soft_shapes else frozenset())

    def summary(self):
        return {**super().summary(), 'soft_shape_size': len(self.soft_shapes),
                'soft_shape_sha256': hashlib.sha256('\n'.join(sorted(self.soft_shapes)).encode()).hexdigest(),
                'soft_guess_set_sha256': hashlib.sha256('\n'.join(sorted(self.guessed)).encode()).hexdigest() if self.soft_shapes else None,
                'restriction_mode': 'within_structure' if self.soft_shapes else 'whole_structure_or_exact'}


def graded_candidates(base, recent, hold, ks, js, guessed):
    # Retain all original options. The added options use risk-ranked strings and
    # shapes; each risk score is the number of recent registered accounts hit.
    original = candidates(base, recent, hold, ks, js)
    risky_words = sorted((w for w, n in recent.items()
                         if n >= 2 and w in guessed and base.accepts(w)),
                         key=lambda w: (-recent[w], w))
    shapes, hit_shapes = Counter(), Counter()
    for w, n in recent.items():
        if base.accepts(w):
            shapes[structure(w)] += n
            if w in guessed:
                hit_shapes[structure(w)] += n
    ranked = sorted((s for s in shapes if shapes[s] >= 20 and hit_shapes[s] > 0),
                    key=lambda s: (-hit_shapes[s], -shapes[s], s))
    output, seen = list(original), {r.signature() for r, _ in original}
    for k in ks:
        for j in js:
            soft = frozenset(ranked[:j])
            # Exclude overlap: a string covered by a soft rule takes no extra
            # explicit-list slot. Fill the remaining k positions afterwards.
            exact = frozenset(w for w in risky_words if structure(w) not in soft)
            exact = frozenset(sorted(exact, key=lambda w: (-recent[w], w))[:k])
            rule = GradedRule(minimum=base.minimum, common=base.common,
                              exact=exact, k=k, j=j, soft_shapes=soft,
                              guessed=guessed if soft else frozenset())
            if rule.signature() not in seen:
                seen.add(rule.signature())
                output.append((rule, False))
    return output


class ReusableAttackCache:
    """Read old caches only when the exact counts/config cache key matches."""
    def __init__(self, cache, old_roots):
        self.cache, self.old_roots = cache, list(map(Path, old_roots))

    def __getattr__(self, name):
        return getattr(self.cache, name)

    def raw(self, counts, budget, model):
        from core.corpus import counts_hash
        key = counts_hash(counts)
        for root in self.old_roots:
            files = sorted((root / 'prefixes').glob(f'{key}-{model}-*.pkl.gz'))
            for p in files:
                available = int(p.name.split('-')[-1].split('.')[0])
                if available < budget:
                    continue
                prefix = load_private(p)
                if prefix['complete']:
                    return {**prefix, 'ranks': {w:r for w,r in prefix['ranks'].items() if r <= budget},
                            'raw_emissions': budget, 'requested_budget': budget,
                            'reused_from': str(p)}
        return self.cache.raw(counts, budget, model)
