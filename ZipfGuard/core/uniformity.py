"""Comparable password concentration summaries for equal-size timelines."""
from __future__ import annotations

import math
from collections import Counter


def concentration(passwords, *, curve_limit=1000):
    counts = passwords if isinstance(passwords, Counter) else Counter(passwords)
    n = sum(counts.values())
    ranked = sorted(counts.values(), reverse=True)
    collision = sum(c * (c - 1) for c in ranked) / (n * (n - 1)) if n >= 2 else None
    entropy = -sum((c / n) * math.log2(c / n) for c in ranked) if n else None
    cdf, rank_curve = [], []
    acc = 0
    for rank, count in enumerate(ranked[:curve_limit], 1):
        acc += count
        rank_curve.append([rank, count / n])
        cdf.append([rank, acc / n])
    return {'users': n, 'unique': len(counts),
            'unique_rate': len(counts) / n if n else None,
            'collision_probability': collision,
            'entropy_bits': entropy,
            'normalized_entropy': entropy / math.log2(n) if n > 1 else None,
            'top_mass': {str(k): sum(ranked[:k]) / n if n else None
                         for k in (1, 10, 100, 1000)},
            'rank_curve': rank_curve, 'cdf_curve': cdf,
            'curve_tail_mass': 1 - acc / n if n else None,
            'curve_limit': curve_limit}


def combined_collision(history, predicted):
    """Use completed-user counts; a failure cannot improve concentration."""
    merged = Counter(history)
    merged.update(predicted)
    n = sum(merged.values())
    return sum(count * (count - 1) for count in merged.values()) / (n * (n - 1)) if n >= 2 else None
