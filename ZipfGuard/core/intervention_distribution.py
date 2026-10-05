"""Distribution-only objective for Google-started local interventions.

The fixed top-k rank is shared by all arms. The CDF sampling fit supplies the
decision score; empirical top-k mass is a separate sanity check, not a weight.
"""
from collections import Counter
import math

import numpy as np

from core.cdf_sampling import fit_cdf_sampling, frequency_sample


def top_k(population, cfg):
    return cfg['controller'].get('distribution_top_k') or max(
        1, math.ceil(len(population.counts()) * cfg['controller']['distribution_top_fraction']))


def empirical_top_mass(counts, k):
    total = sum(counts.values())
    return sum(sorted(counts.values(), reverse=True)[:k]) / total


def moved_counts(counts, rows):
    updated = Counter(counts)
    for row in rows:
        if row.get('status', 'changed') == 'changed' and row['new'] != row['old']:
            updated[row['old']] -= 1
            updated[row['new']] += 1
    return Counter({word: count for word, count in updated.items() if count > 0})


def fitted_top_mass(counts, k, seed):
    """Evaluate the fitted ranked CDF at a fixed rank using independent draws."""
    fit = fit_cdf_sampling(list(counts.values()), seed=seed)
    n = sum(counts.values())
    parameters = fit['parameters']
    masses = []
    for offset in (1001, 1002, 1003):
        uniforms = np.maximum(np.random.default_rng(seed + offset).random(n),
                              np.finfo(float).tiny)
        frequencies = frequency_sample(parameters['c'], parameters['s'], uniforms)
        masses.append(float(frequencies[:k].sum() / n))
    return {'top_mass': sum(masses) / len(masses),
            'parameters': parameters,
            'fit_error': fit['training_max_cdf_error'],
            'rank': k, 'replicate_top_masses': masses}
