"""Distribution-only objective for Google-started local interventions.

The CDF sampling fit supplies log-rank W1 to a fixed N-singleton reference.
Empirical area/top-k mass remain screening and diagnostic quantities, not weights.
"""
from collections import Counter
from functools import lru_cache
import math

import numpy as np

from core.cdf_sampling import fit_cdf_sampling, frequency_sample
from core.ideal_distribution import distance_from_area, ideal_log_area


def top_k(population, cfg):
    return cfg['controller'].get('distribution_top_k') or max(
        1, math.ceil(len(population.counts()) * cfg['controller']['distribution_top_fraction']))


def empirical_top_mass(counts, k):
    total = sum(counts.values())
    return sum(sorted(counts.values(), reverse=True)[:k]) / total


def log_cdf_area(counts):
    """Area of the ranked CDF against log rank on the fixed 1..N axis.

    The equivalent closed form avoids constructing a padded N-element CDF.
    This is a distribution score, not a percentage of cracked accounts.
    """
    frequencies = sorted((int(v) for v in counts.values() if v > 0), reverse=True) if hasattr(counts, 'values') else sorted((int(v) for v in counts if v > 0), reverse=True)
    n = sum(frequencies)
    if n < 2:
        return 1.
    return sum((f/n) * math.log(n/r) for r, f in enumerate(frequencies, 1)) / math.log(n)


def fitted_log_cdf_area_from_parameters(parameters, n, seed):
    """Evaluate the complete fitted CDF with common independent sample draws."""
    scores = []
    for offset in (1001, 1002, 1003):
        uniforms = np.maximum(np.random.default_rng(seed + offset).random(n),
                              np.finfo(float).tiny)
        scores.append(log_cdf_area(frequency_sample(parameters['c'], parameters['s'], uniforms)))
    return {'score': sum(scores)/len(scores), 'replicate_scores': scores,
            'parameters': parameters}


def fitted_log_cdf_area(counts, seed):
    # The fitter uses the ranked frequency multiset, never password identity.
    # Caching repeated no-change simulations is exact and stores no passwords.
    shape = tuple(sorted(Counter(counts.values()).items()))
    return _fitted_log_cdf_area_shape(shape, seed)


def fitted_ideal_distance(counts, seed):
    area = fitted_log_cdf_area(counts, seed)
    n = sum(counts.values())
    return {**area, 'score': distance_from_area(area['score'], n),
            'raw_log_area': area['score'], 'ideal_log_area': ideal_log_area(n),
            'replicate_scores': [distance_from_area(s, n) for s in area['replicate_scores']]}


@lru_cache(maxsize=512)
def _fitted_log_cdf_area_shape(shape, seed):
    values = [frequency for frequency, multiplicity in shape
              for _ in range(multiplicity)]
    fit = fit_cdf_sampling(values, seed=seed)
    result = fitted_log_cdf_area_from_parameters(fit['parameters'], sum(values), seed)
    return {**result, 'fit_error': fit['training_max_cdf_error']}


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
