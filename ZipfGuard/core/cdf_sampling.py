"""Reproducible CDF-Zipf occupancy fitting adapted from the supplied GSS script.

The model samples latent bins floor((U/c)**(1/s)), then ranks their observed
counts. It describes a sample's frequency shape, not a password identity PMF.
Rank zero is intentional: its mass is c, matching the supplied implementation.
"""
from __future__ import annotations

import math
import numpy as np

from core.distributions import _golden_min

FIT_VERSION = 'cdf-zipf-sampling-gss-v1'


def sorted_counts(counts):
    values = np.asarray(counts, dtype=float)
    if (values.ndim != 1 or not len(values) or not np.all(np.isfinite(values))
            or np.any(values < 0) or np.any(values != np.floor(values))
            or not 1 <= values.sum() <= 1_000_000):
        raise ValueError('需要总数 1 至 100 万的非负整数频次数组')
    return np.sort(values[values > 0].astype(np.int64))[::-1]


def frequency_sample(c, s, uniforms):
    if not 0 < c <= 1 or not 0 < s <= 1:
        raise ValueError('c 和 s 须位于 (0, 1]')
    u = np.asarray(uniforms, dtype=float)
    if u.ndim != 1 or not len(u) or not np.all(np.isfinite(u)) or np.any((u <= 0) | (u >= 1)):
        raise ValueError('采样值须位于 (0, 1)')
    log_ranks = (np.log(u) - math.log(c)) / s
    if log_ranks.max() > math.log(2**53 - 1):
        raise ValueError('潜在排名超过浮点整数精确范围')
    bins = np.floor(np.exp(log_ranks)).astype(np.int64)
    _, counts = np.unique(bins, return_counts=True)
    return np.sort(counts)[::-1]


def ranked_cdf(counts, length=None):
    values = sorted_counts(counts)
    result = np.cumsum(values, dtype=float) / values.sum()
    if length is None:
        return result
    return np.pad(result[:length], (0, max(0, length-len(result))), constant_values=1.)


def cdf_distance(first, second):
    # Exhausted support contributes CDF=1; neither distribution is truncated.
    size = max(len(first), len(second))
    delta = ranked_cdf(first, size) - ranked_cdf(second, size)
    return float(np.max(np.abs(delta)))


def _uniforms(n, seed):
    return np.maximum(np.random.default_rng(seed).random(n), np.finfo(float).tiny)


def fit_cdf_sampling(counts, *, seed=42, iterations=12):
    values = sorted_counts(counts)
    n, k = int(values.sum()), len(values)
    if k < 2:
        return {'fit_version': FIT_VERSION, 'parameters': {'c': 1., 's': 1.},
                'training_max_cdf_error': 0., 'seed': seed, 'samples': n,
                'evaluations': 0, 'status': 'degenerate_point_mass',
                'interpretation': 'one observed category; c=s=1 is a sampling convention, not identifiable estimates'}
    if not 4 <= iterations <= 30:
        raise ValueError('搜索迭代数须介于 4 与 30')
    real_cdf = ranked_cdf(values)
    ranks = np.unique(np.geomspace(1, k, min(k, 1024)).astype(int))
    slope, intercept = np.polyfit(np.log(ranks), np.log(real_cdf[ranks-1]), 1)
    initial_c = float(np.clip(np.exp(intercept), 1e-9, 1.))
    initial_s = float(np.clip(slope, 1e-4, 1.))
    xlow, xhigh = math.log(max(1e-9, initial_c/3)), math.log(min(1., initial_c*3))
    slow, shigh = max(1e-4, initial_s/3), min(1., initial_s*3)
    common_uniforms = _uniforms(n, seed)
    history = {}

    def objective(log_c, s):
        key = (float(log_c), float(s))
        if key not in history:
            try:
                sample = frequency_sample(math.exp(log_c), s, common_uniforms)
                error = cdf_distance(values, sample)
            except ValueError:
                error = math.inf
            history[key] = error
        return history[key]

    objective(math.log(initial_c), initial_s)
    # A coarse grid and best-of-history retention protect against a non-unimodal
    # discrete sampling objective. GSS is a heuristic, not a global guarantee.
    for x in np.linspace(xlow, xhigh, 5):
        for s in np.linspace(slow, shigh, 5):
            objective(x, s)

    def profile(s):
        x = _golden_min(lambda x: objective(x, s), xlow, xhigh, iterations)
        return objective(x, s)

    _golden_min(profile, slow, shigh, iterations)
    (best_x, best_s), error = min(history.items(), key=lambda item: item[1])
    if not math.isfinite(error):
        raise ValueError('参数范围内没有数值有效的采样模型')
    return {'fit_version': FIT_VERSION, 'parameters': {'c': math.exp(best_x), 's': best_s},
            'training_max_cdf_error': error, 'seed': seed, 'samples': n,
            'evaluations': len(history), 'initial_parameters': {'c': initial_c, 's': initial_s},
            'search_bounds': {'c': [math.exp(xlow), math.exp(xhigh)], 's': [slow, shigh]},
            'objective': 'maximum absolute ranked CDF error, fixed common random numbers',
            'interpretation': 'sample frequency shape; not an identity-aware password probability model'}


def replicate_fit(counts, fit, *, seeds):
    values = sorted_counts(counts)
    params = fit['parameters']
    errors, cdfs = [], []
    for seed in seeds:
        sample = frequency_sample(params['c'], params['s'], _uniforms(int(values.sum()), seed))
        errors.append(cdf_distance(values, sample))
        cdfs.append(ranked_cdf(sample, len(values)))
    if not errors:
        raise ValueError('需要独立复核种子')
    return {'seeds': list(seeds), 'max_cdf_errors': errors,
            'mean_max_cdf_error': float(np.mean(errors)),
            'worst_max_cdf_error': max(errors),
            'mean_cdf': np.mean(cdfs, axis=0).tolist(),
            'scope': 'independent simulation draws against the same observed data; not held-out accounts'}
