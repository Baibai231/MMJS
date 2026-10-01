"""Comparable password concentration summaries for equal-size timelines."""
from __future__ import annotations

import math
from collections import Counter


def concentration(passwords, *, curve_limit=1000, full_curve=False):
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
    summary = {'users': n, 'unique': len(counts),
            'unique_rate': len(counts) / n if n else None,
            'collision_probability': collision,
            'entropy_bits': entropy,
            'normalized_entropy': entropy / math.log2(n) if n > 1 else None,
            'top_mass': {str(k): sum(ranked[:k]) / n if n else None
                         for k in (1, 10, 100, 1000)},
            'rank_curve': rank_curve, 'cdf_curve': cdf,
            'curve_tail_mass': 1 - acc / n if n else None,
            'curve_limit': curve_limit}
    if full_curve:
        # Lossless endpoints of constant-frequency plateaus, including the tail.
        curve = []
        start = 1
        for rank, count in enumerate(ranked, 1):
            if rank == 1:
                previous = count
            elif count != previous:
                curve.append([start, previous])
                if rank - 1 > start:
                    curve.append([rank - 1, previous])
                start, previous = rank, count
        if ranked:
            curve.append([start, previous])
            if len(ranked) > start:
                curve.append([len(ranked), previous])
        summary['full_rank_frequency'] = curve
    return summary


def combined_collision(history, predicted):
    """Use completed-user counts; a failure cannot improve concentration."""
    merged = Counter(history)
    merged.update(predicted)
    n = sum(merged.values())
    return sum(count * (count - 1) for count in merged.values()) / (n * (n - 1)) if n >= 2 else None


def forecast_collision(history, preview, batch_users):
    """Expected pair collision for a full next batch, estimated from a preview.

    Historical pairs are exact; cross pairs use preview frequencies; new-new
    pairs use the without-replacement estimate to avoid counting a sample with
    itself. Assumes future candidates follow the preview distribution.
    """
    h, m = sum(history.values()), sum(preview.values())
    total = h + batch_users
    if batch_users < 1 or m < 2 or total < 2:
        return None
    old_pairs = sum(c * (c - 1) for c in history.values())
    cross_pairs = 2 * batch_users * sum(history.get(w, 0) * c for w, c in preview.items()) / m
    new_pairs = batch_users * (batch_users - 1) * sum(c * (c - 1) for c in preview.values()) / (m * (m - 1))
    return (old_pairs + cross_pairs + new_pairs) / (total * (total - 1))
